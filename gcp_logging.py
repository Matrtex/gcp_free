from datetime import datetime
import os
from pathlib import Path
import sys
import threading


class AppLogger:
    def __init__(self):
        self._log_file = None
        self._lock = threading.Lock()

    def set_log_file(self, log_file):
        if not log_file:
            self._log_file = None
            return

        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = path

    def _write_file(self, level, message):
        if not self._log_file:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            with self._log_file.open("a", encoding="utf-8") as fh:
                fh.write(f"[{timestamp}] [{level}] {message}\n")

    def _should_use_color(self):
        force_color = os.environ.get("GCP_FREE_FORCE_COLOR", "").strip().lower()
        if force_color in {"1", "true", "yes", "on"}:
            return True
        if force_color in {"0", "false", "no", "off"}:
            return False

        stream = sys.stdout
        if not hasattr(stream, "isatty") or not stream.isatty():
            return False

        # Windows 终端对 ANSI 支持差异较大，默认保守关闭，只在明确可识别的宿主里启用。
        if os.name == "nt":
            if os.environ.get("WT_SESSION"):
                return True
            if os.environ.get("ANSICON"):
                return True
            if os.environ.get("ConEmuANSI", "").upper() == "ON":
                return True
            if os.environ.get("TERM_PROGRAM", "").lower() == "vscode":
                return True
            return False

        return True

    def _emit(self, level, prefix, message, color=None):
        text = f"{prefix} {message}"
        if color and self._should_use_color():
            print(f"{color}{text}\033[0m")
        else:
            print(text)
        self._write_file(level, message)

    def info(self, message):
        self._emit("INFO", "[信息]", message)

    def success(self, message):
        self._emit("SUCCESS", "[成功]", message, color="\033[92m")

    def warning(self, message):
        self._emit("WARNING", "[警告]", message, color="\033[93m")

    def error(self, message):
        self._emit("ERROR", "[错误]", message, color="\033[91m")


LOGGER = AppLogger()


def configure_logger(log_file=None):
    LOGGER.set_log_file(log_file)


def get_logger():
    return LOGGER
