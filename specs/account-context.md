# 账号上下文规范

本文记录 `gcloud`、ADC、默认项目和 ADC quota project 的一致性规则。任何修改账号切换、项目切换、preflight 或回滚逻辑的变更都必须遵守本规范。

## 核心原则

非交互 CLI 的安全边界是“不要串账号、不要串项目”。当上下文不明确时，命令应停止并提示用户显式指定 `--account`，而不是猜测当前账号。

本项目同时管理四类本机状态：

- `gcloud config get-value account`
- `gcloud config get-value project`
- ADC 凭据文件 `application_default_credentials.json`
- ADC quota project

## 无 `--account` 的非交互 CLI

需要 preflight 的命令如果没有传 `--account`，必须检查：

1. 当前 `gcloud` 活跃账号可读取。
2. 当前 ADC 账号可确认。
3. 两者一致。
4. 默认项目与目标项目没有明显冲突。

任一条件不满足时，应停止执行并给出明确提示。这样做是为了避免 Python API 使用旧 ADC 账号访问用户以为的目标项目。

## 带 `--account` 的非交互 CLI

带 `--account` 且需要 preflight 的命令必须在切换前快照当前上下文。执行链路为：

```text
run_cli()
  -> snapshot_cli_project_context()
  -> prepare_cli_account_context()
  -> prepare_gcloud_context()
  -> switch_gcloud_account()
  -> handler(args)
```

失败时必须尽量恢复原上下文：

```text
restore_cli_project_context()
  -> restore_adc_credentials()
  -> restore_gcloud_account()
  -> restore_gcloud_project()
```

恢复失败只能告警或记录，不应覆盖原始 handler 失败。

## 快照语义

`gcloud config get-value` 快照必须区分三种状态：

- 读取成功且有值。
- 读取成功但未设置。
- 读取失败。

读取成功但未设置时，恢复阶段可以执行 unset。读取失败时，恢复阶段必须跳过对应字段，不能把未知状态当成空值清空用户原配置。

## ADC 文件语义

ADC 凭据文件恢复必须使用临时文件替换模式：

1. 写入同目录临时文件。
2. flush 并关闭。
3. 使用 replace 替换目标文件。

不得直接用 `write_text()` 覆写 `application_default_credentials.json`，避免进程或机器中断时写坏凭据。

## quota project 语义

ADC quota project 应尽量同步到目标项目，但不得在 ADC 账号无法确认或与目标账号不一致时盲目修改。恢复 quota project 时同样必须保护 ADC 文件完整性。

## 菜单模式

交互菜单允许引导用户选择账号和项目，但选择后仍应尽量同步：

- `gcloud` 活跃账号
- ADC
- 默认项目
- ADC quota project

菜单路径中用户明确选择了账号，因此可以比非交互 CLI 更主动，但仍不能在账号未知时静默继续执行云端操作。

## 回归测试要求

修改账号上下文逻辑时，至少检查或补充以下测试方向：

- 无 `--account` 且 gcloud / ADC 不一致时拒绝执行。
- preflight 失败时恢复账号、项目和 ADC 文件。
- handler 失败时恢复账号、项目和 ADC 文件。
- `gcloud config get-value project` 读取失败时不会执行 unset。
- ADC 凭据恢复使用原子写。
- ADC 账号不一致时不盲目设置 quota project。

常用命令：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_gcp_helpers -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
