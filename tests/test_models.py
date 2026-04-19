import unittest

from gcp_models import InstanceInfo, RemoteConfig, RerollStats


class ModelsTestCase(unittest.TestCase):
    def test_instance_info_roundtrip(self):
        instance = InstanceInfo(
            name="test-vm",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="AMD EPYC Milan",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="35.1.2.3",
        )
        restored = InstanceInfo.from_dict(instance.to_dict())
        self.assertEqual(restored, instance)

    def test_remote_config_roundtrip(self):
        config = RemoteConfig(method="ssh", user="demo", port="2222", key="C:/id_rsa")
        restored = RemoteConfig.from_dict(config.to_dict())
        self.assertEqual(restored, config)

    def test_reroll_stats_serialization_contains_counters(self):
        stats = RerollStats(
            project_id="demo-project",
            instance_name="test-vm",
            zone="us-west1-a",
            start_time=123.0,
        )
        stats.cpu_counter["AMD EPYC Milan"] = 2
        stats.recent_results.append("AMD EPYC Milan")
        stats.last_cpu = "AMD EPYC Milan"
        payload = stats.to_dict()
        self.assertEqual(payload["cpu_counter"]["AMD EPYC Milan"], 2)
        self.assertEqual(payload["last_cpu"], "AMD EPYC Milan")
        self.assertEqual(payload["oauth_timeout_count"], 0)

    def test_reroll_stats_roundtrip(self):
        stats = RerollStats(
            project_id="demo-project",
            instance_name="test-vm",
            zone="us-west1-a",
            start_time=123.0,
            attempts=9,
            exception_count=2,
            oauth_timeout_count=1,
            compute_timeout_count=0,
            instance_stuck_count=1,
            hard_failure_count=0,
            consecutive_oauth_timeouts=0,
            success_cpu="AMD EPYC Milan",
            last_cpu="AMD EPYC Milan",
            last_error="timeout",
            last_updated=456.0,
        )
        stats.cpu_counter["AMD EPYC Milan"] = 3
        stats.recent_results.extend(["Intel Broadwell", "AMD EPYC Milan"])
        stats.recent_errors.append("timeout")

        restored = RerollStats.from_dict(stats.to_dict())

        self.assertEqual(restored, stats)


if __name__ == "__main__":
    unittest.main()
