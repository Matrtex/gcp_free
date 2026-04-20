import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch
import subprocess

from gcp import (
    classify_reroll_exception,
    find_instance_by_name,
    ensure_instance_running,
    get_instance_cache_key,
    get_oauth_circuit_breaker_cooldown,
    get_instance_with_retry,
    get_reroll_cooldown_policy,
    get_soft_exception_count,
    handle_setup_cli,
    is_transient_gcp_error,
    is_reroll_state_compatible,
    list_instances_via_gcloud,
    load_reroll_stats_from_file,
    record_reroll_exception,
    resolve_os_config,
    sleep_and_detect_pause,
    summarize_text_block,
    warn_if_long_pause,
    wait_for_instance_status_change,
    wait_for_instance_status,
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
        self.assertIn("不再追加额外冷却", reason)

    def test_get_reroll_cooldown_policy_uses_error_backoff_on_exception(self):
        cooldown, reason = get_reroll_cooldown_policy(had_exception=True, stop_wait_seconds=0)
        self.assertEqual(cooldown, 6)
        self.assertIn("异常", reason)

    def test_get_reroll_cooldown_policy_triggers_oauth_circuit_breaker(self):
        cooldown, reason = get_reroll_cooldown_policy(
            had_exception=True,
            stop_wait_seconds=0,
            exception_kind="oauth_timeout",
            consecutive_oauth_timeouts=4,
        )
        self.assertEqual(cooldown, 90)
        self.assertIn("熔断", reason)

    def test_get_oauth_circuit_breaker_cooldown_caps_at_maximum(self):
        self.assertEqual(get_oauth_circuit_breaker_cooldown(2), 0)
        self.assertEqual(get_oauth_circuit_breaker_cooldown(3), 60)
        self.assertEqual(get_oauth_circuit_breaker_cooldown(7), 180)

    def test_is_transient_gcp_error_recognizes_https_connection_pool_message(self):
        exc = RuntimeError(
            "获取实例 vm-1 状态 在 4 次尝试后仍失败: "
            "HTTPSConnectionPool(host='compute.googleapis.com', port=443): "
            "Max retries exceeded with url: /compute/v1/projects/demo/zones/us-west1-a/instances/vm-1"
        )
        self.assertTrue(is_transient_gcp_error(exc))

    def test_classify_reroll_exception_distinguishes_oauth_and_instance_stuck(self):
        oauth_exc = RuntimeError(
            "获取实例 vm-1 状态 在 4 次尝试后仍失败: "
            "HTTPSConnectionPool(host='oauth2.googleapis.com', port=443): Read timed out. (read timeout=10.0)"
        )
        stop_exc = TimeoutError("等待虚拟机 vm-1 关停超时，最后状态: STOPPING")
        compute_exc = RuntimeError(
            "获取实例 vm-1 状态 在 4 次尝试后仍失败: "
            "HTTPSConnectionPool(host='compute.googleapis.com', port=443): Read timed out. (read timeout=10.0)"
        )
        hard_exc = RuntimeError("permission denied")

        self.assertEqual(classify_reroll_exception(oauth_exc), "oauth_timeout")
        self.assertEqual(classify_reroll_exception(stop_exc), "instance_stuck")
        self.assertEqual(classify_reroll_exception(compute_exc), "compute_timeout")
        self.assertEqual(classify_reroll_exception(hard_exc), "hard_failure")

    def test_record_reroll_exception_tracks_soft_and_hard_counters(self):
        stats = RerollStats(
            project_id="demo-project",
            instance_name="vm-1",
            zone="us-west1-a",
            start_time=123.0,
        )

        oauth_kind, _ = record_reroll_exception(
            stats,
            RuntimeError(
                "获取实例 vm-1 状态 在 4 次尝试后仍失败: "
                "HTTPSConnectionPool(host='oauth2.googleapis.com', port=443): Read timed out. (read timeout=10.0)"
            ),
        )
        compute_kind, _ = record_reroll_exception(
            stats,
            RuntimeError(
                "获取实例 vm-1 状态 在 4 次尝试后仍失败: "
                "HTTPSConnectionPool(host='compute.googleapis.com', port=443): Read timed out. (read timeout=10.0)"
            ),
        )
        hard_kind, _ = record_reroll_exception(stats, RuntimeError("permission denied"))

        self.assertEqual(oauth_kind, "oauth_timeout")
        self.assertEqual(compute_kind, "compute_timeout")
        self.assertEqual(hard_kind, "hard_failure")
        self.assertEqual(stats.oauth_timeout_count, 1)
        self.assertEqual(stats.compute_timeout_count, 1)
        self.assertEqual(stats.hard_failure_count, 1)
        self.assertEqual(get_soft_exception_count(stats), 2)
        self.assertEqual(stats.exception_count, 3)
        self.assertEqual(stats.consecutive_oauth_timeouts, 0)

    @patch("gcp_instance.sleep_and_detect_pause", return_value=0)
    @patch("gcp_instance.get_instance_with_retry")
    def test_wait_for_instance_status_change_returns_as_soon_as_status_changes(
        self,
        mock_get_instance,
        _mock_sleep_and_detect_pause,
    ):
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

    @patch("gcp_instance.print_info")
    @patch("gcp_instance.warn_if_long_pause", side_effect=lambda last, *_args, **_kwargs: last)
    @patch("gcp_instance.sleep_and_detect_pause", return_value=0)
    @patch("gcp_instance.time.time")
    @patch("gcp_instance.get_instance_with_retry")
    def test_wait_for_instance_status_emits_heartbeat_when_status_stays_unchanged(
        self,
        mock_get_instance,
        mock_time,
        _mock_sleep_and_detect_pause,
        _mock_warn_if_long_pause,
        mock_print_info,
    ):
        mock_get_instance.side_effect = [
            SimpleNamespace(status="STOPPING"),
            SimpleNamespace(status="STOPPING"),
            SimpleNamespace(status="STOPPED"),
        ]
        mock_time.side_effect = [0, 0, 0, 0, 0, 0, 5.1, 5.1, 5.1, 5.1, 5.1, 5.2, 5.2]

        instance, status = wait_for_instance_status(
            instance_client=None,
            project_id="demo-project",
            zone="us-west1-a",
            instance_name="vm-1",
            expected_statuses={"STOPPED"},
            timeout=10,
            poll_interval=0,
            heartbeat_interval=5,
        )

        self.assertEqual(status, "STOPPED")
        self.assertEqual(instance.status, "STOPPED")
        self.assertTrue(
            any("实例仍为 STOPPING" in args[0] for args, _kwargs in mock_print_info.call_args_list)
        )

    @patch("gcp_instance.print_warning")
    @patch("gcp_instance.warn_if_long_pause", side_effect=lambda last, *_args, **_kwargs: last)
    @patch("gcp_instance.sleep_and_detect_pause", return_value=0)
    @patch("gcp_instance.get_instance_with_retry")
    def test_wait_for_instance_status_continues_after_transient_network_error(
        self,
        mock_get_instance,
        _mock_sleep_and_detect_pause,
        _mock_warn_if_long_pause,
        mock_print_warning,
    ):
        mock_get_instance.side_effect = [
            RuntimeError(
                "获取实例 vm-1 状态 在 4 次尝试后仍失败: "
                "HTTPSConnectionPool(host='compute.googleapis.com', port=443): "
                "Max retries exceeded with url: /compute/v1/projects/demo/zones/us-west1-a/instances/vm-1"
            ),
            SimpleNamespace(status="STOPPING"),
            SimpleNamespace(status="STOPPED"),
        ]

        instance, status = wait_for_instance_status(
            instance_client=None,
            project_id="demo-project",
            zone="us-west1-a",
            instance_name="vm-1",
            expected_statuses={"STOPPED"},
            timeout=5,
            poll_interval=0,
            heartbeat_interval=0,
        )

        self.assertEqual(status, "STOPPED")
        self.assertEqual(instance.status, "STOPPED")
        self.assertTrue(
            any("临时网络错误" in args[0] for args, _kwargs in mock_print_warning.call_args_list)
        )

    @patch("gcp_instance.print_info")
    @patch("gcp_instance.warn_if_long_pause", side_effect=lambda last, *_args, **_kwargs: last)
    @patch("gcp_instance.sleep_and_detect_pause", return_value=0)
    @patch("gcp_instance.time.time")
    @patch("gcp_instance.get_instance_with_retry")
    def test_wait_for_instance_status_change_emits_heartbeat_when_status_stays_unchanged(
        self,
        mock_get_instance,
        mock_time,
        _mock_sleep_and_detect_pause,
        _mock_warn_if_long_pause,
        mock_print_info,
    ):
        mock_get_instance.side_effect = [
            SimpleNamespace(status="RUNNING"),
            SimpleNamespace(status="RUNNING"),
            SimpleNamespace(status="STOPPING"),
        ]
        mock_time.side_effect = [0, 0, 0, 0, 0, 0, 5.1, 5.1, 5.1, 5.1, 5.1, 5.2, 5.2]

        instance, status = wait_for_instance_status_change(
            instance_client=None,
            project_id="demo-project",
            zone="us-west1-a",
            instance_name="vm-1",
            from_statuses={"RUNNING"},
            timeout=10,
            poll_interval=0,
            heartbeat_interval=5,
        )

        self.assertEqual(status, "STOPPING")
        self.assertEqual(instance.status, "STOPPING")
        self.assertTrue(
            any("实例仍为 RUNNING" in args[0] for args, _kwargs in mock_print_info.call_args_list)
        )

    @patch("gcp_instance.wait_for_instance_status")
    @patch("gcp_instance.wait_for_operation")
    @patch("gcp_instance.wait_for_instance_status_change")
    @patch("gcp_instance.start_instance_with_retry")
    @patch("gcp_instance.get_instance_with_retry")
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

    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    @patch("gcp_instance.subprocess.run")
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

    @patch("gcp_instance.list_instances")
    @patch("gcp_instance.get_instance_by_name_with_zone")
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

    def test_get_instance_with_retry_passes_request_timeout(self):
        instance_client = SimpleNamespace(
            get=lambda **kwargs: SimpleNamespace(status="RUNNING", kwargs=kwargs)
        )

        result = get_instance_with_retry(
            instance_client,
            "demo-project",
            "us-west1-a",
            "vm-1",
        )

        self.assertEqual(result.kwargs["timeout"], 10)

    @patch("gcp_cli.run_setup_remote_step", side_effect=lambda _args, inst, _remote, _name, _action: inst)
    @patch("gcp_cli.build_remote_config_from_args", return_value=SimpleNamespace(method="gcloud"))
    @patch("gcp_cli.configure_firewall_non_interactive")
    @patch("gcp_cli.reroll_cpu_loop")
    @patch("gcp_cli.create_instance")
    def test_handle_setup_cli_skip_reroll_does_not_call_reroll(
        self,
        mock_create_instance,
        mock_reroll_cpu_loop,
        _mock_configure_firewall,
        _mock_build_remote_config,
        mock_run_setup_remote_step,
    ):
        mock_create_instance.return_value = InstanceInfo(
            name="vm-1",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="Intel Broadwell",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="35.1.2.3",
        )
        args = SimpleNamespace(
            project_id="demo-project",
            zone="us-west1-a",
            region="us-west1",
            os="debian-12",
            instance_name="vm-1",
            skip_reroll=True,
            traffic_script="net_iptables",
            dry_run=False,
        )

        handle_setup_cli(args)

        mock_reroll_cpu_loop.assert_not_called()
        self.assertEqual(mock_run_setup_remote_step.call_count, 4)

    @patch("gcp_cli.run_setup_remote_step", side_effect=lambda _args, inst, _remote, _name, _action: inst)
    @patch("gcp_cli.build_remote_config_from_args", return_value=SimpleNamespace(method="gcloud"))
    @patch("gcp_cli.configure_firewall_non_interactive")
    @patch("gcp_cli.reroll_cpu_loop")
    @patch("gcp_cli.create_instance")
    def test_handle_setup_cli_default_calls_reroll(
        self,
        mock_create_instance,
        mock_reroll_cpu_loop,
        _mock_configure_firewall,
        _mock_build_remote_config,
        _mock_run_setup_remote_step,
    ):
        instance = InstanceInfo(
            name="vm-1",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="Intel Broadwell",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="35.1.2.3",
        )
        mock_create_instance.return_value = instance
        mock_reroll_cpu_loop.return_value = instance
        args = SimpleNamespace(
            project_id="demo-project",
            zone="us-west1-a",
            region="us-west1",
            os="debian-12",
            instance_name="vm-1",
            skip_reroll=False,
            traffic_script="net_iptables",
            dry_run=False,
        )

        handle_setup_cli(args)

        mock_reroll_cpu_loop.assert_called_once()

    @patch("gcp_utils.print_warning")
    @patch("gcp_utils.time.time", side_effect=[65, 65])
    def test_warn_if_long_pause_emits_clear_warning(self, _mock_time, mock_print_warning):
        current_time = warn_if_long_pause(0, "等待实例 vm-1 进入 STOPPED")
        self.assertEqual(current_time, 65)
        self.assertTrue(
            any("检测到长时间挂起/冻结" in args[0] for args, _kwargs in mock_print_warning.call_args_list)
        )

    @patch("gcp_utils.print_warning")
    @patch("gcp_utils.time.sleep", return_value=None)
    @patch("gcp_utils.time.time", side_effect=[100, 200])
    def test_sleep_and_detect_pause_emits_warning_when_sleep_is_suspended(
        self,
        _mock_time,
        _mock_sleep,
        mock_print_warning,
    ):
        elapsed = sleep_and_detect_pause(1, "等待实例 vm-1 进入 STOPPED", threshold=30)

        self.assertEqual(elapsed, 100)
        self.assertTrue(
            any("检测到本地进程可能被暂停/系统睡眠" in args[0] for args, _kwargs in mock_print_warning.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
