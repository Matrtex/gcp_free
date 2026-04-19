import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence, Tuple

from gcp_config import LOCAL_SCRIPT_FILES, LOG_DIR_NAME, STATE_DIR_NAME
from gcp_models import DoctorCheck

GCLOUD_COMMAND_ENV = "GCP_FREE_GCLOUD_COMMAND"
REQUIRED_GCP_SERVICES = (
    "compute.googleapis.com",
    "cloudresourcemanager.googleapis.com",
)
OPTIONAL_LOCAL_FILES = (
    "config.dae",
    "cdnip.txt",
)


def find_gcloud_command() -> Optional[str]:
    command = os.getenv(GCLOUD_COMMAND_ENV, "").strip()
    if command:
        return command
    return shutil.which("gcloud")


def find_python_command() -> Optional[str]:
    command = (sys.executable or "").strip()
    if command and Path(command).exists():
        return command
    return shutil.which("python") or shutil.which("python3")


def _run_command(cmd: Sequence[str], timeout: int = 30) -> Tuple[bool, str, str]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        return result.returncode == 0, (result.stdout or "").strip(), (result.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return False, "", f"命令执行超时（{timeout} 秒）"
    except OSError as exc:
        return False, "", str(exc)


def get_active_gcloud_account(gcloud_path: str) -> Tuple[str, str]:
    ok, stdout, stderr = _run_command(
        [
            gcloud_path,
            "auth",
            "list",
            "--filter=status:ACTIVE",
            "--format=value(account)",
        ]
    )
    account = (stdout or "").strip()
    if ok and account:
        return account, ""

    ok, stdout, fallback_stderr = _run_command([gcloud_path, "config", "get-value", "account"])
    account = (stdout or "").strip()
    if ok and account and account != "(unset)":
        return account, ""

    return "", stderr or fallback_stderr or "无法确认 gcloud 登录状态"


def get_current_gcloud_project(gcloud_path: str) -> Tuple[str, str]:
    ok, stdout, stderr = _run_command([gcloud_path, "config", "get-value", "project"])
    project_id = (stdout or "").strip()
    if ok and project_id and project_id != "(unset)":
        return project_id, ""
    return "", stderr or "未设置默认项目"


def is_directory_writable(path: Path | str) -> Tuple[bool, str]:
    target_dir = Path(path)
    target_dir.mkdir(parents=True, exist_ok=True)
    temp_file = target_dir / ".doctor_write_test"
    try:
        temp_file.write_text("ok", encoding="utf-8")
        temp_file.unlink(missing_ok=True)
        return True, f"目录可写: {target_dir}"
    except OSError as exc:
        return False, f"目录不可写: {target_dir} ({exc})"


def collect_workspace_checks(workspace_dir: Path) -> list[DoctorCheck]:
    checks = []
    scripts_dir = workspace_dir / "scripts"
    missing_scripts = [
        script_name
        for script_name in LOCAL_SCRIPT_FILES.values()
        if not (scripts_dir / script_name).is_file()
    ]
    if missing_scripts:
        checks.append(DoctorCheck("scripts", "FAIL", f"缺少脚本文件: {', '.join(missing_scripts)}"))
    else:
        checks.append(DoctorCheck("scripts", "PASS", f"脚本目录完整: {scripts_dir}"))

    for file_name in OPTIONAL_LOCAL_FILES:
        target = workspace_dir / file_name
        if target.exists():
            checks.append(DoctorCheck(file_name, "PASS", f"文件存在: {target}"))
        else:
            checks.append(DoctorCheck(file_name, "WARN", f"文件不存在，相关功能执行前需自行准备: {target}"))

    for dir_name, check_name in ((LOG_DIR_NAME, "log-dir"), (STATE_DIR_NAME, "state-dir")):
        ok, message = is_directory_writable(workspace_dir / dir_name)
        checks.append(DoctorCheck(check_name, "PASS" if ok else "FAIL", message))

    return checks


def get_enabled_gcp_services(gcloud_path: str, project_id: str) -> Tuple[set[str], list[str]]:
    enabled_services = set()
    errors = []
    for service_name in REQUIRED_GCP_SERVICES:
        ok, stdout, stderr = _run_command(
            [
                gcloud_path,
                "services",
                "list",
                "--enabled",
                "--project",
                project_id,
                "--filter",
                f"config.name={service_name}",
                "--format=value(config.name)",
            ],
            timeout=60,
        )
        if ok:
            if stdout.strip():
                enabled_services.add(service_name)
        else:
            errors.append(stderr or f"检查服务 {service_name} 失败")
    return enabled_services, errors


def run_doctor(requirements_file: str | Path, project_id: Optional[str] = None) -> list[DoctorCheck]:
    requirements_path = Path(requirements_file)
    workspace_dir = requirements_path.parent
    checks = []

    gcloud_path = find_gcloud_command()
    if gcloud_path:
        checks.append(DoctorCheck("gcloud", "PASS", f"已找到 gcloud: {gcloud_path}"))
    else:
        checks.append(DoctorCheck("gcloud", "FAIL", "未找到 gcloud 命令"))

    python_path = find_python_command()
    if python_path:
        checks.append(DoctorCheck("python", "PASS", f"已找到 Python: {python_path}"))
    else:
        checks.append(DoctorCheck("python", "FAIL", "未找到 Python 命令"))

    current_project = ""
    if gcloud_path:
        account, auth_error = get_active_gcloud_account(gcloud_path)
        if account:
            checks.append(DoctorCheck("gcloud-auth", "PASS", f"当前账号: {account}"))
        else:
            checks.append(DoctorCheck("gcloud-auth", "WARN", auth_error or "无法确认 gcloud 登录状态"))

        ok, stdout, stderr = _run_command([gcloud_path, "auth", "application-default", "print-access-token"])
        if ok and stdout:
            checks.append(DoctorCheck("adc", "PASS", "Application Default Credentials 可用"))
        else:
            checks.append(DoctorCheck("adc", "WARN", stderr or "ADC 未配置"))

        current_project, project_error = get_current_gcloud_project(gcloud_path)
        if current_project:
            if project_id and project_id != current_project:
                checks.append(
                    DoctorCheck(
                        "project",
                        "WARN",
                        f"当前默认项目为 {current_project}，与目标项目 {project_id} 不一致",
                    )
                )
            else:
                checks.append(DoctorCheck("project", "PASS", f"当前默认项目: {current_project}"))
        else:
            checks.append(DoctorCheck("project", "WARN", project_error or "未设置默认项目"))

        effective_project = project_id or current_project
        if effective_project:
            enabled_services, service_errors = get_enabled_gcp_services(gcloud_path, effective_project)
            for service_name in REQUIRED_GCP_SERVICES:
                if service_name in enabled_services:
                    checks.append(DoctorCheck(service_name, "PASS", f"项目 {effective_project} 已启用 {service_name}"))
                elif service_errors:
                    checks.append(
                        DoctorCheck(
                            service_name,
                            "WARN",
                            f"无法确认项目 {effective_project} 的服务状态: {' | '.join(service_errors)}",
                        )
                    )
                else:
                    checks.append(
                        DoctorCheck(
                            service_name,
                            "FAIL",
                            f"项目 {effective_project} 未启用 {service_name}",
                        )
                    )

    for command_name in ("ssh", "scp"):
        command_path = shutil.which(command_name)
        if command_path:
            checks.append(DoctorCheck(command_name, "PASS", f"已找到 {command_name}: {command_path}"))
        else:
            checks.append(DoctorCheck(command_name, "WARN", f"未找到 {command_name}"))

    if requirements_path.exists():
        checks.append(DoctorCheck("requirements", "PASS", f"依赖文件存在: {requirements_path}"))
    else:
        checks.append(DoctorCheck("requirements", "WARN", f"依赖文件不存在: {requirements_path}"))

    checks.extend(collect_workspace_checks(workspace_dir))

    return checks
