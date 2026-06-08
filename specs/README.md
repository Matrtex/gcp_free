# 技术规范索引

本目录记录维护 `gcp_free` 时必须遵守的工程约束。它不是用户快速开始文档；用户使用说明优先看 `README.md`，完整维护说明优先看 `docs/wiki.md`。

## 文档列表

- `architecture.md`：入口、模块边界、菜单 / CLI 动作表和兼容层约束。
- `account-context.md`：`gcloud`、ADC、默认项目和 quota project 的事务性切换 / 回滚语义。
- `remote-state.md`：远程执行、临时文件清理、状态文件和资源路径约束。
- `security-quality.md`：日志脱敏、CodeQL / Secret scanning、CI、发布 workflow 和许可证要求。

## 更新规则

修改以下内容时必须同步更新相关规范：

- CLI 参数、菜单动作、模块边界或入口分发。
- 账号切换、ADC 同步、默认项目、quota project 或失败回滚。
- 远程执行方式、临时文件清理、状态文件或资源覆盖路径。
- 日志脱敏、CodeQL、Secret scanning、CI、发布 workflow 或许可证。

规范和代码冲突时，以当前代码事实为准先修正文档；如果要改变代码行为，再补回归测试并更新 README、Wiki 和 context。
