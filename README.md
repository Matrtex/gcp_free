# GCP Free 工具集

这是一个用于管理 GCP 免费实例的脚本集合，提供创建实例、刷 AMD CPU、配置防火墙、换源、安装 dae，以及远程安装流量监控脚本等功能。

创建免费实例需要绑定结算账号，也就是说目前应该处于试用赠金或者付费账号状态。

## 功能概览

- 创建/选择 GCP 免费实例
- 登录新账号，或切换已有 gcloud/ADC 账号，并重新选择项目
- 刷 AMD CPU
- 刷外网 IP，或同时刷外网 IP + AMD/EPYC CPU
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
.\start.ps1 login-account --account <你的Google账号邮箱>
.\start.ps1 switch-account --account <你的Google账号邮箱>
.\start.ps1 reroll-ip --project-id <你的项目ID> --instance <实例名> --zone <可用区> --resume
.\start.ps1 reroll-ip-amd --project-id <你的项目ID> --instance <实例名> --zone <可用区> --resume
.\start.ps1 run-script --project-id <你的项目ID> --instance <实例名> --zone <可用区> apt
.\start.ps1 doctor --project-id <你的项目ID>
.\start.ps1 setup --project-id <你的项目ID> --region us-west1 --skip-reroll
.\start.ps1 status --project-id <你的项目ID> --instance <实例名> --zone <可用区>
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

## 打包 EXE

仓库现在自带 Windows EXE 打包脚本：

```powershell
python -m pip install -r requirements.txt pyinstaller
python scripts/build_exe.py --clean --version v1.0.0
```

构建完成后会在 `dist/release/` 下生成：

- 一个可直接分发的 ZIP 包
- 一个包含 `gcp_free.exe`、`config.dae`、`cdnip.txt`、`scripts/` 等文件的目录

注意：EXE 只打包了 Python 程序本身，不会替代本机 `gcloud`。本地运行 EXE 仍然需要：

- 已安装 Google Cloud SDK（`gcloud`）
- 已完成 `gcloud auth login`
- 已完成 `gcloud auth application-default login`

## 常用命令

环境预检：

```powershell
.\start.ps1 doctor --project-id <你的项目ID>
```

登录新账号：

```powershell
.\start.ps1 login-account
.\start.ps1 login-account --account <你的Google账号邮箱>
.\start.ps1 login-account --account <你的Google账号邮箱> --no-browser
```

这个命令会调用 `gcloud auth login --update-adc`，一次完成新账号登录、切换活跃账号和同步 `Application Default Credentials`。

切换账号：

```powershell
.\start.ps1 switch-account --account <你的Google账号邮箱>
.\start.ps1 switch-account --account <你的Google账号邮箱> --no-sync-adc
```

这个命令用于切换“已经登录过”的账号。默认会同时切换 `gcloud` 活跃账号，并同步 `Application Default Credentials`。如果你只想切换 `gcloud` 配置账号、不改 ADC，可以加 `--no-sync-adc`。

刷 CPU 状态查看：

```powershell
.\start.ps1 show-reroll-state
```

从状态文件恢复刷 CPU：

```powershell
.\start.ps1 reroll-amd --project-id <你的项目ID> --instance <实例名> --zone <可用区> --resume
```

刷外网 IP：

```powershell
.\start.ps1 reroll-ip --project-id <你的项目ID> --instance <实例名> --zone <可用区> --resume
```

同时刷外网 IP 和 AMD/EPYC CPU：

```powershell
.\start.ps1 reroll-ip-amd --project-id <你的项目ID> --instance <实例名> --zone <可用区> --resume
```

`reroll-ip` 会以启动前读取到的外网 IP 为基准，停启实例直到外网 IP 变化；如果一开始没有外网 IP，则获取到任意有效外网 IP 即视为成功。`reroll-ip-amd` 需要同一轮同时满足外网 IP 变化和 CPU 命中 AMD/EPYC。

非交互远程 dry-run：

```powershell
.\start.ps1 run-script --project-id <你的项目ID> --instance <实例名> --zone <可用区> --dry-run apt
```

一键部署流程：

```powershell
.\start.ps1 setup --project-id <你的项目ID> --region us-west1
.\start.ps1 setup --project-id <你的项目ID> --region us-west1 --skip-reroll
```

`setup` 会串联创建实例、刷 AMD/EPYC、防火墙、换源、安装 dae、上传 `config.dae` 和安装流量监控脚本。默认流量监控限额来自 `gcp_config.py` 的 `TRAFFIC_LIMIT_GB`。

远程状态仪表盘：

```powershell
.\start.ps1 status --project-id <你的项目ID> --instance <实例名> --zone <可用区>
```

状态命令会读取远端 `vnstat` 当月流量、`dae` 服务状态、根分区磁盘使用率和系统运行时长；如果组件未安装，只显示警告，不自动安装。

更新 `cdnip.txt`：

```powershell
.\start.ps1 update-cdnip
.\start.ps1 update-cdnip --output cdnip.txt
```

日志文件默认写入：

```text
.gcp_free_logs/gcp_free.log
```

刷 CPU 状态文件默认写入：

```text
.gcp_free_state/reroll_state.json
```

刷 IP 和刷 IP + AMD 状态文件默认写入：

```text
.gcp_free_state/reroll_ip_state.json
.gcp_free_state/reroll_ip_amd_state.json
```

`doctor` 现在会额外检查：

- 当前账号与 ADC 状态
- 默认项目与目标项目是否一致
- `compute.googleapis.com` / `cloudresourcemanager.googleapis.com` 是否已启用
- `scripts/` 目录是否完整
- `config.dae` / `cdnip.txt` 是否已准备
- `.gcp_free_logs/` 与 `.gcp_free_state/` 是否可写

## GitHub Actions

仓库现在包含四条 GitHub Actions：

- `自动检查`
  在 `push` 到 `master` 和 `pull_request` 时自动执行语法检查与单元测试。
- 本地如果需要运行与 CI 一致的 lint 环境，请安装开发依赖：
  `python -m pip install -r requirements-dev.txt`
- `构建并发布 Windows EXE`
  支持两种触发方式：
  1. 在 GitHub Actions 页面手动运行 `workflow_dispatch`
  2. 推送形如 `v1.2.3` 的 Git tag，自动构建并创建 Release
- `PR 评论指令触发 EXE 构建`
  在 GitHub PR 评论里发送指令后，自动转发到 EXE 构建/发布工作流
- `清理 GitHub Actions 缓存`
  每天北京时间 03:30 自动清理 Actions cache，也支持手动触发；默认删除 7 天未访问的 cache，并在总量超过 6 GB 时按最久未访问优先删除。

如果你想通过命令触发手动构建，可以使用 GitHub CLI：

```bash
gh workflow run "构建并发布 Windows EXE" -f version=v1.2.3 -f create_release=true -f draft=false
```

如果需要手动清理 Actions cache：

```bash
gh workflow run "清理 GitHub Actions 缓存" -f max_age_days=7 -f max_cache_gb=6
```

手动运行工作流时：

- `version` 留空：只构建并上传 artifact，不创建 Release
- `create_release=true`：会按输入版本创建 GitHub Release，并上传 ZIP 包
- 发布工作流会先等待当前提交在默认分支上的 `自动检查` 变绿；即使通过了这道门禁，工作流内部仍会再跑一遍语法检查和单元测试
- 手动构建上传的 Actions artifact 默认保留 30 天；正式 Release 的 ZIP 仍会作为 Release 资产长期保留

评论指令支持：

```text
/build-exe
/build-exe v1.2.3
/release-exe v1.2.3
```

出于安全考虑，评论触发只接受仓库 `OWNER` / `MEMBER` / `COLLABORATOR` 在 **PR 评论** 中发出的指令。

含义如下：

- `/build-exe`：只构建并上传 artifact
- `/build-exe v1.2.3`：构建时附带版本标识，但不创建 Release
- `/release-exe v1.2.3`：构建后自动创建 GitHub Release 并上传 ZIP 包

### EXE 代码签名

如果你希望 GitHub Actions 构建出的 `exe` 自动签名，需要在仓库中配置以下密钥：

- `WINDOWS_CODESIGN_CERT_BASE64`
  把 `.pfx` 证书文件做 Base64 编码后的完整内容
- `WINDOWS_CODESIGN_CERT_PASSWORD`
  上述 `.pfx` 证书对应的密码

可选仓库变量：

- `WINDOWS_CODESIGN_TIMESTAMP_URL`
  时间戳服务地址；留空时默认使用 `http://timestamp.digicert.com`

PowerShell 下可用下面的命令生成 Base64：

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("D:\path\to\codesign.pfx"))
```

如果未配置上述密钥，工作流仍会正常构建，但产物为未签名版本。

注意：代码签名可以降低 SmartScreen 的拦截概率，但 **不能保证** 完全消除提示。SmartScreen 还会参考证书信誉、下载量和历史分发记录；如果你追求更稳定的效果，通常需要长期使用同一张可信证书，EV 证书效果会更明显。

## 脚本说明

- `gcp.py`: 轻量入口脚本
- `gcp_app.py`: 兼容聚合层与直接运行入口
- `gcp_common.py`: 职责模块共享依赖入口
- `gcp_config.py`: 配置常量
- `gcp_clients.py`: GCP client 工厂
- `gcp_operations.py`: API 重试、瞬时错误判定与 operation 等待
- `gcp_instance.py`: 项目/实例查询、创建与状态轮询
- `gcp_reroll.py`: 刷 CPU 状态持久化、异常分类与循环逻辑
- `gcp_firewall.py`: 防火墙与免费资源清理
- `gcp_remote.py`: SSH/SCP、远程脚本上传执行与状态仪表盘
- `gcp_menu.py`: 交互式菜单与菜单动作
- `gcp_cli.py`: CLI 解析、子命令和 setup 流程编排
- `gcp_utils.py`: 通用工具函数、日志输出与基础交互辅助
- `gcp_models.py`: 数据模型
- `gcp_logging.py`: 日志写入
- `gcp_state.py`: JSON 状态持久化
- `gcp_doctor.py`: 环境预检
- `gcp_ips.py`: GCP 区域 IP 段更新
- `start.ps1`: Windows PowerShell 启动脚本
- `config.dae`: dae 配置模板
- `scripts/apt.sh`: 换源脚本
- `scripts/dae.sh`: 安装 dae
- `scripts/net_iptables.sh`: 流量监控（iptables）
- `scripts/net_shutdown.sh`: 超额自动关机

## 常见问题

- 如果 `start.sh` 报错提示未找到 venv，可删除 `.gcp_free_initialized` 后重新初始化。
