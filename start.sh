#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="${SCRIPT_DIR}/.venv"
INIT_MARKER="${SCRIPT_DIR}/.gcp_free_initialized"
REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"
DEPS_HASH_FILE="${SCRIPT_DIR}/.deps.sha256"

get_requirements_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$REQUIREMENTS_FILE" | awk '{print $1}'
    return
  fi

  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$REQUIREMENTS_FILE" | awk '{print $1}'
    return
  fi

  python3 - "$REQUIREMENTS_FILE" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
print(hashlib.sha256(path.read_bytes()).hexdigest())
PY
}

if [[ ! -f "$INIT_MARKER" ]]; then
  if ! command -v gcloud >/dev/null 2>&1; then
    echo "[错误] 未找到 gcloud，请先安装 Google Cloud SDK。" >&2
    exit 1
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "[错误] 未找到 python3，请先安装 Python 3。" >&2
    exit 1
  fi

  echo "[初始化] 正在启用所需的 GCP API..."
  gcloud services enable cloudresourcemanager.googleapis.com
  gcloud services enable compute.googleapis.com

  if [[ -d "$VENV_DIR" && ! -f "$VENV_DIR/bin/activate" ]]; then
    echo "[初始化] 检测到 venv 不完整，正在重新创建..."
    python3 -m venv --clear "$VENV_DIR"
  elif [[ ! -d "$VENV_DIR" ]]; then
    echo "[初始化] 正在创建 venv..."
    python3 -m venv "$VENV_DIR"
  fi

  if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
    echo "[错误] venv 创建失败，请检查 python3-venv 是否已安装。" >&2
    exit 1
  fi

  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"

  touch "$INIT_MARKER"
else
  if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
    echo "[错误] 未找到 venv 激活脚本：$VENV_DIR/bin/activate" >&2
    echo "[错误] 请删除 $INIT_MARKER 以重新初始化。" >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
fi

export GCP_FREE_GCLOUD_COMMAND="$(command -v gcloud)"
export PYTHONUNBUFFERED=1

if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
  echo "[错误] 未找到依赖文件：$REQUIREMENTS_FILE" >&2
  exit 1
fi

CURRENT_DEPS_HASH="$(get_requirements_hash)"
INSTALLED_DEPS_HASH=""
if [[ -f "$DEPS_HASH_FILE" ]]; then
  INSTALLED_DEPS_HASH="$(cat "$DEPS_HASH_FILE")"
fi

if [[ "$CURRENT_DEPS_HASH" != "$INSTALLED_DEPS_HASH" ]]; then
  echo "[初始化] 检测到依赖变更，正在安装 requirements.txt ..."
  python -m pip install -r "$REQUIREMENTS_FILE"
  printf '%s' "$CURRENT_DEPS_HASH" > "$DEPS_HASH_FILE"
else
  echo "[初始化] Python 依赖已是最新。"
fi

exec python -u gcp.py
