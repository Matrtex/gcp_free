import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch
import subprocess

from gcp import (
    find_instance_by_name,
    ensure_instance_running,
    get_instance_cache_key,
    get_reroll_cooldown_policy,
    is_reroll_state_compatible,
    list_instances_via_gcloud,
    load_reroll_stats_from_file,
    resolve_os_config,
    summarize_text_block,
    wait_for_instance_status_change,
)
from gcp_models import InstanceInfo, RerollStats
from gcp_state import save_json_state


class GcpHelpersTestCase(unittest.TestCase):
    def test_resolve_os_config_supports_alias(self):
        config = resolve_os_config("ubuntu")
        self.assertEqual(config["family"], "ubuntu-2204-lts")

    def test_summarize_text_block_limits_lines(self):
        text = "a\nb\nc\nd"
        summary = summarize_text_block(text, max_lines=2, max_length=20)
        self.assertEqual(summary, "a\nb\n...")

    def test_instance_cache_key_uses_project_zone_and_name(self):
        instance = InstanceInfo(
            name="vm-1",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="Intel Broadwell",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="35.1.2.3",
        )
        self.assertEqual(
            get_instance_cache_key("demo-project", instance),
            "demo-project:us-west1-a:vm-1",
        )

    def test_load_reroll_stats_from_file_supports_resume_payload(self):
        with TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir, "reroll_state.json")
            stats = RerollStats(
                project_id="demo-project",
                instance_name="vm-1",
                zone="us-west1-a",
                start_time=123.0,
            )
            stats.attempts = 7
            stats.cpu_counter["Intel Broadwell"] = 5
            save_json_state(state_path, stats.to_dict())

            loaded_stats = load_reroll_stats_from_file(state_path)

        self.assertIsNotNone(loaded_stats)
        self.assertEqual(loaded_stats.attempts, 7)
        self.assertTrue(
            is_reroll_state_compatible(
                loaded_stats,
                project_id="demo-project",
                instance_name="vm-1",
                zone="us-west1-a",
            )
        )

    def test_get_reroll_cooldown_policy_prefers_short_cooldown_on_normal_round(self):
        cooldown, reason = get_reroll_cooldown_policy(had_exception=False, stop_wait_seconds=2)
        self.assertEqual(cooldown, 1)
        self.assertIn("短冷却", reason)

    def test_get_reroll_cooldown_policy_uses_fast_path_after_long_stop_wait(self):
        cooldown, reason = get_reroll_cooldown_policy(had_exception=False, stop_wait_seconds=8)
        self.assertEqual(cooldown, 0)
        self.assertIn("跳过长冷却", reason)

    def test_get_reroll_cooldown_policy_uses_error_backoff_on_exception(self):
        cooldown, reason = get_reroll_cooldown_policy(had_exception=True, stop_wait_seconds=0)
        self.assertEqual(cooldown, 6)
        self.assertIn("异常", reason)

    @patch("gcp.time.sleep", return_value=None)
    @patch("gcp.get_instance_with_retry")
    def test_wait_for_instance_status_change_returns_as_soon_as_status_changes(self, mock_get_instance, _mock_sleep):
        mock_get_instance.side_effect = [
            SimpleNamespace(status="STOPPED"),
            SimpleNamespace(status="PROVISIONING"),
        ]

        instance, status = wait_for_instance_status_change(
            instance_client=None,
            project_id="demo-project",
            zone="us-west1-a",
            instance_name="vm-1",
            from_statuses={"STOPPED"},
            timeout=5,
            poll_interval=0,
        )

        self.assertEqual(status, "PROVISIONING")
        self.assertEqual(instance.status, "PROVISIONING")

    @patch("gcp.wait_for_instance_status")
    @patch("gcp.wait_for_operation")
    @patch("gcp.wait_for_instance_status_change")
    @patch("gcp.start_instance_with_retry")
    @patch("gcp.get_instance_with_retry")
    def test_ensure_instance_running_skips_operation_wait_when_instance_reaches_running_fast(
        self,
        mock_get_instance,
        mock_start_instance,
        mock_wait_status_change,
        mock_wait_operation,
        mock_wait_for_instance_status,
    ):
        mock_get_instance.return_value = SimpleNamespace(status="STOPPED")
        mock_start_instance.return_value = SimpleNamespace(name="op-1")
        mock_wait_status_change.return_value = (SimpleNamespace(status="RUNNING"), "RUNNING")

        instance = ensure_instance_running(
            instance_client=None,
            project_id="demo-project",
            zone="us-west1-a",
            instance_name="vm-1",
        )

        self.assertEqual(instance.status, "RUNNING")
        mock_wait_operation.assert_not_called()
        mock_wait_for_instance_status.assert_not_called()

    @patch("gcp.find_gcloud_command", return_value="gcloud")
    @patch("gcp.subprocess.run")
    def test_list_instances_via_gcloud_parses_core_fields(self, mock_run, _mock_find_gcloud):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                '[{"name":"vm-1","zone":"https://www.googleapis.com/compute/v1/projects/demo/zones/us-west1-a",'
                '"status":"RUNNING","cpuPlatform":"AMD EPYC Milan",'
                '"networkInterfaces":[{"network":"global/networks/default","networkIP":"10.0.0.2",'
                '"accessConfigs":[{"natIP":"35.1.2.3"}]}]}]'
            ),
            stderr="",
        )

        instances = list_instances_via_gcloud("demo-project")

        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].name, "vm-1")
        self.assertEqual(instances[0].zone, "us-west1-a")
        self.assertEqual(instances[0].external_ip, "35.1.2.3")

    @patch("gcp.list_instances")
    @patch("gcp.get_instance_by_name_with_zone")
    def test_find_instance_by_name_uses_direct_get_when_zone_is_provided(
        self,
        mock_get_instance_by_zone,
        mock_list_instances,
    ):
        mock_get_instance_by_zone.return_value = InstanceInfo(
            name="vm-1",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="Intel Broadwell",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="35.1.2.3",
        )

        instance = find_instance_by_name("demo-project", "vm-1", zone="us-west1-a")

        self.assertEqual(instance.zone, "us-west1-a")
        mock_list_instances.assert_not_called()


if __name__ == "__main__":
    unittest.main()
