import unittest
from unittest.mock import patch

from gcp_models import InstanceInfo, RemoteConfig
from gcp import build_remote_exec_command, build_remote_upload_command


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

    @patch("gcp.find_gcloud_command", return_value="D:/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd")
    def test_build_gcloud_exec_command_contains_ssh_flags(self, _mock_find_gcloud):
        config = RemoteConfig(method="gcloud")
        cmd = build_remote_exec_command("demo-project", self.instance, config, "echo ok")
        self.assertEqual(cmd[0], "D:/Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd")
        self.assertIn("--command", cmd)
        self.assertTrue(any(item.startswith("--ssh-flag=") for item in cmd))

    @patch("gcp.shutil.which", return_value="C:/Windows/System32/OpenSSH/scp.exe")
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


if __name__ == "__main__":
    unittest.main()
