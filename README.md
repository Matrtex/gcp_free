# GCP Free 工具集

这是一个用于管理 GCP 免费实例的脚本集合，提供创建实例、刷 AMD CPU、配置防火墙、换源、安装 dae，以及远程安装流量监控脚本等功能。

创建免费实例需要绑定结算账号，也就是说目前应该处于试用赠金或者付费账号状态。

## 功能概览

- 创建/选择 GCP 免费实例
- 刷 AMD CPU
- 配置防火墙规则
- 换源、安装 dae、上传 `config.dae`
- 远程安装流量监控脚本（iptables 监控 / 超额自动关机）
## 快速开始（推荐）

打开 https://console.cloud.google.com/
在右上角点击 Cloud Shell 
在 Cloud Shell 服务器运行
```bash
# 初次运行
git clone https://github.com/Matrtex/gcp_free && cd gcp_free && bash start.sh
# 再次运行
cd ~/gcp_free && bash start.sh
```

## 环境要求

- 已安装 Google Cloud SDK（`gcloud`）
- 已登录并具备对应项目权限（建议先 `gcloud auth login`）
- Python 3

## 本地运行

### 环境要求

- 已安装 Google Cloud SDK（`gcloud`）
- 已登录并具备对应项目权限（建议先 `gcloud auth application-default login`）
- Python 3
### Windows PowerShell 运行脚本

先确保 `gcloud` 可用，并完成这两步认证：

```powershell
gcloud auth login
gcloud auth application-default login
```

然后在仓库目录执行：

```powershell
.\start.ps1
```

如果你想直接使用非交互 CLI，也可以这样执行：

```powershell
.\start.ps1 list-instances --project-id <你的项目ID>
.\start.ps1 run-script --project-id <你的项目ID> --instance <实例名> --zone <可用区> apt
.\start.ps1 doctor --project-id <你的项目ID>
```

### Linux / WSL / Git Bash 运行脚本

使用 `start.sh` 自动初始化环境：

```bash
bash start.sh
```

首次运行会：

1. 启用所需 GCP API
2. 创建并进入 venv
3. 按 `requirements.txt` 安装依赖
4. 执行 `gcp.py`

再次运行会比较 `requirements.txt` 的哈希，只有依赖变更时才重新安装。

## 手动运行

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install google-cloud-compute google-cloud-resource-manager
gcloud auth application-default login
.\.venv\Scripts\python.exe gcp.py
```

Linux / macOS / WSL：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install google-cloud-compute google-cloud-resource-manager
python gcp.py
```

## 常用命令

环境预检：

```powershell
.\start.ps1 doctor --project-id <你的项目ID>
```

非交互远程 dry-run：

```powershell
.\start.ps1 run-script --project-id <你的项目ID> --instance <实例名> --zone <可用区> --dry-run apt
```

日志文件默认写入：

```text
.gcp_free_logs/gcp_free.log
```

刷 CPU 状态文件默认写入：

```text
.gcp_free_state/reroll_state.json
```

## 脚本说明

- `gcp.py`: 主控制脚本
- `gcp_config.py`: 配置常量
- `gcp_clients.py`: GCP client 工厂
- `gcp_models.py`: 数据模型
- `gcp_logging.py`: 日志写入
- `gcp_state.py`: JSON 状态持久化
- `gcp_doctor.py`: 环境预检
- `start.ps1`: Windows PowerShell 启动脚本
- `config.dae`: dae 配置模板
- `scripts/apt.sh`: 换源脚本
- `scripts/dae.sh`: 安装 dae
- `scripts/net_iptables.sh`: 流量监控（iptables）
- `scripts/net_shutdown.sh`: 超额自动关机

## 常见问题

- 如果 `start.sh` 报错提示未找到 venv，可删除 `.gcp_free_initialized` 后重新初始化。
