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
    deploy_dae_config,
    format_traffic_limit_gb,
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

    def test_render_traffic_script_preserves_fractional_limit(self):
        content = render_local_script_content("net_shutdown", traffic_limit_gb=123.5)
        self.assertIn("LIMIT=123.5", content)
        self.assertNotIn("LIMIT=123\n", content)

    def test_render_traffic_script_updates_shutdown_summary_limit(self):
        content = render_local_script_content("net_shutdown", traffic_limit_gb=123.5)
        self.assertIn("流量 >= 123.5 GB", content)
        self.assertNotIn("流量 >= 180 GB", content)

    def test_format_traffic_limit_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            format_traffic_limit_gb("not-a-number")
        with self.assertRaises(ValueError):
            format_traffic_limit_gb(0)
        with self.assertRaises(ValueError):
            format_traffic_limit_gb(float("inf"))

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

    def test_deploy_dae_config_cleans_remote_temp_when_apply_fails(self):
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir, "config.dae")
            config_path.write_text("global {}\n", encoding="utf-8")
            remote_config = RemoteConfig(method="gcloud")

            with (
                patch("gcp_remote.resolve_asset_path", return_value=config_path),
                patch("gcp_remote.detect_remote_os_info", return_value={"id": "debian", "pretty_name": "Debian"}),
                patch("gcp_remote.validate_dae_config_os", return_value=True),
                patch("gcp_remote.make_remote_temp_path", return_value="/tmp/gcp_free_config_test.dae"),
                patch("gcp_remote.build_remote_upload_command", return_value=["scp", "upload"]),
                patch(
                    "gcp_remote.build_remote_exec_command",
                    side_effect=[["ssh", "apply"], ["ssh", "cleanup"]],
                ) as mock_build_exec,
                patch("gcp_remote.run_subprocess_command", side_effect=[True, False, True]) as mock_run,
            ):
                result = deploy_dae_config("demo-project", self.instance, remote_config)

        self.assertFalse(result)
        self.assertEqual(mock_build_exec.call_count, 2)
        self.assertEqual(mock_build_exec.call_args_list[1].args[3], "rm -f '/tmp/gcp_free_config_test.dae'")
        self.assertEqual(mock_run.call_args_list[2].args[1], "清理远端临时文件")

    def test_deploy_dae_config_cleans_remote_temp_when_exec_command_build_fails(self):
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir, "config.dae")
            config_path.write_text("global {}\n", encoding="utf-8")
            remote_config = RemoteConfig(method="gcloud")

            with (
                patch("gcp_remote.resolve_asset_path", return_value=config_path),
                patch("gcp_remote.detect_remote_os_info", return_value={"id": "debian", "pretty_name": "Debian"}),
                patch("gcp_remote.validate_dae_config_os", return_value=True),
                patch("gcp_remote.make_remote_temp_path", return_value="/tmp/gcp_free_config_test.dae"),
                patch("gcp_remote.build_remote_upload_command", return_value=["scp", "upload"]),
                patch(
                    "gcp_remote.build_remote_exec_command",
                    side_effect=[None, ["ssh", "cleanup"]],
                ) as mock_build_exec,
                patch("gcp_remote.run_subprocess_command", side_effect=[True, True]) as mock_run,
            ):
                result = deploy_dae_config("demo-project", self.instance, remote_config)

        self.assertFalse(result)
        self.assertEqual(mock_build_exec.call_count, 2)
        self.assertEqual(mock_build_exec.call_args_list[1].args[3], "rm -f '/tmp/gcp_free_config_test.dae'")
        self.assertEqual(mock_run.call_args_list[1].args[1], "清理远端临时文件")


if __name__ == "__main__":
    unittest.main()
