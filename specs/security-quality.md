# 安全与质量规范

本文记录日志脱敏、CodeQL、Secret scanning、CI 和本地验证要求。

## 日志和异常脱敏

日志、异常摘要和状态文件中保存的异常文本不得明文保留以下内容：

- password / passwd / pwd
- secret
- token / access_token / refresh_token / id_token
- Authorization / Bearer token
- credential
- URL 中的 `user:password@host`

统一使用 `gcp_logging.redact_sensitive_text()` 或经过它的封装路径。当前关键路径包括：

- `AppLogger._emit()`
- `AppLogger._write_console()`
- `gcp_utils.summarize_exception()`
- `gcp_reroll` 的异常摘要输出

新增日志 sink 时，必须确认输出前已经脱敏。

## URL / host 判断

安全相关 host 判断不得使用任意子串匹配。例如不能用：

```python
"oauth2.googleapis.com" in str(exc).lower()
```

应使用解析后的 host 精确匹配，避免 `https://example.com/oauth2.googleapis.com/...` 这类路径子串误判。

## Code scanning

GitHub Actions `CodeQL` 已启用：

- 触发：`push` / `pull_request` 到 `master`、定时、手动。
- language：`python`
- queries：`security-extended,security-and-quality`
- category：`/language:python`

处理 Code scanning alerts 的顺序：

1. 拉取 open alerts。
2. 按规则、路径和 source/sink 分组。
3. 对真实问题修代码并补测试。
4. 对质量类告警优先用代码收敛。
5. 只有确认误报时才 dismiss。
6. dismiss 必须写清楚中文理由。

GitHub API 的 dismissed reason 使用带空格枚举，例如 `false positive`。

## Secret scanning

Secret scanning 和 push protection 应保持启用。Secret scanning alerts 应保持为 0。

如果出现 Secret scanning alert，优先级高于普通 CodeQL quality alert。处理流程：

1. 判断 secret 是否真实。
2. 如果真实，立即轮换或废弃对应凭据。
3. 从仓库历史和工作区移除泄漏内容。
4. 确认 push protection 没有被绕过。
5. 记录处理结果。

## CI 门禁

本地提交前常用验证：

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

GitHub Actions `自动检查` 会运行：

- `ruff check .`
- `py_compile` 核心 Python 文件和 `scripts/build_exe.py`
- `python -m unittest discover -s tests -v`

CI 矩阵：

- `ubuntu-latest`
- `windows-2025-vs2026`
- Python `3.10`、`3.11`、`3.12`

## 测试风格

项目使用 `unittest`，不是 `pytest`。新增测试应遵循现有 `unittest.TestCase` 风格。

测试覆盖按风险选择：

- 账号上下文、ADC、回滚：补 `tests/test_gcp_helpers.py`。
- 远程命令和临时文件：补 `tests/test_remote_commands.py`。
- 日志脱敏：补 `tests/test_logging.py`。
- GitHub Actions / 发布脚本：补 `tests/test_entrypoints_and_scripts.py`。
- 资源路径：补 `tests/test_config_paths.py`。

## 发布和 workflow 安全

正式发布 workflow 只能构建默认分支或 tag 的可信代码，不应接受外部源码 ref。

PR 评论触发 EXE 构建只能构建 PR artifact，不读取发布或签名密钥，也不创建 Release。

代码签名证书只能通过 GitHub Secrets 注入，工作流结束后必须清理临时证书文件。

## 许可证

仓库使用 MIT License，根目录 `LICENSE` 是唯一许可证源文件。发布包、README、Wiki 或其它文档提到许可证时，应指向该文件，不要复制出多个可能漂移的许可证正文。

如果未来需要更换许可证，必须同步检查：

- `LICENSE`
- `README.md`
- `docs/wiki.md`
- `contexts/context.md`
- `specs/`
