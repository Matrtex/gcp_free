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
    @patch("gcp_doctor.collect_workspace_checks", return_value=[])
    @patch(
        "gcp_doctor.get_enabled_gcp_services",
        return_value=({"compute.googleapis.com", "cloudresourcemanager.googleapis.com"}, []),
    )
    @patch("gcp_doctor.get_current_gcloud_project", return_value=("demo-project", ""))
    @patch("gcp_doctor.find_python_command", return_value="/mock/python")
    @patch("gcp_doctor.find_gcloud_command", return_value="/mock/gcloud")
    @patch("gcp_doctor.subprocess.run")
    def test_doctor_reports_pass_when_tools_exist(
        self,
        mock_run,
        _mock_find_gcloud,
        _mock_find_python,
        _mock_current_project,
        _mock_services,
        _mock_workspace_checks,
        mock_which,
    ):
        mock_which.side_effect = lambda name: f"/mock/{name}" if name in {"ssh", "scp"} else None
        mock_run.side_effect = [
            self._completed(stdout="demo@example.com"),
            self._completed(stdout="token"),
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
    @patch("gcp_doctor.collect_workspace_checks", return_value=[])
    @patch("gcp_doctor.get_enabled_gcp_services", return_value=(set(), []))
    @patch("gcp_doctor.get_current_gcloud_project", return_value=("", "未设置默认项目"))
    @patch("gcp_doctor.find_python_command", return_value="/mock/python")
    @patch("gcp_doctor.find_gcloud_command", return_value="/mock/gcloud")
    @patch("gcp_doctor.subprocess.run")
    def test_doctor_reports_warn_when_adc_missing(
        self,
        mock_run,
        _mock_find_gcloud,
        _mock_find_python,
        _mock_current_project,
        _mock_services,
        _mock_workspace_checks,
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
    @patch("gcp_doctor.collect_workspace_checks", return_value=[])
    @patch("gcp_doctor.get_enabled_gcp_services", return_value=(set(), []))
    @patch("gcp_doctor.get_current_gcloud_project", return_value=("", "未设置默认项目"))
    @patch("gcp_doctor.find_python_command", return_value="/mock/python")
    @patch("gcp_doctor.find_gcloud_command", return_value="/mock/gcloud")
    @patch("gcp_doctor.subprocess.run")
    def test_doctor_warns_when_no_active_gcloud_account(
        self,
        mock_run,
        _mock_find_gcloud,
        _mock_find_python,
        _mock_current_project,
        _mock_services,
        _mock_workspace_checks,
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

    def test_collect_workspace_checks_reports_missing_optional_files_as_warn(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir)
            scripts_dir = workspace / "scripts"
            scripts_dir.mkdir()
            for script_name in ("apt.sh", "dae.sh", "net_iptables.sh", "net_shutdown.sh"):
                (scripts_dir / script_name).write_text("#!/bin/bash\n", encoding="utf-8")

            from gcp_doctor import collect_workspace_checks

            checks = collect_workspace_checks(workspace)

        status_map = {item.name: item.status for item in checks}
        self.assertEqual(status_map["scripts"], "PASS")
        self.assertEqual(status_map["config.dae"], "WARN")
        self.assertEqual(status_map["log-dir"], "PASS")
        self.assertEqual(status_map["state-dir"], "PASS")

    @patch("gcp_doctor.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["gcloud"], timeout=5))
    def test_run_command_returns_error_when_subprocess_times_out(self, _mock_run):
        ok, stdout, stderr = _run_command(["gcloud"], timeout=5)
        self.assertFalse(ok)
        self.assertEqual(stdout, "")
        self.assertIn("超时", stderr)


if __name__ == "__main__":
    unittest.main()
