import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


ROOT_DIR = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT_DIR / "build" / "pyinstaller"
DIST_DIR = ROOT_DIR / "dist"
PYINSTALLER_DIST_DIR = DIST_DIR / "pyinstaller"
RELEASE_DIR = DIST_DIR / "release"
PACKAGE_FILES = [
    "README.md",
    "requirements.txt",
    "config.dae",
    "cdnip.txt",
    "geoip.dat",
    "geoip.dat.sha256sum",
    "geoip_config.json",
]
PACKAGE_DIRS = ["scripts"]


def parse_args():
    parser = argparse.ArgumentParser(description="构建 gcp_free Windows 可执行包")
    parser.add_argument("--version", help="发布版本号，例如 v1.2.3", default="")
    parser.add_argument("--name", help="生成的 exe 名称", default="gcp_free")
    parser.add_argument("--clean", action="store_true", help="构建前清理 build/dist 目录")
    package_mode = parser.add_mutually_exclusive_group()
    package_mode.add_argument("--onefile", dest="onefile", action="store_true", help="生成单文件 exe（默认）")
    package_mode.add_argument("--onedir", dest="onefile", action="store_false", help="生成目录模式 exe")
    parser.set_defaults(onefile=True)
    parser.add_argument("--sign-pfx", help="可选，PFX 证书路径")
    parser.add_argument("--sign-password", help="可选，PFX 证书密码", default="")
    parser.add_argument(
        "--timestamp-url",
        help="可选，代码签名时间戳服务地址",
        default="http://timestamp.digicert.com",
    )
    return parser.parse_args()


def add_data_argument(path, target):
    separator = ";" if os.name == "nt" else ":"
    return f"{path}{separator}{target}"


def ensure_windows():
    if os.name != "nt":
        raise RuntimeError("当前构建脚本只支持在 Windows 环境下生成 exe。")


def clean_outputs():
    for path in (BUILD_DIR, DIST_DIR):
        if path.exists():
            shutil.rmtree(path)


def find_signtool():
    candidates = []
    from_path = shutil.which("signtool.exe")
    if from_path:
        candidates.append(Path(from_path))

    kit_roots = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Kits" / "10" / "bin",
    ]
    for root in kit_roots:
        if not root.exists():
            continue
        for candidate in sorted(root.rglob("signtool.exe"), reverse=True):
            candidates.append(candidate)

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def build_pyinstaller_exe(name, onefile=True):
    PYINSTALLER_DIST_DIR.mkdir(parents=True, exist_ok=True)
    spec_dir = BUILD_DIR / "spec"
    work_dir = BUILD_DIR / "work"
    spec_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--console",
        "--name",
        name,
        "--distpath",
        str(PYINSTALLER_DIST_DIR),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
    ]
    if onefile:
        command.append("--onefile")

    for filename in PACKAGE_FILES:
        source_path = ROOT_DIR / filename
        command.extend(["--add-data", add_data_argument(source_path, ".")])

    for directory in PACKAGE_DIRS:
        source_path = ROOT_DIR / directory
        command.extend(["--add-data", add_data_argument(source_path, directory)])

    for module_name in (
        "google.cloud.compute_v1",
        "google.cloud.resourcemanager_v3",
        "google.api_core",
        "grpc",
        "proto",
    ):
        command.extend(["--collect-submodules", module_name])

    command.append(str(ROOT_DIR / "gcp.py"))
    subprocess.run(command, check=True, cwd=ROOT_DIR)

    exe_path = PYINSTALLER_DIST_DIR / f"{name}.exe"
    if not exe_path.is_file():
        raise RuntimeError(f"未找到构建产物: {exe_path}")
    return exe_path


def sign_executable(exe_path, sign_pfx, sign_password, timestamp_url):
    signtool = find_signtool()
    if not signtool:
        raise RuntimeError("已请求代码签名，但当前环境未找到 signtool.exe。")
    if not sign_password:
        raise RuntimeError("已请求代码签名，但未提供证书密码。")

    command = [
        signtool,
        "sign",
        "/fd",
        "SHA256",
        "/f",
        str(sign_pfx),
        "/p",
        sign_password,
        "/td",
        "SHA256",
    ]
    if timestamp_url:
        command.extend(["/tr", timestamp_url])
    command.append(str(exe_path))
    subprocess.run(command, check=True, cwd=ROOT_DIR)


def copy_release_assets(package_dir):
    for filename in PACKAGE_FILES:
        source_path = ROOT_DIR / filename
        shutil.copy2(source_path, package_dir / source_path.name)

    for directory in PACKAGE_DIRS:
        source_dir = ROOT_DIR / directory
        target_dir = package_dir / directory
        shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)


def write_build_info(package_dir, version):
    info_path = package_dir / "BUILD_INFO.txt"
    lines = [
        f"version={version}",
        f"built_at_utc={datetime.now(timezone.utc).isoformat()}",
    ]
    github_sha = os.environ.get("GITHUB_SHA")
    if github_sha:
        lines.append(f"git_sha={github_sha}")
    info_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_release_package(exe_path, version, name):
    package_name = f"{name}-windows-x64-{version}"
    package_dir = RELEASE_DIR / package_name
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(exe_path, package_dir / exe_path.name)
    copy_release_assets(package_dir)
    write_build_info(package_dir, version)

    zip_path = RELEASE_DIR / f"{package_name}.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in package_dir.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(package_dir))

    return package_dir, zip_path


def main():
    args = parse_args()
    ensure_windows()

    version = args.version.strip() or "manual-build"
    if args.clean:
        clean_outputs()

    exe_path = build_pyinstaller_exe(args.name, onefile=args.onefile)
    if args.sign_pfx:
        sign_executable(exe_path, args.sign_pfx, args.sign_password, args.timestamp_url)
    package_dir, zip_path = make_release_package(exe_path, version, args.name)

    print(f"EXE_PATH={exe_path}")
    print(f"PACKAGE_DIR={package_dir}")
    print(f"ZIP_PATH={zip_path}")


if __name__ == "__main__":
    main()
