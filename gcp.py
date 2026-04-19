import sys

import gcp_app as _app


def _run() -> None:
    try:
        _app.configure_stdio()
        args = _app.parse_args()
        if not _app.run_cli(args):
            _app.main()
    except KeyboardInterrupt:
        print("\n[用户终止] 脚本已停止。")
    except Exception as exc:
        _app.print_error(f"发生异常: {_app.summarize_exception(exc)}")
        _app.LOGGER.error(_app.traceback.format_exc())


if __name__ == "__main__":
    _run()
else:
    # 兼容旧测试和外部脚本的 `import gcp`：直接暴露聚合实现模块。
    sys.modules[__name__] = _app
