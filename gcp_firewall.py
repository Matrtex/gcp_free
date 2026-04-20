from __future__ import annotations

from gcp_common import (
    Any,
    FIREWALL_RULES_TO_CLEAN,
    LOGGER,
    compute_v1,
    disks_client,
    firewalls_client,
    instances_client,
    os,
    resolve_asset_path,
    traceback,
)
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
    'add_allow_all_ingress',
    'add_deny_cdn_egress',
    'configure_firewall',
    'configure_firewall_non_interactive',
    'delete_firewall_rule',
    'delete_disks_if_needed',
    'delete_free_resources',
]

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
        print(f"【错误】找不到文件: {resolved_filename}")
        print("请在脚本同目录下创建该文件，并填入IP段。")
        return []

    ip_list = []
    with open(resolved_filename, "r", encoding="utf-8") as f:
        for line in f:
            clean_line = line.strip()
            if clean_line:
                ip = clean_line.split()[0]
                ip_list.append(ip)

    print(f"已从 {resolved_filename} 读取到 {len(ip_list)} 个 IP 段。")
    return ip_list

def set_protocol_field(config_object: Any,  value: Any) -> Any:
    try:
        config_object.ip_protocol = value
    except AttributeError:
        try:
            config_object.I_p_protocol = value
        except AttributeError:
            print(f"\n【调试信息】无法设置协议字段。对象 '{type(config_object).__name__}' 的有效属性如下:")
            print([d for d in dir(config_object) if not d.startswith("_")])
            raise

def add_allow_all_ingress(project_id: Any,  network: Any) -> Any:
    firewall_client = firewalls_client()
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
        return True
    except Exception as e:
        if "already exists" in str(e):
            print_warning(f"规则 {rule_name} 已存在。")
            return True
        else:
            print_warning(f"创建防火墙规则失败: {summarize_exception(e)}")
            LOGGER.error(traceback.format_exc())
            return False

def add_deny_cdn_egress(project_id: Any,  ip_ranges: Any,  network: Any) -> Any:
    if not ip_ranges:
        print("IP 列表为空，跳过创建拒绝规则。")
        return True

    firewall_client = firewalls_client()
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
        return True
    except Exception as e:
        if "already exists" in str(e):
            print_warning(f"规则 {rule_name} 已存在。")
            return True
        else:
            print_warning(f"创建防火墙规则失败: {summarize_exception(e)}")
            LOGGER.error(traceback.format_exc())
            return False

def configure_firewall(project_id: Any,  network: Any) -> Any:
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

def configure_firewall_non_interactive( project_id: Any,  network: Any,  allow_all_ingress: Any=False,  deny_cdn_egress: Any=False,  cdnip_filename: Any="cdnip.txt",  ) -> Any:
    if not allow_all_ingress and not deny_cdn_egress:
        raise ValueError("非交互防火墙模式至少要指定 --allow-all-ingress 或 --deny-cdn-egress。")

    print("\n------------------------------------------------")
    print("防火墙规则管理（非交互模式）")
    print("------------------------------------------------")
    print(f"目标网络: {network}")
    all_ok = True

    if allow_all_ingress:
        all_ok = add_allow_all_ingress(project_id, network) and all_ok
    else:
        print("已跳过入站规则配置。")

    if deny_cdn_egress:
        ips = read_cdn_ips(cdnip_filename)
        if ips:
            if len(ips) > 256:
                print(f"【警告】IP 数量 ({len(ips)}) 超过 GCP 单条规则上限 (256)。")
                print("脚本将只取前 256 个 IP。")
                ips = ips[:256]
            all_ok = add_deny_cdn_egress(project_id, ips, network) and all_ok
    else:
        print("已跳过出站规则配置。")

    if not all_ok:
        raise RuntimeError("非交互防火墙规则配置失败，已停止后续流程。")

    print("\n所有操作完成。")

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

    print("\n------------------------------------------------")
    print("即将删除以下资源（可以重新创建免费资源）：")
    print(f"- 实例: {instance_name} ({zone})")
    print("- 相关磁盘（如仍存在）")
    print(f"- 防火墙规则: {', '.join(FIREWALL_RULES_TO_CLEAN)}")
    if not confirmed:
        confirm = input("请输入 DELETE 确认删除: ").strip()
        if confirm != "DELETE":
            print("已取消删除操作。")
            return False
    else:
        print_info("已通过非交互参数确认删除。")

    instance_client = instances_client()
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
