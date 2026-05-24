from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gcp_models import InstanceInfo, RemoteConfig
from gcp import (
    build_remote_exec_command,
    build_remote_status_command,
    build_remote_upload_command,
    cleanup_temp_upload_file,
    prepare_local_script_for_upload,
    render_local_script_content,
)


class RemoteCommandTestCase(unittest.TestCase):
    def setUp(self):
        self.instance = InstanceInfo(
            name="test-vm",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="AMD EPYC Milan",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="35.1.2.3",
        )

    @patch("gcp_remote.find_gcloud_command", return_value="D:/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd")
    def test_build_gcloud_exec_command_contains_ssh_flags(self, _mock_find_gcloud):
        config = RemoteConfig(method="gcloud")
        cmd = build_remote_exec_command("demo-project", self.instance, config, "echo ok")
        self.assertEqual(cmd[0], "D:/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd")
        self.assertIn("--command", cmd)
        self.assertTrue(any(item.startswith("--ssh-flag=") for item in cmd))

    @patch("gcp_remote.shutil.which", return_value="C:/Windows/System32/OpenSSH/scp.exe")
    def test_build_ssh_upload_command_contains_port_and_key(self, _mock_which):
        config = RemoteConfig(method="ssh", user="demo", port="2222", key="C:/id_rsa")
        cmd = build_remote_upload_command(
            "demo-project",
            self.instance,
            config,
            "local.sh",
            "/tmp/local.sh",
        )
        self.assertIn("-P", cmd)
        self.assertIn("2222", cmd)
        self.assertIn("-i", cmd)
        self.assertIn("C:/id_rsa", cmd)
        self.assertTrue(any(item == "StrictHostKeyChecking=accept-new" for item in cmd))

    def test_render_traffic_script_injects_configured_limit(self):
        content = render_local_script_content("net_shutdown", traffic_limit_gb=123)
        self.assertIn("LIMIT=123", content)
        self.assertNotIn("LIMIT=180", content)

    @patch("gcp_remote.get_local_script_path")
    def test_prepare_local_script_for_upload_normalizes_shell_line_endings(self, mock_get_local_script_path):
        with TemporaryDirectory() as tmp_dir:
            script_path = Path(tmp_dir, "dae.sh")
            script_path.write_bytes(b"#!/usr/bin/env sh\r\necho ok\r\n")
            mock_get_local_script_path.return_value = str(script_path)

            upload_path, source_path = prepare_local_script_for_upload("dae")
            try:
                self.assertEqual(source_path, str(script_path))
                self.assertNotEqual(upload_path, str(script_path))
                self.assertEqual(Path(upload_path).read_bytes(), b"#!/usr/bin/env sh\necho ok\n")
            finally:
                cleanup_temp_upload_file(upload_path, source_path)

    def test_build_remote_status_command_contains_expected_tools(self):
        command = build_remote_status_command()
        self.assertIn("vnstat", command)
        self.assertIn("systemctl", command)
        self.assertIn("df -h /", command)
        self.assertIn("uptime", command)


if __name__ == "__main__":
    unittest.main()
