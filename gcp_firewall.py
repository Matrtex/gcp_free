from __future__ import annotations

import ipaddress

from gcp_common import (
    Any,
    LOGGER,
    RESOURCE_LIST_REQUEST_TIMEOUT,
    RESOURCE_READ_REQUEST_TIMEOUT,
    compute_v1,
    disks_client,
    firewalls_client,
    instances_client,
    os,
    resolve_asset_path,
    traceback,
)
from gcp_instance import list_instances
from gcp_operations import (
    delete_disk_with_retry,
    delete_firewall_with_retry,
    delete_instance_with_retry,
    get_instance_with_retry,
    insert_firewall_with_retry,
    wait_for_global_operation,
    wait_for_operation,
)
from gcp_utils import (
    is_not_found_error,
    print_info,
    print_success,
    print_warning,
    summarize_exception,
)

__all__ = [
    'resolve_cdn_ip_path',
    'read_cdn_ips',
    'set_protocol_field',
    'is_already_exists_error',
    'add_allow_all_ingress',
    'add_deny_cdn_egress',
    'configure_firewall',
    'configure_firewall_non_interactive',
    'delete_firewall_rule',
    'delete_deny_cdn_egress',
    'delete_managed_firewall_rules',
    'ensure_deny_cdn_rebuild_scope_safe',
    'delete_disks_if_needed',
    'delete_free_resources',
]

ALLOW_ALL_INGRESS_RULE_NAME = "allow-all-ingress-custom"
DENY_CDN_EGRESS_RULE_NAME = "deny-cdn-egress-custom"
DENY_CDN_EGRESS_RULE_PREFIX = f"{DENY_CDN_EGRESS_RULE_NAME}-"
FIREWALL_IP_RANGES_PER_RULE = 256
FALLBACK_DENY_RULE_DELETE_LIMIT = 64

def resolve_cdn_ip_path(filename: Any="cdnip.txt") -> Any:
    filename = str(filename)
    if os.path.isabs(filename):
        return filename
    if filename == "cdnip.txt":
        return str(resolve_asset_path(filename))
    return filename

def read_cdn_ips(filename: Any="cdnip.txt") -> Any:
    resolved_filename = resolve_cdn_ip_path(filename)
    if not os.path.exists(resolved_filename):
        print_warning(f"找不到文件: {resolved_filename}")
        print_warning("请在脚本同目录下创建该文件，并填入 IP 段。")
        return []

    ip_list = []
    with open(resolved_filename, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            clean_line = line.strip()
            if not clean_line or clean_line.startswith("#"):
                continue
            ip_range = clean_line.split("#", 1)[0].split()[0].strip()
            if not ip_range:
                continue
            try:
                network = ipaddress.ip_network(ip_range, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"{resolved_filename}:{line_number} 不是有效的 IP 或 CIDR: {ip_range}"
                ) from exc
            ip_list.append(str(network))

    print_info(f"已从 {resolved_filename} 读取到 {len(ip_list)} 个 IP 段。")
    return ip_list

def set_protocol_field(config_object: Any,  value: Any) -> Any:
    try:
        config_object.ip_protocol = value
    except AttributeError:
        try:
            config_object.I_p_protocol = value
        except AttributeError:
            print_warning(f"无法设置协议字段。对象 '{type(config_object).__name__}' 的有效属性如下:")
            print_warning(str([d for d in dir(config_object) if not d.startswith("_")]))
            raise

def iter_exception_chain(exc: Any) -> Any:
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)

def is_already_exists_error(exc: Any, rule_name: Any = None) -> Any:
    target_rule = str(rule_name or "").lower()
    for item in iter_exception_chain(exc):
        message = str(item).lower()
        if "already exists" not in message:
            continue
        if not target_rule or target_rule in message:
            return True
    return False

def get_firewall_rule(firewall_client: Any, project_id: Any, rule_name: Any) -> Any:
    try:
        return firewall_client.get(project=project_id, firewall=rule_name, timeout=RESOURCE_READ_REQUEST_TIMEOUT)
    except Exception as exc:
        if is_not_found_error(exc):
            return None
        print_warning(f"检查防火墙规则 {rule_name} 是否存在失败，将继续尝试创建: {summarize_exception(exc)}")
        return None

def firewall_rule_exists(firewall_client: Any, project_id: Any, rule_name: Any) -> Any:
    return get_firewall_rule(firewall_client, project_id, rule_name) is not None

def get_network_name(network: Any) -> Any:
    network_text = str(network or "").strip()
    if not network_text:
        return ""
    marker = "/global/networks/"
    if marker in network_text:
        return network_text.rsplit(marker, 1)[1].strip("/")
    marker = "global/networks/"
    if network_text.startswith(marker):
        return network_text[len(marker):].strip("/")
    return network_text.strip("/")

def normalize_firewall_ranges(ranges: Any) -> Any:
    return sorted(str(item).strip() for item in (ranges or []) if str(item).strip())

def get_protocol_value(config_object: Any) -> Any:
    return (
        getattr(config_object, "ip_protocol", None)
        or getattr(config_object, "I_p_protocol", None)
        or ""
    )

def firewall_rule_has_single_protocol_all(rule: Any, field_name: Any) -> Any:
    configs = list(getattr(rule, field_name, None) or [])
    if len(configs) != 1:
        return False
    return str(get_protocol_value(configs[0])).lower() == "all"

def firewall_rule_network_matches(rule: Any, network: Any) -> Any:
    return get_network_name(getattr(rule, "network", "")) == get_network_name(network)

def firewall_rule_priority_matches(rule: Any, expected_priority: Any) -> Any:
    try:
        return int(getattr(rule, "priority", -1)) == int(expected_priority)
    except (TypeError, ValueError):
        return False

def is_allow_all_ingress_rule_compatible(rule: Any, network: Any) -> Any:
    return (
        str(getattr(rule, "direction", "")).upper() == "INGRESS"
        and firewall_rule_network_matches(rule, network)
        and firewall_rule_priority_matches(rule, 1000)
        and normalize_firewall_ranges(getattr(rule, "source_ranges", None)) == ["0.0.0.0/0"]
        and firewall_rule_has_single_protocol_all(rule, "allowed")
    )

def is_deny_cdn_egress_rule_compatible(rule: Any, network: Any, ip_ranges: Any) -> Any:
    return (
        str(getattr(rule, "direction", "")).upper() == "EGRESS"
        and firewall_rule_network_matches(rule, network)
        and firewall_rule_priority_matches(rule, 900)
        and normalize_firewall_ranges(getattr(rule, "destination_ranges", None)) == normalize_firewall_ranges(ip_ranges)
        and firewall_rule_has_single_protocol_all(rule, "denied")
    )

def handle_existing_firewall_rule(rule_name: Any, existing_rule: Any, network: Any, compatible: Any) -> Any:
    if compatible:
        print_success(f"防火墙规则已存在且匹配当前网络，已跳过重复创建: {rule_name}")
        return True
    print_warning(
        f"防火墙规则 {rule_name} 已存在，但网络或配置与本次目标不一致。"
        f"现有网络: {getattr(existing_rule, 'network', '-') or '-'}，"
        f"目标网络: {network}。请先删除本工具添加的规则后重试。"
    )
    return False

def chunk_ip_ranges(ip_ranges: Any, chunk_size: Any=FIREWALL_IP_RANGES_PER_RULE) -> Any:
    ranges = list(ip_ranges or [])
    return [ranges[index:index + chunk_size] for index in range(0, len(ranges), chunk_size)]

def get_deny_cdn_egress_rule_specs(ip_ranges: Any) -> Any:
    chunks = chunk_ip_ranges(ip_ranges)
    if not chunks:
        return []
    if len(chunks) == 1:
        return [(DENY_CDN_EGRESS_RULE_NAME, chunks[0])]
    return [
        (f"{DENY_CDN_EGRESS_RULE_NAME}-{index:03d}", chunk)
        for index, chunk in enumerate(chunks, start=1)
    ]

def is_deny_cdn_egress_rule_name(rule_name: Any) -> Any:
    rule_name = str(rule_name or "")
    if rule_name == DENY_CDN_EGRESS_RULE_NAME:
        return True
    if not rule_name.startswith(DENY_CDN_EGRESS_RULE_PREFIX):
        return False
    suffix = rule_name[len(DENY_CDN_EGRESS_RULE_PREFIX):]
    return len(suffix) == 3 and suffix.isdigit()

def is_managed_firewall_rule_name(rule_name: Any) -> Any:
    rule_name = str(rule_name or "")
    return rule_name == ALLOW_ALL_INGRESS_RULE_NAME or is_deny_cdn_egress_rule_name(rule_name)

def fallback_deny_cdn_rule_names() -> Any:
    return [
        DENY_CDN_EGRESS_RULE_NAME,
        *[
            f"{DENY_CDN_EGRESS_RULE_NAME}-{index:03d}"
            for index in range(1, FALLBACK_DENY_RULE_DELETE_LIMIT + 1)
        ],
    ]

def list_firewall_rules(project_id: Any, firewall_client: Any) -> Any:
    try:
        rules = firewall_client.list(
            project=project_id,
            timeout=RESOURCE_LIST_REQUEST_TIMEOUT,
        )
        return sorted(
            [rule for rule in rules if getattr(rule, "name", None)],
            key=lambda rule: str(rule.name),
        )
    except Exception as exc:
        print_warning(f"列出防火墙规则失败: {summarize_exception(exc)}")
        return None

def list_firewall_rule_names(project_id: Any, firewall_client: Any) -> Any:
    rules = list_firewall_rules(project_id, firewall_client)
    if rules is None:
        return None
    return [str(rule.name) for rule in rules]

def get_deny_cdn_rules(project_id: Any, firewall_client: Any) -> Any:
    rules = list_firewall_rules(project_id, firewall_client)
    if rules is None:
        return None
    return [rule for rule in rules if is_deny_cdn_egress_rule_name(getattr(rule, "name", ""))]

def get_deny_cdn_rule_names_to_delete(project_id: Any, firewall_client: Any, network: Any = None) -> Any:
    rules = get_deny_cdn_rules(project_id, firewall_client)
    target_network = str(network or "").strip()
    if rules is None:
        if target_network:
            print_warning("无法列出防火墙规则，不能安全按目标网络清理拒绝 CDN 出站规则。")
            return None
        return fallback_deny_cdn_rule_names()
    if target_network:
        return [
            str(rule.name)
            for rule in rules
            if firewall_rule_network_matches(rule, target_network)
        ]
    return [str(rule.name) for rule in rules]

def get_managed_firewall_rule_names_to_delete(project_id: Any, firewall_client: Any) -> Any:
    rule_names = list_firewall_rule_names(project_id, firewall_client)
    if rule_names is None:
        return [
            ALLOW_ALL_INGRESS_RULE_NAME,
            *fallback_deny_cdn_rule_names(),
        ]
    return [rule_name for rule_name in rule_names if is_managed_firewall_rule_name(rule_name)]

def describe_managed_firewall_rules() -> Any:
    return f"{ALLOW_ALL_INGRESS_RULE_NAME}, {DENY_CDN_EGRESS_RULE_NAME} 及其拆分规则"

def ensure_deny_cdn_rebuild_scope_safe(project_id: Any, firewall_client: Any, network: Any) -> Any:
    rules = get_deny_cdn_rules(project_id, firewall_client)
    if rules is None:
        print_warning("无法确认现有拒绝 CDN 出站规则所属网络，已停止重建以避免误删其它网络规则。")
        return False

    foreign_rules = [
        rule
        for rule in rules
        if not firewall_rule_network_matches(rule, network)
    ]
    if not foreign_rules:
        return True

    rule_summaries = ", ".join(
        f"{getattr(rule, 'name', '-') or '-'}({getattr(rule, 'network', '-') or '-'})"
        for rule in foreign_rules[:5]
    )
    if len(foreign_rules) > 5:
        rule_summaries += f" 等 {len(foreign_rules)} 条"
    print_warning(
        "检测到同项目中已有其它网络的拒绝 CDN 出站规则，已停止重建以避免误删: "
        f"{rule_summaries}。目标网络: {network}。请先手动确认或删除旧规则。"
    )
    return False

def add_allow_all_ingress(project_id: Any,  network: Any) -> Any:
    firewall_client = firewalls_client()
    rule_name = ALLOW_ALL_INGRESS_RULE_NAME

    print_info(f"正在创建入站规则: {rule_name} ...")
    existing_rule = get_firewall_rule(firewall_client, project_id, rule_name)
    if existing_rule is not None:
        return handle_existing_firewall_rule(
            rule_name,
            existing_rule,
            network,
            is_allow_all_ingress_rule_compatible(existing_rule, network),
        )

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
        print_info("正在应用规则...")
        wait_for_global_operation(project_id, operation.name, f"创建防火墙规则 {rule_name}")
        print_success("已添加允许所有入站连接的规则。")
        return True
    except Exception as e:
        if is_already_exists_error(e, rule_name):
            existing_rule = get_firewall_rule(firewall_client, project_id, rule_name)
            if existing_rule is not None:
                return handle_existing_firewall_rule(
                    rule_name,
                    existing_rule,
                    network,
                    is_allow_all_ingress_rule_compatible(existing_rule, network),
                )
            print_warning(f"防火墙规则 {rule_name} 已存在，但无法读取现有规则详情，已停止以避免误判。")
            return False
        else:
            print_warning(f"创建防火墙规则失败: {summarize_exception(e)}")
            LOGGER.error(traceback.format_exc())
            return False

def add_single_deny_cdn_egress_rule(project_id: Any,  rule_name: Any,  ip_ranges: Any,  network: Any) -> Any:
    firewall_client = firewalls_client()

    print_info(f"正在创建出站拒绝规则: {rule_name} ...")
    existing_rule = get_firewall_rule(firewall_client, project_id, rule_name)
    if existing_rule is not None:
        return handle_existing_firewall_rule(
            rule_name,
            existing_rule,
            network,
            is_deny_cdn_egress_rule_compatible(existing_rule, network, ip_ranges),
        )

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
        print_info("正在应用规则...")
        wait_for_global_operation(project_id, operation.name, f"创建防火墙规则 {rule_name}")
        print_success(f"已添加拒绝规则 {rule_name}，共拦截 {len(ip_ranges)} 个 IP 段。")
        return True
    except Exception as e:
        if is_already_exists_error(e, rule_name):
            existing_rule = get_firewall_rule(firewall_client, project_id, rule_name)
            if existing_rule is not None:
                return handle_existing_firewall_rule(
                    rule_name,
                    existing_rule,
                    network,
                    is_deny_cdn_egress_rule_compatible(existing_rule, network, ip_ranges),
                )
            print_warning(f"防火墙规则 {rule_name} 已存在，但无法读取现有规则详情，已停止以避免误判。")
            return False
        else:
            print_warning(f"创建防火墙规则失败: {summarize_exception(e)}")
            LOGGER.error(traceback.format_exc())
            return False

def add_deny_cdn_egress(project_id: Any,  ip_ranges: Any,  network: Any) -> Any:
    rule_specs = get_deny_cdn_egress_rule_specs(ip_ranges)
    if not rule_specs:
        print_info("IP 列表为空，跳过创建拒绝规则。")
        return True

    print_info("正在按当前 cdnip.txt 重建拒绝 CDN 出站规则。")
    firewall_client = firewalls_client()
    if not ensure_deny_cdn_rebuild_scope_safe(project_id, firewall_client, network):
        return False
    if not delete_deny_cdn_egress(project_id, allow_cdn_message=False, network=network):
        print_warning("清理旧的拒绝 CDN 出站规则失败，已停止创建新规则。")
        return False

    if len(rule_specs) > 1:
        total_ip_ranges = sum(len(rule_ip_ranges) for _rule_name, rule_ip_ranges in rule_specs)
        print_info(
            f"IP 数量 ({total_ip_ranges}) 超过 GCP 单条规则上限 "
            f"({FIREWALL_IP_RANGES_PER_RULE})，将拆分为 {len(rule_specs)} 条规则。"
        )

    all_ok = True
    for rule_name, rule_ip_ranges in rule_specs:
        all_ok = add_single_deny_cdn_egress_rule(project_id, rule_name, rule_ip_ranges, network) and all_ok
    return all_ok

def configure_firewall(project_id: Any,  network: Any) -> Any:
    print_info("防火墙规则管理菜单")
    print_info(f"目标网络: {network}")
    while True:
        print("\n[1] 添加允许所有入站连接规则 (0.0.0.0/0)")
        print("[2] 添加拒绝 cdnip.txt 中 IP 出站连接规则")
        print("[3] 删除拒绝 CDN 出站规则（允许 CDN 访问）")
        print("[4] 删除本工具添加的全部防火墙规则")
        print("[0] 返回")
        choice = input("请输入数字选择: ").strip().lower()

        if choice == "0":
            return
        if choice == "1":
            add_allow_all_ingress(project_id, network)
        elif choice == "2":
            ips = read_cdn_ips()
            if ips:
                add_deny_cdn_egress(project_id, ips, network)
        elif choice == "3":
            delete_deny_cdn_egress(project_id)
        elif choice == "4":
            confirm = input("请输入 DELETE 确认删除本工具添加的全部防火墙规则: ").strip()
            if confirm == "DELETE":
                delete_managed_firewall_rules(project_id)
            else:
                print_info("已取消删除全部防火墙规则。")
        else:
            print_warning("输入无效，请重试。")

def configure_firewall_non_interactive(
    project_id: Any,
    network: Any,
    allow_all_ingress: Any=False,
    deny_cdn_egress: Any=False,
    cdnip_filename: Any="cdnip.txt",
    delete_deny_cdn: Any=False,
    delete_managed_rules: Any=False,
) -> Any:
    if not any([allow_all_ingress, deny_cdn_egress, delete_deny_cdn, delete_managed_rules]):
        raise ValueError(
            "非交互防火墙模式至少要指定一个操作：--allow-all-ingress、--deny-cdn-egress、"
            "--delete-deny-cdn-egress 或 --delete-managed-rules。"
        )

    print_info("防火墙规则管理（非交互模式）")
    print_info(f"目标网络: {network}")
    all_ok = True

    if delete_managed_rules:
        all_ok = delete_managed_firewall_rules(project_id) and all_ok
    elif delete_deny_cdn:
        all_ok = delete_deny_cdn_egress(project_id) and all_ok

    if allow_all_ingress:
        all_ok = add_allow_all_ingress(project_id, network) and all_ok
    else:
        print_info("已跳过入站规则配置。")

    if deny_cdn_egress:
        ips = read_cdn_ips(cdnip_filename)
        if not ips:
            print_warning("已要求添加拒绝 CDN 出站规则，但 IP 列表为空，无法创建规则。")
            all_ok = False
        else:
            all_ok = add_deny_cdn_egress(project_id, ips, network) and all_ok
    else:
        print_info("已跳过出站规则配置。")

    if not all_ok:
        raise RuntimeError("非交互防火墙规则配置失败，已停止后续流程。")

    print_info("所有操作完成。")

def delete_firewall_rule(project_id: Any,  rule_name: Any) -> Any:
    firewall_client = firewalls_client()
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

def delete_deny_cdn_egress(project_id: Any, allow_cdn_message: Any=True, network: Any = None) -> Any:
    if allow_cdn_message:
        print_info("正在删除拒绝 CDN 出站规则，删除后将允许 CDN 访问。")
    else:
        print_info("正在清理旧的拒绝 CDN 出站规则。")
    firewall_client = firewalls_client()
    rule_names = get_deny_cdn_rule_names_to_delete(project_id, firewall_client, network=network)
    if rule_names is None:
        return False
    if not rule_names:
        print_info("未发现本工具添加的拒绝 CDN 出站规则。")
        return True
    all_ok = True
    for rule_name in rule_names:
        all_ok = delete_firewall_rule(project_id, rule_name) and all_ok
    return all_ok

def delete_managed_firewall_rules(project_id: Any) -> Any:
    print_info("正在删除本工具添加的全部防火墙规则...")
    firewall_client = firewalls_client()
    rule_names = get_managed_firewall_rule_names_to_delete(project_id, firewall_client)
    if not rule_names:
        print_info("未发现本工具添加的防火墙规则。")
        return True
    all_ok = True
    for rule_name in rule_names:
        all_ok = delete_firewall_rule(project_id, rule_name) and all_ok
    return all_ok

def delete_disks_if_needed(project_id: Any,  zone: Any,  disk_names: Any) -> Any:
    if not disk_names:
        return True
    disk_client = disks_client()
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

def delete_free_resources(project_id: Any,  instance_info: Any,  confirmed: Any=False) -> Any:
    instance_name = instance_info.name
    zone = instance_info.zone

    print_info("即将删除以下资源（可以重新创建免费资源）：")
    print_info(f"- 实例: {instance_name} ({zone})")
    print_info("- 相关磁盘（如仍存在）")
    print_info(f"- 防火墙规则: {describe_managed_firewall_rules()}")
    if not confirmed:
        confirm = input("请输入 DELETE 确认删除: ").strip()
        if confirm != "DELETE":
            print_info("已取消删除操作。")
            return False
    else:
        print_info("已通过非交互参数确认删除。")

    cleanup_ok = True
    instance_client = instances_client()
    disk_names = []
    try:
        inst = get_instance_with_retry(instance_client, project_id, zone, instance_name)
        for disk in inst.disks:
            if getattr(disk, 'source', None):
                try:
                    disk_name = disk.source.split("/")[-1]
                    if disk_name:
                        disk_names.append(disk_name)
                except Exception:
                    print_warning(f"无法解析磁盘来源: {disk.source}")
    except Exception as e:
        print_warning(f"读取实例信息失败，磁盘清理可能不完整: {e}")
        cleanup_ok = False

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

    disks_deleted = delete_disks_if_needed(project_id, zone, disk_names)
    if not disks_deleted:
        print_warning("部分磁盘删除失败，请手动检查控制台。")
        cleanup_ok = False

    print_info("正在检查项目中其他实例...")
    try:
        remaining_instances = list_instances(project_id)
        remaining_instances = [
            inst for inst in remaining_instances
            if not (inst.name == instance_name and inst.zone == zone)
        ]

        if not remaining_instances:
            print_info("项目中无其他实例，正在清理防火墙规则...")
            firewall_rules_deleted = delete_managed_firewall_rules(project_id)
            if not firewall_rules_deleted:
                print_warning("部分防火墙规则清理失败，请手动检查控制台。")
                cleanup_ok = False
        else:
            print_info(f"项目中还有 {len(remaining_instances)} 个其他实例，保留防火墙规则。")
    except Exception as e:
        print_warning(f"获取实例列表失败，将跳过防火墙规则清理: {e}")
        cleanup_ok = False

    if not cleanup_ok:
        print_warning("清理未完全完成，请手动检查控制台确认残留资源。")
        return False

    print_success("清理完成。建议到控制台确认无残留资源。")
    return True
