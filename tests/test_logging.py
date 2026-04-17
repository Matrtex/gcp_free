import io
import unittest
from unittest.mock import patch

from gcp_logging import AppLogger


class FakeStdout(io.StringIO):
    def __init__(self, *, is_tty):
        super().__init__()
        self._is_tty = is_tty

    def isatty(self):
        return self._is_tty


class Cp1252Stdout:
    def __init__(self, *, is_tty):
        self._is_tty = is_tty
        self.encoding = "cp1252"
        self._chunks = []

    def isatty(self):
        return self._is_tty

    def write(self, text):
        text.encode(self.encoding)
        self._chunks.append(text)
        return len(text)

    def flush(self):
        return None

    def getvalue(self):
        return "".join(self._chunks)


class LoggingTestCase(unittest.TestCase):
    def test_logger_disables_color_when_stdout_is_not_tty(self):
        logger = AppLogger()
        stream = FakeStdout(is_tty=False)

        with patch("sys.stdout", stream), patch.dict("os.environ", {}, clear=False):
            logger.warning("测试消息")

        self.assertEqual(stream.getvalue(), "[警告] 测试消息\n")

    def test_logger_honors_force_color_override(self):
        logger = AppLogger()
        stream = FakeStdout(is_tty=False)

        with patch("sys.stdout", stream), patch.dict("os.environ", {"GCP_FREE_FORCE_COLOR": "1"}, clear=False):
            logger.warning("测试消息")

        self.assertIn("\033[93m[警告] 测试消息\033[0m", stream.getvalue())

    def test_logger_falls_back_when_console_encoding_cannot_print_chinese(self):
        logger = AppLogger()
        stream = Cp1252Stdout(is_tty=False)

        with patch("sys.stdout", stream), patch.dict("os.environ", {}, clear=False):
            logger.warning("测试消息")

        self.assertIn(r"\u8b66\u544a", stream.getvalue())
        self.assertIn(r"\u6d4b\u8bd5\u6d88\u606f", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
