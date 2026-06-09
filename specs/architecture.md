# 架构规范

本文记录 `gcp_free` 的入口、模块边界和动作注册约束。修改架构相关代码时，必须同步检查本文件、`contexts/context.md` 和 `docs/wiki.md`。

## 设计目标

`gcp_free` 是面向 GCP Compute Engine 免费实例运维的 Python CLI 工具，不是通用 IaC 框架。架构设计优先保证：

- 账号和项目上下文明确。
- 交互菜单和非交互 CLI 行为一致但边界清楚。
- 远程执行、刷机和防火墙逻辑可测试。
- 旧入口和旧测试导入保持兼容。

## 启动入口

- `gcp.py` 是最薄入口，只负责调用 `gcp_app` 中的解析和分发函数。
- `gcp_app.py` 是兼容聚合层，通过 `import *` 暴露历史公共面。这里不应继续新增业务逻辑。
- `start.ps1` 和 `start.sh` 负责环境准备和参数透传，不应实现核心业务逻辑。

典型分发路径：

```text
gcp.py
  -> gcp_app.parse_args()
  -> gcp_app.run_cli(args)
  -> 如果没有 CLI handler，则进入 gcp_app.main()
```

## 模块边界

- `gcp_cli.py`：非交互 CLI 参数、handler、`setup` 编排和账号 preflight。
- `gcp_menu.py`：交互菜单、菜单动作和人工输入流程。
- `gcp_instance.py`：账号、项目、实例查询、实例创建、状态等待和实例网络配置变更。
- `gcp_reroll.py`：刷 CPU / IP 循环、状态恢复、异常分类和冷却策略。
- `gcp_firewall.py`：防火墙规则、CDN 出站拒绝和资源清理。
- `gcp_remote.py`：远程命令构造、上传、执行、OS 检测和状态面板。
- `gcp_operations.py`：GCP API 重试、operation 等待和瞬时错误分类。
- `gcp_clients.py`：Google Cloud client 工厂和缓存。
- `gcp_utils.py`：输出、交互、路径解析、文本摘要和通用工具。
- `gcp_logging.py`：控制台 / 文件日志和敏感字段脱敏。
- `gcp_state.py`：JSON 状态文件持久化。
- `gcp_config.py`：常量和资源路径。
- `gcp_models.py`：数据模型。
- `gcp_doctor.py`：本地和云端环境体检。
- `gcp_ips.py`：GCP IP 段下载和合并。

## 动作表

交互菜单和 CLI 使用不同动作表：

- `gcp_menu.MENU_ACTIONS`：菜单项顺序和菜单 handler。
- `gcp_cli.ACTION_SPECS` / `ACTION_SPEC_MAP`：CLI 子命令、帮助文本和 CLI handler。

新增动作时：

1. 在职责模块中实现实际行为。
2. 如需菜单入口，添加菜单 action 并更新 `MENU_ACTIONS`。
3. 如需 CLI 入口，添加 CLI handler、parser 和 `ACTION_SPECS`。
4. 增加 parser 或菜单回归测试。
5. 更新 README、Wiki、context 和相关 specs。

不得让 `gcp_menu.py` import `gcp_cli.py`。`gcp_cli.py` 可以引用菜单 action 作为 CLI 和菜单共享动作元数据的来源，但菜单路径不能反向依赖 CLI 动作表。

## 实例网络配置动作

不停机切换临时外网 IP 属于实例网络配置变更，实际行为应放在 `gcp_instance.py`，再由 `gcp_cli.py` 和 `gcp_menu.py` 编排入口。

当前 `switch-ip` / `reroll-ip --method access-config` 使用本机 `gcloud compute instances delete-access-config` 和 `gcloud compute instances add-access-config`。维护约束：

- 不得改变 `reroll-ip` 默认 stop/start 循环刷 IP 语义。
- 新增或修改 access config 参数时，应同步 CLI parser、菜单说明、README、Wiki、context 和测试。
- 执行前必须刷新实例状态并要求目标实例为 `RUNNING`。
- 未显式传 `--access-config-name` 时应优先探测现有 access config 名称，探测失败时再回退 `external-nat`。
- 新增临时外网 IP 的 `network tier` 默认保持 `STANDARD`，避免与创建实例时的默认配置不一致。

## 兼容层约束

`gcp_app.py` 仍然承担历史兼容职责，因此允许星号导入。新增业务时不要把逻辑写入 `gcp_app.py`，应写入职责模块，再由 `gcp_app.py` 聚合暴露。

`gcp_common.py` 是共享依赖重导出入口，显式 `__all__` 表示其公共面。删除其中导出前，必须确认所有职责模块不再经由 `gcp_common` 使用该符号。

## 测试约束

架构相关修改至少要运行：

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

涉及 CLI parser 或动作表时，优先补 `tests/test_gcp_helpers.py` 中的解析测试。涉及入口脚本或 workflow 时，补 `tests/test_entrypoints_and_scripts.py`。
