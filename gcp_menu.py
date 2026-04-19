from __future__ import annotations

from gcp_common import (
    Any,
    RuntimeContext,
    sys,
)
from gcp_firewall import (
    configure_firewall,
    delete_free_resources,
)
from gcp_instance import (
    create_instance,
    get_instance_cache_key,
    select_gcp_project,
    select_instance,
    select_os_image,
    select_zone,
)
from gcp_remote import (
    deploy_dae_config,
    get_remote_config_for_instance,
    prepare_instance_for_remote,
    run_remote_script,
    select_traffic_monitor_script,
)
from gcp_reroll import (
    is_reroll_state_compatible,
    load_reroll_stats_from_file,
    reroll_cpu_loop,
    show_reroll_state,
)
from gcp_utils import (
    configure_runtime_logging,
    configure_stdio,
    ensure_libraries_or_exit,
    get_default_reroll_state_file,
    handle_doctor,
    print_info,
    print_warning,
    summarize_exception,
)

__all__ = [
    'ensure_context_instance',
    'run_remote_action_for_context',
    'menu_create_action',
    'menu_select_instance_action',
    'menu_reroll_action',
    'menu_show_reroll_state_action',
    'menu_firewall_action',
    'menu_remote_apt_action',
    'menu_remote_dae_action',
    'menu_deploy_dae_config_action',
    'menu_traffic_monitor_action',
    'menu_delete_resources_action',
    'menu_doctor_action',
    'main',
]

def ensure_context_instance(context: Any) -> Any:
    if not context.current_instance:
        context.current_instance = select_instance(context.project_id)
    return context.current_instance

def run_remote_action_for_context(context: Any,  action_name: Any,  action_func: Any) -> Any:
    current_instance = ensure_context_instance(context)
    if not current_instance:
        return

    remote_config = get_remote_config_for_instance(
        context.project_id,
        current_instance,
        context.remote_config_cache,
    )
    if not remote_config:
        return

    remote_instance = prepare_instance_for_remote(context.project_id, current_instance, remote_config)
    if not remote_instance:
        return

    context.current_instance = remote_instance
    action_func(context.project_id, context.current_instance, remote_config)

def menu_create_action(context: Any) -> Any:
    zone = select_zone(context.project_id)
    os_config = select_os_image()
    created_instance = create_instance(context.project_id, zone, os_config)
    if created_instance:
        context.current_instance = created_instance

def menu_select_instance_action(context: Any) -> Any:
    context.current_instance = select_instance(context.project_id)

def menu_reroll_action(context: Any) -> Any:
    current_instance = ensure_context_instance(context)
    if current_instance:
        default_state_path = get_default_reroll_state_file()
        existing_stats = load_reroll_stats_from_file(default_state_path)
        resume = bool(
            existing_stats
            and not existing_stats.success_cpu
            and is_reroll_state_compatible(
                existing_stats,
                project_id=context.project_id,
                instance_name=current_instance.name,
                zone=current_instance.zone,
            )
        )
        if resume:
            print_info("检测到当前实例存在可恢复的刷 CPU 状态，将自动继续上次进度。")
        context.current_instance = reroll_cpu_loop(
            context.project_id,
            current_instance,
            state_file=default_state_path,
            resume=resume,
        )

def menu_show_reroll_state_action(context: Any) -> Any:
    show_reroll_state(
        project_id=context.project_id,
        instance_info=context.current_instance,
    )

def menu_firewall_action(context: Any) -> Any:
    current_instance = ensure_context_instance(context)
    if current_instance:
        network = current_instance.network or "global/networks/default"
        configure_firewall(context.project_id, network)

def menu_remote_apt_action(context: Any) -> Any:
    run_remote_action_for_context(
        context,
        "apt",
        lambda project_id, instance, remote_config: run_remote_script(
            project_id,
            instance,
            "apt",
            remote_config,
        ),
    )

def menu_remote_dae_action(context: Any) -> Any:
    run_remote_action_for_context(
        context,
        "dae",
        lambda project_id, instance, remote_config: run_remote_script(
            project_id,
            instance,
            "dae",
            remote_config,
        ),
    )

def menu_deploy_dae_config_action(context: Any) -> Any:
    run_remote_action_for_context(
        context,
        "deploy-dae-config",
        lambda project_id, instance, remote_config: deploy_dae_config(
            project_id,
            instance,
            remote_config,
        ),
    )

def menu_traffic_monitor_action(context: Any) -> Any:
    current_instance = ensure_context_instance(context)
    if not current_instance:
        return

    remote_config = get_remote_config_for_instance(
        context.project_id,
        current_instance,
        context.remote_config_cache,
    )
    if not remote_config:
        return

    remote_instance = prepare_instance_for_remote(context.project_id, current_instance, remote_config)
    if not remote_instance:
        return

    context.current_instance = remote_instance
    script_key = select_traffic_monitor_script()
    if script_key:
        run_remote_script(context.project_id, context.current_instance, script_key, remote_config)

def menu_delete_resources_action(context: Any) -> Any:
    current_instance = ensure_context_instance(context)
    if current_instance:
        cache_key = get_instance_cache_key(context.project_id, current_instance)
        if delete_free_resources(context.project_id, current_instance):
            context.remote_config_cache.pop(cache_key, None)
            context.current_instance = None

def menu_doctor_action(context: Any) -> Any:
    handle_doctor(context.project_id)

def main() -> Any:
    from gcp_cli import ACTION_SPECS

    configure_stdio()
    configure_runtime_logging()
    ensure_libraries_or_exit()
    print("GCP 免费服务器多功能管理工具")
    sys.stdout.flush()
    project_id = select_gcp_project()
    context = RuntimeContext(project_id=project_id)

    while True:
        print("\n================================================")
        print(f"当前项目: {context.project_id}")
        if context.current_instance:
            print(f"当前服务器: {context.current_instance.name} ({context.current_instance.zone})")
        else:
            print("当前服务器: 未选择")
        print("------------------------------------------------")
        for index, action in enumerate(ACTION_SPECS, start=1):
            print(f"[{index}] {action.menu_label}")
        print("[0] 退出")
        choice = input("请输入数字选择: ").strip()

        if choice == "0":
            print("已退出。")
            break
        if not choice.isdigit():
            print("输入无效，请重试。")
            continue

        action_index = int(choice) - 1
        if not (0 <= action_index < len(ACTION_SPECS)):
            print("输入无效，请重试。")
            continue

        action = ACTION_SPECS[action_index]
        handler = action.menu_handler
        if handler is None:
            print_warning(f"{action.menu_label} 暂未绑定菜单处理函数。")
            continue
        try:
            handler(context)
        except Exception as e:
            print_warning(f"{action.menu_label} 执行失败: {summarize_exception(e)}")
