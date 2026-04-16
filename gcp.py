import argparse
from collections import Counter
import getpass
import math
import os
import random
import shutil
import subprocess
import sys
import time
import traceback

IMPORT_ERROR_MESSAGE = (
    "【错误】缺少必要的 Python 库。\n"
    "请先在终端运行以下命令安装：\n"
    "pip install google-cloud-compute google-cloud-resource-manager"
)

try:
    from google.cloud import compute_v1
    from google.cloud import resourcemanager_v3
    from google.api_core import exceptions as google_exceptions
except ImportError:
    compute_v1 = None
    resourcemanager_v3 = None
    google_exceptions = None

LOCAL_SCRIPT_FILES = {
    "apt": "apt.sh",
    "dae": "dae.sh",
    "net_iptables": "net_iptables.sh",
    "net_shutdown": "net_shutdown.sh",
}
FIREWALL_RULES_TO_CLEAN = [
    "allow-all-ingress-custom",
    "deny-cdn-egress-custom",
]

REGION_OPTIONS = [
    {"name": "俄勒冈 (Oregon) [推荐]", "region": "us-west1", "default_zone": "us-west1-b"},
    {"name": "爱荷华 (Iowa)", "region": "us-central1", "default_zone": "us-central1-f"},
    {"name": "南卡罗来纳 (South Carolina)", "region": "us-east1", "default_zone": "us-east1-b"},
]

OS_IMAGE_OPTIONS = [
    {"name": "Debian 12 (Bookworm)", "project": "debian-cloud", "family": "debian-12"},
    {"name": "Ubuntu 22.04 LTS", "project": "ubuntu-os-cloud", "family": "ubuntu-2204-lts"},
]

OPERATION_WAIT_TIMEOUT = 300
OPERATION_POLL_INTERVAL = 3
INSTANCE_API_MAX_RETRIES = 4
INSTANCE_API_RETRY_BASE_DELAY = 5
INSTANCE_CONFLICT_RETRY_DELAY = 10
INSTANCE_STATUS_WAIT_TIMEOUT = 180
INSTANCE_STATUS_POLL_INTERVAL = 3
CPU_PLATFORM_WAIT_TIMEOUT = 120
CPU_PLATFORM_POLL_INTERVAL = 2
REROLL_LOOP_COOLDOWN = 15
RETRY_JITTER_RATIO = 0.2
RETRY_JITTER_CAP = 3
COOLDOWN_JITTER_RATIO = 0.15
COOLDOWN_JITTER_CAP = 4
REROLL_RECENT_HISTORY_LIMIT = 8
REMOTE_READY_TIMEOUT = 180
REMOTE_READY_POLL_INTERVAL = 5
REMOTE_PROBE_TIMEOUT = 20
REMOTE_UPLOAD_TIMEOUT = 300
REMOTE_COMMAND_TIMEOUT = 1800
REMOTE_CONFIG_APPLY_TIMEOUT = 300
SSH_CONNECT_TIMEOUT = 10
SSH_SERVER_ALIVE_INTERVAL = 15
SSH_SERVER_ALIVE_COUNT_MAX = 3
SSH_STRICT_HOST_KEY_CHECKING = "accept-new"
SUBPROCESS_ERROR_SUMMARY_LIMIT = 600
SUBPROCESS_ERROR_LINE_LIMIT = 8


def print_info(msg):
    print(f"[信息] {msg}")
    sys.stdout.flush()


def print_success(msg):
    print(f"\033[92m[成功] {msg}\033[0m")
    sys.stdout.flush()


def print_warning(msg):
    print(f"\033[93m[警告] {msg}\033[0m")
    sys.stdout.flush()


def ensure_google_cloud_libraries():
    if compute_v1 and resourcemanager_v3 and google_exceptions:
        return
    print(IMPORT_ERROR_MESSAGE)
    sys.exit(1)


def format_seconds(seconds):
    rounded = round(seconds, 1)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.1f}"


def format_duration(seconds):
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def sleep_with_countdown(total_seconds, message):
    remaining_seconds = max(0.0, float(total_seconds))
    if remaining_seconds <= 0:
        return

    deadline = time.time() + remaining_seconds
    last_display = None
    last_message_length = 0

    while True:
        remaining = max(0.0, deadline - time.time())
        display_seconds = max(0, math.ceil(remaining))
        if display_seconds != last_display:
            output = f"\r[信息] {message}，剩余 {display_seconds} 秒..."
            sys.stdout.write(output)
            if last_message_length > len(output):
                sys.stdout.write(" " * (last_message_length - len(output)))
            sys.stdout.flush()
            last_display = display_seconds
            last_message_length = len(output)

        if remaining <= 0:
            break

        time.sleep(min(0.2, remaining))

    sys.stdout.write("\r" + " " * last_message_length + "\r")
    sys.stdout.flush()


def apply_jitter(base_delay, jitter_ratio=RETRY_JITTER_RATIO, jitter_cap=RETRY_JITTER_CAP):
    if base_delay <= 0:
        return 0

    jitter_span = min(base_delay * jitter_ratio, jitter_cap)
    return base_delay + random.uniform(0, jitter_span)


def remember_recent(history, value, limit=REROLL_RECENT_HISTORY_LIMIT):
    history.append(value)
    if len(history) > limit:
        del history[0]


def make_remote_temp_path(prefix, suffix):
    return f"/tmp/{prefix}_{time.time_ns()}_{random.randint(1000, 9999)}{suffix}"


def summarize_text_block(
    text,
    max_lines=SUBPROCESS_ERROR_LINE_LIMIT,
    max_length=SUBPROCESS_ERROR_SUMMARY_LIMIT,
):
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


def get_region_config(region):
    for config in REGION_OPTIONS:
        if config["region"] == region:
            return config
    return None


def resolve_zone_for_create(zone=None, region=None):
    if zone:
        return zone

    if not region:
        raise ValueError("非交互创建实例时必须提供 --zone 或 --region。")

    region_config = get_region_config(region)
    if not region_config:
        supported_regions = ", ".join(item["region"] for item in REGION_OPTIONS)
        raise ValueError(f"不支持的区域: {region}。可选值: {supported_regions}")

    return region_config["default_zone"]


def resolve_os_config(os_value):
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


def build_ssh_option_values(include_identities_only=False):
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


def extend_ssh_options(cmd, option_values):
    for option_value in option_values:
        cmd += ["-o", option_value]
    return cmd


def extend_gcloud_passthrough_flags(cmd, flag_name, option_values):
    for option_value in option_values:
        cmd.append(f"{flag_name}=-o")
        cmd.append(f"{flag_name}={option_value}")
    return cmd


def format_command_for_log(cmd):
    return subprocess.list2cmdline([str(part) for part in cmd])


def select_from_list(items, prompt_text, label_fn):
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


def prompt_manual_project_id():
    while True:
        project_id = input("请输入项目 ID: ").strip()
        if project_id:
            return project_id
        print("输入不能为空，请重试。")


def select_gcp_project():
    print_info("正在扫描您的项目列表...")
    try:
        client = resourcemanager_v3.ProjectsClient()
        request = resourcemanager_v3.SearchProjectsRequest(query="")
        page_result = search_projects_with_retry(client, request)

        active_projects = []
        for project in page_result:
            if project.state == resourcemanager_v3.Project.State.ACTIVE:
                active_projects.append(project)

        if not active_projects:
            print_warning("未找到活跃的项目。请手动输入项目 ID。")
            return prompt_manual_project_id()

        print("\n--- 请选择目标项目 ---")
        for i, p in enumerate(active_projects):
            print(f"[{i+1}] {p.project_id} ({p.display_name})")

        while True:
            choice = input(f"请输入数字选择 (1-{len(active_projects)}): ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(active_projects):
                    selected = active_projects[idx]
                    print_info(f"已选择项目: {selected.project_id} ({selected.display_name})")
                    return selected.project_id
            print("输入无效，请重试。")
    except Exception as e:
        print_warning(f"无法列出项目: {e}。请手动输入项目 ID。")
        return prompt_manual_project_id()


def list_zones_for_region(project_id, region):
    zones_client = compute_v1.ZonesClient()
    zones = []
    for zone in list_zones_with_retry(zones_client, project_id):
        if zone.status != "UP":
            continue
        zone_region = zone.region.split("/")[-1] if zone.region else ""
        if zone_region == region:
            zones.append(zone.name)
    return sorted(zones)


def select_zone(project_id):
    region_config = select_from_list(REGION_OPTIONS, "请选择部署区域", lambda r: r["name"])
    region = region_config["region"]
    default_zone = region_config["default_zone"]

    print_info(f"正在获取 {region} 的可用区列表...")
    try:
        zones = list_zones_for_region(project_id, region)
    except Exception as e:
        print_warning(f"获取可用区失败: {e}。将使用默认可用区 {default_zone}。")
        return default_zone

    if not zones:
        print_warning(f"未获取到可用区列表，使用默认可用区 {default_zone}。")
        return default_zone

    return select_from_list(zones, f"请选择可用区 ({region})", lambda z: z)


def select_os_image():
    return select_from_list(OS_IMAGE_OPTIONS, "请选择操作系统", lambda o: o["name"])


def create_instance(project_id, zone, os_config, instance_name="free-tier-vm"):
    instance_client = compute_v1.InstancesClient()
    images_client = compute_v1.ImagesClient()

    print(f"\n[开始] 正在 {project_id} 项目中准备资源...")
    print(f"可用区: {zone}")
    print(f"系统: {os_config['name']}")

    try:
        image_response = get_image_from_family_with_retry(
            images_client,
            os_config["project"],
            os_config["family"],
        )
        source_disk_image = image_response.self_link

        disk = compute_v1.AttachedDisk()
        disk.boot = True
        disk.auto_delete = True
        initialize_params = compute_v1.AttachedDiskInitializeParams()
        initialize_params.source_image = source_disk_image
        initialize_params.disk_size_gb = 30
        initialize_params.disk_type = f"zones/{zone}/diskTypes/pd-standard"
        disk.initialize_params = initialize_params

        network_interface = compute_v1.NetworkInterface()
        network_interface.name = "global/networks/default"

        access_config = compute_v1.AccessConfig()
        access_config.name = "External NAT"
        access_config.type_ = compute_v1.AccessConfig.Type.ONE_TO_ONE_NAT.name
        access_config.network_tier = compute_v1.AccessConfig.NetworkTier.STANDARD.name
        network_interface.access_configs = [access_config]

        instance = compute_v1.Instance()
        instance.name = instance_name
        instance.machine_type = f"zones/{zone}/machineTypes/e2-micro"
        instance.disks = [disk]
        instance.network_interfaces = [network_interface]

        tags = compute_v1.Tags()
        tags.items = ["http-server", "https-server"]
        instance.tags = tags

        print("配置组装完成，正在向 Google Cloud 发送创建请求...")
        operation = insert_instance_with_retry(instance_client, project_id, zone, instance)

        print("请求已发送，正在等待操作完成... (约 30-60 秒)")
        operation = wait_for_operation(project_id, zone, operation.name, f"创建实例 {instance_name}")

        if operation.error:
            print("创建失败:", operation.error)
        else:
            print_success(f"实例 '{instance_name}' 已创建！")
            try:
                inst_info = get_instance_with_retry(instance_client, project_id, zone, instance_name)
                ip = inst_info.network_interfaces[0].access_configs[0].nat_i_p
                print(f"外部 IP 地址: {ip}")
                print("请前往 GCP 控制台查看详情。")
                return build_instance_info(inst_info, zone)
            except Exception:
                print("请前往 GCP 控制台查看详情。")
                return {
                    "name": instance_name,
                    "zone": zone,
                    "status": "PROVISIONING",
                    "cpu_platform": "Unknown CPU Platform",
                    "network": "global/networks/default",
                    "internal_ip": "-",
                    "external_ip": "-",
                }

    except Exception as e:
        print(f"\n[失败] 操作中止: {e}")
        traceback.print_exc()
    return None


def build_instance_info(instance, zone):
    network = None
    internal_ip = "-"
    external_ip = "-"
    if instance.network_interfaces:
        network = instance.network_interfaces[0].network
        internal_ip = instance.network_interfaces[0].network_i_p
        access_configs = instance.network_interfaces[0].access_configs
        if access_configs:
            external_ip = access_configs[0].nat_i_p or "-"
    return {
        "name": instance.name,
        "zone": zone,
        "status": instance.status,
        "cpu_platform": instance.cpu_platform or "Unknown CPU Platform",
        "network": network or "global/networks/default",
        "internal_ip": internal_ip,
        "external_ip": external_ip,
    }


def get_instance_cache_key(project_id, instance_info):
    return f"{project_id}:{instance_info['zone']}:{instance_info['name']}"


def list_instances(project_id):
    instance_client = compute_v1.InstancesClient()
    request = compute_v1.AggregatedListInstancesRequest(project=project_id)

    print_info(f"正在扫描项目 {project_id} 中的实例...")

    instances = []
    for zone_path, response in aggregated_list_instances_with_retry(instance_client, request, project_id):
        if not response.instances:
            continue
        zone_short = zone_path.split("/")[-1]
        for instance in response.instances:
            instances.append(build_instance_info(instance, zone_short))
    return instances


def format_instance_display_line(inst, index=None):
    status_color = "\033[92m" if inst["status"] == "RUNNING" else "\033[91m"
    network_short = inst["network"].split("/")[-1] if inst["network"] else "-"
    prefix = f"[{index}] " if index is not None else "- "
    return (
        f"{prefix}{inst['name']:<20} | 区域: {inst['zone']:<15} | 状态: "
        f"{status_color}{inst['status']}\033[0m | 网络: {network_short} | 内网IP: "
        f"{inst['internal_ip']} | 外网IP: {inst['external_ip']} | CPU: {inst['cpu_platform']}"
    )


def print_instance_list(instances, numbered=False):
    for idx, inst in enumerate(instances, start=1):
        print(format_instance_display_line(inst, idx if numbered else None))


def find_instance_by_name(project_id, instance_name, zone=None):
    instances = list_instances(project_id)
    matched_instances = [
        inst
        for inst in instances
        if inst["name"] == instance_name and (not zone or inst["zone"] == zone)
    ]

    if not matched_instances:
        zone_hint = f"（zone={zone}）" if zone else ""
        raise ValueError(f"未找到实例: {instance_name}{zone_hint}")

    if len(matched_instances) > 1:
        raise ValueError(f"找到多个同名实例 {instance_name}，请补充 --zone 指定可用区。")

    return matched_instances[0]


def select_instance(project_id):
    instances = list_instances(project_id)
    if not instances:
        print_warning("该项目中没有任何实例！")
        return None

    print("\n--- 请选择目标服务器 ---")
    print_instance_list(instances, numbered=True)

    while True:
        choice = input(f"请输入数字选择 (1-{len(instances)}): ").strip()
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(instances):
                return instances[idx]
        print("输入无效，请重试。")


def summarize_exception(exc, max_length=160):
    message = " ".join(str(exc).split())
    if len(message) <= max_length:
        return message
    return message[: max_length - 3] + "..."


def print_reroll_summary(stats):
    print("\n" + "-" * 50)
    print_info("刷 AMD 运行摘要")
    print(f"总耗时: {format_duration(time.time() - stats['start_time'])}")
    print(f"尝试轮次: {stats['attempts']} | 异常轮次: {stats['exception_count']}")

    if stats["success_cpu"]:
        print_success(f"命中目标 CPU: {stats['success_cpu']}")

    if stats["cpu_counter"]:
        top_results = " | ".join(
            f"{platform} x{count}" for platform, count in stats["cpu_counter"].most_common(5)
        )
        print(f"结果统计: {top_results}")

    if stats["recent_results"]:
        print(f"最近结果: {' -> '.join(stats['recent_results'])}")

    if stats["recent_errors"]:
        print(f"最近异常: {' | '.join(stats['recent_errors'])}")


def is_transient_gcp_error(exc):
    transient_error_types = (
        google_exceptions.BadGateway,
        google_exceptions.GatewayTimeout,
        google_exceptions.ServiceUnavailable,
        google_exceptions.TooManyRequests,
    )
    if isinstance(exc, transient_error_types):
        return True

    message = str(exc).lower()
    transient_markers = [" 429 ", " 502 ", " 503 ", " 504 ", "try again in 30 seconds"]
    return any(marker in message for marker in transient_markers)


def is_operation_in_progress_error(exc):
    if isinstance(exc, google_exceptions.Conflict):
        return True

    message = str(exc).lower()
    conflict_markers = [
        " 409 ",
        "already in progress",
        "already being used by an operation",
        "operation is already in progress",
        "operationinprogress",
        "resource not ready",
        "resource_not_ready",
        "resourceinusebyanotherresource",
    ]
    return any(marker in message for marker in conflict_markers)


def extract_operation_error(operation):
    operation_error = getattr(operation, "error", None)
    if operation_error and getattr(operation_error, "errors", None):
        error_messages = []
        for item in operation_error.errors:
            code = getattr(item, "code", "") or "UNKNOWN"
            message = getattr(item, "message", "") or "未知错误"
            error_messages.append(f"{code}: {message}")
        if error_messages:
            return "; ".join(error_messages)

    http_status = getattr(operation, "http_error_status_code", None)
    http_message = getattr(operation, "http_error_message", None)
    if http_status or http_message:
        return f"{http_status or ''} {http_message or ''}".strip()

    return ""


def ensure_operation_success(operation, operation_desc):
    error_message = extract_operation_error(operation)
    if error_message:
        raise RuntimeError(f"{operation_desc}失败: {error_message}")


def wait_for_operation_result(
    operation_client,
    operation_desc,
    timeout=OPERATION_WAIT_TIMEOUT,
    poll_interval=OPERATION_POLL_INTERVAL,
    **kwargs,
):
    deadline = time.time() + timeout
    last_error = None

    try:
        operation = operation_client.wait(**kwargs)
        ensure_operation_success(operation, operation_desc)
        return operation
    except Exception as exc:
        if not is_transient_gcp_error(exc):
            raise
        last_error = exc
        print_warning(
            f"{operation_desc} 的 wait 接口暂时不可用，改用轮询继续等待: {summarize_exception(exc)}"
        )

    while time.time() < deadline:
        try:
            operation = operation_client.get(**kwargs)
        except Exception as exc:
            if not is_transient_gcp_error(exc):
                raise
            last_error = exc
            sleep_time = apply_jitter(poll_interval)
            print_warning(
                f"{operation_desc} 轮询状态时遇到临时错误，约 {format_seconds(sleep_time)} 秒后重试: "
                f"{summarize_exception(exc)}"
            )
            time.sleep(sleep_time)
            continue

        if getattr(operation, "status", None) == "DONE":
            ensure_operation_success(operation, operation_desc)
            return operation

        time.sleep(poll_interval)

    if last_error:
        raise TimeoutError(f"{operation_desc}等待超时，最后一次错误: {summarize_exception(last_error)}") from last_error
    raise TimeoutError(f"{operation_desc}等待超时。")


def wait_for_operation(project_id, zone, operation_name, operation_desc="区域操作"):
    operation_client = compute_v1.ZoneOperationsClient()
    return wait_for_operation_result(
        operation_client,
        operation_desc,
        project=project_id,
        zone=zone,
        operation=operation_name,
    )


def wait_for_global_operation(project_id, operation_name, operation_desc="全局操作"):
    operation_client = compute_v1.GlobalOperationsClient()
    return wait_for_operation_result(
        operation_client,
        operation_desc,
        project=project_id,
        operation=operation_name,
    )


def call_with_retries(
    action_desc,
    func,
    max_retries=INSTANCE_API_MAX_RETRIES,
    base_delay=INSTANCE_API_RETRY_BASE_DELAY,
):
    last_error = None

    for attempt in range(max_retries):
        try:
            return func()
        except Exception as exc:
            if not is_transient_gcp_error(exc) and not is_operation_in_progress_error(exc):
                raise

            last_error = exc
            current_try = attempt + 1
            if current_try >= max_retries:
                break

            if is_operation_in_progress_error(exc):
                sleep_time = apply_jitter(INSTANCE_CONFLICT_RETRY_DELAY)
                print_warning(
                    f"{action_desc} 遇到资源冲突，可能已有操作正在进行，准备重试 "
                    f"({current_try}/{max_retries}): {summarize_exception(exc)}"
                )
            else:
                sleep_time = apply_jitter(base_delay * current_try)
                print_warning(
                    f"{action_desc} 遇到临时错误，准备重试 ({current_try}/{max_retries}): "
                    f"{summarize_exception(exc)}"
                )
            print_info(f"等待约 {format_seconds(sleep_time)} 秒后继续 {action_desc}...")
            time.sleep(sleep_time)

    raise RuntimeError(
        f"{action_desc} 在 {max_retries} 次尝试后仍失败: {summarize_exception(last_error)}"
    ) from last_error


def get_instance_with_retry(instance_client, project_id, zone, instance_name):
    return call_with_retries(
        f"获取实例 {instance_name} 状态",
        lambda: instance_client.get(project=project_id, zone=zone, instance=instance_name),
    )


def refresh_instance_info(project_id, instance_info, announce=False):
    instance_client = compute_v1.InstancesClient()
    instance_name = instance_info["name"]
    zone = instance_info["zone"]
    instance = get_instance_with_retry(instance_client, project_id, zone, instance_name)
    refreshed = build_instance_info(instance, zone)
    if announce:
        print_info(
            f"已刷新实例详情: 状态 {refreshed['status']} | 外网IP: {refreshed['external_ip']} | "
            f"CPU: {refreshed['cpu_platform']}"
        )
    return refreshed


def prepare_instance_for_remote(project_id, instance_info, remote_config):
    refreshed = refresh_instance_info(project_id, instance_info, announce=True)
    if refreshed["status"] != "RUNNING":
        print_warning(f"实例当前状态为 {refreshed['status']}，请先启动实例后再执行远程操作。")
        return None
    if not wait_for_remote_ready(project_id, refreshed, remote_config):
        return None
    return refreshed


def start_instance_with_retry(instance_client, project_id, zone, instance_name):
    return call_with_retries(
        f"启动虚拟机 {instance_name}",
        lambda: instance_client.start(project=project_id, zone=zone, instance=instance_name),
    )


def stop_instance_with_retry(instance_client, project_id, zone, instance_name):
    return call_with_retries(
        f"关停虚拟机 {instance_name}",
        lambda: instance_client.stop(project=project_id, zone=zone, instance=instance_name),
    )


def insert_instance_with_retry(instance_client, project_id, zone, instance_resource):
    instance_name = getattr(instance_resource, "name", "未命名实例")
    return call_with_retries(
        f"创建实例 {instance_name}",
        lambda: instance_client.insert(
            project=project_id,
            zone=zone,
            instance_resource=instance_resource,
        ),
    )


def delete_instance_with_retry(instance_client, project_id, zone, instance_name):
    return call_with_retries(
        f"删除实例 {instance_name}",
        lambda: instance_client.delete(project=project_id, zone=zone, instance=instance_name),
    )


def get_image_from_family_with_retry(images_client, project, family):
    return call_with_retries(
        f"获取镜像族 {project}/{family}",
        lambda: images_client.get_from_family(project=project, family=family),
    )


def insert_firewall_with_retry(firewall_client, project_id, firewall_rule):
    rule_name = getattr(firewall_rule, "name", "未命名规则")
    return call_with_retries(
        f"创建防火墙规则 {rule_name}",
        lambda: firewall_client.insert(project=project_id, firewall_resource=firewall_rule),
    )


def delete_firewall_with_retry(firewall_client, project_id, rule_name):
    return call_with_retries(
        f"删除防火墙规则 {rule_name}",
        lambda: firewall_client.delete(project=project_id, firewall=rule_name),
    )


def delete_disk_with_retry(disk_client, project_id, zone, disk_name):
    return call_with_retries(
        f"删除磁盘 {disk_name}",
        lambda: disk_client.delete(project=project_id, zone=zone, disk=disk_name),
    )


def search_projects_with_retry(projects_client, request):
    return call_with_retries(
        "扫描 GCP 项目列表",
        lambda: list(projects_client.search_projects(request=request)),
    )


def list_zones_with_retry(zones_client, project_id):
    return call_with_retries(
        f"获取项目 {project_id} 的可用区列表",
        lambda: list(zones_client.list(project=project_id)),
    )


def aggregated_list_instances_with_retry(instance_client, request, project_id):
    return call_with_retries(
        f"扫描项目 {project_id} 的实例列表",
        lambda: list(instance_client.aggregated_list(request=request)),
    )


def wait_for_instance_status(
    instance_client,
    project_id,
    zone,
    instance_name,
    expected_statuses,
    timeout=INSTANCE_STATUS_WAIT_TIMEOUT,
    poll_interval=INSTANCE_STATUS_POLL_INTERVAL,
):
    if isinstance(expected_statuses, str):
        expected_statuses = {expected_statuses}
    else:
        expected_statuses = set(expected_statuses)

    deadline = time.time() + timeout
    last_status = None
    target_text = "/".join(sorted(expected_statuses))

    while time.time() < deadline:
        current_inst = get_instance_with_retry(instance_client, project_id, zone, instance_name)
        current_status = current_inst.status or "UNKNOWN"
        if current_status in expected_statuses:
            return current_inst, current_status

        if current_status != last_status:
            print_info(f"实例当前状态: {current_status}，继续等待进入 {target_text}...")
            last_status = current_status

        time.sleep(poll_interval)

    return None, last_status or "UNKNOWN"


def ensure_instance_running(instance_client, project_id, zone, instance_name):
    current_inst = get_instance_with_retry(instance_client, project_id, zone, instance_name)
    current_status = current_inst.status or "UNKNOWN"

    if current_status == "STOPPING":
        print_info(f"虚拟机 {instance_name} 当前正在关停，先等待完全停止...")
        stopped_inst, last_status = wait_for_instance_status(
            instance_client, project_id, zone, instance_name, {"TERMINATED", "STOPPED"}
        )
        if stopped_inst is None:
            raise TimeoutError(f"等待虚拟机 {instance_name} 关停超时，最后状态: {last_status}")
        current_status = last_status

    if current_status in {"TERMINATED", "STOPPED"}:
        print_info(f"正在启动虚拟机 {instance_name}...")
        operation = start_instance_with_retry(instance_client, project_id, zone, instance_name)
        wait_for_operation(project_id, zone, operation.name, f"启动虚拟机 {instance_name}")
        print_info("虚拟机已通电，正在等待系统初始化...")
    elif current_status != "RUNNING":
        print_info(f"虚拟机当前状态为 {current_status}，等待其进入 RUNNING...")

    running_inst, last_status = wait_for_instance_status(
        instance_client, project_id, zone, instance_name, "RUNNING"
    )
    if running_inst is None:
        raise TimeoutError(f"等待虚拟机 {instance_name} 启动超时，最后状态: {last_status}")
    return running_inst


def wait_for_cpu_platform(
    instance_client,
    project_id,
    zone,
    instance_name,
    timeout=CPU_PLATFORM_WAIT_TIMEOUT,
    poll_interval=CPU_PLATFORM_POLL_INTERVAL,
):
    deadline = time.time() + timeout
    attempt_counter = 0
    last_status = "UNKNOWN"

    while time.time() < deadline:
        current_inst = get_instance_with_retry(instance_client, project_id, zone, instance_name)
        last_status = current_inst.status or "UNKNOWN"

        if last_status == "RUNNING":
            current_platform = current_inst.cpu_platform
            if current_platform and current_platform != "Unknown CPU Platform":
                return current_platform, last_status

        attempt_counter += 1
        if attempt_counter % 5 == 0:
            if last_status == "RUNNING":
                print_info(f"正在等待 CPU 元数据同步... ({attempt_counter} 次轮询)")
            else:
                print_warning(f"实例状态暂未稳定为 RUNNING: {last_status}，继续等待 CPU 信息同步。")

        time.sleep(poll_interval)

    return None, last_status


def ensure_instance_stopped(instance_client, project_id, zone, instance_name):
    current_inst = get_instance_with_retry(instance_client, project_id, zone, instance_name)
    current_status = current_inst.status or "UNKNOWN"

    if current_status in {"TERMINATED", "STOPPED"}:
        print_info(f"虚拟机 {instance_name} 已处于关机状态，跳过关停请求。")
        return current_inst

    if current_status == "STOPPING":
        print_info(f"虚拟机 {instance_name} 正在关停，等待其完全停止...")
    else:
        operation = stop_instance_with_retry(instance_client, project_id, zone, instance_name)
        wait_for_operation(project_id, zone, operation.name, f"关停虚拟机 {instance_name}")

    stopped_inst, last_status = wait_for_instance_status(
        instance_client, project_id, zone, instance_name, {"TERMINATED", "STOPPED"}
    )
    if stopped_inst is None:
        raise TimeoutError(f"等待虚拟机 {instance_name} 关停超时，最后状态: {last_status}")
    return stopped_inst


def reroll_cpu_loop(project_id, instance_info):
    instance_name = instance_info["name"]
    zone = instance_info["zone"]

    instance_client = compute_v1.InstancesClient()
    attempt_counter = 1
    stats = {
        "start_time": time.time(),
        "attempts": 0,
        "exception_count": 0,
        "cpu_counter": Counter(),
        "recent_results": [],
        "recent_errors": [],
        "success_cpu": None,
    }

    print_info(f"目标实例: {instance_name} ({zone})")
    print_info("目标: 只要 CPU 包含 'AMD' 即停止。")

    try:
        while True:
            stats["attempts"] += 1
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
                stats["cpu_counter"][current_platform] += 1
                remember_recent(stats["recent_results"], current_platform)

                if "AMD" in current_platform.upper():
                    stats["success_cpu"] = current_platform
                    print_success(f"恭喜！已成功刷到目标 CPU: {current_platform}")
                    print_info("脚本执行完毕。")
                    break

                print_warning(f"结果不满意 ({current_platform})。准备重置...")
                print_info(f"正在关停虚拟机 {instance_name}...")
                ensure_instance_stopped(instance_client, project_id, zone, instance_name)
            except Exception as e:
                stats["exception_count"] += 1
                remember_recent(stats["recent_errors"], summarize_exception(e), limit=5)
                print_warning(f"本轮尝试遇到异常，将自动恢复后继续: {summarize_exception(e)}")

            attempt_counter += 1
            cooldown = apply_jitter(
                REROLL_LOOP_COOLDOWN,
                jitter_ratio=COOLDOWN_JITTER_RATIO,
                jitter_cap=COOLDOWN_JITTER_CAP,
            )
            print_info(
                f"为降低 GCP API 抖动和频率限制影响，正在冷却约 {format_seconds(cooldown)} 秒后继续..."
            )
            sleep_with_countdown(cooldown, "冷却中")
            print_info("冷却结束，开始下一轮尝试。")
    finally:
        print_reroll_summary(stats)

    try:
        return refresh_instance_info(project_id, instance_info, announce=False)
    except Exception:
        return instance_info


def read_cdn_ips(filename="cdnip.txt"):
    if not os.path.exists(filename):
        print(f"【错误】找不到文件: {filename}")
        print("请在脚本同目录下创建该文件，并填入IP段。")
        return []

    ip_list = []
    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            clean_line = line.strip()
            if clean_line:
                ip = clean_line.split()[0]
                ip_list.append(ip)

    print(f"已从 {filename} 读取到 {len(ip_list)} 个 IP 段。")
    return ip_list


def set_protocol_field(config_object, value):
    try:
        config_object.ip_protocol = value
    except AttributeError:
        try:
            config_object.I_p_protocol = value
        except AttributeError:
            print(f"\n【调试信息】无法设置协议字段。对象 '{type(config_object).__name__}' 的有效属性如下:")
            print([d for d in dir(config_object) if not d.startswith("_")])
            raise


def add_allow_all_ingress(project_id, network):
    firewall_client = compute_v1.FirewallsClient()
    rule_name = "allow-all-ingress-custom"

    print(f"\n正在创建入站规则: {rule_name} ...")

    firewall_rule = compute_v1.Firewall()
    firewall_rule.name = rule_name
    firewall_rule.direction = "INGRESS"
    firewall_rule.network = network
    firewall_rule.priority = 1000
    firewall_rule.source_ranges = ["0.0.0.0/0"]

    allow_config = compute_v1.Allowed()
    set_protocol_field(allow_config, "all")
    firewall_rule.allowed = [allow_config]

    try:
        operation = insert_firewall_with_retry(firewall_client, project_id, firewall_rule)
        print("正在应用规则...")
        wait_for_global_operation(project_id, operation.name, f"创建防火墙规则 {rule_name}")
        print_success("已添加允许所有入站连接的规则。")
    except Exception as e:
        if "already exists" in str(e):
            print_warning(f"规则 {rule_name} 已存在。")
        else:
            print(f"【失败】{e}")
            traceback.print_exc()


def add_deny_cdn_egress(project_id, ip_ranges, network):
    if not ip_ranges:
        print("IP 列表为空，跳过创建拒绝规则。")
        return

    firewall_client = compute_v1.FirewallsClient()
    rule_name = "deny-cdn-egress-custom"

    print(f"\n正在创建出站拒绝规则: {rule_name} ...")

    firewall_rule = compute_v1.Firewall()
    firewall_rule.name = rule_name
    firewall_rule.direction = "EGRESS"
    firewall_rule.network = network
    firewall_rule.priority = 900
    firewall_rule.destination_ranges = ip_ranges

    deny_config = compute_v1.Denied()
    set_protocol_field(deny_config, "all")
    firewall_rule.denied = [deny_config]

    try:
        operation = insert_firewall_with_retry(firewall_client, project_id, firewall_rule)
        print("正在应用规则...")
        wait_for_global_operation(project_id, operation.name, f"创建防火墙规则 {rule_name}")
        print_success(f"已添加拒绝规则，共拦截 {len(ip_ranges)} 个 IP 段。")
    except Exception as e:
        if "already exists" in str(e):
            print_warning(f"规则 {rule_name} 已存在。")
        else:
            print(f"【失败】{e}")
            traceback.print_exc()


def configure_firewall(project_id, network):
    print("\n------------------------------------------------")
    print("防火墙规则管理菜单")
    print("------------------------------------------------")
    print(f"目标网络: {network}")

    choice_in = input("\n[1/2] 是否添加【允许所有入站连接 (0.0.0.0/0)】规则? (y/n): ").strip().lower()
    if choice_in == "y":
        add_allow_all_ingress(project_id, network)
    else:
        print("已跳过入站规则配置。")

    choice_out = input("\n[2/2] 是否添加【拒绝对 cdnip.txt 中 IP 的出站连接】规则? (y/n): ").strip().lower()
    if choice_out == "y":
        ips = read_cdn_ips()
        if ips:
            if len(ips) > 256:
                print(f"【警告】IP 数量 ({len(ips)}) 超过 GCP 单条规则上限 (256)。")
                print("脚本将只取前 256 个 IP。")
                ips = ips[:256]

            add_deny_cdn_egress(project_id, ips, network)
    else:
        print("已跳过出站规则配置。")

    print("\n所有操作完成。")


def configure_firewall_non_interactive(
    project_id,
    network,
    allow_all_ingress=False,
    deny_cdn_egress=False,
    cdnip_filename="cdnip.txt",
):
    if not allow_all_ingress and not deny_cdn_egress:
        raise ValueError("非交互防火墙模式至少要指定 --allow-all-ingress 或 --deny-cdn-egress。")

    print("\n------------------------------------------------")
    print("防火墙规则管理（非交互模式）")
    print("------------------------------------------------")
    print(f"目标网络: {network}")

    if allow_all_ingress:
        add_allow_all_ingress(project_id, network)
    else:
        print("已跳过入站规则配置。")

    if deny_cdn_egress:
        ips = read_cdn_ips(cdnip_filename)
        if ips:
            if len(ips) > 256:
                print(f"【警告】IP 数量 ({len(ips)}) 超过 GCP 单条规则上限 (256)。")
                print("脚本将只取前 256 个 IP。")
                ips = ips[:256]
            add_deny_cdn_egress(project_id, ips, network)
    else:
        print("已跳过出站规则配置。")

    print("\n所有操作完成。")


def is_not_found_error(exc):
    msg = str(exc).lower()
    return "notfound" in msg or "not found" in msg or "404" in msg


def delete_firewall_rule(project_id, rule_name):
    firewall_client = compute_v1.FirewallsClient()
    try:
        operation = delete_firewall_with_retry(firewall_client, project_id, rule_name)
        wait_for_global_operation(project_id, operation.name, f"删除防火墙规则 {rule_name}")
        print_success(f"已删除防火墙规则: {rule_name}")
        return True
    except Exception as e:
        if is_not_found_error(e):
            print_info(f"防火墙规则不存在，已跳过: {rule_name}")
            return True
        print_warning(f"删除防火墙规则失败: {rule_name} ({e})")
        return False


def delete_disks_if_needed(project_id, zone, disk_names):
    if not disk_names:
        return True
    disk_client = compute_v1.DisksClient()
    all_ok = True
    for disk_name in disk_names:
        try:
            operation = delete_disk_with_retry(disk_client, project_id, zone, disk_name)
            wait_for_operation(project_id, zone, operation.name)
            print_success(f"已删除磁盘: {disk_name}")
        except Exception as e:
            if is_not_found_error(e):
                print_info(f"磁盘不存在，已跳过: {disk_name}")
            else:
                print_warning(f"删除磁盘失败: {disk_name} ({e})")
                all_ok = False
    return all_ok


def delete_free_resources(project_id, instance_info, confirmed=False):
    instance_name = instance_info["name"]
    zone = instance_info["zone"]

    print("\n------------------------------------------------")
    print("即将删除以下资源（可以重新创建免费资源）：")
    print(f"- 实例: {instance_name} ({zone})")
    print(f"- 相关磁盘（如仍存在）")
    print(f"- 防火墙规则: {', '.join(FIREWALL_RULES_TO_CLEAN)}")
    if not confirmed:
        confirm = input("请输入 DELETE 确认删除: ").strip()
        if confirm != "DELETE":
            print("已取消删除操作。")
            return False
    else:
        print_info("已通过非交互参数确认删除。")

    instance_client = compute_v1.InstancesClient()
    disk_names = []
    try:
        inst = get_instance_with_retry(instance_client, project_id, zone, instance_name)
        for disk in inst.disks:
            if disk.source:
                disk_names.append(disk.source.split("/")[-1])
    except Exception as e:
        print_warning(f"读取实例信息失败，磁盘清理可能不完整: {e}")

    print_info("正在删除实例...")
    try:
        operation = delete_instance_with_retry(instance_client, project_id, zone, instance_name)
        wait_for_operation(project_id, zone, operation.name)
        print_success("实例已删除。")
    except Exception as e:
        if is_not_found_error(e):
            print_info("实例不存在，已跳过删除。")
        else:
            print_warning(f"实例删除失败: {e}")
            return False

    delete_disks_if_needed(project_id, zone, disk_names)

    print_info("正在清理防火墙规则...")
    for rule_name in FIREWALL_RULES_TO_CLEAN:
        delete_firewall_rule(project_id, rule_name)

    print_success("清理完成。建议到控制台确认无残留资源。")
    return True


def pick_remote_method():
    has_gcloud = shutil.which("gcloud") is not None
    has_ssh = shutil.which("ssh") is not None

    if not has_gcloud and not has_ssh:
        print_warning("本机未发现 gcloud 或 ssh，无法执行远程脚本。")
        return None

    if has_gcloud:
        choice = input("是否使用 gcloud compute ssh 远程执行? (Y/n): ").strip().lower()
        if choice in ("", "y", "yes"):
            return {"method": "gcloud"}

    if not has_ssh:
        print_warning("未找到 ssh 命令，无法继续。")
        return None

    default_user = getpass.getuser()
    ssh_user = input(f"请输入 SSH 用户名 (默认 {default_user}): ").strip() or default_user
    ssh_port = input("请输入 SSH 端口 (默认 22): ").strip() or "22"
    ssh_key = input("请输入 SSH 私钥路径 (留空表示使用默认密钥): ").strip()
    return {"method": "ssh", "user": ssh_user, "port": ssh_port, "key": ssh_key}


def get_remote_config_for_instance(project_id, instance_info, remote_config_cache):
    cache_key = get_instance_cache_key(project_id, instance_info)
    remote_config = remote_config_cache.get(cache_key)
    if remote_config:
        return remote_config

    remote_config = pick_remote_method()
    if remote_config:
        remote_config_cache[cache_key] = remote_config
    return remote_config


def get_local_script_path(script_key):
    script_name = LOCAL_SCRIPT_FILES.get(script_key)
    if not script_name:
        print_warning("未知的脚本类型，无法执行。")
        return None

    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", script_name)
    if not os.path.isfile(local_path):
        print_warning(f"找不到本地脚本文件: {local_path}")
        return None
    return local_path


def build_remote_script_exec_command(remote_script_path):
    return (
        "set -e;"
        f"tmp='{remote_script_path}';"
        "cleanup(){ rm -f \"$tmp\"; };"
        "trap cleanup EXIT;"
        "sudo bash \"$tmp\""
    )


def run_subprocess_command(cmd, action_desc, timeout=None):
    try:
        result = subprocess.run(
            cmd,
            timeout=timeout,
            text=True,
            stdin=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if result.returncode == 0:
            return True
        print_warning(f"{action_desc}失败，退出码: {result.returncode}")
        stderr_summary = summarize_text_block(result.stderr)
        if stderr_summary:
            print_warning(f"{action_desc}错误摘要:\n{stderr_summary}")
        return False
    except subprocess.TimeoutExpired as exc:
        print_warning(f"{action_desc}失败：执行超时。")
        stderr_summary = summarize_text_block(exc.stderr)
        if stderr_summary:
            print_warning(f"{action_desc}超时前输出:\n{stderr_summary}")
        print_info(f"命令: {format_command_for_log(cmd)}")
        return False
    except Exception as e:
        print_warning(f"{action_desc}失败: {e}")
        print_info(f"命令: {format_command_for_log(cmd)}")
        return False


def run_subprocess_capture_command(cmd, action_desc, timeout=REMOTE_PROBE_TIMEOUT):
    try:
        result = subprocess.run(
            cmd,
            timeout=timeout,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        return (
            result.returncode == 0,
            (result.stdout or "").strip(),
            (result.stderr or "").strip(),
        )
    except subprocess.TimeoutExpired:
        return False, "", f"{action_desc}超时"
    except Exception as e:
        return False, "", str(e)


def build_remote_exec_command(project_id, instance_info, remote_config, remote_command):
    instance_name = instance_info["name"]
    zone = instance_info["zone"]
    method = remote_config.get("method")
    ssh_options = build_ssh_option_values()

    if method == "gcloud":
        cmd = [
            "gcloud",
            "compute",
            "ssh",
            instance_name,
            "--project",
            project_id,
            "--zone",
            zone,
            "--command",
            remote_command,
        ]
        return extend_gcloud_passthrough_flags(cmd, "--ssh-flag", ssh_options)
    if method == "ssh":
        host = instance_info.get("external_ip")
        if not host or host == "-":
            print_warning("该实例没有外网 IP，无法使用 SSH 直连。")
            return None
        cmd = ["ssh"]
        port = remote_config.get("port")
        if port:
            cmd += ["-p", str(port)]
        key_path = remote_config.get("key")
        if key_path:
            cmd += ["-i", key_path]
        extend_ssh_options(cmd, build_ssh_option_values(include_identities_only=bool(key_path)))
        cmd += [f"{remote_config.get('user')}@{host}", remote_command]
        return cmd

    print_warning("远程执行方式未设置。")
    return None


def probe_remote_command(project_id, instance_info, remote_config, remote_command, action_desc):
    cmd = build_remote_exec_command(project_id, instance_info, remote_config, remote_command)
    if not cmd:
        return False, "", "无法构建远程执行命令"
    return run_subprocess_capture_command(cmd, action_desc)


def wait_for_remote_ready(project_id, instance_info, remote_config):
    print_info("正在等待 SSH 服务就绪...")
    deadline = time.time() + REMOTE_READY_TIMEOUT
    attempt_counter = 0

    while time.time() < deadline:
        attempt_counter += 1
        success, stdout, stderr = probe_remote_command(
            project_id,
            instance_info,
            remote_config,
            "echo gcp_free_ready",
            "探测 SSH 就绪状态",
        )
        if success and "gcp_free_ready" in stdout:
            print_success("SSH 服务已就绪。")
            return True

        if attempt_counter == 1 or attempt_counter % 3 == 0:
            reason = summarize_text_block(stderr or stdout, max_lines=1, max_length=120) or "无返回内容"
            print_info(f"SSH 尚未就绪，继续等待... ({attempt_counter} 次探测) | 最近响应: {reason}")

        sleep_time = apply_jitter(REMOTE_READY_POLL_INTERVAL, jitter_ratio=0.1, jitter_cap=2)
        time.sleep(sleep_time)

    print_warning(f"等待 SSH 就绪超时（{format_duration(REMOTE_READY_TIMEOUT)}）。")
    return False


def parse_os_release(content):
    data = {}
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")

    return {
        "id": data.get("ID", "unknown").lower(),
        "version_id": data.get("VERSION_ID", "unknown"),
        "pretty_name": data.get("PRETTY_NAME", "Unknown OS"),
    }


def detect_remote_os_info(project_id, instance_info, remote_config):
    success, stdout, stderr = probe_remote_command(
        project_id,
        instance_info,
        remote_config,
        "cat /etc/os-release",
        "检测远程操作系统",
    )
    if not success or not stdout:
        print_warning(f"无法识别远程系统类型，将跳过系统校验: {stderr or '无返回内容'}")
        return None

    os_info = parse_os_release(stdout)
    print_info(f"远程系统: {os_info['pretty_name']} (ID={os_info['id']}, VERSION_ID={os_info['version_id']})")
    return os_info


def validate_remote_script_os(script_key, os_info):
    if not os_info:
        return True

    os_id = os_info["id"]
    if script_key == "apt" and os_id not in {"debian", "ubuntu"}:
        print_warning(f"脚本 apt.sh 仅建议在 Debian/Ubuntu 上运行，当前系统为 {os_info['pretty_name']}。")
        return False

    if script_key in {"net_iptables", "net_shutdown"} and os_id != "debian":
        print_warning(f"脚本 {script_key} 仅适配 Debian，当前系统为 {os_info['pretty_name']}。")
        return False

    return True


def validate_dae_config_os(os_info):
    if not os_info:
        return True

    if os_info["id"] not in {"debian", "ubuntu"}:
        print_warning(f"当前系统为 {os_info['pretty_name']}，dae 配置流程未做专门适配，请自行确认。")
    return True


def build_remote_upload_command(project_id, instance_info, remote_config, local_path, remote_path):
    instance_name = instance_info["name"]
    zone = instance_info["zone"]
    method = remote_config.get("method")
    key_path = remote_config.get("key")
    ssh_options = build_ssh_option_values(include_identities_only=bool(key_path))

    if method == "gcloud":
        cmd = [
            "gcloud",
            "compute",
            "scp",
            local_path,
            f"{instance_name}:{remote_path}",
            "--project",
            project_id,
            "--zone",
            zone,
        ]
        return extend_gcloud_passthrough_flags(cmd, "--scp-flag", ssh_options)
    if method == "ssh":
        if shutil.which("scp") is None:
            print_warning("未找到 scp 命令，无法上传文件。")
            return None
        host = instance_info.get("external_ip")
        if not host or host == "-":
            print_warning("该实例没有外网 IP，无法使用 SSH 直连。")
            return None
        cmd = ["scp"]
        port = remote_config.get("port")
        if port:
            cmd += ["-P", str(port)]
        if key_path:
            cmd += ["-i", key_path]
        extend_ssh_options(cmd, ssh_options)
        cmd += [local_path, f"{remote_config.get('user')}@{host}:{remote_path}"]
        return cmd

    print_warning("远程执行方式未设置。")
    return None


def run_remote_script(project_id, instance_info, script_key, remote_config):
    local_script = get_local_script_path(script_key)
    if not local_script:
        return False

    os_info = detect_remote_os_info(project_id, instance_info, remote_config)
    if not validate_remote_script_os(script_key, os_info):
        return False

    remote_tmp = make_remote_temp_path("gcp_free_script", ".sh")
    upload_cmd = build_remote_upload_command(
        project_id,
        instance_info,
        remote_config,
        local_script,
        remote_tmp,
    )
    if not upload_cmd:
        return False

    remote_command = build_remote_script_exec_command(remote_tmp)
    exec_cmd = build_remote_exec_command(project_id, instance_info, remote_config, remote_command)
    if not exec_cmd:
        return False

    print_info(f"正在上传本地脚本: {local_script}")
    if not run_subprocess_command(upload_cmd, "上传远程脚本", timeout=REMOTE_UPLOAD_TIMEOUT):
        return False

    print_info(f"正在远程执行本地脚本: {os.path.basename(local_script)}")
    if not run_subprocess_command(exec_cmd, "远程脚本执行", timeout=REMOTE_COMMAND_TIMEOUT):
        return False

    print_success("远程脚本执行完成。")
    return True


def select_traffic_monitor_script():
    print("\n--- 请选择流量监控脚本 ---")
    print("[1] 安装 超额关闭 ssh 之外其他入站 (net_iptables.sh)")
    print("[2] 安装 超额自动关机 (net_shutdown.sh)")
    print("[0] 返回")
    while True:
        choice = input("请输入数字选择: ").strip()
        if choice == "1":
            return "net_iptables"
        if choice == "2":
            return "net_shutdown"
        if choice == "0":
            return None
        print("输入无效，请重试。")


def deploy_dae_config(project_id, instance_info, remote_config):
    local_config = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.dae")
    if not os.path.isfile(local_config):
        print_warning(f"找不到本地配置文件: {local_config}")
        return False

    os_info = detect_remote_os_info(project_id, instance_info, remote_config)
    if not validate_dae_config_os(os_info):
        return False

    remote_tmp = make_remote_temp_path("gcp_free_config", ".dae")
    upload_cmd = build_remote_upload_command(
        project_id,
        instance_info,
        remote_config,
        local_config,
        remote_tmp,
    )
    if not upload_cmd:
        return False

    print_info("正在上传 config.dae ...")
    if not run_subprocess_command(upload_cmd, "上传 config.dae", timeout=REMOTE_UPLOAD_TIMEOUT):
        return False

    remote_command = (
        "set -e;"
        "sudo mkdir -p /usr/local/etc/dae;"
        f"sudo cp '{remote_tmp}' /usr/local/etc/dae/config.dae;"
        "sudo chmod 600 /usr/local/etc/dae/config.dae;"
        "sudo systemctl enable dae;"
        "sudo systemctl restart dae;"
        f"rm -f '{remote_tmp}'"
    )
    exec_cmd = build_remote_exec_command(project_id, instance_info, remote_config, remote_command)
    if not exec_cmd:
        return False

    print_info("正在应用配置并重启 dae ...")
    if not run_subprocess_command(exec_cmd, "应用 dae 配置", timeout=REMOTE_CONFIG_APPLY_TIMEOUT):
        return False
    print_success("配置已更新并重启 dae。")
    return True


def build_remote_config_from_args(args):
    has_gcloud = shutil.which("gcloud") is not None
    has_ssh = shutil.which("ssh") is not None
    requested_method = getattr(args, "remote_method", None)

    if not requested_method and (
        any(getattr(args, attr_name, None) for attr_name in ("ssh_user", "ssh_key"))
        or str(getattr(args, "ssh_port", "22") or "22") != "22"
    ):
        requested_method = "ssh"

    if requested_method == "gcloud":
        if not has_gcloud:
            raise ValueError("当前环境未安装 gcloud，无法使用 gcloud 远程模式。")
        return {"method": "gcloud"}

    if requested_method == "ssh" or (not requested_method and not has_gcloud):
        if not has_ssh:
            raise ValueError("当前环境未安装 ssh，无法使用 SSH 远程模式。")
        ssh_key = getattr(args, "ssh_key", "") or ""
        if ssh_key:
            ssh_key = os.path.expanduser(ssh_key)
            if not os.path.isfile(ssh_key):
                raise ValueError(f"SSH 私钥文件不存在: {ssh_key}")
        return {
            "method": "ssh",
            "user": getattr(args, "ssh_user", None) or getpass.getuser(),
            "port": str(getattr(args, "ssh_port", "22") or "22"),
            "key": ssh_key,
        }

    if has_gcloud:
        return {"method": "gcloud"}

    if has_ssh:
        ssh_key = getattr(args, "ssh_key", "") or ""
        if ssh_key:
            ssh_key = os.path.expanduser(ssh_key)
            if not os.path.isfile(ssh_key):
                raise ValueError(f"SSH 私钥文件不存在: {ssh_key}")
        return {
            "method": "ssh",
            "user": getattr(args, "ssh_user", None) or getpass.getuser(),
            "port": str(getattr(args, "ssh_port", "22") or "22"),
            "key": ssh_key,
        }

    raise ValueError("当前环境既没有 gcloud，也没有 ssh，无法执行远程操作。")


def get_cli_instance(args):
    return find_instance_by_name(args.project_id, args.instance, getattr(args, "zone", None))


def prepare_cli_remote_instance(args):
    instance_info = get_cli_instance(args)
    remote_config = build_remote_config_from_args(args)
    remote_instance = prepare_instance_for_remote(args.project_id, instance_info, remote_config)
    if not remote_instance:
        raise RuntimeError("远程实例尚未就绪，无法继续执行远程操作。")
    return remote_instance, remote_config


def handle_create_cli(args):
    zone = resolve_zone_for_create(args.zone, args.region)
    os_config = resolve_os_config(args.os)
    created_instance = create_instance(
        args.project_id,
        zone,
        os_config,
        instance_name=args.instance_name,
    )
    if not created_instance:
        raise RuntimeError("创建实例失败。")


def handle_list_instances_cli(args):
    instances = list_instances(args.project_id)
    if not instances:
        print_warning("该项目中没有任何实例。")
        return
    print_instance_list(instances, numbered=False)


def handle_reroll_amd_cli(args):
    instance_info = get_cli_instance(args)
    reroll_cpu_loop(args.project_id, instance_info)


def handle_firewall_cli(args):
    instance_info = get_cli_instance(args)
    network = instance_info.get("network") or "global/networks/default"
    configure_firewall_non_interactive(
        args.project_id,
        network,
        allow_all_ingress=args.allow_all_ingress,
        deny_cdn_egress=args.deny_cdn_egress,
        cdnip_filename=args.cdnip_file,
    )


def handle_run_script_cli(args):
    remote_instance, remote_config = prepare_cli_remote_instance(args)
    if not run_remote_script(args.project_id, remote_instance, args.script_key, remote_config):
        raise RuntimeError("远程脚本执行失败。")


def handle_deploy_dae_config_cli(args):
    remote_instance, remote_config = prepare_cli_remote_instance(args)
    if not deploy_dae_config(args.project_id, remote_instance, remote_config):
        raise RuntimeError("dae 配置部署失败。")


def handle_delete_resources_cli(args):
    if not args.yes:
        raise ValueError("非交互删除资源时必须显式传入 --yes。")
    instance_info = get_cli_instance(args)
    if not delete_free_resources(args.project_id, instance_info, confirmed=True):
        raise RuntimeError("删除资源失败。")


def build_arg_parser():
    parser = argparse.ArgumentParser(description="GCP 免费服务器多功能管理工具")
    subparsers = parser.add_subparsers(dest="cli_action", metavar="命令")

    project_parent = argparse.ArgumentParser(add_help=False)
    project_parent.add_argument("--project-id", required=True, help="GCP 项目 ID")

    instance_parent = argparse.ArgumentParser(add_help=False)
    instance_parent.add_argument("--project-id", required=True, help="GCP 项目 ID")
    instance_parent.add_argument("--instance", required=True, help="实例名称")
    instance_parent.add_argument("--zone", help="实例所在可用区；存在同名实例时建议显式指定")

    remote_parent = argparse.ArgumentParser(add_help=False)
    remote_parent.add_argument(
        "--remote-method",
        choices=["gcloud", "ssh"],
        help="远程连接方式，默认优先 gcloud",
    )
    remote_parent.add_argument("--ssh-user", help="SSH 用户名，仅在 ssh 模式下生效")
    remote_parent.add_argument("--ssh-port", default="22", help="SSH 端口，仅在 ssh 模式下生效")
    remote_parent.add_argument("--ssh-key", help="SSH 私钥路径，仅在 ssh 模式下生效")

    create_parser = subparsers.add_parser("create", parents=[project_parent], help="新建免费实例")
    create_parser.add_argument("--zone", help="实例部署可用区，例如 us-west1-b")
    create_parser.add_argument(
        "--region",
        choices=[item["region"] for item in REGION_OPTIONS],
        help="实例部署区域；未提供 --zone 时会使用该区域的默认可用区",
    )
    create_parser.add_argument(
        "--os",
        default="debian-12",
        choices=["debian", "debian-12", "ubuntu", "ubuntu-2204-lts"],
        help="实例操作系统，默认 debian-12",
    )
    create_parser.add_argument("--instance-name", default="free-tier-vm", help="实例名称")
    create_parser.set_defaults(handler=handle_create_cli)

    list_parser = subparsers.add_parser(
        "list-instances",
        parents=[project_parent],
        help="列出项目中的实例",
    )
    list_parser.set_defaults(handler=handle_list_instances_cli)

    reroll_parser = subparsers.add_parser(
        "reroll-amd",
        parents=[instance_parent],
        help="循环重刷 CPU，直到命中 AMD",
    )
    reroll_parser.set_defaults(handler=handle_reroll_amd_cli)

    firewall_parser = subparsers.add_parser(
        "firewall",
        parents=[instance_parent],
        help="非交互配置防火墙规则",
    )
    firewall_parser.add_argument(
        "--allow-all-ingress",
        action="store_true",
        help="添加允许所有入站连接的规则",
    )
    firewall_parser.add_argument(
        "--deny-cdn-egress",
        action="store_true",
        help="添加拒绝 cdnip.txt 中 IP 的出站规则",
    )
    firewall_parser.add_argument("--cdnip-file", default="cdnip.txt", help="CDN IP 列表文件路径")
    firewall_parser.set_defaults(handler=handle_firewall_cli)

    run_script_parser = subparsers.add_parser(
        "run-script",
        parents=[instance_parent, remote_parent],
        help="上传并执行本地远程脚本",
    )
    run_script_parser.add_argument(
        "script_key",
        choices=sorted(LOCAL_SCRIPT_FILES.keys()),
        help="脚本类型",
    )
    run_script_parser.set_defaults(handler=handle_run_script_cli)

    dae_config_parser = subparsers.add_parser(
        "deploy-dae-config",
        parents=[instance_parent, remote_parent],
        help="上传 config.dae 并重启 dae",
    )
    dae_config_parser.set_defaults(handler=handle_deploy_dae_config_cli)

    delete_parser = subparsers.add_parser(
        "delete-resources",
        parents=[instance_parent],
        help="删除实例、磁盘和关联防火墙规则",
    )
    delete_parser.add_argument("--yes", action="store_true", help="确认执行删除")
    delete_parser.set_defaults(handler=handle_delete_resources_cli)

    return parser


def parse_args(argv=None):
    return build_arg_parser().parse_args(argv)


def run_cli(args):
    handler = getattr(args, "handler", None)
    if not handler:
        return False
    ensure_google_cloud_libraries()
    handler(args)
    return True


def main():
    ensure_google_cloud_libraries()
    print("GCP 免费服务器多功能管理工具")
    project_id = select_gcp_project()
    current_instance = None
    remote_config_cache = {}

    while True:
        print("\n================================================")
        print(f"当前项目: {project_id}")
        if current_instance:
            print(f"当前服务器: {current_instance['name']} ({current_instance['zone']})")
        else:
            print("当前服务器: 未选择")
        print("------------------------------------------------")
        print("[1] 新建免费实例")
        print("[2] 选择服务器")
        print("[3] 刷 AMD CPU")
        print("[4] 配置防火墙规则")
        print("[5] Debian换源")
        print("[6] 安装 dae")
        print("[7] 上传 config.dae 并启用 dae")
        print("[8] 安装流量监控脚本（仅适配 Debian）")
        print("[9] 删除当前免费资源")
        print("[0] 退出")
        choice = input("请输入数字选择: ").strip()

        if choice == "1":
            zone = select_zone(project_id)
            os_config = select_os_image()
            created_instance = create_instance(project_id, zone, os_config)
            if created_instance:
                current_instance = created_instance
        elif choice == "2":
            current_instance = select_instance(project_id)
        elif choice == "3":
            if not current_instance:
                current_instance = select_instance(project_id)
            if current_instance:
                current_instance = reroll_cpu_loop(project_id, current_instance)
        elif choice == "4":
            if not current_instance:
                current_instance = select_instance(project_id)
            if current_instance:
                network = current_instance.get("network") or "global/networks/default"
                configure_firewall(project_id, network)
        elif choice == "5":
            if not current_instance:
                current_instance = select_instance(project_id)
            if current_instance:
                remote_config = get_remote_config_for_instance(project_id, current_instance, remote_config_cache)
                if remote_config:
                    remote_instance = prepare_instance_for_remote(project_id, current_instance, remote_config)
                    if remote_instance:
                        current_instance = remote_instance
                        run_remote_script(project_id, current_instance, "apt", remote_config)
        elif choice == "6":
            if not current_instance:
                current_instance = select_instance(project_id)
            if current_instance:
                remote_config = get_remote_config_for_instance(project_id, current_instance, remote_config_cache)
                if remote_config:
                    remote_instance = prepare_instance_for_remote(project_id, current_instance, remote_config)
                    if remote_instance:
                        current_instance = remote_instance
                        run_remote_script(project_id, current_instance, "dae", remote_config)
        elif choice == "7":
            if not current_instance:
                current_instance = select_instance(project_id)
            if current_instance:
                remote_config = get_remote_config_for_instance(project_id, current_instance, remote_config_cache)
                if remote_config:
                    remote_instance = prepare_instance_for_remote(project_id, current_instance, remote_config)
                    if remote_instance:
                        current_instance = remote_instance
                        deploy_dae_config(project_id, current_instance, remote_config)
        elif choice == "8":
            if not current_instance:
                current_instance = select_instance(project_id)
            if current_instance:
                remote_config = get_remote_config_for_instance(project_id, current_instance, remote_config_cache)
                if remote_config:
                    remote_instance = prepare_instance_for_remote(project_id, current_instance, remote_config)
                    if remote_instance:
                        current_instance = remote_instance
                        script_key = select_traffic_monitor_script()
                        if script_key:
                            run_remote_script(project_id, current_instance, script_key, remote_config)
        elif choice == "9":
            if not current_instance:
                current_instance = select_instance(project_id)
            if current_instance:
                cache_key = get_instance_cache_key(project_id, current_instance)
                if delete_free_resources(project_id, current_instance):
                    remote_config_cache.pop(cache_key, None)
                    current_instance = None
        elif choice == "0":
            print("已退出。")
            break
        else:
            print("输入无效，请重试。")


if __name__ == "__main__":
    try:
        args = parse_args()
        if not run_cli(args):
            main()
    except KeyboardInterrupt:
        print("\n[用户终止] 脚本已停止。")
    except Exception as e:
        print(f"\n[错误] 发生异常: {e}")
        traceback.print_exc()
