from __future__ import annotations

import shlex

from gcp_common import (
    Any,
    InstanceInfo,
    LOCAL_SCRIPT_FILES,
    REMOTE_COMMAND_TIMEOUT,
    REMOTE_CONFIG_APPLY_TIMEOUT,
    REMOTE_PROBE_TIMEOUT,
    REMOTE_READY_POLL_INTERVAL,
    REMOTE_READY_TIMEOUT,
    REMOTE_UPLOAD_TIMEOUT,
    RemoteConfig,
    TRAFFIC_LIMIT_GB,
    find_gcloud_command,
    getpass,
    math,
    os,
    resolve_asset_path,
    shutil,
    subprocess,
    sys,
    tempfile,
    time,
)
from gcp_instance import (
    get_instance_cache_key,
    refresh_instance_info,
)
from gcp_utils import (
    apply_jitter,
    build_ssh_option_values,
    extend_gcloud_passthrough_flags,
    extend_ssh_options,
    format_command_for_log,
    format_duration,
    make_remote_temp_path,
    print_info,
    print_success,
    print_warning,
    sleep_and_detect_pause,
    summarize_exception,
    summarize_text_block,
)

__all__ = [
    'prepare_instance_for_remote',
    'pick_remote_method',
    'get_remote_config_for_instance',
    'get_local_script_path',
    'format_traffic_limit_gb',
    'render_local_script_content',
    'prepare_local_script_for_upload',
    'cleanup_temp_upload_file',
    'prepare_temp_script_content',
    'build_remote_script_exec_command',
    'run_remote_generated_script',
    'run_subprocess_command',
    'run_subprocess_capture_command',
    'build_remote_exec_command',
    'probe_remote_command',
    'wait_for_remote_ready',
    'parse_os_release',
    'detect_remote_os_info',
    'validate_remote_script_os',
    'validate_dae_config_os',
    'build_remote_upload_command',
    'run_remote_script',
    'select_traffic_monitor_script',
    'deploy_dae_config',
    'validate_root_password',
    'prompt_root_password',
    'get_password_from_env_or_prompt',
    'build_root_account_script',
    'configure_root_login',
    'change_root_password',
    'build_3xui_install_command',
    'install_3xui_panel',
    'build_remote_status_command',
    'show_remote_status',
]

DEFAULT_ROOT_PASSWORD_ENV = "GCP_FREE_ROOT_PASSWORD"

def prepare_instance_for_remote(project_id: Any,  instance_info: Any,  remote_config: Any) -> Any:
    refreshed = refresh_instance_info(project_id, instance_info, announce=True)
    if refreshed.status != "RUNNING":
        print_warning(f"实例当前状态为 {refreshed.status}，请先启动实例后再执行远程操作。")
        return None
    if not wait_for_remote_ready(project_id, refreshed, remote_config):
        return None
    return refreshed

def pick_remote_method() -> Any:
    has_gcloud = find_gcloud_command() is not None
    has_ssh = shutil.which("ssh") is not None

    if not has_gcloud and not has_ssh:
        print_warning("本机未发现 gcloud 或 ssh，无法执行远程脚本。")
        return None

    if has_gcloud:
        print("[0] 返回")
        choice = input("是否使用 gcloud compute ssh 远程执行? (Y/n/0): ").strip().lower()
        if choice == "0":
            return None
        if choice in ("", "y", "yes"):
            return RemoteConfig(method="gcloud")

    if not has_ssh:
        print_warning("未找到 ssh 命令，无法继续。")
        return None

    print("[0] 返回")
    default_user = getpass.getuser()
    ssh_user_input = input(f"请输入 SSH 用户名 (默认 {default_user}, 0 返回): ").strip()
    if ssh_user_input == "0":
        return None
    ssh_user = ssh_user_input or default_user
    
    ssh_port_input = input("请输入 SSH 端口 (默认 22, 0 返回): ").strip()
    if ssh_port_input == "0":
        return None
    ssh_port = ssh_port_input or "22"
    
    ssh_key_input = input("请输入 SSH 私钥路径 (留空表示使用默认密钥, 0 返回): ").strip()
    if ssh_key_input == "0":
        return None
    ssh_key = ssh_key_input
    return RemoteConfig(method="ssh", user=ssh_user, port=ssh_port, key=ssh_key)

def get_remote_config_for_instance(project_id: Any,  instance_info: Any,  remote_config_cache: Any) -> Any:
    cache_key = get_instance_cache_key(project_id, instance_info)
    remote_config = remote_config_cache.get(cache_key)
    if remote_config:
        return remote_config

    remote_config = pick_remote_method()
    if remote_config:
        remote_config_cache[cache_key] = remote_config
    return remote_config

def get_local_script_path(script_key: Any) -> Any:
    script_name = LOCAL_SCRIPT_FILES.get(script_key)
    if not script_name:
        print_warning("未知的脚本类型，无法执行。")
        return None

    local_path = str(resolve_asset_path("scripts", script_name))
    if not os.path.isfile(local_path):
        print_warning(f"找不到本地脚本文件: {local_path}")
        return None
    return local_path

def format_traffic_limit_gb(traffic_limit_gb: Any) -> str:
    try:
        limit = float(traffic_limit_gb)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"流量监控限额必须是数字: {traffic_limit_gb}") from exc
    if limit <= 0 or not math.isfinite(limit):
        raise ValueError(f"流量监控限额必须是大于 0 的有限数字: {traffic_limit_gb}")
    if limit.is_integer():
        return str(int(limit))
    return f"{limit:.6f}".rstrip("0").rstrip(".")

def render_local_script_content(script_key: Any,  traffic_limit_gb: Any=TRAFFIC_LIMIT_GB) -> Any:
    local_script = get_local_script_path(script_key)
    if not local_script:
        return None

    with open(local_script, "r", encoding="utf-8") as fh:
        script_content = fh.read()

    if script_key in {"net_iptables", "net_shutdown"}:
        traffic_limit_text = format_traffic_limit_gb(traffic_limit_gb)
        script_content = script_content.replace("LIMIT=180", f"LIMIT={traffic_limit_text}")
        script_content = script_content.replace("流量 >= 180 GB", f"流量 >= {traffic_limit_text} GB")
    return script_content

def prepare_local_script_for_upload(script_key: Any,  traffic_limit_gb: Any=TRAFFIC_LIMIT_GB) -> Any:
    local_script = get_local_script_path(script_key)
    if not local_script:
        return None, None

    script_content = render_local_script_content(script_key, traffic_limit_gb=traffic_limit_gb)
    if script_content is None:
        return None, None

    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        suffix=f"_{script_key}.sh",
        delete=False,
    )
    try:
        temp_file.write(script_content)
        temp_path = temp_file.name
    finally:
        temp_file.close()
    return temp_path, local_script

def prepare_temp_script_content(script_content: Any,  suffix: str = "_generated.sh") -> Any:
    temp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        suffix=suffix,
        delete=False,
    )
    try:
        temp_file.write(str(script_content))
        return temp_file.name
    finally:
        temp_file.close()

def cleanup_local_temp_file(upload_path: Any) -> Any:
    if not upload_path:
        return
    try:
        os.remove(upload_path)
    except OSError as exc:
        print_warning(f"清理本地临时脚本失败: {summarize_exception(exc)}")

def cleanup_temp_upload_file(upload_path: Any,  source_path: Any) -> Any:
    if not source_path or not upload_path or upload_path == source_path:
        return
    try:
        os.remove(upload_path)
    except OSError as exc:
        print_warning(f"清理本地临时上传文件失败: {summarize_exception(exc)}")

def build_remote_script_exec_command(remote_script_path: Any) -> Any:
    return (
        "set -e;"
        f"tmp='{remote_script_path}';"
        "cleanup(){ rm -f \"$tmp\"; };"
        "trap cleanup EXIT;"
        "sudo bash \"$tmp\""
    )

def cleanup_remote_temp_path(
    project_id: Any,
    instance_info: Any,
    remote_config: Any,
    remote_path: Any,
    dry_run: bool = False,
) -> Any:
    target_path = str(remote_path or "").strip()
    if not target_path:
        return False
    cleanup_cmd = build_remote_exec_command(
        project_id,
        instance_info,
        remote_config,
        f"rm -f '{target_path}'",
    )
    if not cleanup_cmd:
        print_warning(f"无法构建远端临时文件清理命令: {target_path}")
        return False
    return run_subprocess_command(
        cleanup_cmd,
        "清理远端临时文件",
        timeout=REMOTE_COMMAND_TIMEOUT,
        dry_run=dry_run,
    )

def run_remote_generated_script(
    project_id: Any,
    instance_info: Any,
    remote_config: Any,
    script_content: Any,
    action_desc: str,
    remote_prefix: str = "gcp_free_action",
    timeout: Any = REMOTE_COMMAND_TIMEOUT,
    dry_run: bool = False,
    sensitive: bool = False,
) -> Any:
    if dry_run and sensitive:
        print_info(f"[dry-run] {action_desc}")
        print_info("[dry-run] 将上传临时脚本并远程执行，敏感脚本内容不打印。")
        return True

    upload_script = prepare_temp_script_content(script_content, suffix=f"_{remote_prefix}.sh")
    remote_tmp = make_remote_temp_path(remote_prefix, ".sh")
    cleanup_needed = False
    try:
        upload_cmd = build_remote_upload_command(
            project_id,
            instance_info,
            remote_config,
            upload_script,
            remote_tmp,
        )
        if not upload_cmd:
            return False

        print_info(f"正在上传{action_desc}临时脚本...")
        if not run_subprocess_command(
            upload_cmd,
            f"上传{action_desc}临时脚本",
            timeout=REMOTE_UPLOAD_TIMEOUT,
            dry_run=dry_run,
        ):
            return False
        cleanup_needed = not dry_run

        remote_command = build_remote_script_exec_command(remote_tmp)
        exec_cmd = build_remote_exec_command(project_id, instance_info, remote_config, remote_command)
        if not exec_cmd:
            return False

        print_info(f"正在远程执行{action_desc}...")
        if not run_subprocess_command(
            exec_cmd,
            action_desc,
            timeout=timeout,
            dry_run=dry_run,
        ):
            return False
        cleanup_needed = False
        print_success(f"{action_desc}完成。")
        return True
    finally:
        cleanup_local_temp_file(upload_script)
        if cleanup_needed:
            cleanup_remote_temp_path(project_id, instance_info, remote_config, remote_tmp, dry_run=dry_run)

def run_subprocess_command(cmd: Any,  action_desc: Any,  timeout: Any=None,  dry_run: Any=False) -> Any:
    if dry_run:
        print_info(f"[dry-run] {action_desc}")
        print_info(f"[dry-run] 命令: {format_command_for_log(cmd)}")
        return True
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

def run_subprocess_capture_command(cmd: Any,  action_desc: Any,  timeout: Any=REMOTE_PROBE_TIMEOUT) -> Any:
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

def build_remote_exec_command(project_id: Any,  instance_info: Any,  remote_config: Any,  remote_command: Any) -> Any:
    instance_name = instance_info.name
    zone = instance_info.zone
    method = remote_config.method
    ssh_options = build_ssh_option_values()

    if method == "gcloud":
        gcloud_command = find_gcloud_command()
        if not gcloud_command:
            print_warning("当前环境未找到 gcloud，无法使用 gcloud 远程模式。")
            return None
        cmd = [
            gcloud_command,
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
        host = instance_info.external_ip
        if not host or host == "-":
            print_warning("该实例没有外网 IP，无法使用 SSH 直连。")
            return None
        cmd = ["ssh"]
        port = remote_config.port
        if port:
            cmd += ["-p", str(port)]
        key_path = remote_config.key
        if key_path:
            cmd += ["-i", key_path]
        cmd = extend_ssh_options(cmd, build_ssh_option_values(include_identities_only=bool(key_path)))
        cmd += [f"{remote_config.user}@{host}", remote_command]
        return cmd

    print_warning("远程执行方式未设置。")
    return None

def probe_remote_command(project_id: Any,  instance_info: Any,  remote_config: Any,  remote_command: Any,  action_desc: Any) -> Any:
    cmd = build_remote_exec_command(project_id, instance_info, remote_config, remote_command)
    if not cmd:
        return False, "", "无法构建远程执行命令"
    return run_subprocess_capture_command(cmd, action_desc)

def wait_for_remote_ready(project_id: Any,  instance_info: Any,  remote_config: Any) -> Any:
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
        sleep_and_detect_pause(sleep_time, "等待 SSH 服务就绪")

    print_warning(f"等待 SSH 就绪超时（{format_duration(REMOTE_READY_TIMEOUT)}）。")
    return False

def parse_os_release(content: Any) -> Any:
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

def detect_remote_os_info(project_id: Any,  instance_info: Any,  remote_config: Any) -> Any:
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

def validate_remote_script_os(script_key: Any,  os_info: Any) -> Any:
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

def validate_dae_config_os(os_info: Any) -> Any:
    if not os_info:
        return True

    if os_info["id"] not in {"debian", "ubuntu"}:
        print_warning(f"当前系统为 {os_info['pretty_name']}，dae 配置流程未做专门适配，请自行确认。")
    return True

def build_remote_upload_command(project_id: Any,  instance_info: Any,  remote_config: Any,  local_path: Any,  remote_path: Any) -> Any:
    instance_name = instance_info.name
    zone = instance_info.zone
    method = remote_config.method
    key_path = remote_config.key
    ssh_options = build_ssh_option_values(include_identities_only=bool(key_path))

    if method == "gcloud":
        gcloud_command = find_gcloud_command()
        if not gcloud_command:
            print_warning("当前环境未找到 gcloud，无法使用 gcloud 远程模式。")
            return None
        cmd = [
            gcloud_command,
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
        host = instance_info.external_ip
        if not host or host == "-":
            print_warning("该实例没有外网 IP，无法使用 SSH 直连。")
            return None
        cmd = ["scp"]
        port = remote_config.port
        if port:
            cmd += ["-P", str(port)]
        if key_path:
            cmd += ["-i", key_path]
        cmd = extend_ssh_options(cmd, ssh_options)
        cmd += [local_path, f"{remote_config.user}@{host}:{remote_path}"]
        return cmd

    print_warning("远程执行方式未设置。")
    return None

def run_remote_script(project_id: str,  instance_info: InstanceInfo,  script_key: str,  remote_config: RemoteConfig,  dry_run: bool = False) -> bool:
    upload_script, source_script = prepare_local_script_for_upload(script_key, TRAFFIC_LIMIT_GB)
    if not upload_script:
        return False

    try:
        os_info = None
        if not dry_run:
            os_info = detect_remote_os_info(project_id, instance_info, remote_config)
            if not validate_remote_script_os(script_key, os_info):
                return False

        remote_tmp = make_remote_temp_path("gcp_free_script", ".sh")
        upload_cmd = build_remote_upload_command(
            project_id,
            instance_info,
            remote_config,
            upload_script,
            remote_tmp,
        )
        if not upload_cmd:
            return False

        remote_command = build_remote_script_exec_command(remote_tmp)
        exec_cmd = build_remote_exec_command(project_id, instance_info, remote_config, remote_command)
        if not exec_cmd:
            return False

        display_script = source_script or upload_script
        print_info(f"正在上传本地脚本: {display_script}")
        if script_key in {"net_iptables", "net_shutdown"}:
            print_info(f"流量监控限额: {TRAFFIC_LIMIT_GB} GB")
        if not run_subprocess_command(
            upload_cmd,
            "上传远程脚本",
            timeout=REMOTE_UPLOAD_TIMEOUT,
            dry_run=dry_run,
        ):
            return False

        print_info(f"正在远程执行本地脚本: {os.path.basename(display_script)}")
        if not run_subprocess_command(
            exec_cmd,
            "远程脚本执行",
            timeout=REMOTE_COMMAND_TIMEOUT,
            dry_run=dry_run,
        ):
            return False

        print_success("远程脚本执行完成。")
        return True
    finally:
        cleanup_temp_upload_file(upload_script, source_script)

def select_traffic_monitor_script() -> Any:
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

def deploy_dae_config(project_id: str,  instance_info: InstanceInfo,  remote_config: RemoteConfig,  dry_run: bool = False) -> bool:
    local_config = str(resolve_asset_path("config.dae"))
    if not os.path.isfile(local_config):
        print_warning(f"找不到本地配置文件: {local_config}")
        return False

    if not dry_run:
        os_info = detect_remote_os_info(project_id, instance_info, remote_config)
        if not validate_dae_config_os(os_info):
            return False

    remote_tmp = make_remote_temp_path("gcp_free_config", ".dae")
    cleanup_needed = False
    try:
        upload_cmd = build_remote_upload_command(
            project_id,
            instance_info,
            remote_config,
            local_config,
            remote_tmp,
        )
        if not upload_cmd:
            return False

        cleanup_needed = not dry_run
        print_info("正在上传 config.dae ...")
        if not run_subprocess_command(
            upload_cmd,
            "上传 config.dae",
            timeout=REMOTE_UPLOAD_TIMEOUT,
            dry_run=dry_run,
        ):
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
        if not run_subprocess_command(
            exec_cmd,
            "应用 dae 配置",
            timeout=REMOTE_CONFIG_APPLY_TIMEOUT,
            dry_run=dry_run,
        ):
            return False
        cleanup_needed = False
        print_success("配置已更新并重启 dae。")
        return True
    finally:
        if cleanup_needed:
            cleanup_remote_temp_path(project_id, instance_info, remote_config, remote_tmp, dry_run=dry_run)

def validate_root_password(password: Any) -> str:
    password_text = str(password or "")
    if not password_text:
        raise ValueError("root 密码不能为空。")
    if "\n" in password_text or "\r" in password_text:
        raise ValueError("root 密码不能包含换行符。")
    return password_text

def prompt_root_password(prompt_title: str = "请输入 root 密码") -> str:
    while True:
        password = getpass.getpass(f"{prompt_title}: ")
        confirm = getpass.getpass("请再次输入 root 密码: ")
        if password != confirm:
            print_warning("两次输入的密码不一致，请重新输入。")
            continue
        return validate_root_password(password)

def get_password_from_env_or_prompt(
    env_name: Any = DEFAULT_ROOT_PASSWORD_ENV,
    prompt_title: str = "请输入 root 密码",
) -> str:
    env_key = str(env_name or DEFAULT_ROOT_PASSWORD_ENV).strip() or DEFAULT_ROOT_PASSWORD_ENV
    env_password = os.environ.get(env_key)
    if env_password:
        return validate_root_password(env_password)
    if not sys.stdin.isatty():
        raise ValueError(f"未提供 root 密码。请设置环境变量 {env_key}，或在交互终端运行。")
    return prompt_root_password(prompt_title)

def build_root_account_script(password: Any,  enable_ssh_login: bool = True) -> str:
    password_text = validate_root_password(password)
    enable_ssh_value = "1" if enable_ssh_login else "0"
    quoted_password = shlex.quote(password_text)
    return f"""#!/usr/bin/env bash
set -euo pipefail

ROOT_PASSWORD={quoted_password}
ENABLE_SSH_LOGIN={enable_ssh_value}

printf 'root:%s\\n' "$ROOT_PASSWORD" | sudo chpasswd
sudo passwd -u root >/dev/null 2>&1 || true
sudo usermod -s /bin/bash root >/dev/null 2>&1 || true

if [ "$ENABLE_SSH_LOGIN" = "1" ]; then
  sudo mkdir -p /etc/ssh/sshd_config.d
  sudo tee /etc/ssh/sshd_config.d/99-gcp-free-root-login.conf >/dev/null <<'EOF'
PermitRootLogin yes
PasswordAuthentication yes
KbdInteractiveAuthentication yes
EOF

  ensure_sshd_option() {{
    local key="$1"
    local value="$2"
    local file="/etc/ssh/sshd_config"
    if [ ! -f "$file" ]; then
      return 0
    fi
    if sudo grep -Eiq "^[#[:space:]]*$key[[:space:]]+" "$file"; then
      sudo sed -i -E "s|^[#[:space:]]*$key[[:space:]].*|$key $value|I" "$file"
    else
      printf '%s %s\\n' "$key" "$value" | sudo tee -a "$file" >/dev/null
    fi
  }}

  ensure_sshd_option PermitRootLogin yes
  ensure_sshd_option PasswordAuthentication yes
  ensure_sshd_option KbdInteractiveAuthentication yes

  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl restart ssh 2>/dev/null || sudo systemctl restart sshd 2>/dev/null
  else
    sudo service ssh restart 2>/dev/null || sudo service sshd restart 2>/dev/null
  fi
fi
"""

def configure_root_login(
    project_id: str,
    instance_info: InstanceInfo,
    remote_config: RemoteConfig,
    password: Any,
    dry_run: bool = False,
) -> bool:
    script_content = build_root_account_script(password, enable_ssh_login=True)
    return run_remote_generated_script(
        project_id,
        instance_info,
        remote_config,
        script_content,
        "启用 root 账号和 SSH 密码登录",
        remote_prefix="gcp_free_root_login",
        timeout=REMOTE_CONFIG_APPLY_TIMEOUT,
        dry_run=dry_run,
        sensitive=True,
    )

def change_root_password(
    project_id: str,
    instance_info: InstanceInfo,
    remote_config: RemoteConfig,
    password: Any,
    dry_run: bool = False,
) -> bool:
    script_content = build_root_account_script(password, enable_ssh_login=False)
    return run_remote_generated_script(
        project_id,
        instance_info,
        remote_config,
        script_content,
        "更改 root 密码",
        remote_prefix="gcp_free_root_password",
        timeout=REMOTE_CONFIG_APPLY_TIMEOUT,
        dry_run=dry_run,
        sensitive=True,
    )

def build_3xui_install_command() -> str:
    return (
        "set -e;"
        "if ! command -v curl >/dev/null 2>&1; then "
        "sudo apt-get update && sudo apt-get install -y curl; "
        "fi;"
        "sudo bash -lc 'bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh)'"
    )

def install_3xui_panel(
    project_id: str,
    instance_info: InstanceInfo,
    remote_config: RemoteConfig,
    dry_run: bool = False,
) -> bool:
    exec_cmd = build_remote_exec_command(
        project_id,
        instance_info,
        remote_config,
        build_3xui_install_command(),
    )
    if not exec_cmd:
        return False
    return run_subprocess_command(
        exec_cmd,
        "安装 3x-ui 面板",
        timeout=REMOTE_COMMAND_TIMEOUT,
        dry_run=dry_run,
    )

def build_remote_status_command() -> str:
    return (
        "set +e;"
        "echo '=== 当月出站流量（vnstat） ===';"
        "if command -v vnstat >/dev/null 2>&1; then "
        "vnstat -m 2>/dev/null || echo 'vnstat 已安装，但暂时无法读取月度流量。'; "
        "else echo 'vnstat 未安装，无法读取流量统计。'; fi;"
        "echo '';"
        "echo '=== dae 服务状态 ===';"
        "if command -v systemctl >/dev/null 2>&1; then "
        "echo -n 'active: '; systemctl is-active dae 2>/dev/null || true; "
        "systemctl --no-pager --full status dae 2>/dev/null | sed -n '1,8p' || echo '无法读取 dae 服务详情。'; "
        "else echo 'systemctl 不可用，无法读取 dae 服务状态。'; fi;"
        "echo '';"
        "echo '=== 磁盘使用率 ===';"
        "df -h / 2>/dev/null || echo '无法读取根分区磁盘使用率。';"
        "echo '';"
        "echo '=== 系统运行时长 ===';"
        "uptime -p 2>/dev/null || uptime 2>/dev/null || echo '无法读取系统运行时长。'"
    )

def show_remote_status(project_id: str,  instance_info: InstanceInfo,  remote_config: RemoteConfig,  dry_run: bool = False) -> bool:
    remote_command = build_remote_status_command()
    exec_cmd = build_remote_exec_command(project_id, instance_info, remote_config, remote_command)
    if not exec_cmd:
        return False
    return run_subprocess_command(
        exec_cmd,
        "读取远程实例状态",
        timeout=REMOTE_PROBE_TIMEOUT,
        dry_run=dry_run,
    )
