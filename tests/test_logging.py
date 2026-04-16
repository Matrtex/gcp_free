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


if __name__ == "__main__":
    unittest.main()
