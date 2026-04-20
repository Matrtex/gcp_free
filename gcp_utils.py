from __future__ import annotations

from gcp_common import (
    Any,
    Counter,
    DEFAULT_REROLL_STATE_FILE,
    IMPORT_ERROR_MESSAGE,
    LOGGER,
    LOG_DIR_NAME,
    LONG_PAUSE_WARNING_THRESHOLD,
    OS_IMAGE_OPTIONS,
    REGION_OPTIONS,
    REQUIREMENTS_FILE,
    REROLL_RECENT_HISTORY_LIMIT,
    RETRY_JITTER_CAP,
    RETRY_JITTER_RATIO,
    SSH_CONNECT_TIMEOUT,
    SSH_SERVER_ALIVE_COUNT_MAX,
    SSH_SERVER_ALIVE_INTERVAL,
    SSH_STRICT_HOST_KEY_CHECKING,
    STATE_DIR_NAME,
    SUBPROCESS_ERROR_LINE_LIMIT,
    SUBPROCESS_ERROR_SUMMARY_LIMIT,
    configure_logger,
    ensure_client_libraries,
    get_region_config_from_config,
    get_runtime_root,
    math,
    os,
    random,
    resolve_asset_path,
    run_doctor,
    subprocess,
    sys,
    time,
)


__all__ = [
    'configure_stdio',
    'print_info',
    'print_success',
    'print_warning',
    'print_error',
    'flush_stdout',
    'ensure_libraries_or_exit',
    'format_seconds',
    'format_duration',
    'warn_if_long_pause',
    'sleep_and_detect_pause',
    'get_default_log_file',
    'get_default_reroll_state_file',
    'configure_runtime_logging',
    'sleep_with_countdown',
    'apply_jitter',
    'remember_recent',
    'make_remote_temp_path',
    'summarize_text_block',
    'get_region_config',
    'resolve_zone_for_create',
    'resolve_os_config',
    'build_ssh_option_values',
    'extend_ssh_options',
    'extend_gcloud_passthrough_flags',
    'format_command_for_log',
    'select_from_list',
    'prompt_manual_project_id',
    'prompt_project_selection',
    'summarize_exception',
    'is_not_found_error',
    'print_doctor_results',
    'handle_doctor',
]

def configure_stdio() -> Any:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if not stream or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(line_buffering=True, write_through=True)
        except Exception:
            continue

def print_info(msg: Any) -> Any:
    LOGGER.info(msg)
    flush_stdout()

def print_success(msg: Any) -> Any:
    LOGGER.success(msg)
    flush_stdout()

def print_warning(msg: Any) -> Any:
    LOGGER.warning(msg)
    flush_stdout()

def print_error(msg: Any) -> Any:
    LOGGER.error(msg)
    flush_stdout()

def flush_stdout() -> Any:
    try:
        sys.stdout.flush()
    except OSError:
        pass

def ensure_libraries_or_exit() -> Any:
    try:
        ensure_client_libraries()
    except RuntimeError:
        print(IMPORT_ERROR_MESSAGE)
        sys.exit(1)

def format_seconds(seconds: Any) -> Any:
    rounded = round(seconds, 1)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.1f}"

def format_duration(seconds: Any) -> Any:
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"

def warn_if_long_pause(last_activity_time: Any,  context: Any,  threshold: Any=LONG_PAUSE_WARNING_THRESHOLD) -> Any:
    now = time.time()
    if last_activity_time is None:
        return now

    gap = now - last_activity_time
    if gap >= threshold:
        print_warning(
            f"检测到长时间挂起/冻结：{context} 距上次活动已过去 {format_duration(gap)}，"
            "可能是系统休眠、远程会话挂起、网络阻塞或进程被暂停。"
        )
    return now

def sleep_and_detect_pause(seconds: Any,  context: Any,  threshold: Any=LONG_PAUSE_WARNING_THRESHOLD) -> Any:
    planned_seconds = max(0.0, float(seconds))
    started_at = time.time()
    time.sleep(planned_seconds)
    elapsed = time.time() - started_at

    if elapsed - planned_seconds >= float(threshold):
        print_warning(
            f"检测到本地进程可能被暂停/系统睡眠：{context} 原计划等待 "
            f"{format_duration(planned_seconds)}，实际过去 {format_duration(elapsed)}。"
            "常见原因是 Windows 睡眠、远程会话挂起或控制台选择模式；脚本已恢复继续运行。"
        )

    return elapsed

def get_default_log_file() -> Any:
    root_dir = str(get_runtime_root())
    log_dir = os.path.join(root_dir, LOG_DIR_NAME)
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "gcp_free.log")

def get_default_reroll_state_file() -> Any:
    root_dir = str(get_runtime_root())
    state_dir = os.path.join(root_dir, STATE_DIR_NAME)
    os.makedirs(state_dir, exist_ok=True)
    return os.path.join(state_dir, DEFAULT_REROLL_STATE_FILE)

def configure_runtime_logging(log_file: Any=None) -> Any:
    configure_logger(log_file or get_default_log_file())

def sleep_with_countdown(total_seconds: Any,  message: Any) -> Any:
    remaining_seconds = max(0.0, float(total_seconds))
    if remaining_seconds <= 0:
        return

    deadline = time.time() + remaining_seconds
    last_display = None

    while True:
        remaining = max(0.0, deadline - time.time())
        display_seconds = max(0, math.ceil(remaining))
        if display_seconds != last_display:
            should_print = (
                last_display is None
                or display_seconds <= 5
                or display_seconds % 5 == 0
            )
            if should_print:
                print_info(f"{message}，剩余 {display_seconds} 秒...")
            last_display = display_seconds

        if remaining <= 0:
            break

        sleep_and_detect_pause(min(0.2, remaining), message)

def apply_jitter(base_delay: Any,  jitter_ratio: Any=RETRY_JITTER_RATIO,  jitter_cap: Any=RETRY_JITTER_CAP) -> Any:
    if base_delay <= 0:
        return 0

    jitter_span = min(base_delay * jitter_ratio, jitter_cap)
    return base_delay + random.uniform(0, jitter_span)

def remember_recent(history: Any,  value: Any,  limit: Any=REROLL_RECENT_HISTORY_LIMIT) -> Any:
    history.append(value)
    if len(history) > limit:
        del history[0]

def make_remote_temp_path(prefix: Any,  suffix: Any) -> Any:
    return f"/tmp/{prefix}_{time.time_ns()}_{random.randint(1000, 9999)}{suffix}"

def summarize_text_block( text: Any,  max_lines: Any=SUBPROCESS_ERROR_LINE_LIMIT,  max_length: Any=SUBPROCESS_ERROR_SUMMARY_LIMIT,  ) -> Any:
    if not text:
        return ""

    cleaned_lines = []
    for raw_line in str(text).splitlines():
        line = " ".join(raw_line.strip().split())
        if line:
            cleaned_lines.append(line)

    if not cleaned_lines:
        return ""

    summary = "\n".join(cleaned_lines[:max_lines])
    if len(cleaned_lines) > max_lines:
        summary += "\n..."
    if len(summary) > max_length:
        summary = summary[: max_length - 3] + "..."
    return summary

def get_region_config(region: Any) -> Any:
    return get_region_config_from_config(region)

def resolve_zone_for_create(zone: Any=None,  region: Any=None) -> Any:
    if zone:
        return zone

    if not region:
        raise ValueError("非交互创建实例时必须提供 --zone 或 --region。")

    region_config = get_region_config(region)
    if not region_config:
        supported_regions = ", ".join(item["region"] for item in REGION_OPTIONS)
        raise ValueError(f"不支持的区域: {region}。可选值: {supported_regions}")

    return region_config["default_zone"]

def resolve_os_config(os_value: Any) -> Any:
    alias_map = {
        "debian": "debian-12",
        "debian-12": "debian-12",
        "ubuntu": "ubuntu-2204-lts",
        "ubuntu-2204-lts": "ubuntu-2204-lts",
    }
    normalized = alias_map.get((os_value or "").strip().lower())
    if not normalized:
        supported = ", ".join(sorted(alias_map))
        raise ValueError(f"不支持的操作系统选项: {os_value}。可选值: {supported}")

    for item in OS_IMAGE_OPTIONS:
        if item["family"] == normalized:
            return item

    raise ValueError(f"未找到操作系统配置: {os_value}")

def build_ssh_option_values(include_identities_only: Any=False) -> Any:
    option_values = [
        f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
        f"ServerAliveInterval={SSH_SERVER_ALIVE_INTERVAL}",
        f"ServerAliveCountMax={SSH_SERVER_ALIVE_COUNT_MAX}",
        "BatchMode=yes",
        f"StrictHostKeyChecking={SSH_STRICT_HOST_KEY_CHECKING}",
    ]
    if include_identities_only:
        option_values.append("IdentitiesOnly=yes")
    return option_values

def extend_ssh_options(cmd: Any,  option_values: Any) -> Any:
    for option_value in option_values:
        cmd += ["-o", option_value]
    return cmd

def extend_gcloud_passthrough_flags(cmd: Any,  flag_name: Any,  option_values: Any) -> Any:
    for option_value in option_values:
        cmd.append(f"{flag_name}=-o")
        cmd.append(f"{flag_name}={option_value}")
    return cmd

def format_command_for_log(cmd: Any) -> Any:
    return subprocess.list2cmdline([str(part) for part in cmd])

def select_from_list(items: Any,  prompt_text: Any,  label_fn: Any) -> Any:
    print(f"\n--- {prompt_text} ---")
    for i, item in enumerate(items):
        print(f"[{i+1}] {label_fn(item)}")
    while True:
        choice = input(f"请输入数字选择 (1-{len(items)}): ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return items[idx]
        print("输入无效，请重试。")

def prompt_manual_project_id() -> Any:
    while True:
        project_id = input("请输入项目 ID: ").strip()
        if project_id:
            return project_id
        print("输入不能为空，请重试。")

def prompt_project_selection(items: Any,  project_id_fn: Any,  display_name_fn: Any) -> Any:
    if not items:
        return None

    print("\n--- 请选择目标项目 ---")
    for i, item in enumerate(items):
        print(f"[{i+1}] {project_id_fn(item)} ({display_name_fn(item)})")

    while True:
        choice = input(f"请输入数字选择 (1-{len(items)}): ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                selected = items[idx]
                selected_project_id = project_id_fn(selected)
                selected_display_name = display_name_fn(selected)
                print_info(f"已选择项目: {selected_project_id} ({selected_display_name})")
                return selected_project_id
        print("输入无效，请重试。")

def summarize_exception(exc: Any,  max_length: Any=160) -> Any:
    message = " ".join(str(exc).split())
    if len(message) <= max_length:
        return message
    return message[: max_length - 3] + "..."

def is_not_found_error(exc: Any) -> Any:
    msg = str(exc).lower()
    return "notfound" in msg or "not found" in msg or "404" in msg

def print_doctor_results(checks: Any) -> Any:
    print("\n--- 环境预检结果 ---")
    status_counter = Counter(item.status for item in checks)
    for item in checks:
        prefix = {
            "PASS": "[通过]",
            "WARN": "[警告]",
            "FAIL": "[失败]",
        }.get(item.status, "[信息]")
        print(f"{prefix} {item.name}: {item.message}")
    print(
        f"\n汇总: 通过 {status_counter.get('PASS', 0)} 项 | "
        f"警告 {status_counter.get('WARN', 0)} 项 | 失败 {status_counter.get('FAIL', 0)} 项"
    )

def handle_doctor(project_id: Any=None) -> Any:
    requirements_path = str(resolve_asset_path(REQUIREMENTS_FILE))
    checks = run_doctor(requirements_path, project_id=project_id)
    print_doctor_results(checks)
    has_failures = any(item.status == "FAIL" for item in checks)
    if has_failures:
        raise RuntimeError("环境预检发现失败项，请先修复后再执行。")
