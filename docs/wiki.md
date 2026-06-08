# GCP Free 项目 Wiki

本文是 `gcp_free` 的维护型 wiki，用于解释项目目标、执行模型、账号上下文、远程执行、状态文件、测试验证和常见故障。快速使用命令见仓库根目录的 `README.md`，项目定位和维护提醒见 `contexts/context.md`。

## 目录

- 项目目标
- 运行环境
- 入口和执行模型
- CLI 命令速查
- 账号、项目和 ADC 上下文
- 一键 setup 流程
- 实例和刷机逻辑
- 防火墙规则
- 远程执行和脚本
- 状态文件、日志和资源路径
- 测试和质量门禁
- 打包和发布
- 排障手册
- 技术规范和许可证
- 维护约定

## 项目目标

`gcp_free` 是围绕 GCP Compute Engine 免费层实例的半自动运维工具。它不是通用 IaC 框架，核心目标是把下面这条高频链路做稳：

1. 选择或切换本机 `gcloud` 账号。
2. 选择目标 GCP 项目。
3. 创建免费层规格实例。
4. 通过 stop/start 循环刷到目标 CPU 平台，通常是 AMD / EPYC。
5. 通过 stop/start 循环刷新外网 IP。
6. 配置防火墙。
7. 远程换源、安装 `dae`、上传 `config.dae`。
8. 安装流量监控脚本。

项目的安全边界是“避免串账号、串项目和错误 ADC 调用”。所有非交互 CLI 都应优先保护用户当前本机 `gcloud` / ADC 环境，而不是为了继续执行而默默猜测。

## 运行环境

本工具依赖本机环境：

- Python 3。
- Google Cloud SDK，也就是 `gcloud`。
- 已完成 `gcloud auth login`。
- 已完成 `gcloud auth application-default login`。
- 目标项目启用了 `compute.googleapis.com` 和 `cloudresourcemanager.googleapis.com`。
- 创建免费层实例的项目已绑定结算账号。

Windows 推荐用：

```powershell
.\start.ps1
```

Linux / WSL / Git Bash 推荐用：

```bash
bash start.sh
```

直接运行 Python 时：

```powershell
.\.venv\Scripts\python.exe gcp.py doctor --project-id <项目ID>
```

## 入口和执行模型

### 启动入口

- `gcp.py`：最薄入口，负责初始化 stdio、解析参数、分流到 CLI 或交互菜单。
- `gcp_app.py`：兼容聚合层，用 `import *` 暴露旧测试和外部导入依赖的函数。
- `start.ps1`：Windows 启动脚本，负责 venv、依赖安装和参数透传。
- `start.sh`：Linux / WSL / Git Bash 启动脚本，负责 venv、依赖哈希检查和参数透传。

### 交互模式

直接运行 `gcp.py`、`start.ps1` 或 `start.sh` 且未传子命令时，会进入 `gcp_menu.main()`。交互模式适合人工选择账号、项目、实例和操作。

### 非交互 CLI

传入子命令时会进入 `gcp_cli.run_cli()`。典型路径如下：

1. `build_arg_parser()` 解析子命令。
2. `run_cli()` 判断 handler 是否需要 preflight。
3. 需要 preflight 时先快照当前账号上下文。
4. `prepare_cli_account_context()` 校验或切换账号和项目。
5. `ensure_libraries_or_exit()` 检查 Google Cloud Python 依赖。
6. 执行具体 handler。
7. 如果 preflight 或 handler 失败，恢复账号上下文快照。

不需要 Google Cloud library preflight 的命令包括：

- `doctor`
- `show-reroll-state`
- `update-gcp-ip-ranges`
- `update-cdnip`
- `login-account`
- `switch-account`

## CLI 命令速查

环境体检：

```powershell
.\start.ps1 doctor --project-id <项目ID>
```

登录新账号：

```powershell
.\start.ps1 login-account --account <账号邮箱>
```

切换已登录账号：

```powershell
.\start.ps1 switch-account --account <账号邮箱>
```

列出实例：

```powershell
.\start.ps1 list-instances --project-id <项目ID> --account <账号邮箱>
```

创建实例：

```powershell
.\start.ps1 create --project-id <项目ID> --account <账号邮箱> --region us-west1
```

刷 AMD / EPYC CPU：

```powershell
.\start.ps1 reroll-amd --project-id <项目ID> --account <账号邮箱> --instance <实例名> --zone <可用区> --resume
```

刷外网 IP：

```powershell
.\start.ps1 reroll-ip --project-id <项目ID> --account <账号邮箱> --instance <实例名> --zone <可用区> --resume
```

同时刷 IP 和 AMD / EPYC：

```powershell
.\start.ps1 reroll-ip-amd --project-id <项目ID> --account <账号邮箱> --instance <实例名> --zone <可用区> --resume
```

运行远程脚本：

```powershell
.\start.ps1 run-script --project-id <项目ID> --account <账号邮箱> --instance <实例名> --zone <可用区> apt
```

部署 `config.dae`：

```powershell
.\start.ps1 deploy-dae-config --project-id <项目ID> --account <账号邮箱> --instance <实例名> --zone <可用区>
```

查看远程状态：

```powershell
.\start.ps1 status --project-id <项目ID> --account <账号邮箱> --instance <实例名> --zone <可用区>
```

一键 setup：

```powershell
.\start.ps1 setup --project-id <项目ID> --account <账号邮箱>
```

## 账号、项目和 ADC 上下文

本项目同时关心四类本机状态：

- `gcloud` 活跃账号。
- `gcloud` 默认项目。
- ADC 凭据文件，也就是 `application_default_credentials.json`。
- ADC quota project。

### 为什么必须显式传 `--account`

非交互 CLI 如果只传 `--project-id`，代码会读取当前 `gcloud` 账号和 ADC 账号。只要任一账号未知，或二者不一致，命令会停止执行并提示添加 `--account`。这是为了避免 Python API 使用旧 ADC 账号访问新项目，导致 403 或误操作。

### 带 `--account` 的命令如何切换

带 `--account` 且需要 preflight 的命令会调用：

```text
run_cli()
  -> snapshot_cli_project_context()
  -> prepare_cli_account_context()
  -> prepare_gcloud_context()
  -> switch_gcloud_account()
```

`switch_gcloud_account()` 会按需执行：

1. `gcloud config set account <账号邮箱>`。
2. `gcloud config set project <项目ID>`。
3. `gcloud auth application-default login <账号邮箱>`。
4. `gcloud auth application-default set-quota-project <项目ID>`。

### 失败回滚语义

需要 preflight 的 CLI 会在切换前快照：

- `gcloud config get-value account`
- `gcloud config get-value project`
- 完整 ADC 凭据文件内容
- ADC quota project

如果 preflight 或 handler 失败，CLI 会恢复：

1. ADC 凭据文件。
2. `gcloud` 活跃账号。
3. `gcloud` 默认项目。

快照会区分“读取成功但未设置”和“读取失败”。读取成功但未设置会恢复为 `unset`；读取失败则跳过对应恢复，避免一次瞬时 `gcloud config get-value project` 失败把用户原本的默认项目清空。

ADC 凭据恢复使用临时文件加 replace 的原子写模式，避免进程中断时写坏 `application_default_credentials.json`。

## 一键 setup 流程

`setup` 是高层编排，不是独立实现。它复用现有能力，默认顺序为：

1. 校验 OS 选择。流量监控脚本默认只支持 Debian，选择 `--os ubuntu` 时会在创建资源前停止。
2. 解析区域和可用区。`--zone` 优先级最高。
3. 创建实例。
4. 默认刷 AMD / EPYC，传 `--skip-reroll` 时跳过。
5. 配置防火墙。
6. 远程执行 `apt` 换源脚本。
7. 远程执行 `dae` 安装脚本。
8. 上传并应用 `config.dae`。
9. 安装流量监控脚本。

setup 的原则是“资源创建前能验证的错误尽量提前失败”。例如 Ubuntu 与默认流量监控脚本不兼容时，不应先创建实例再在远程阶段失败。

## 实例和刷机逻辑

### 实例创建

实例创建逻辑主要在 `gcp_instance.py`。默认规格偏向免费层：

- 机器类型：`e2-micro`。
- 免费区域：`us-west1`、`us-central1`、`us-east1`。
- 默认系统：Debian 12。

### 刷 CPU

刷 CPU 逻辑在 `gcp_reroll.py`。核心行为是：

1. 读取实例当前状态。
2. 如果实例运行中，停止实例。
3. 等待进入 `STOPPED`。
4. 启动实例。
5. 等待进入 `RUNNING`。
6. 读取 `cpu_platform`。
7. 命中 AMD / EPYC 时停止循环，否则继续。

### 刷 IP

刷 IP 以启动前外网 IP 为基准。若启动前已有外网 IP，则刷到不同 IP 才算命中；若启动前没有外网 IP，则获取任意有效外网 IP 即算命中。

### 状态恢复

刷 CPU / IP 支持 `--resume`。状态文件在 `.gcp_free_state/`：

- `reroll_state.json`
- `reroll_ip_state.json`
- `reroll_ip_amd_state.json`

状态兼容性会校验项目、实例名和可用区，避免把旧实例的刷机状态套到新目标上。

## 防火墙规则

防火墙逻辑在 `gcp_firewall.py`。本工具管理的规则名包括：

- `allow-all-ingress-custom`
- `deny-cdn-egress-custom`
- `deny-cdn-egress-custom-001` 等拆分规则

重要约束：

- 添加入站规则时必须能确定目标网络。
- 拒绝 CDN 出站规则会按网络匹配，避免误删其它 VPC 的同名规则。
- 如果发现同名规则属于其它网络或配置不一致，命令会停止并提示人工处理。
- CDN IP 列表超过 GCP 单条规则上限时会自动拆分。
- 重建拒绝规则失败时会尝试恢复旧规则。

删除拒绝 CDN 出站规则：

```powershell
.\start.ps1 firewall --project-id <项目ID> --account <账号邮箱> --delete-deny-cdn-egress
```

删除全部本工具管理的防火墙规则：

```powershell
.\start.ps1 firewall --project-id <项目ID> --account <账号邮箱> --delete-managed-rules
```

## 远程执行和脚本

远程执行逻辑在 `gcp_remote.py`。支持两种模式：

- `gcloud compute ssh/scp`
- 原生 `ssh/scp`

默认优先 `gcloud` 模式。只有显式传 `--remote-method ssh` 或无法使用 `gcloud` 时，才使用原生 SSH。

### 远程脚本

本地脚本位于 `scripts/`：

- `apt.sh`：Debian / Ubuntu 换源。
- `dae.sh`：安装 `dae`。
- `net_iptables.sh`：按月流量限额追加 iptables 限制。
- `net_shutdown.sh`：超额自动关机。

上传脚本前会统一转换为 LF 行尾，避免 Windows CRLF 导致远程 shell 执行异常。远程脚本执行命令内置 `trap cleanup EXIT`，正常和失败路径都会删除远端临时脚本。

### dae 配置部署

`deploy_dae_config()` 会：

1. 解析本地 `config.dae`。
2. 检测远程 OS。
3. 上传到 `/tmp/gcp_free_config_*.dae`。
4. 复制到 `/usr/local/etc/dae/config.dae`。
5. 设置权限为 `600`。
6. enable 并 restart `dae`。
7. 删除远端临时文件。

如果上传后应用命令构建失败或应用步骤失败，会额外发起 `rm -f` 清理远端临时配置文件。清理失败只告警，不覆盖原始失败结果。

## 状态文件、日志和资源路径

### 日志

默认日志目录：

```text
.gcp_free_logs/
```

默认日志文件：

```text
.gcp_free_logs/gcp_free.log
```

### 状态

默认状态目录：

```text
.gcp_free_state/
```

JSON 状态文件通过 `gcp_state.py` 写入，采用临时文件替换模式，避免部分写入。

### 资源路径

静态资源读取优先级：

1. EXE / 当前运行目录下的外部资源。
2. 包内资源。

这意味着用户可以通过运行目录中的 `config.dae`、`cdnip.txt`、`scripts/` 覆盖默认资源。

## 测试和质量门禁

本地常用检查：

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试目录：

- `tests/test_gcp_helpers.py`：核心业务和 CLI 回归。
- `tests/test_remote_commands.py`：远程命令构造、上传和清理。
- `tests/test_doctor.py`：环境体检。
- `tests/test_entrypoints_and_scripts.py`：入口脚本和 GitHub Actions 约束。
- `tests/test_gcp_clients.py`：Google Cloud client 工厂。
- `tests/test_models.py`：数据模型。
- `tests/test_config_paths.py`：资源路径。
- `tests/test_gcp_ips.py`：GCP IP 段更新。
- `tests/test_logging.py`：日志写入。

CI 使用 `unittest`，不是 `pytest`。新增测试时应遵循现有 `unittest.TestCase` 风格。

## 打包和发布

Windows EXE 打包脚本：

```powershell
python -m pip install -r requirements.txt pyinstaller
python scripts/build_exe.py --clean --version v1.0.0
```

构建产物位于：

```text
dist/release/
```

EXE 只打包 Python 程序，不包含 Google Cloud SDK。运行 EXE 的机器仍必须安装 `gcloud` 并完成认证。

GitHub Actions 包含：

- 自动检查。
- 构建并发布 Windows EXE。
- PR 评论触发 EXE 构建。
- PR Windows EXE 构建。
- 清理 GitHub Actions 缓存。

正式 Release 建议在默认分支检查通过后再触发。

## 排障手册

### 403 或权限错误

优先检查：

```powershell
gcloud auth list
gcloud config get-value account
gcloud config get-value project
gcloud auth application-default print-access-token
```

然后运行：

```powershell
.\start.ps1 doctor --project-id <项目ID>
```

如果 `gcloud` 账号和 ADC 账号不一致，建议显式执行：

```powershell
.\start.ps1 switch-account --account <账号邮箱>
```

### 非交互 CLI 要求添加 `--account`

这是预期保护。非交互模式下，代码不会在账号未知或 ADC 不一致时继续执行。请显式传入：

```powershell
--account <账号邮箱>
```

### 默认项目被判定不一致

检查：

```powershell
gcloud config get-value project
```

如需修正：

```powershell
gcloud config set project <项目ID>
```

### ADC quota project 异常

可尝试：

```powershell
gcloud auth application-default set-quota-project <项目ID>
```

如果 ADC 文件损坏，重新登录：

```powershell
gcloud auth application-default login
```

### 远程 SSH 不通

检查：

- 实例状态是否为 `RUNNING`。
- 实例是否有外网 IP。
- 防火墙是否允许 SSH。
- 本机 `gcloud compute ssh` 是否可用。
- 使用原生 SSH 时 `--ssh-key` 是否存在。

可先运行 dry-run：

```powershell
.\start.ps1 run-script --project-id <项目ID> --account <账号邮箱> --instance <实例名> --zone <可用区> --dry-run apt
```

### dae 配置未生效

检查远程服务状态：

```powershell
.\start.ps1 status --project-id <项目ID> --account <账号邮箱> --instance <实例名> --zone <可用区>
```

常见原因：

- 本地 `config.dae` 不存在。
- 远程系统不是 Debian / Ubuntu。
- `dae` 服务安装失败。
- 配置文件语法错误导致 `systemctl restart dae` 失败。

### 刷 CPU / IP 长时间不中

这是 GCP 调度结果，不保证固定时间命中。建议：

- 使用 `--resume` 保留进度。
- 查看 `.gcp_free_state/` 中的状态文件。
- 遇到 OAuth 或 Compute API 瞬时错误时观察冷却日志。
- 如果频繁 429 / 502，优先调整 `gcp_config.py` 中的轮询和重试常量。

## 技术规范和许可证

维护规范位于 `specs/`：

- `specs/README.md`：规范索引和更新规则。
- `specs/architecture.md`：入口、模块边界、菜单 / CLI 动作表和兼容层约束。
- `specs/account-context.md`：`gcloud`、ADC、默认项目和 quota project 的事务性切换 / 回滚语义。
- `specs/remote-state.md`：远程执行、临时文件清理、状态文件和资源路径约束。
- `specs/security-quality.md`：日志脱敏、CodeQL / Secret scanning、CI、发布 workflow 和许可证要求。

项目使用 MIT License，根目录 `LICENSE` 是唯一许可证源文件。发布包、README、Wiki 或其它文档提到许可证时，应指向该文件，避免维护多份可能漂移的许可证正文。

## 维护约定

### 修改账号上下文逻辑

必须同步考虑：

- `gcloud` 活跃账号。
- `gcloud` 默认项目。
- ADC 文件。
- ADC quota project。
- 失败回滚是否会破坏用户原环境。
- `tests/test_gcp_helpers.py` 中的回归测试。

### 修改远程执行逻辑

必须同步考虑：

- gcloud 模式和原生 SSH 模式。
- Windows 路径与 shell 引号。
- 远端临时文件清理。
- dry-run 输出。
- `tests/test_remote_commands.py`。

### 修改 CLI 参数

必须同步更新：

- `README.md`
- `contexts/context.md`
- 本 wiki
- 相关 CLI parser 测试

### 新增脚本

必须同步更新：

- `scripts/` 下的脚本文件。
- `LOCAL_SCRIPT_FILES`。
- `doctor` 脚本完整性检查。
- 菜单和 CLI 可选项。
- 远程执行测试。
