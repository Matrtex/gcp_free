#!/bin/bash

set -e

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

case "$OS_ID" in
    debian)
        OS_CODENAME="${OS_CODENAME:-bookworm}"
        SOURCE_FILE="/etc/apt/sources.list.d/debian.sources"
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
        SOURCE_FILE="/etc/apt/sources.list.d/ubuntu.sources"
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
