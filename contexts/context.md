# 项目上下文

## 项目定位

`gcp_free` 是一个面向 GCP Compute Engine 免费实例运维的 Python CLI 工具集，目标不是做通用 IaC，而是围绕“快速创建实例、刷到目标 CPU / 外网 IP、完成基础远程配置”这一条操作链提供半自动化能力。

维护型项目 Wiki 位于 `docs/wiki.md`，用于承载比 README 更完整的执行模型、账号上下文、远程执行、状态文件、测试发布和排障说明。

当前仓库同时支持两种使用方式：

- 交互式菜单：直接运行 `gcp.py` / `start.ps1` / `start.sh` 后进入菜单。
- 非交互 CLI：通过 `gcp_cli.py` 暴露的子命令完成账号切换、创建实例、刷 CPU / IP、防火墙、远程脚本执行、状态查看和一键 `setup`。

核心使用场景：

- 在免费区域创建 `e2-micro` 实例。
- 通过 stop/start 循环刷到 AMD / EPYC CPU。
- 通过 stop/start 循环刷新外网 IP，或同时刷 IP + AMD / EPYC。
- 远程执行换源、安装 `dae`、上传 `config.dae`、部署流量监控脚本。
- 在多账号 / 多项目环境下，保证 `gcloud` 活跃账号与 ADC（Application Default Credentials）一致，减少串账号和 403 问题。

## 运行前提

- 本工具依赖本机已安装 `gcloud`，并具备目标项目权限。
- 本地 Python 运行与 EXE 运行都不替代 `gcloud`；EXE 只是打包 Python 程序本身。
- 账号认证分为两层：
  - `gcloud auth login`
  - `gcloud auth application-default login`
- 对非交互 CLI，默认要求账号上下文明确；如果只传 `--project-id` 不传 `--account`，代码会主动检查当前 `gcloud` 账号和 ADC 账号是否一致，不一致时直接停止执行。
- 创建免费实例依然要求项目已绑定结算账号；“免费”指免费层规格，不是免账号结算。

## 入口与执行模型

### 启动入口

- `gcp.py`：最薄入口，负责初始化 stdio、解析参数、决定走 CLI 还是交互菜单。
- `gcp_app.py`：聚合层入口，向旧测试和外部导入保留兼容面。
- `start.ps1`：Windows PowerShell 启动器，负责 venv、依赖安装和参数透传。
- `start.sh`：Linux / WSL / Git Bash 启动器，负责初始化环境并透传参数。

### 调用链

典型执行路径如下：

1. 启动脚本准备运行环境。
2. `gcp.py` 调用 `gcp_app.parse_args()`。
3. 如果识别到子命令，则走 `gcp_cli.run_cli()`。
4. 如果没有子命令，则进入 `gcp_menu.main()` 的交互菜单。

### CLI 组织方式

- `gcp_cli.py` 通过 `ACTION_SPECS` / `ACTION_SPEC_MAP` 维护动作描述与 handler 映射。
- `build_arg_parser()` 负责定义所有子命令。
- `run_cli()` 负责执行 handler，并在必要时先调用 `prepare_cli_account_context()` 校验账号上下文。
- `handle_setup_cli()` 是最高层编排入口，会把创建实例、刷 CPU、防火墙、换源、安装 `dae`、上传配置、安装流量监控串成一个工作流。

## 模块职责

### 入口与聚合

- `gcp.py`：主入口。
- `gcp_app.py`：兼容聚合层。
- `gcp_common.py`：集中管理共享依赖、配置、client、model 和常用标准库符号。

### 核心业务

- `gcp_cli.py`：非交互 CLI 解析与工作流编排。
- `gcp_menu.py`：交互菜单与菜单动作。
- `gcp_instance.py`：实例查询、创建、状态轮询、账号与项目选择。
- `gcp_reroll.py`：刷 CPU / IP 的循环逻辑、状态恢复、异常分类。
- `gcp_firewall.py`：防火墙规则和免费资源清理。
- `gcp_remote.py`：SSH / `gcloud compute ssh`、SCP / `gcloud compute scp`、远程脚本执行和状态面板。
- `gcp_doctor.py`：环境体检，检查工具、账号、API、脚本目录、资源文件和可写目录。
- `gcp_ips.py`：下载并更新 GCP 区域 IP 段文件。

### 基础设施

- `gcp_config.py`：超时、重试、区域、镜像、状态文件、日志目录等常量。
- `gcp_clients.py`：Google Cloud client 工厂与缓存。
- `gcp_operations.py`：operation 等待、瞬时错误判定、重试逻辑。
- `gcp_utils.py`：输出、交互、配置解析和一些通用辅助函数。
- `gcp_models.py`：数据模型。
- `gcp_logging.py`：日志写入与控制台输出适配。
- `gcp_state.py`：JSON 状态文件持久化。

### 远程脚本与发布

- `scripts/apt.sh`：按 Debian / Ubuntu 区分换源。
- `scripts/dae.sh`：远程安装 `dae`。
- `scripts/net_iptables.sh`：按月出站流量限额追加 `iptables` 限制。
- `scripts/net_shutdown.sh`：超额自动关机。
- `scripts/build_exe.py`：Windows EXE 打包。

## 关键业务约束

### 账号与项目约束

- 非交互模式下，账号一致性优先于“尽量执行”。
- 如果当前 `gcloud` 账号未知、ADC 账号未知，或两者不一致，而用户又没有显式传 `--account`，CLI 会停止执行，避免误用错误账号调用 Python API。
- 菜单和 CLI 都会尽量同步：
  - `gcloud` 活跃账号
  - ADC
  - 默认项目
  - ADC quota project
- 需要 preflight 的非交互 CLI 会在切换前快照 `gcloud` 活跃账号、默认项目和完整 ADC 凭据文件；如果账号切换、ADC 同步或 handler 失败，会尝试恢复原上下文。
- `gcloud config get-value` 快照会区分“读取成功但未设置”和“读取失败”。读取失败时恢复阶段不得把未知状态当成空字符串执行 `gcloud config unset project`。
- ADC 凭据文件恢复必须使用临时文件替换模式，避免写入中断损坏 `application_default_credentials.json`。

### 区域与可用区约束

- `--zone` 优先级最高；一旦传入，会覆盖 `--region` 和 `--tier`。
- 未传 `--zone` 时，默认优先免费区域，默认免费区是 `us-west1`。
- 付费区域默认走 `australia-southeast1`，也支持多个亚洲、美国、欧洲区域。

### 远程执行约束

- 优先使用 `gcloud` 远程模式；只有在显式指定 `--remote-method ssh` 或本机没有 `gcloud` 时，才切换到原生 SSH。
- 若使用 SSH 且传入 `--ssh-key`，会先校验私钥文件是否存在。
- 远程执行前会等待实例就绪；实例未就绪时不会盲目继续。
- 上传到远端 `/tmp` 的临时脚本或配置文件必须覆盖成功和失败路径清理；清理失败只告警，不覆盖原始失败结果。

### `setup` 流程约束

- `setup` 是高层工作流，不是独立实现；其本质是依次调用已有能力。
- 默认会执行刷 AMD / EPYC；传 `--skip-reroll` 时跳过。
- 流量监控脚本默认只支持 Debian。
- 如果用户在 `setup` 中选择 `--os ubuntu`，流程会在创建实例前直接停止，避免资源创建完成后远程脚本阶段失败。

### 防火墙约束

- 添加规则时必须能确定目标网络，来源是 `--network` 或实例自身网络。
- 删除拒绝 CDN 出站规则时会按目标网络删除，默认网络是 `global/networks/default`，避免误删其他 VPC 规则。
- 遇到“同名但网络不一致”的既有规则时，工具会停止，而不是覆盖重建。

### 状态与资源路径约束

- 日志目录固定为 `.gcp_free_logs/`。
- 状态目录固定为 `.gcp_free_state/`。
- 默认状态文件包括：
  - `reroll_state.json`
  - `reroll_ip_state.json`
  - `reroll_ip_amd_state.json`
- 静态资源读取优先级是：
  1. EXE / 运行目录下的外部资源
  2. 包内资源
- 这意味着 `config.dae`、`cdnip.txt` 等文件允许用户在运行目录直接覆盖。

### 运行稳定性约束

- Google Cloud client 默认强制使用 REST transport，以避开部分 Windows / 本地环境下的 gRPC 兼容问题。
- 实例状态轮询、operation 等待、重试、OAuth 熔断和抖动参数都集中在 `gcp_config.py`。
- 当前配置偏向“刷 CPU / IP 周期尽量短”，因此轮询比较激进；如果后续出现更多 429 / 502，应先调整配置常量，不要直接散改业务逻辑。

## 关键默认值

- 默认流量限额：`TRAFFIC_LIMIT_GB = 180`
- 免费区域：
  - `us-west1`
  - `us-central1`
  - `us-east1`
- 默认支持系统镜像：
  - Debian 12
  - Ubuntu 22.04

## 测试与验证

### 本地常用验证命令

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

### 当前测试结构

- `tests/test_gcp_helpers.py`：主测试文件，覆盖大量核心行为和回归场景。
- `tests/test_doctor.py`：环境体检相关。
- `tests/test_entrypoints_and_scripts.py`：入口脚本、shell 脚本和 GitHub Actions 约束。
- `tests/test_remote_commands.py`：远程命令构造。
- `tests/test_gcp_clients.py`、`tests/test_models.py`、`tests/test_config_paths.py`、`tests/test_gcp_ips.py`、`tests/test_logging.py`：基础能力验证。

### CI 事实

- GitHub Actions `自动检查` 会在 `push` 到 `master` 和 `pull_request` 时运行。
- CI 矩阵覆盖 `ubuntu-latest` / `windows-latest` 与 Python `3.10`、`3.11`、`3.12`。
- CI 使用 `python -m unittest discover -s tests -v`，不是 `pytest`。

## 维护提示

### 修改代码时优先注意

- 如果修改 CLI 参数、子命令名称、`setup` 编排顺序或账号同步行为，必须同步更新 `README.md` 和本文件。
- 如果修改 `gcp_config.py` 中的轮询 / 超时 / 冷却常量，优先说明修改目的属于：
  - 减少 429 / 502
  - 提高刷 CPU / IP 成功率
  - 改善远程执行稳定性
- 如果新增远程脚本，除了补脚本本身，还要同步更新：
  - `LOCAL_SCRIPT_FILES`
  - CLI / 菜单可选项
  - `doctor` 对脚本目录完整性的检查
  - 测试文件

### 当前代码结构的注意点

- `gcp_common.py` 的设计是“集中重导出共享依赖”，这样减少了重复 import，但也提高了模块耦合度。
- `gcp_app.py` 依赖大量星号导入，适合作为兼容层，不适合继续堆业务逻辑。
- 如果未来继续扩展功能，优先在职责模块中新增显式函数，再由 `gcp_cli.py` / `gcp_menu.py` 编排，不要继续把复杂逻辑回灌到入口层。
- `tests/test_gcp_helpers.py` 已经比较大；如果未来继续增长，优先按领域拆分，而不是继续把所有回归都堆到一个文件。

## 给后续 Agent 的建议

- 先读本文件，再读 `README.md`。
- 处理线上行为问题时，先确认是菜单路径还是非交互 CLI 路径。
- 遇到账号 / 权限 / 403 / 串项目问题时，优先检查 `gcloud` 与 ADC 是否一致，再看业务代码。
- 需要排查“为什么本地能跑、EXE 不能跑”时，优先检查运行目录下是否覆盖了 `config.dae`、`cdnip.txt` 或状态文件。
