from __future__ import annotations

from gcp_common import (
    Any,
    CPU_PLATFORM_POLL_INTERVAL,
    CPU_PLATFORM_WAIT_TIMEOUT,
    INSTANCE_STATUS_HEARTBEAT_INTERVAL,
    INSTANCE_STATUS_POLL_INTERVAL,
    INSTANCE_STATUS_WAIT_TIMEOUT,
    INSTANCE_TRANSITION_CONFIRM_POLL_INTERVAL,
    INSTANCE_TRANSITION_CONFIRM_TIMEOUT,
    InstanceInfo,
    LOGGER,
    OS_IMAGE_OPTIONS,
    REGION_OPTIONS,
    compute_v1,
    find_gcloud_command,
    images_client,
    instances_client,
    json,
    projects_client,
    resourcemanager_v3,
    subprocess,
    time,
    traceback,
    zones_client,
)
from gcp_operations import (
    aggregated_list_instances_with_retry,
    get_image_from_family_with_retry,
    get_instance_with_retry,
    insert_instance_with_retry,
    is_transient_gcp_error,
    list_zones_with_retry,
    search_projects_with_retry,
    start_instance_with_retry,
    stop_instance_with_retry,
    wait_for_operation,
)
from gcp_utils import (
    format_duration,
    is_not_found_error,
    print_error,
    print_info,
    print_success,
    print_warning,
    prompt_manual_project_id,
    prompt_project_selection,
    select_from_list,
    sleep_and_detect_pause,
    summarize_exception,
    summarize_text_block,
    warn_if_long_pause,
)

__all__ = [
    'list_active_projects_via_gcloud',
    'build_instance_info_from_gcloud',
    'list_instances_via_gcloud',
    'select_gcp_project',
    'list_zones_for_region',
    'select_zone',
    'select_os_image',
    'create_instance',
    'build_instance_info',
    'get_instance_cache_key',
    'get_instance_by_name_with_zone',
    'list_instances',
    'format_instance_display_line',
    'print_instance_list',
    'find_instance_by_name',
    'select_instance',
    'refresh_instance_info',
    'wait_for_instance_status',
    'wait_for_instance_status_change',
    'ensure_instance_running',
    'wait_for_cpu_platform',
    'ensure_instance_stopped',
    'build_setup_dry_run_instance',
]

def list_active_projects_via_gcloud() -> Any:
    gcloud_command = find_gcloud_command()
    if not gcloud_command:
        raise RuntimeError("当前环境未找到 gcloud，无法通过 CLI 获取项目列表。")

    result = subprocess.run(
        [
            gcloud_command,
            "projects",
            "list",
            "--format=json(projectId,name,lifecycleState)",
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    if result.returncode != 0:
        stderr_summary = summarize_text_block(result.stderr) or f"退出码: {result.returncode}"
        raise RuntimeError(f"gcloud projects list 执行失败: {stderr_summary}")

    raw_projects = json.loads(result.stdout or "[]")
    active_projects = []
    for item in raw_projects:
        lifecycle_state = (item.get("lifecycleState") or "ACTIVE").upper()
        if lifecycle_state != "ACTIVE":
            continue
        active_projects.append(
            {
                "project_id": item.get("projectId", ""),
                "display_name": item.get("name", ""),
            }
        )
    return active_projects

def build_instance_info_from_gcloud(item: Any) -> Any:
    zone_value = item.get("zone", "")
    zone = zone_value.split("/")[-1] if zone_value else "unknown-zone"
    network_interface = (item.get("networkInterfaces") or [{}])[0]
    access_configs = network_interface.get("accessConfigs") or [{}]
    return InstanceInfo(
        name=item.get("name", ""),
        zone=zone,
        status=item.get("status", "UNKNOWN"),
        cpu_platform=item.get("cpuPlatform") or "Unknown CPU Platform",
        network=network_interface.get("network") or "global/networks/default",
        internal_ip=network_interface.get("networkIP") or "-",
        external_ip=access_configs[0].get("natIP") or "-",
    )

def list_instances_via_gcloud(project_id: Any) -> Any:
    gcloud_command = find_gcloud_command()
    if not gcloud_command:
        raise RuntimeError("当前环境未找到 gcloud，无法通过 CLI 获取实例列表。")

    result = subprocess.run(
        [
            gcloud_command,
            "compute",
            "instances",
            "list",
            "--project",
            project_id,
            # gcloud 的 JSON 投影在部分嵌套字段上会直接裁掉 networkInterfaces，导致 IP 全变成 "-".
            # 这里直接取完整 JSON，再由本地代码提取需要的字段，结果更稳定。
            "--format=json",
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    if result.returncode != 0:
        stderr_summary = summarize_text_block(result.stderr) or f"退出码: {result.returncode}"
        raise RuntimeError(f"gcloud compute instances list 执行失败: {stderr_summary}")

    raw_instances = json.loads(result.stdout or "[]")
    return [build_instance_info_from_gcloud(item) for item in raw_instances]

def select_gcp_project() -> Any:
    print_info("正在扫描您的项目列表...")
    gcloud_command = find_gcloud_command()
    if gcloud_command:
        try:
            print_info("优先通过本机 gcloud 获取项目列表...")
            active_projects = list_active_projects_via_gcloud()
            if active_projects:
                return prompt_project_selection(
                    active_projects,
                    project_id_fn=lambda item: item["project_id"],
                    display_name_fn=lambda item: item["display_name"],
                )
            print_warning("gcloud 未返回任何活跃项目，将回退到 Resource Manager API。")
        except Exception as e:
            print_warning(f"通过 gcloud 列出项目失败，将回退到 Resource Manager API: {summarize_exception(e)}")

    try:
        client = projects_client()
        request = resourcemanager_v3.SearchProjectsRequest(query="")
        page_result = search_projects_with_retry(client, request)

        active_projects = []
        for project in page_result:
            if project.state == resourcemanager_v3.Project.State.ACTIVE:
                active_projects.append(project)

        if not active_projects:
            print_warning("未找到活跃的项目。请手动输入项目 ID。")
            return prompt_manual_project_id()

        return prompt_project_selection(
            active_projects,
            project_id_fn=lambda item: item.project_id,
            display_name_fn=lambda item: item.display_name,
        )
    except Exception as e:
        print_warning(f"无法列出项目: {e}。请手动输入项目 ID。")
        return prompt_manual_project_id()

def list_zones_for_region(project_id: Any,  region: Any) -> Any:
    zones_client_instance = zones_client()
    zones = []
    for zone in list_zones_with_retry(zones_client_instance, project_id):
        if zone.status != "UP":
            continue
        zone_region = zone.region.split("/")[-1] if zone.region else ""
        if zone_region == region:
            zones.append(zone.name)
    return sorted(zones)

def select_zone(project_id: Any) -> Any:
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

def select_os_image() -> Any:
    return select_from_list(OS_IMAGE_OPTIONS, "请选择操作系统", lambda o: o["name"])

def create_instance(project_id: Any,  zone: Any,  os_config: Any,  instance_name: Any="free-tier-vm") -> Any:
    instance_client = instances_client()
    image_client = images_client()

    print(f"\n[开始] 正在 {project_id} 项目中准备资源...")
    print(f"可用区: {zone}")
    print(f"系统: {os_config['name']}")

    try:
        image_response = get_image_from_family_with_retry(
            image_client,
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
            print_error(f"创建失败: {operation.error}")
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
                return InstanceInfo(
                    name=instance_name,
                    zone=zone,
                    status="PROVISIONING",
                    cpu_platform="Unknown CPU Platform",
                    network="global/networks/default",
                    internal_ip="-",
                    external_ip="-",
                )

    except Exception as e:
        print_warning(f"创建实例操作中止: {summarize_exception(e)}")
        LOGGER.error(traceback.format_exc())
    return None

def build_instance_info(instance: Any,  zone: Any) -> Any:
    return InstanceInfo.from_api_instance(instance, zone)

def get_instance_cache_key(project_id: Any,  instance_info: Any) -> Any:
    return f"{project_id}:{instance_info.zone}:{instance_info.name}"

def get_instance_by_name_with_zone(project_id: Any,  instance_name: Any,  zone: Any) -> Any:
    instance_client = instances_client()
    instance = get_instance_with_retry(instance_client, project_id, zone, instance_name)
    return build_instance_info(instance, zone)

def list_instances(project_id: Any) -> Any:
    print_info(f"正在扫描项目 {project_id} 中的实例...")

    gcloud_command = find_gcloud_command()
    if gcloud_command:
        try:
            # 本地/Cloud Shell 下 gcloud 通常比 Python REST 聚合扫描更快，优先走这条快路径。
            return list_instances_via_gcloud(project_id)
        except Exception as e:
            print_warning(f"通过 gcloud 获取实例列表失败，改用 API 回退: {summarize_exception(e)}")

    instance_client = instances_client()
    request = compute_v1.AggregatedListInstancesRequest(project=project_id)

    instances = []
    for zone_path, response in aggregated_list_instances_with_retry(instance_client, request, project_id):
        if not response.instances:
            continue
        zone_short = zone_path.split("/")[-1]
        for instance in response.instances:
            instances.append(build_instance_info(instance, zone_short))
    return instances

def format_instance_display_line(inst: Any,  index: Any=None) -> Any:
    network_short = inst.network.split("/")[-1] if inst.network else "-"
    prefix = f"[{index}] " if index is not None else "- "
    return (
        f"{prefix}{inst.name:<20} | 区域: {inst.zone:<15} | 状态: "
        f"{inst.status} | 网络: {network_short} | 内网IP: "
        f"{inst.internal_ip} | 外网IP: {inst.external_ip} | CPU: {inst.cpu_platform}"
    )

def print_instance_list(instances: Any,  numbered: Any=False) -> Any:
    for idx, inst in enumerate(instances, start=1):
        print(format_instance_display_line(inst, idx if numbered else None))

def find_instance_by_name(project_id: Any,  instance_name: Any,  zone: Any=None) -> Any:
    # 已知 zone 时直接按实例名读取，避免每次都把整个项目的实例列表扫一遍。
    if zone:
        try:
            return get_instance_by_name_with_zone(project_id, instance_name, zone)
        except Exception as e:
            if not is_not_found_error(e):
                raise

    instances = list_instances(project_id)
    matched_instances = [
        inst
        for inst in instances
        if inst.name == instance_name and (not zone or inst.zone == zone)
    ]

    if not matched_instances:
        zone_hint = f"（zone={zone}）" if zone else ""
        raise ValueError(f"未找到实例: {instance_name}{zone_hint}")

    if len(matched_instances) > 1:
        raise ValueError(f"找到多个同名实例 {instance_name}，请补充 --zone 指定可用区。")

    return matched_instances[0]

def select_instance(project_id: Any) -> Any:
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

def refresh_instance_info(project_id: Any,  instance_info: Any,  announce: Any=False) -> Any:
    instance_client = instances_client()
    instance_name = instance_info.name
    zone = instance_info.zone
    instance = get_instance_with_retry(instance_client, project_id, zone, instance_name)
    refreshed = build_instance_info(instance, zone)
    if announce:
        print_info(
            f"已刷新实例详情: 状态 {refreshed.status} | 外网IP: {refreshed.external_ip} | "
            f"CPU: {refreshed.cpu_platform}"
        )
    return refreshed

def wait_for_instance_status( instance_client: Any,  project_id: Any,  zone: Any,  instance_name: Any,  expected_statuses: Any,  timeout: Any=INSTANCE_STATUS_WAIT_TIMEOUT,  poll_interval: Any=INSTANCE_STATUS_POLL_INTERVAL,  heartbeat_interval: Any=INSTANCE_STATUS_HEARTBEAT_INTERVAL,  ) -> Any:
    if isinstance(expected_statuses, str):
        expected_statuses = {expected_statuses}
    else:
        expected_statuses = set(expected_statuses)

    deadline = time.time() + timeout
    wait_start_time = time.time()
    last_status = None
    last_heartbeat_time = None
    last_activity_time = time.time()
    target_text = "/".join(sorted(expected_statuses))

    while time.time() < deadline:
        last_activity_time = warn_if_long_pause(
            last_activity_time,
            f"等待实例 {instance_name} 进入 {target_text}",
        )
        get_started_at = time.time()
        try:
            current_inst = get_instance_with_retry(instance_client, project_id, zone, instance_name)
            last_activity_time = warn_if_long_pause(get_started_at, f"获取实例 {instance_name} 状态")
        except Exception as exc:
            last_activity_time = warn_if_long_pause(get_started_at, f"获取实例 {instance_name} 状态异常前")
            if is_transient_gcp_error(exc):
                print_warning(
                    f"获取实例 {instance_name} 状态时遇到临时网络错误，继续等待: {summarize_exception(exc)}"
                )
                sleep_and_detect_pause(poll_interval, f"等待实例 {instance_name} 状态重试")
                continue
            raise
        current_status = current_inst.status or "UNKNOWN"
        if current_status in expected_statuses:
            return current_inst, current_status

        if current_status != last_status:
            print_info(f"实例当前状态: {current_status}，继续等待进入 {target_text}...")
            last_status = current_status
            last_heartbeat_time = time.time()
        elif heartbeat_interval and last_heartbeat_time is not None and time.time() - last_heartbeat_time >= heartbeat_interval:
            waited = format_duration(time.time() - wait_start_time)
            print_info(f"实例仍为 {current_status}，已等待 {waited}，继续等待进入 {target_text}...")
            last_heartbeat_time = time.time()

        sleep_and_detect_pause(poll_interval, f"等待实例 {instance_name} 进入 {target_text}")

    return None, last_status or "UNKNOWN"

def wait_for_instance_status_change( instance_client: Any,  project_id: Any,  zone: Any,  instance_name: Any,  from_statuses: Any,  timeout: Any=INSTANCE_TRANSITION_CONFIRM_TIMEOUT,  poll_interval: Any=INSTANCE_TRANSITION_CONFIRM_POLL_INTERVAL,  heartbeat_interval: Any=INSTANCE_STATUS_HEARTBEAT_INTERVAL,  ) -> Any:
    from_statuses = set(from_statuses)
    deadline = time.time() + timeout
    wait_start_time = time.time()
    last_status = None
    last_heartbeat_time = None
    last_activity_time = time.time()

    while time.time() < deadline:
        last_activity_time = warn_if_long_pause(
            last_activity_time,
            f"等待实例 {instance_name} 脱离 {'/'.join(sorted(from_statuses))}",
        )
        get_started_at = time.time()
        try:
            current_inst = get_instance_with_retry(instance_client, project_id, zone, instance_name)
            last_activity_time = warn_if_long_pause(get_started_at, f"获取实例 {instance_name} 状态")
        except Exception as exc:
            last_activity_time = warn_if_long_pause(get_started_at, f"获取实例 {instance_name} 状态异常前")
            if is_transient_gcp_error(exc):
                print_warning(
                    f"获取实例 {instance_name} 状态时遇到临时网络错误，继续等待: {summarize_exception(exc)}"
                )
                sleep_and_detect_pause(poll_interval, f"等待实例 {instance_name} 状态变化重试")
                continue
            raise
        current_status = current_inst.status or "UNKNOWN"
        if current_status not in from_statuses:
            return current_inst, current_status

        if current_status != last_status:
            print_info(f"实例当前状态: {current_status}，继续等待状态变化...")
            last_status = current_status
            last_heartbeat_time = time.time()
        elif heartbeat_interval and last_heartbeat_time is not None and time.time() - last_heartbeat_time >= heartbeat_interval:
            waited = format_duration(time.time() - wait_start_time)
            print_info(f"实例仍为 {current_status}，已等待 {waited}，继续等待状态变化...")
            last_heartbeat_time = time.time()

        sleep_and_detect_pause(
            poll_interval,
            f"等待实例 {instance_name} 脱离 {'/'.join(sorted(from_statuses))}",
        )

    return None, last_status or "UNKNOWN"

def ensure_instance_running(instance_client: Any,  project_id: Any,  zone: Any,  instance_name: Any) -> Any:
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
        # 先盯实例状态变化，能避免“operation 已提交成功但我们还在同步等待”造成的重复耗时。
        changed_inst, changed_status = wait_for_instance_status_change(
            instance_client,
            project_id,
            zone,
            instance_name,
            {"TERMINATED", "STOPPED"},
        )
        if changed_inst is None:
            print_info("实例状态尚未变化，继续检查启动操作状态...")
            wait_for_operation(project_id, zone, operation.name, f"启动虚拟机 {instance_name}")
            print_info("虚拟机已通电，正在等待系统初始化...")
        elif changed_status == "RUNNING":
            print_info("虚拟机已进入 RUNNING。")
            return changed_inst
        else:
            print_info(f"虚拟机已进入 {changed_status}，正在等待系统初始化...")
    elif current_status != "RUNNING":
        print_info(f"虚拟机当前状态为 {current_status}，等待其进入 RUNNING...")

    running_inst, last_status = wait_for_instance_status(
        instance_client, project_id, zone, instance_name, "RUNNING"
    )
    if running_inst is None:
        raise TimeoutError(f"等待虚拟机 {instance_name} 启动超时，最后状态: {last_status}")
    return running_inst

def wait_for_cpu_platform( instance_client: Any,  project_id: Any,  zone: Any,  instance_name: Any,  timeout: Any=CPU_PLATFORM_WAIT_TIMEOUT,  poll_interval: Any=CPU_PLATFORM_POLL_INTERVAL,  ) -> Any:
    deadline = time.time() + timeout
    attempt_counter = 0
    last_status = "UNKNOWN"
    last_activity_time = time.time()

    while time.time() < deadline:
        last_activity_time = warn_if_long_pause(
            last_activity_time,
            f"等待实例 {instance_name} 同步 CPU 平台",
        )
        get_started_at = time.time()
        try:
            current_inst = get_instance_with_retry(instance_client, project_id, zone, instance_name)
            last_activity_time = warn_if_long_pause(get_started_at, f"获取实例 {instance_name} CPU 信息")
        except Exception as exc:
            last_activity_time = warn_if_long_pause(get_started_at, f"获取实例 {instance_name} CPU 信息异常前")
            if is_transient_gcp_error(exc):
                print_warning(
                    f"获取实例 {instance_name} CPU 信息时遇到临时网络错误，继续等待: {summarize_exception(exc)}"
                )
                sleep_and_detect_pause(poll_interval, f"等待实例 {instance_name} CPU 信息重试")
                continue
            raise
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

        sleep_and_detect_pause(poll_interval, f"等待实例 {instance_name} 同步 CPU 平台")

    return None, last_status

def ensure_instance_stopped(instance_client: Any,  project_id: Any,  zone: Any,  instance_name: Any) -> Any:
    stop_start_time = time.time()
    current_inst = get_instance_with_retry(instance_client, project_id, zone, instance_name)
    current_status = current_inst.status or "UNKNOWN"

    if current_status in {"TERMINATED", "STOPPED"}:
        print_info(f"虚拟机 {instance_name} 已处于关机状态，跳过关停请求。")
        return current_inst, 0

    if current_status == "STOPPING":
        print_info(f"虚拟机 {instance_name} 正在关停，等待其完全停止...")
    else:
        operation = stop_instance_with_retry(instance_client, project_id, zone, instance_name)
        # 关停同样优先看状态变化，只有状态迟迟不动时才回退等 operation。
        changed_inst, changed_status = wait_for_instance_status_change(
            instance_client,
            project_id,
            zone,
            instance_name,
            {"RUNNING"},
        )
        if changed_inst is None:
            print_info("实例状态尚未变化，继续检查关停操作状态...")
            wait_for_operation(project_id, zone, operation.name, f"关停虚拟机 {instance_name}")
        elif changed_status in {"TERMINATED", "STOPPED"}:
            return changed_inst, max(0.0, time.time() - stop_start_time)
        else:
            print_info(f"实例已进入 {changed_status}，继续等待完全停止...")

    stopped_inst, last_status = wait_for_instance_status(
        instance_client, project_id, zone, instance_name, {"TERMINATED", "STOPPED"}
    )
    if stopped_inst is None:
        raise TimeoutError(f"等待虚拟机 {instance_name} 关停超时，最后状态: {last_status}")
    return stopped_inst, max(0.0, time.time() - stop_start_time)

def build_setup_dry_run_instance(instance_name: str,  zone: str) -> InstanceInfo:
    return InstanceInfo(
        name=instance_name,
        zone=zone,
        status="UNKNOWN",
        cpu_platform="Unknown CPU Platform",
        network="global/networks/default",
        internal_ip="-",
        external_ip="-",
    )
