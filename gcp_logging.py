from datetime import datetime
from pathlib import Path
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

    def _emit(self, level, prefix, message, color=None):
        text = f"{prefix} {message}"
        if color:
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
