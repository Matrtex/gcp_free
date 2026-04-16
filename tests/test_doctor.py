import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gcp_doctor import _run_command, find_gcloud_command, find_python_command, run_doctor


class DoctorTestCase(unittest.TestCase):
    def _completed(self, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)

    @patch("gcp_doctor.shutil.which")
    @patch("gcp_doctor.find_python_command", return_value="/mock/python")
    @patch("gcp_doctor.find_gcloud_command", return_value="/mock/gcloud")
    @patch("gcp_doctor.subprocess.run")
    def test_doctor_reports_pass_when_tools_exist(
        self,
        mock_run,
        _mock_find_gcloud,
        _mock_find_python,
        mock_which,
    ):
        mock_which.side_effect = lambda name: f"/mock/{name}" if name in {"ssh", "scp"} else None
        mock_run.side_effect = [
            self._completed(stdout="demo@example.com"),
            self._completed(stdout="token"),
            self._completed(stdout="demo-project"),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            requirements = Path(tmp_dir, "requirements.txt")
            requirements.write_text("google-cloud-compute\n", encoding="utf-8")
            checks = run_doctor(requirements, project_id="demo-project")

        status_map = {item.name: item.status for item in checks}
        message_map = {item.name: item.message for item in checks}
        self.assertEqual(status_map["gcloud"], "PASS")
        self.assertEqual(status_map["python"], "PASS")
        self.assertEqual(status_map["gcloud-auth"], "PASS")
        self.assertEqual(status_map["adc"], "PASS")
        self.assertEqual(status_map["requirements"], "PASS")
        self.assertIn("demo@example.com", message_map["gcloud-auth"])

    @patch("gcp_doctor.shutil.which")
    @patch("gcp_doctor.find_python_command", return_value="/mock/python")
    @patch("gcp_doctor.find_gcloud_command", return_value="/mock/gcloud")
    @patch("gcp_doctor.subprocess.run")
    def test_doctor_reports_warn_when_adc_missing(
        self,
        mock_run,
        _mock_find_gcloud,
        _mock_find_python,
        mock_which,
    ):
        mock_which.side_effect = lambda name: None
        mock_run.side_effect = [
            self._completed(stdout="demo@example.com"),
            self._completed(returncode=1, stderr="adc missing"),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            requirements = Path(tmp_dir, "requirements.txt")
            requirements.write_text("google-cloud-compute\n", encoding="utf-8")
            checks = run_doctor(str(requirements))

        status_map = {item.name: item.status for item in checks}
        self.assertEqual(status_map["adc"], "WARN")
        self.assertEqual(status_map["ssh"], "WARN")

    @patch("gcp_doctor.shutil.which", return_value=None)
    def test_find_gcloud_command_prefers_env_override(self, _mock_which):
        with patch.dict("gcp_doctor.os.environ", {"GCP_FREE_GCLOUD_COMMAND": "D:/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"}):
            self.assertEqual(
                find_gcloud_command(),
                "D:/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd",
            )

    @patch("gcp_doctor.shutil.which", return_value=None)
    @patch("gcp_doctor.Path.exists", return_value=True)
    def test_find_python_command_prefers_current_interpreter(self, _mock_exists, _mock_which):
        with patch("gcp_doctor.sys.executable", "C:/Python/python.exe"):
            self.assertEqual(find_python_command(), "C:/Python/python.exe")

    @patch("gcp_doctor.shutil.which")
    @patch("gcp_doctor.find_python_command", return_value="/mock/python")
    @patch("gcp_doctor.find_gcloud_command", return_value="/mock/gcloud")
    @patch("gcp_doctor.subprocess.run")
    def test_doctor_warns_when_no_active_gcloud_account(
        self,
        mock_run,
        _mock_find_gcloud,
        _mock_find_python,
        mock_which,
    ):
        mock_which.side_effect = lambda name: None
        mock_run.side_effect = [
            self._completed(stdout=""),
            self._completed(stdout="(unset)"),
            self._completed(stdout="token"),
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            requirements = Path(tmp_dir, "requirements.txt")
            requirements.write_text("google-cloud-compute\n", encoding="utf-8")
            checks = run_doctor(str(requirements))

        status_map = {item.name: item.status for item in checks}
        self.assertEqual(status_map["gcloud-auth"], "WARN")

    @patch("gcp_doctor.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["gcloud"], timeout=5))
    def test_run_command_returns_error_when_subprocess_times_out(self, _mock_run):
        ok, stdout, stderr = _run_command(["gcloud"], timeout=5)
        self.assertFalse(ok)
        self.assertEqual(stdout, "")
        self.assertIn("超时", stderr)


if __name__ == "__main__":
    unittest.main()
