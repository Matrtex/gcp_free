import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gcp import (
    get_instance_cache_key,
    get_reroll_cooldown_policy,
    is_reroll_state_compatible,
    load_reroll_stats_from_file,
    resolve_os_config,
    summarize_text_block,
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
        self.assertEqual(cooldown, 3)
        self.assertIn("短冷却", reason)

    def test_get_reroll_cooldown_policy_uses_fast_path_after_long_stop_wait(self):
        cooldown, reason = get_reroll_cooldown_policy(had_exception=False, stop_wait_seconds=8)
        self.assertEqual(cooldown, 1)
        self.assertIn("跳过长冷却", reason)

    def test_get_reroll_cooldown_policy_uses_error_backoff_on_exception(self):
        cooldown, reason = get_reroll_cooldown_policy(had_exception=True, stop_wait_seconds=0)
        self.assertEqual(cooldown, 10)
        self.assertIn("异常", reason)


if __name__ == "__main__":
    unittest.main()
