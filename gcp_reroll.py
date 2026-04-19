from __future__ import annotations

from gcp_common import (
    Any,
    COOLDOWN_JITTER_CAP,
    COOLDOWN_JITTER_RATIO,
    Counter,
    OAUTH_CIRCUIT_BREAKER_BASE_COOLDOWN,
    OAUTH_CIRCUIT_BREAKER_MAX_COOLDOWN,
    OAUTH_CIRCUIT_BREAKER_STEP_COOLDOWN,
    OAUTH_CIRCUIT_BREAKER_THRESHOLD,
    REROLL_ERROR_COOLDOWN,
    REROLL_LOOP_COOLDOWN,
    REROLL_POST_STOP_FAST_COOLDOWN,
    REROLL_STOP_WAIT_THRESHOLD,
    RerollStats,
    instances_client,
    time,
)
from gcp_instance import (
    ensure_instance_running,
    ensure_instance_stopped,
    refresh_instance_info,
    wait_for_cpu_platform,
)
from gcp_state import load_json_state, save_json_state
from gcp_utils import (
    apply_jitter,
    format_duration,
    format_seconds,
    get_default_reroll_state_file,
    print_info,
    print_success,
    print_warning,
    remember_recent,
    sleep_with_countdown,
    summarize_exception,
    warn_if_long_pause,
)

__all__ = [
    'print_reroll_summary',
    'format_timestamp',
    'load_reroll_stats_from_file',
    'is_reroll_state_compatible',
    'print_reroll_state_snapshot',
    'print_reroll_progress',
    'get_soft_exception_count',
    'get_legacy_exception_count',
    'format_exception_breakdown',
    'is_oauth_timeout_error',
    'is_compute_timeout_error',
    'is_instance_stuck_error',
    'classify_reroll_exception',
    'format_exception_kind_label',
    'recalculate_exception_count',
    'record_reroll_exception',
    'get_oauth_circuit_breaker_cooldown',
    'get_reroll_cooldown_policy',
    'show_reroll_state',
    'reroll_cpu_loop',
]

def print_reroll_summary(stats: Any) -> Any:
    print("\n" + "-" * 50)
    print_info("刷 AMD 运行摘要")
    print(f"总耗时: {format_duration(time.time() - stats.start_time)}")
    soft_count = get_soft_exception_count(stats)
    print(f"尝试轮次: {stats.attempts} | 软异常轮次: {soft_count} | 硬异常轮次: {stats.hard_failure_count}")

    exception_breakdown = format_exception_breakdown(stats)
    if exception_breakdown:
        print(f"异常明细: {exception_breakdown}")

    if stats.success_cpu:
        print_success(f"命中目标 CPU: {stats.success_cpu}")

    if stats.cpu_counter:
        top_results = " | ".join(
            f"{platform} x{count}" for platform, count in Counter(stats.cpu_counter).most_common(5)
        )
        print(f"结果统计: {top_results}")

    if stats.recent_results:
        print(f"最近结果: {' -> '.join(stats.recent_results)}")

    if stats.recent_errors:
        print(f"最近异常: {' | '.join(stats.recent_errors)}")

def format_timestamp(timestamp_value: Any) -> Any:
    if not timestamp_value:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp_value))

def load_reroll_stats_from_file(state_path: Any) -> Any:
    payload = load_json_state(state_path, default=None)
    if not payload:
        return None
    required_keys = {"project_id", "instance_name", "zone", "start_time"}
    if not required_keys.issubset(payload):
        return None
    try:
        stats = RerollStats.from_dict(payload)
        recalculate_exception_count(stats)
        return stats
    except (TypeError, ValueError, KeyError):
        return None

def is_reroll_state_compatible(stats: Any,  project_id: Any=None,  instance_name: Any=None,  zone: Any=None) -> Any:
    if not stats:
        return False
    if project_id and stats.project_id != project_id:
        return False
    if instance_name and stats.instance_name != instance_name:
        return False
    if zone and stats.zone != zone:
        return False
    return True

def print_reroll_state_snapshot(stats: Any,  state_path: Any,  title: Any="刷 CPU 状态") -> Any:
    print("\n" + "-" * 50)
    print_info(title)
    print(f"状态文件: {state_path}")
    print(f"目标项目: {stats.project_id}")
    print(f"目标实例: {stats.instance_name} ({stats.zone})")
    print(f"开始时间: {format_timestamp(stats.start_time)}")
    print(f"最后更新: {format_timestamp(stats.last_updated)}")
    soft_count = get_soft_exception_count(stats)
    print(f"累计尝试: {stats.attempts} | 软异常: {soft_count} | 硬异常: {stats.hard_failure_count}")
    print(f"最近 CPU: {stats.last_cpu or '-'}")
    print(f"最近异常: {stats.last_error or '-'}")
    exception_breakdown = format_exception_breakdown(stats)
    if exception_breakdown:
        print(f"异常明细: {exception_breakdown}")
    if stats.success_cpu:
        print_success(f"状态文件记录的命中 CPU: {stats.success_cpu}")
    if stats.cpu_counter:
        top_results = " | ".join(
            f"{platform} x{count}" for platform, count in Counter(stats.cpu_counter).most_common(5)
        )
        print(f"累计结果: {top_results}")
    if stats.recent_results:
        print(f"最近结果: {' -> '.join(stats.recent_results)}")
    if stats.recent_errors:
        print(f"最近异常列表: {' | '.join(stats.recent_errors)}")

def print_reroll_progress(stats: Any,  state_path: Any) -> Any:
    top_cpu = "-"
    if stats.cpu_counter:
        top_cpu, top_count = Counter(stats.cpu_counter).most_common(1)[0]
        top_cpu = f"{top_cpu} x{top_count}"
    soft_count = get_soft_exception_count(stats)
    print_info(
        f"累计进度: 已尝试 {stats.attempts} 次 | 软异常 {soft_count} 次 | 硬异常 {stats.hard_failure_count} 次 | "
        f"最高频结果 {top_cpu} | 状态文件 {state_path}"
    )

def get_soft_exception_count(stats: Any) -> Any:
    return stats.oauth_timeout_count + stats.compute_timeout_count + stats.instance_stuck_count

def get_legacy_exception_count(stats: Any) -> Any:
    classified = get_soft_exception_count(stats) + stats.hard_failure_count
    return max(0, stats.exception_count - classified)

def format_exception_breakdown(stats: Any) -> Any:
    parts = []
    if stats.oauth_timeout_count:
        parts.append(f"OAuth 超时 {stats.oauth_timeout_count}")
    if stats.compute_timeout_count:
        parts.append(f"Compute 超时 {stats.compute_timeout_count}")
    if stats.instance_stuck_count:
        parts.append(f"实例卡住 {stats.instance_stuck_count}")
    if stats.hard_failure_count:
        parts.append(f"硬异常 {stats.hard_failure_count}")
    legacy_count = get_legacy_exception_count(stats)
    if legacy_count:
        parts.append(f"历史未分类 {legacy_count}")
    return " | ".join(parts)

def is_oauth_timeout_error(exc: Any) -> Any:
    return "oauth2.googleapis.com" in str(exc).lower()

def is_compute_timeout_error(exc: Any) -> Any:
    message = str(exc).lower()
    return "compute.googleapis.com" in message or "/compute/v1/" in message

def is_instance_stuck_error(exc: Any) -> Any:
    message = str(exc)
    return (
        "等待虚拟机 " in message
        and ("关停超时" in message or "启动超时" in message)
    )

def classify_reroll_exception(exc: Any) -> Any:
    if is_oauth_timeout_error(exc):
        return "oauth_timeout"
    if is_instance_stuck_error(exc):
        return "instance_stuck"
    if is_compute_timeout_error(exc):
        return "compute_timeout"
    return "hard_failure"

def format_exception_kind_label(exception_kind: Any) -> Any:
    return {
        "oauth_timeout": "OAuth 超时",
        "compute_timeout": "Compute 超时",
        "instance_stuck": "实例卡住",
        "hard_failure": "硬异常",
    }.get(exception_kind, "未知异常")

def recalculate_exception_count(stats: Any) -> Any:
    classified = get_soft_exception_count(stats) + stats.hard_failure_count
    if stats.exception_count < classified:
        stats.exception_count = classified
    return stats.exception_count

def record_reroll_exception(stats: Any,  exc: Any) -> Any:
    exception_kind = classify_reroll_exception(exc)
    if exception_kind == "oauth_timeout":
        stats.oauth_timeout_count += 1
        stats.consecutive_oauth_timeouts += 1
    else:
        stats.consecutive_oauth_timeouts = 0
        if exception_kind == "compute_timeout":
            stats.compute_timeout_count += 1
        elif exception_kind == "instance_stuck":
            stats.instance_stuck_count += 1
        else:
            stats.hard_failure_count += 1

    stats.exception_count += 1
    recalculate_exception_count(stats)

    summarized_error = summarize_exception(exc)
    stats.last_error = summarized_error
    remember_recent(
        stats.recent_errors,
        f"{format_exception_kind_label(exception_kind)}: {summarized_error}",
        limit=5,
    )
    return exception_kind, summarized_error

def get_oauth_circuit_breaker_cooldown(consecutive_oauth_timeouts: Any) -> Any:
    if consecutive_oauth_timeouts < OAUTH_CIRCUIT_BREAKER_THRESHOLD:
        return 0
    extra_steps = consecutive_oauth_timeouts - OAUTH_CIRCUIT_BREAKER_THRESHOLD
    cooldown = OAUTH_CIRCUIT_BREAKER_BASE_COOLDOWN + (extra_steps * OAUTH_CIRCUIT_BREAKER_STEP_COOLDOWN)
    return min(OAUTH_CIRCUIT_BREAKER_MAX_COOLDOWN, cooldown)

def get_reroll_cooldown_policy( had_exception: Any=False,  stop_wait_seconds: Any=0,  exception_kind: Any=None,  consecutive_oauth_timeouts: Any=0,  ) -> Any:
    # 正常轮次尽量快刷；只有异常时才放大退避，避免把 502/409 频率继续顶高。
    if exception_kind == "oauth_timeout":
        breaker_cooldown = get_oauth_circuit_breaker_cooldown(consecutive_oauth_timeouts)
        if breaker_cooldown > 0:
            return (
                breaker_cooldown,
                f"连续 {consecutive_oauth_timeouts} 轮 OAuth 超时，触发认证链路熔断",
            )
    if had_exception:
        return REROLL_ERROR_COOLDOWN, "本轮出现异常，使用保护性冷却"
    if stop_wait_seconds >= REROLL_STOP_WAIT_THRESHOLD:
        waited = format_duration(stop_wait_seconds)
        return REROLL_POST_STOP_FAST_COOLDOWN, f"本轮关停已耗时 {waited}，不再追加额外冷却"
    return REROLL_LOOP_COOLDOWN, "正常轮次，使用短冷却"

def show_reroll_state(state_file: Any=None,  project_id: Any=None,  instance_info: Any=None) -> Any:
    state_path = state_file or get_default_reroll_state_file()
    stats = load_reroll_stats_from_file(state_path)
    if not stats:
        print_warning(f"未找到有效的刷 CPU 状态文件: {state_path}")
        return False

    if instance_info and not is_reroll_state_compatible(
        stats,
        project_id=project_id,
        instance_name=instance_info.name,
        zone=instance_info.zone,
    ):
        print_warning("状态文件存在，但与当前选中的实例不一致，下面显示文件中的实际目标。")
    elif project_id and not is_reroll_state_compatible(stats, project_id=project_id):
        print_warning("状态文件存在，但与当前项目不一致，下面显示文件中的实际目标。")

    print_reroll_state_snapshot(stats, state_path, title="当前刷 CPU 状态")
    return True

def reroll_cpu_loop(project_id: Any,  instance_info: Any,  state_file: Any=None,  resume: Any=False) -> Any:
    instance_name = instance_info.name
    zone = instance_info.zone

    instance_client = instances_client()
    attempt_counter = 1
    state_path = state_file or get_default_reroll_state_file()
    stats = None

    if resume:
        existing_stats = load_reroll_stats_from_file(state_path)
        if existing_stats and is_reroll_state_compatible(
            existing_stats,
            project_id=project_id,
            instance_name=instance_name,
            zone=zone,
        ):
            # 已经命中过的状态文件只用于查看，不自动续跑，避免误以为脚本还在继续刷。
            if existing_stats.success_cpu:
                print_warning("检测到状态文件已记录命中结果，本次将忽略旧状态并重新开始。")
            else:
                stats = existing_stats
                attempt_counter = stats.attempts + 1
                print_info(f"检测到可恢复的状态文件，正在从第 {attempt_counter} 轮继续。")
                print_reroll_state_snapshot(stats, state_path, title="已恢复上次刷 CPU 状态")
        elif existing_stats:
            print_warning("检测到旧状态文件，但目标项目或实例不一致，将忽略并重新开始。")

    if stats is None:
        stats = RerollStats(
            project_id=project_id,
            instance_name=instance_name,
            zone=zone,
            start_time=time.time(),
        )
        stats.last_updated = time.time()
        save_json_state(state_path, stats.to_dict())

    print_info(f"目标实例: {instance_name} ({zone})")
    print_info("目标: 只要 CPU 包含 'AMD' 或 'EPYC' 即停止。")
    last_loop_activity_time = time.time()

    try:
        while True:
            last_loop_activity_time = warn_if_long_pause(
                last_loop_activity_time,
                f"刷 CPU 主循环（实例 {instance_name}）",
            )
            had_exception = False
            exception_kind = None
            stop_wait_seconds = 0
            stats.attempts += 1
            print("\n" + "=" * 50)
            print_info(f"第 {attempt_counter} 次尝试...")

            try:
                ensure_instance_running(instance_client, project_id, zone, instance_name)
                current_platform, current_status = wait_for_cpu_platform(
                    instance_client, project_id, zone, instance_name
                )

                if current_platform is None:
                    if current_status == "RUNNING":
                        current_platform = "CPU 信息同步超时"
                    else:
                        current_platform = f"实例未稳定运行（当前状态: {current_status}）"
                    print_warning(f"本轮未拿到有效 CPU 信息：{current_platform}")
                else:
                    print_info(f"检测到 CPU: {current_platform}")

                current_platform = str(current_platform)
                stats.cpu_counter[current_platform] = stats.cpu_counter.get(current_platform, 0) + 1
                stats.consecutive_oauth_timeouts = 0
                remember_recent(stats.recent_results, current_platform)
                stats.last_cpu = current_platform
                stats.last_error = None
                stats.last_updated = time.time()
                recalculate_exception_count(stats)
                save_json_state(state_path, stats.to_dict())
                print_reroll_progress(stats, state_path)

                current_platform_upper = current_platform.upper()
                if "AMD" in current_platform_upper or "EPYC" in current_platform_upper:
                    stats.success_cpu = current_platform
                    stats.last_updated = time.time()
                    save_json_state(state_path, stats.to_dict())
                    print_success(f"恭喜！已成功刷到目标 CPU: {current_platform}")
                    print_info("脚本执行完毕。")
                    break

                print_warning(f"结果不满意 ({current_platform})。准备重置...")
                print_info(f"正在关停虚拟机 {instance_name}...")
                _, stop_wait_seconds = ensure_instance_stopped(instance_client, project_id, zone, instance_name)
            except Exception as e:
                had_exception = True
                exception_kind, summarized_error = record_reroll_exception(stats, e)
                stats.last_updated = time.time()
                save_json_state(state_path, stats.to_dict())
                print_warning(
                    f"本轮尝试遇到{format_exception_kind_label(exception_kind)}，将自动恢复后继续: "
                    f"{summarized_error}"
                )
                print_reroll_progress(stats, state_path)

            attempt_counter += 1
            cooldown_base, cooldown_reason = get_reroll_cooldown_policy(
                had_exception=had_exception,
                stop_wait_seconds=stop_wait_seconds,
                exception_kind=exception_kind,
                consecutive_oauth_timeouts=stats.consecutive_oauth_timeouts,
            )
            cooldown = apply_jitter(
                cooldown_base,
                jitter_ratio=COOLDOWN_JITTER_RATIO,
                jitter_cap=COOLDOWN_JITTER_CAP,
            )
            if cooldown <= 0:
                print_info(f"{cooldown_reason}，直接开始下一轮尝试。")
            else:
                print_info(
                    f"{cooldown_reason}，正在冷却约 {format_seconds(cooldown)} 秒后继续..."
                )
                sleep_with_countdown(cooldown, "冷却中")
                print_info("冷却结束，开始下一轮尝试。")
            last_loop_activity_time = time.time()
    finally:
        print_reroll_summary(stats)
        print_reroll_state_snapshot(stats, state_path, title="刷 CPU 状态文件摘要")

    try:
        return refresh_instance_info(project_id, instance_info, announce=False)
    except Exception:
        return instance_info
