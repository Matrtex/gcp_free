import os
import shutil
import subprocess
import sys
from pathlib import Path

from gcp_models import DoctorCheck

GCLOUD_COMMAND_ENV = "GCP_FREE_GCLOUD_COMMAND"


def find_gcloud_command():
    command = os.getenv(GCLOUD_COMMAND_ENV, "").strip()
    if command:
        return command
    return shutil.which("gcloud")


def find_python_command():
    command = (sys.executable or "").strip()
    if command and Path(command).exists():
        return command
    return shutil.which("python") or shutil.which("python3")


def _run_command(cmd, timeout=30):
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


def get_active_gcloud_account(gcloud_path):
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


def run_doctor(requirements_file, project_id=None):
    requirements_path = Path(requirements_file)
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

        if project_id:
            ok, stdout, stderr = _run_command([gcloud_path, "config", "get-value", "project"])
            if ok and stdout and stdout != "(unset)":
                checks.append(DoctorCheck("project", "PASS", f"当前默认项目: {stdout}"))
            else:
                checks.append(DoctorCheck("project", "WARN", stderr or "未设置默认项目"))

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

    return checks
