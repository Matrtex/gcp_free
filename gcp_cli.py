from __future__ import annotations

from gcp_common import (
    ActionSpec,
    Any,
    InstanceInfo,
    LOCAL_SCRIPT_FILES,
    Namespace,
    REGION_OPTIONS,
    RemoteConfig,
    argparse,
    find_gcloud_command,
    getpass,
    os,
    shutil,
    update_cdnip_file,
)
from gcp_firewall import (
    configure_firewall_non_interactive,
    delete_free_resources,
)
from gcp_instance import (
    build_setup_dry_run_instance,
    create_instance,
    find_instance_by_name,
    list_instances,
    print_instance_list,
)
from gcp_menu import (
    menu_create_action,
    menu_delete_resources_action,
    menu_deploy_dae_config_action,
    menu_doctor_action,
    menu_firewall_action,
    menu_remote_apt_action,
    menu_remote_dae_action,
    menu_reroll_action,
    menu_reroll_ip_action,
    menu_reroll_ip_amd_action,
    menu_select_instance_action,
    menu_show_reroll_state_action,
    menu_traffic_monitor_action,
)
from gcp_remote import (
    deploy_dae_config,
    prepare_instance_for_remote,
    run_remote_script,
    show_remote_status,
)
from gcp_reroll import (
    reroll_cpu_loop,
    reroll_ip_amd_loop,
    reroll_ip_loop,
    show_reroll_state,
)
from gcp_utils import (
    configure_runtime_logging,
    ensure_libraries_or_exit,
    handle_doctor,
    print_info,
    print_success,
    print_warning,
    resolve_os_config,
    resolve_zone_for_create,
)

__all__ = [
    'build_remote_config_from_args',
    'get_cli_instance',
    'prepare_cli_remote_instance',
    'handle_create_cli',
    'handle_list_instances_cli',
    'handle_reroll_amd_cli',
    'handle_reroll_ip_cli',
    'handle_reroll_ip_amd_cli',
    'handle_firewall_cli',
    'handle_run_script_cli',
    'handle_deploy_dae_config_cli',
    'handle_delete_resources_cli',
    'handle_doctor_cli',
    'handle_show_reroll_state_cli',
    'handle_update_cdnip_cli',
    'handle_status_cli',
    'run_setup_remote_step',
    'handle_setup_cli',
    'build_arg_parser',
    'parse_args',
    'run_cli',
    'ACTION_SPECS',
    'ACTION_SPEC_MAP',
]

def build_remote_config_from_args(args: Any) -> Any:
    has_gcloud = find_gcloud_command() is not None
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
        return RemoteConfig(method="gcloud")

    if requested_method == "ssh" or (not requested_method and not has_gcloud):
        if not has_ssh:
            raise ValueError("当前环境未安装 ssh，无法使用 SSH 远程模式。")
        ssh_key = getattr(args, "ssh_key", "") or ""
        if ssh_key:
            ssh_key = os.path.expanduser(ssh_key)
            if not os.path.isfile(ssh_key):
                raise ValueError(f"SSH 私钥文件不存在: {ssh_key}")
        return RemoteConfig(
            method="ssh",
            user=getattr(args, "ssh_user", None) or getpass.getuser(),
            port=str(getattr(args, "ssh_port", "22") or "22"),
            key=ssh_key,
        )

    if has_gcloud:
        return RemoteConfig(method="gcloud")

    if has_ssh:
        ssh_key = getattr(args, "ssh_key", "") or ""
        if ssh_key:
            ssh_key = os.path.expanduser(ssh_key)
            if not os.path.isfile(ssh_key):
                raise ValueError(f"SSH 私钥文件不存在: {ssh_key}")
        return RemoteConfig(
            method="ssh",
            user=getattr(args, "ssh_user", None) or getpass.getuser(),
            port=str(getattr(args, "ssh_port", "22") or "22"),
            key=ssh_key,
        )

    raise ValueError("当前环境既没有 gcloud，也没有 ssh，无法执行远程操作。")

def get_cli_instance(args: Any) -> Any:
    if getattr(args, "dry_run", False) and getattr(args, "instance", None) and getattr(args, "zone", None):
        return InstanceInfo(
            name=args.instance,
            zone=args.zone,
            status="UNKNOWN",
            cpu_platform="Unknown CPU Platform",
            network="global/networks/default",
            internal_ip="-",
            external_ip="-",
        )
    return find_instance_by_name(args.project_id, args.instance, getattr(args, "zone", None))

def prepare_cli_remote_instance(args: Any) -> Any:
    instance_info = get_cli_instance(args)
    remote_config = build_remote_config_from_args(args)
    if getattr(args, "dry_run", False):
        return instance_info, remote_config
    remote_instance = prepare_instance_for_remote(args.project_id, instance_info, remote_config)
    if not remote_instance:
        raise RuntimeError("远程实例尚未就绪，无法继续执行远程操作。")
    return remote_instance, remote_config

def handle_create_cli(args: Namespace) -> None:
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

def handle_list_instances_cli(args: Namespace) -> None:
    instances = list_instances(args.project_id)
    if not instances:
        print_warning("该项目中没有任何实例。")
        return
    print_instance_list(instances, numbered=False)

def handle_reroll_amd_cli(args: Namespace) -> None:
    instance_info = get_cli_instance(args)
    reroll_cpu_loop(
        args.project_id,
        instance_info,
        state_file=args.state_file,
        resume=args.resume,
    )

def handle_reroll_ip_cli(args: Namespace) -> None:
    instance_info = get_cli_instance(args)
    reroll_ip_loop(
        args.project_id,
        instance_info,
        state_file=args.state_file,
        resume=args.resume,
    )

def handle_reroll_ip_amd_cli(args: Namespace) -> None:
    instance_info = get_cli_instance(args)
    reroll_ip_amd_loop(
        args.project_id,
        instance_info,
        state_file=args.state_file,
        resume=args.resume,
    )

def handle_firewall_cli(args: Namespace) -> None:
    instance_info = get_cli_instance(args)
    network = instance_info.network or "global/networks/default"
    configure_firewall_non_interactive(
        args.project_id,
        network,
        allow_all_ingress=args.allow_all_ingress,
        deny_cdn_egress=args.deny_cdn_egress,
        cdnip_filename=args.cdnip_file,
    )

def handle_run_script_cli(args: Namespace) -> None:
    remote_instance, remote_config = prepare_cli_remote_instance(args)
    if not run_remote_script(
        args.project_id,
        remote_instance,
        args.script_key,
        remote_config,
        dry_run=args.dry_run,
    ):
        raise RuntimeError("远程脚本执行失败。")

def handle_deploy_dae_config_cli(args: Namespace) -> None:
    remote_instance, remote_config = prepare_cli_remote_instance(args)
    if not deploy_dae_config(
        args.project_id,
        remote_instance,
        remote_config,
        dry_run=args.dry_run,
    ):
        raise RuntimeError("dae 配置部署失败。")

def handle_delete_resources_cli(args: Namespace) -> None:
    if not args.yes:
        raise ValueError("非交互删除资源时必须显式传入 --yes。")
    instance_info = get_cli_instance(args)
    if not delete_free_resources(args.project_id, instance_info, confirmed=True):
        raise RuntimeError("删除资源失败。")

def handle_doctor_cli(args: Namespace) -> None:
    handle_doctor(getattr(args, "project_id", None))

def handle_show_reroll_state_cli(args: Namespace) -> None:
    if not show_reroll_state(
        state_file=args.state_file,
        project_id=getattr(args, "project_id", None),
        instance_info=(
            InstanceInfo(
                name=args.instance,
                zone=args.zone,
                status="UNKNOWN",
                cpu_platform="Unknown CPU Platform",
                network="global/networks/default",
                internal_ip="-",
                external_ip="-",
            )
            if getattr(args, "instance", None) and getattr(args, "zone", None)
            else None
        ),
    ):
        raise RuntimeError("未找到可显示的刷 CPU 状态。")

def handle_update_cdnip_cli(args: Namespace) -> None:
    merged_ranges = update_cdnip_file(output_path=args.output)
    print_success(f"已更新 CDN IP 文件: {args.output}，共 {len(merged_ranges)} 个网段。")

def handle_status_cli(args: Namespace) -> None:
    remote_instance, remote_config = prepare_cli_remote_instance(args)
    if not show_remote_status(
        args.project_id,
        remote_instance,
        remote_config,
        dry_run=args.dry_run,
    ):
        raise RuntimeError("读取远程实例状态失败。")

def run_setup_remote_step(args: Namespace,  instance_info: InstanceInfo,  remote_config: RemoteConfig,  step_name: str,  action: Any) -> InstanceInfo:
    print_info(f"setup: {step_name}")
    if getattr(args, "dry_run", False):
        remote_instance = instance_info
    else:
        remote_instance = prepare_instance_for_remote(args.project_id, instance_info, remote_config)
        if not remote_instance:
            raise RuntimeError(f"setup 步骤失败: {step_name}，远程实例未就绪。")
    if not action(remote_instance):
        raise RuntimeError(f"setup 步骤失败: {step_name}")
    return remote_instance

def handle_setup_cli(args: Namespace) -> None:
    zone = resolve_zone_for_create(args.zone, args.region)
    os_config = resolve_os_config(args.os)
    instance_name = args.instance_name

    if args.dry_run:
        print_info(f"[dry-run] setup 将创建实例 {instance_name} ({zone})，系统 {os_config['name']}")
        instance_info = build_setup_dry_run_instance(instance_name, zone)
    else:
        instance_info = create_instance(
            args.project_id,
            zone,
            os_config,
            instance_name=instance_name,
        )
        if not instance_info:
            raise RuntimeError("setup 步骤失败: 创建实例")

    if args.skip_reroll:
        print_info("setup: 已按参数跳过刷 AMD/EPYC。")
    elif args.dry_run:
        print_info("[dry-run] setup 将执行刷 AMD/EPYC，命中后继续后续部署。")
    else:
        print_info("setup: 开始刷 AMD/EPYC。")
        instance_info = reroll_cpu_loop(args.project_id, instance_info, resume=True)

    if args.dry_run:
        print_info("[dry-run] setup 将配置默认防火墙规则: 允许所有入站，跳过 CDN 出站拒绝。")
    else:
        network = instance_info.network or "global/networks/default"
        configure_firewall_non_interactive(
            args.project_id,
            network,
            allow_all_ingress=True,
            deny_cdn_egress=False,
        )

    remote_config = build_remote_config_from_args(args)
    for step_name, action in [
        ("Debian/Ubuntu 换源", lambda inst: run_remote_script(args.project_id, inst, "apt", remote_config, dry_run=args.dry_run)),
        ("安装 dae", lambda inst: run_remote_script(args.project_id, inst, "dae", remote_config, dry_run=args.dry_run)),
        ("上传 config.dae 并启用 dae", lambda inst: deploy_dae_config(args.project_id, inst, remote_config, dry_run=args.dry_run)),
        (
            f"安装流量监控脚本 {args.traffic_script}",
            lambda inst: run_remote_script(args.project_id, inst, args.traffic_script, remote_config, dry_run=args.dry_run),
        ),
    ]:
        instance_info = run_setup_remote_step(args, instance_info, remote_config, step_name, action)

    print_success("setup 全流程执行完成。")

ACTION_SPECS = [
    ActionSpec("create", "新建免费实例", "create", "新建免费实例", menu_create_action, handle_create_cli),
    ActionSpec("select-instance", "选择服务器", None, "选择当前服务器", menu_select_instance_action, None),
    ActionSpec("reroll-amd", "刷 AMD CPU", "reroll-amd", "循环重刷 CPU，直到命中 AMD/EPYC", menu_reroll_action, handle_reroll_amd_cli),
    ActionSpec("reroll-ip", "刷外网 IP", "reroll-ip", "循环重启实例，直到外网 IP 变化", menu_reroll_ip_action, handle_reroll_ip_cli),
    ActionSpec("reroll-ip-amd", "刷外网 IP + AMD CPU", "reroll-ip-amd", "循环重启实例，直到外网 IP 变化且命中 AMD/EPYC", menu_reroll_ip_amd_action, handle_reroll_ip_amd_cli),
    ActionSpec("show-reroll-state", "查看刷 CPU 状态", "show-reroll-state", "显示当前刷 CPU 状态文件摘要", menu_show_reroll_state_action, handle_show_reroll_state_cli),
    ActionSpec("firewall", "配置防火墙规则", "firewall", "配置入站/出站规则", menu_firewall_action, handle_firewall_cli),
    ActionSpec("apt", "Debian换源", "run-script", "上传并执行 apt.sh", menu_remote_apt_action, handle_run_script_cli),
    ActionSpec("dae", "安装 dae", None, "上传并执行 dae.sh", menu_remote_dae_action, None),
    ActionSpec("dae-config", "上传 config.dae 并启用 dae", "deploy-dae-config", "上传 dae 配置", menu_deploy_dae_config_action, handle_deploy_dae_config_cli),
    ActionSpec("traffic-monitor", "安装流量监控脚本（仅适配 Debian）", None, "安装流量监控脚本", menu_traffic_monitor_action, None),
    ActionSpec("delete-resources", "删除当前免费资源", "delete-resources", "删除实例、磁盘和规则", menu_delete_resources_action, handle_delete_resources_cli),
    ActionSpec("doctor", "环境预检", "doctor", "检查本地与云端运行环境", menu_doctor_action, handle_doctor_cli),
]

ACTION_SPEC_MAP = {item.key: item for item in ACTION_SPECS}

def build_arg_parser() -> Any:
    parser = argparse.ArgumentParser(description="GCP 免费服务器多功能管理工具")
    parser.add_argument("--log-file", help="日志文件路径，默认写入项目目录下的 .gcp_free_logs/gcp_free.log")
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
    remote_parent.add_argument("--dry-run", action="store_true", help="仅打印远程命令，不真正执行")

    create_parser = subparsers.add_parser(
        "create",
        parents=[project_parent],
        help=ACTION_SPEC_MAP["create"].description,
    )
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
    create_parser.set_defaults(handler=ACTION_SPEC_MAP["create"].cli_handler)

    list_parser = subparsers.add_parser(
        "list-instances",
        parents=[project_parent],
        help="列出项目中的实例",
    )
    list_parser.set_defaults(handler=handle_list_instances_cli)

    reroll_parser = subparsers.add_parser(
        "reroll-amd",
        parents=[instance_parent],
        help=ACTION_SPEC_MAP["reroll-amd"].description,
    )
    reroll_parser.add_argument(
        "--state-file",
        help="刷 CPU 状态文件路径，默认写入项目目录下的 .gcp_free_state/reroll_state.json",
    )
    reroll_parser.add_argument("--resume", action="store_true", help="从已有状态文件恢复累计统计并继续执行")
    reroll_parser.set_defaults(handler=ACTION_SPEC_MAP["reroll-amd"].cli_handler)

    reroll_ip_parser = subparsers.add_parser(
        "reroll-ip",
        parents=[instance_parent],
        help=ACTION_SPEC_MAP["reroll-ip"].description,
    )
    reroll_ip_parser.add_argument(
        "--state-file",
        help="刷 IP 状态文件路径，默认写入项目目录下的 .gcp_free_state/reroll_ip_state.json",
    )
    reroll_ip_parser.add_argument("--resume", action="store_true", help="从已有状态文件恢复累计统计并继续执行")
    reroll_ip_parser.set_defaults(handler=ACTION_SPEC_MAP["reroll-ip"].cli_handler)

    reroll_ip_amd_parser = subparsers.add_parser(
        "reroll-ip-amd",
        parents=[instance_parent],
        help=ACTION_SPEC_MAP["reroll-ip-amd"].description,
    )
    reroll_ip_amd_parser.add_argument(
        "--state-file",
        help="刷 IP + AMD 状态文件路径，默认写入项目目录下的 .gcp_free_state/reroll_ip_amd_state.json",
    )
    reroll_ip_amd_parser.add_argument("--resume", action="store_true", help="从已有状态文件恢复累计统计并继续执行")
    reroll_ip_amd_parser.set_defaults(handler=ACTION_SPEC_MAP["reroll-ip-amd"].cli_handler)

    show_reroll_state_parser = subparsers.add_parser(
        "show-reroll-state",
        help=ACTION_SPEC_MAP["show-reroll-state"].description,
    )
    show_reroll_state_parser.add_argument(
        "--state-file",
        help="刷 CPU 状态文件路径，默认读取项目目录下的 .gcp_free_state/reroll_state.json",
    )
    show_reroll_state_parser.add_argument("--project-id", help="可选，校验状态文件中的项目")
    show_reroll_state_parser.add_argument("--instance", help="可选，校验状态文件中的实例名称")
    show_reroll_state_parser.add_argument("--zone", help="可选，校验状态文件中的实例可用区")
    show_reroll_state_parser.set_defaults(handler=ACTION_SPEC_MAP["show-reroll-state"].cli_handler)

    firewall_parser = subparsers.add_parser(
        "firewall",
        parents=[instance_parent],
        help=ACTION_SPEC_MAP["firewall"].description,
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
    firewall_parser.set_defaults(handler=ACTION_SPEC_MAP["firewall"].cli_handler)

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
    run_script_parser.set_defaults(handler=ACTION_SPEC_MAP["apt"].cli_handler)

    dae_config_parser = subparsers.add_parser(
        "deploy-dae-config",
        parents=[instance_parent, remote_parent],
        help=ACTION_SPEC_MAP["dae-config"].description,
    )
    dae_config_parser.set_defaults(handler=ACTION_SPEC_MAP["dae-config"].cli_handler)

    status_parser = subparsers.add_parser(
        "status",
        parents=[instance_parent, remote_parent],
        help="读取远程实例状态仪表盘",
    )
    status_parser.set_defaults(handler=handle_status_cli)

    setup_parser = subparsers.add_parser(
        "setup",
        parents=[project_parent, remote_parent],
        help="创建实例并串联执行刷 CPU、远程部署和流量监控",
    )
    setup_parser.add_argument("--zone", help="实例部署可用区，例如 us-west1-b")
    setup_parser.add_argument(
        "--region",
        choices=[item["region"] for item in REGION_OPTIONS],
        default="us-west1",
        help="实例部署区域；未提供 --zone 时会使用该区域的默认可用区",
    )
    setup_parser.add_argument(
        "--os",
        default="debian-12",
        choices=["debian", "debian-12", "ubuntu", "ubuntu-2204-lts"],
        help="实例操作系统，默认 debian-12",
    )
    setup_parser.add_argument("--instance-name", default="free-tier-vm", help="实例名称")
    setup_parser.add_argument("--skip-reroll", action="store_true", help="跳过刷 AMD/EPYC，直接执行后续部署")
    setup_parser.add_argument(
        "--traffic-script",
        choices=["net_iptables", "net_shutdown"],
        default="net_iptables",
        help="setup 最后安装的流量监控脚本，默认 net_iptables",
    )
    setup_parser.set_defaults(handler=handle_setup_cli)

    delete_parser = subparsers.add_parser(
        "delete-resources",
        parents=[instance_parent],
        help=ACTION_SPEC_MAP["delete-resources"].description,
    )
    delete_parser.add_argument("--yes", action="store_true", help="确认执行删除")
    delete_parser.set_defaults(handler=ACTION_SPEC_MAP["delete-resources"].cli_handler)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help=ACTION_SPEC_MAP["doctor"].description,
    )
    doctor_parser.add_argument("--project-id", help="可选，检查默认项目时显示上下文")
    doctor_parser.set_defaults(handler=ACTION_SPEC_MAP["doctor"].cli_handler)

    update_cdnip_parser = subparsers.add_parser(
        "update-cdnip",
        help="更新 cdnip.txt 中的 GCP 区域 IP 段",
    )
    update_cdnip_parser.add_argument("--output", default="cdnip.txt", help="输出文件路径，默认 cdnip.txt")
    update_cdnip_parser.set_defaults(handler=handle_update_cdnip_cli)

    return parser

def parse_args(argv: Any=None) -> Any:
    return build_arg_parser().parse_args(argv)

def run_cli(args: Any) -> Any:
    handler = getattr(args, "handler", None)
    if not handler:
        return False
    configure_runtime_logging(getattr(args, "log_file", None))
    no_library_handlers = {handle_doctor_cli, handle_show_reroll_state_cli, handle_update_cdnip_cli}
    if handler not in no_library_handlers and not getattr(args, "dry_run", False):
        ensure_libraries_or_exit()
    handler(args)
    return True
