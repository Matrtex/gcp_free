#!/bin/bash

set -euo pipefail

# 检查 root 权限
if [ "$EUID" -ne 0 ]; then
    echo "错误: 请以 root 用户运行此脚本"
    exit 1
fi

if [ ! -r /etc/os-release ]; then
    echo "错误: 无法识别系统版本，未找到 /etc/os-release"
    exit 1
fi

# shellcheck disable=SC1091
. /etc/os-release

OS_ID="${ID:-}"
OS_CODENAME="${VERSION_CODENAME:-}"
SOURCE_FILE=""

echo "=== 正在换源 ==="

backup_existing_apt_sources() {
    local backup_dir="/etc/apt/gcp-free-sources-backup-$(date +%Y%m%d%H%M%S)"
    local moved=0

    mkdir -p "$backup_dir/sources.list.d"

    if [ -f /etc/apt/sources.list ]; then
        mv /etc/apt/sources.list "$backup_dir/sources.list"
        : > /etc/apt/sources.list
        moved=1
    fi

    if [ -d /etc/apt/sources.list.d ]; then
        while IFS= read -r -d '' source_path; do
            mv "$source_path" "$backup_dir/sources.list.d/$(basename "$source_path")"
            moved=1
        done < <(
            find /etc/apt/sources.list.d \
                -maxdepth 1 \
                -type f \
                \( -name '*.list' -o -name '*.sources' \) \
                -print0
        )
    fi

    if [ "$moved" -eq 1 ]; then
        echo "-> 已备份并禁用旧源: $backup_dir"
    else
        rmdir "$backup_dir/sources.list.d" "$backup_dir"
    fi
}

case "$OS_ID" in
    debian)
        OS_CODENAME="${OS_CODENAME:-bookworm}"
        backup_existing_apt_sources
        SOURCE_FILE="/etc/apt/sources.list.d/debian.sources"
        mkdir -p "$(dirname "$SOURCE_FILE")"
        cat > "$SOURCE_FILE" <<EOF
Types: deb deb-src
URIs: http://mirrors.mit.edu/debian
Suites: $OS_CODENAME ${OS_CODENAME}-updates ${OS_CODENAME}-backports
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb deb-src
URIs: http://mirrors.ocf.berkeley.edu/debian-security
Suites: ${OS_CODENAME}-security
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
        ;;
    ubuntu)
        OS_CODENAME="${OS_CODENAME:-jammy}"
        backup_existing_apt_sources
        SOURCE_FILE="/etc/apt/sources.list.d/ubuntu.sources"
        mkdir -p "$(dirname "$SOURCE_FILE")"
        cat > "$SOURCE_FILE" <<EOF
Types: deb
URIs: http://archive.ubuntu.com/ubuntu
Suites: $OS_CODENAME ${OS_CODENAME}-updates ${OS_CODENAME}-backports
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: http://security.ubuntu.com/ubuntu
Suites: ${OS_CODENAME}-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
        ;;
    *)
        echo "错误: apt.sh 仅支持 Debian/Ubuntu，当前系统 ID=${OS_ID:-unknown}"
        exit 1
        ;;
esac

echo "-> 已写入源文件: $SOURCE_FILE"
echo "-> 清理缓存..."
rm -rf /var/lib/apt/lists/*

echo "-> 正在更新源..."
if apt update; then
    echo "=== 所有源均已连接成功 ==="
else
    echo "=== 仍然有错误，请检查网络或尝试其他镜像 ==="
    exit 1
fi
