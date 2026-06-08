# 远程执行与状态规范

本文记录远程命令、临时文件、状态文件和资源路径的维护约束。

## 远程执行模式

`gcp_remote.py` 支持两种远程执行方式：

- `gcloud compute ssh/scp`
- 原生 `ssh/scp`

默认优先 `gcloud` 模式。只有显式传入 `--remote-method ssh`，或当前环境无法使用 `gcloud` 时，才使用原生 SSH。

原生 SSH 模式必须校验：

- `ssh` / `scp` 是否存在。
- `--ssh-key` 指定的私钥文件是否存在。
- 端口和 user 参数是否参与命令构造。

## 实例就绪约束

远程执行前必须刷新实例状态并等待远程连接可用。实例不是 `RUNNING` 时，不得继续执行远程脚本。

远程 ready 检测失败应返回失败或提示用户排查，不应继续上传和执行脚本。

## 临时文件清理

本地临时上传文件：

- 由 `prepare_local_script_for_upload()` 创建。
- 必须在 `finally` 中调用 `cleanup_temp_upload_file()`。
- 清理失败只告警，不覆盖原始执行结果。

远端临时脚本：

- 上传到 `/tmp/gcp_free_*.sh`。
- 执行命令应包含 `trap cleanup EXIT`。
- 成功和失败路径都应删除远端临时脚本。

远端 `config.dae` 临时文件：

- 上传到 `/tmp/gcp_free_config_*.dae`。
- 应用成功后在远端命令中删除。
- 上传成功后如果构造执行命令失败，或应用 dae 配置失败，必须额外执行 `rm -f` 清理。
- 清理失败只告警，不覆盖原始失败结果。

## shell 和路径约束

- 上传 shell 脚本前必须转换为 LF 行尾。
- Windows 本地路径不能直接拼接进远端 shell 字符串；需要按命令构造函数的既有模式处理。
- `dry_run` 不应产生真实远程副作用，但应尽量输出可检查的命令。

## 状态文件

状态目录固定为：

```text
.gcp_free_state/
```

默认状态文件：

- `reroll_state.json`
- `reroll_ip_state.json`
- `reroll_ip_amd_state.json`

状态文件写入由 `gcp_state.py` 负责，必须使用临时文件替换模式，避免部分写入。

刷机状态恢复必须校验：

- project_id
- instance_name
- zone
- target_mode

不匹配时不能复用旧状态。

## 资源路径

静态资源读取优先级：

1. EXE / 当前运行目录下的外部资源。
2. 包内资源。

这允许用户通过运行目录覆盖：

- `config.dae`
- `cdnip.txt`
- `scripts/apt.sh`
- `scripts/dae.sh`
- `scripts/net_iptables.sh`
- `scripts/net_shutdown.sh`

新增资源时必须同步：

- `gcp_config.py` 常量或资源表。
- `gcp_doctor.py` 完整性检查。
- README / Wiki / context。
- 相关测试。

## 回归测试要求

修改远程执行或状态逻辑时，至少运行：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_remote_commands -v
.\.venv\Scripts\python.exe -m unittest tests.test_config_paths -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
