import unittest
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch
import subprocess

import gcp_cli
import gcp_instance
from gcp import (
    classify_reroll_exception,
    clear_adc_account_cache,
    configure_firewall_non_interactive,
    configure_stdio,
    create_instance,
    add_allow_all_ingress,
    add_deny_cdn_egress,
    delete_deny_cdn_egress,
    delete_free_resources,
    delete_managed_firewall_rules,
    find_instance_by_name,
    ensure_instance_running,
    ensure_instance_stopped,
    get_current_adc_account,
    get_instance_cache_key,
    get_oauth_circuit_breaker_cooldown,
    get_instance_with_retry,
    get_reroll_cooldown_policy,
    get_soft_exception_count,
    LOGIN_NEW_ACCOUNT_MARKER,
    handle_setup_cli,
    handle_update_gcp_ip_ranges_cli,
    handle_login_account_cli,
    handle_switch_account_cli,
    handle_switch_ip_cli,
    handle_enable_root_login_cli,
    handle_change_root_password_cli,
    handle_install_3xui_cli,
    handle_firewall_cli,
    handle_reroll_ip_amd_cli,
    handle_reroll_ip_cli,
    is_transient_gcp_error,
    is_already_exists_error,
    is_operation_in_progress_error,
    is_oauth_timeout_error,
    is_reroll_state_compatible,
    is_ip_target_met,
    is_target_cpu,
    list_instances_via_gcloud,
    load_reroll_stats_from_file,
    login_gcloud_account,
    list_active_projects_via_gcloud,
    list_gcloud_accounts_via_gcloud,
    message_mentions_exact_host,
    parse_args,
    prepare_cli_account_context,
    record_reroll_exception,
    read_cdn_ips,
    resolve_os_config,
    select_gcp_project,
    select_startup_gcloud_account,
    switch_gcloud_account,
    switch_external_ip_without_restart,
    sync_adc_account,
    menu_switch_account_action,
    run_cli,
    sleep_and_detect_pause,
    summarize_text_block,
    warn_if_long_pause,
    wait_for_instance_status_change,
    wait_for_instance_status,
)
from gcp_models import InstanceInfo, RerollStats
from gcp_state import save_json_state


class GcpHelpersTestCase(unittest.TestCase):
    def setUp(self):
        clear_adc_account_cache()

    def test_resolve_os_config_supports_alias(self):
        config = resolve_os_config("ubuntu")
        self.assertEqual(config["family"], "ubuntu-2204-lts")

    def test_summarize_text_block_limits_lines(self):
        text = "a\nb\nc\nd"
        summary = summarize_text_block(text, max_lines=2, max_length=20)
        self.assertEqual(summary, "a\nb\n...")

    @patch("gcp_firewall.resolve_asset_path")
    def test_read_cdn_ips_resolves_default_file_from_runtime_assets(self, mock_resolve_asset_path):
        with TemporaryDirectory() as tmp_dir:
            cdnip_path = Path(tmp_dir, "cdnip.txt")
            cdnip_path.write_text(
                "# GCP CDN ranges\n1.1.1.0/24 comment\n\n2.2.2.0/24 # inline\n",
                encoding="utf-8",
            )
            mock_resolve_asset_path.return_value = cdnip_path

            ip_ranges = read_cdn_ips()

        self.assertEqual(ip_ranges, ["1.1.1.0/24", "2.2.2.0/24"])

    @patch("gcp_firewall.resolve_asset_path")
    def test_read_cdn_ips_normalizes_bare_ips_to_cidr(self, mock_resolve_asset_path):
        with TemporaryDirectory() as tmp_dir:
            cdnip_path = Path(tmp_dir, "cdnip.txt")
            cdnip_path.write_text("1.1.1.1\n2.2.2.7/24\n", encoding="utf-8")
            mock_resolve_asset_path.return_value = cdnip_path

            ip_ranges = read_cdn_ips()

        self.assertEqual(ip_ranges, ["1.1.1.1/32", "2.2.2.0/24"])

    @patch("gcp_firewall.resolve_asset_path")
    def test_read_cdn_ips_rejects_invalid_ip_range(self, mock_resolve_asset_path):
        with TemporaryDirectory() as tmp_dir:
            cdnip_path = Path(tmp_dir, "cdnip.txt")
            cdnip_path.write_text("1.1.1.0/24\nnot-an-ip\n", encoding="utf-8")
            mock_resolve_asset_path.return_value = cdnip_path

            with self.assertRaisesRegex(ValueError, "不是有效的 IP 或 CIDR"):
                read_cdn_ips()

    @patch("gcp_firewall.add_allow_all_ingress", return_value=False)
    def test_configure_firewall_non_interactive_raises_when_rule_creation_fails(
        self,
        _mock_add_allow_all_ingress,
    ):
        with self.assertRaises(RuntimeError):
            configure_firewall_non_interactive(
                "demo-project",
                "global/networks/default",
                allow_all_ingress=True,
            )

    @patch("gcp_firewall.add_deny_cdn_egress")
    @patch("gcp_firewall.read_cdn_ips", return_value=[])
    def test_configure_firewall_non_interactive_raises_when_deny_cdn_ip_list_empty(
        self,
        _mock_read_cdn_ips,
        mock_add_deny_cdn_egress,
    ):
        with self.assertRaises(RuntimeError):
            configure_firewall_non_interactive(
                "demo-project",
                "global/networks/default",
                deny_cdn_egress=True,
            )

        mock_add_deny_cdn_egress.assert_not_called()

    @patch("gcp_firewall.add_deny_cdn_egress", return_value=True)
    @patch("gcp_firewall.read_cdn_ips", return_value=[f"10.0.{index // 256}.{index % 256}/32" for index in range(300)])
    def test_configure_firewall_non_interactive_passes_all_cdn_ranges_without_truncation(
        self,
        _mock_read_cdn_ips,
        mock_add_deny_cdn_egress,
    ):
        configure_firewall_non_interactive(
            "demo-project",
            "global/networks/default",
            deny_cdn_egress=True,
        )

        passed_ranges = mock_add_deny_cdn_egress.call_args.args[1]
        self.assertEqual(len(passed_ranges), 300)

    def test_configure_stdio_uses_utf8_backslashreplace(self):
        fake_stdout = Mock()
        fake_stderr = Mock()

        with patch("gcp_utils.sys", SimpleNamespace(stdout=fake_stdout, stderr=fake_stderr)):
            configure_stdio()

        expected_call = call(
            encoding="utf-8",
            errors="backslashreplace",
            line_buffering=True,
            write_through=True,
        )
        self.assertEqual(fake_stdout.reconfigure.call_args, expected_call)
        self.assertEqual(fake_stderr.reconfigure.call_args, expected_call)

    def test_is_already_exists_error_checks_wrapped_cause(self):
        try:
            try:
                raise RuntimeError(
                    "409 POST https://compute.googleapis.com/compute/v1/projects/demo/global/firewalls: "
                    "The resource 'projects/demo/global/firewalls/allow-all-ingress-custom' already exists"
                )
            except RuntimeError as exc:
                raise RuntimeError("创建防火墙规则 allow-all-ingress-custom 在 4 次尝试后仍失败") from exc
        except RuntimeError as wrapped_exc:
            self.assertTrue(is_already_exists_error(wrapped_exc, "allow-all-ingress-custom"))
            self.assertFalse(is_already_exists_error(wrapped_exc, "deny-cdn-egress-custom"))

    def test_operation_conflict_does_not_retry_already_exists(self):
        self.assertFalse(
            is_operation_in_progress_error(
                RuntimeError("409 POST https://compute.googleapis.com/compute/v1/projects/demo: already exists")
            )
        )
        self.assertTrue(
            is_operation_in_progress_error(
                RuntimeError("409 POST https://compute.googleapis.com/compute/v1/projects/demo: operation is already in progress")
            )
        )
        self.assertTrue(
            is_operation_in_progress_error(
                RuntimeError(
                    "400 POST https://compute.googleapis.com/compute/v1/projects/demo/zones/us-west1-a/"
                    "instances/vm-1/stop: A stop operation is already in progress for this instance"
                )
            )
        )

    @patch("gcp_firewall.print_success")
    @patch("gcp_firewall.wait_for_global_operation")
    @patch("gcp_firewall.insert_firewall_with_retry")
    @patch("gcp_firewall.firewalls_client")
    def test_add_allow_all_ingress_treats_existing_rule_as_success(
        self,
        mock_firewalls_client,
        mock_insert_firewall,
        mock_wait_operation,
        _mock_print_success,
    ):
        compatible_rule = SimpleNamespace(
            direction="INGRESS",
            network="https://www.googleapis.com/compute/v1/projects/demo/global/networks/default",
            priority=1000,
            source_ranges=["0.0.0.0/0"],
            allowed=[SimpleNamespace(ip_protocol="all")],
        )
        mock_firewalls_client.return_value = SimpleNamespace(
            get=Mock(side_effect=[RuntimeError("404 not found"), compatible_rule])
        )

        def raise_wrapped_existing(*_args, **_kwargs):
            try:
                raise RuntimeError(
                    "The resource 'projects/demo/global/firewalls/allow-all-ingress-custom' already exists"
                )
            except RuntimeError as exc:
                raise RuntimeError("创建防火墙规则 allow-all-ingress-custom 在 4 次尝试后仍失败") from exc

        mock_insert_firewall.side_effect = raise_wrapped_existing

        self.assertTrue(add_allow_all_ingress("demo-project", "global/networks/default"))
        mock_wait_operation.assert_not_called()

    @patch("gcp_firewall.insert_firewall_with_retry")
    @patch("gcp_firewall.firewalls_client")
    def test_add_allow_all_ingress_rejects_existing_rule_on_other_network(
        self,
        mock_firewalls_client,
        mock_insert_firewall,
    ):
        incompatible_rule = SimpleNamespace(
            direction="INGRESS",
            network="https://www.googleapis.com/compute/v1/projects/demo/global/networks/other",
            priority=1000,
            source_ranges=["0.0.0.0/0"],
            allowed=[SimpleNamespace(ip_protocol="all")],
        )
        mock_firewalls_client.return_value = SimpleNamespace(get=Mock(return_value=incompatible_rule))

        self.assertFalse(add_allow_all_ingress("demo-project", "global/networks/default"))
        mock_insert_firewall.assert_not_called()

    @patch("gcp_firewall.ensure_deny_cdn_rebuild_scope_safe", return_value=True)
    @patch("gcp_firewall.delete_firewall_rule", return_value=True)
    @patch("gcp_firewall.wait_for_global_operation")
    @patch("gcp_firewall.insert_firewall_with_retry", return_value=SimpleNamespace(name="op-1"))
    @patch("gcp_firewall.firewalls_client")
    def test_add_deny_cdn_egress_splits_large_ip_lists(
        self,
        mock_firewalls_client,
        mock_insert_firewall,
        _mock_wait_operation,
        mock_delete_rule,
        _mock_ensure_rebuild_safe,
    ):
        mock_firewalls_client.return_value = SimpleNamespace(
            get=Mock(side_effect=RuntimeError("404 not found")),
            list=Mock(return_value=[]),
        )
        ip_ranges = [f"10.0.{index // 256}.{index % 256}/32" for index in range(300)]

        self.assertTrue(add_deny_cdn_egress("demo-project", ip_ranges, "global/networks/default"))

        mock_delete_rule.assert_not_called()
        created_rules = [call_args.args[2] for call_args in mock_insert_firewall.call_args_list]
        self.assertEqual([rule.name for rule in created_rules], [
            "deny-cdn-egress-custom-001",
            "deny-cdn-egress-custom-002",
        ])
        self.assertEqual(len(created_rules[0].destination_ranges), 256)
        self.assertEqual(len(created_rules[1].destination_ranges), 44)

    @patch("gcp_firewall.insert_firewall_with_retry")
    @patch("gcp_firewall.firewalls_client")
    def test_add_deny_cdn_egress_rejects_rebuild_when_existing_rule_is_on_other_network(
        self,
        mock_firewalls_client,
        mock_insert_firewall,
    ):
        incompatible_rule = SimpleNamespace(
            name="deny-cdn-egress-custom",
            direction="EGRESS",
            network="https://www.googleapis.com/compute/v1/projects/demo/global/networks/other",
            priority=900,
            destination_ranges=["10.0.0.1/32"],
            denied=[SimpleNamespace(ip_protocol="all")],
        )
        mock_firewalls_client.return_value = SimpleNamespace(list=Mock(return_value=[incompatible_rule]))

        self.assertFalse(add_deny_cdn_egress("demo-project", ["10.0.0.1/32"], "global/networks/default"))
        mock_insert_firewall.assert_not_called()

    @patch("gcp_firewall.ensure_deny_cdn_rebuild_scope_safe", return_value=True)
    @patch("gcp_firewall.insert_firewall_with_retry")
    @patch("gcp_firewall.delete_firewall_rule", return_value=False)
    @patch("gcp_firewall.firewalls_client")
    def test_add_deny_cdn_egress_stops_when_old_rules_cannot_be_cleaned(
        self,
        mock_firewalls_client,
        mock_delete_rule,
        mock_insert_firewall,
        _mock_ensure_rebuild_safe,
    ):
        mock_firewalls_client.return_value = SimpleNamespace(
            list=Mock(
                return_value=[
                    SimpleNamespace(
                        name="deny-cdn-egress-custom",
                        network="https://www.googleapis.com/compute/v1/projects/demo/global/networks/default",
                        destination_ranges=["10.0.0.1/32"],
                    )
                ]
            )
        )
        self.assertFalse(add_deny_cdn_egress("demo-project", ["10.0.0.1/32"], "global/networks/default"))

        mock_delete_rule.assert_called_once_with("demo-project", "deny-cdn-egress-custom")
        mock_insert_firewall.assert_not_called()

    @patch("gcp_firewall.ensure_deny_cdn_rebuild_scope_safe", return_value=True)
    @patch("gcp_firewall.delete_firewall_rule", return_value=True)
    @patch("gcp_firewall.wait_for_global_operation")
    @patch("gcp_firewall.insert_firewall_with_retry")
    @patch("gcp_firewall.firewalls_client")
    def test_add_deny_cdn_egress_rolls_back_when_rebuild_fails(
        self,
        mock_firewalls_client,
        mock_insert_firewall,
        _mock_wait_operation,
        mock_delete_rule,
        _mock_ensure_rebuild_safe,
    ):
        mock_firewalls_client.return_value = SimpleNamespace(
            get=Mock(side_effect=RuntimeError("404 not found")),
            list=Mock(
                return_value=[
                    SimpleNamespace(
                        name="deny-cdn-egress-custom",
                        network="https://www.googleapis.com/compute/v1/projects/demo/global/networks/default",
                        destination_ranges=["10.0.0.1/32"],
                    )
                ]
            ),
        )
        mock_insert_firewall.side_effect = [
            SimpleNamespace(name="create-op-1"),
            RuntimeError("boom"),
            SimpleNamespace(name="restore-op"),
        ]
        ip_ranges = [f"10.0.{index // 256}.{index % 256}/32" for index in range(300)]

        self.assertFalse(add_deny_cdn_egress("demo-project", ip_ranges, "global/networks/default"))

        self.assertEqual(
            mock_delete_rule.call_args_list,
            [
                call("demo-project", "deny-cdn-egress-custom"),
                call("demo-project", "deny-cdn-egress-custom-001"),
            ],
        )
        created_rules = [call_args.args[2].name for call_args in mock_insert_firewall.call_args_list]
        self.assertEqual(
            created_rules,
            [
                "deny-cdn-egress-custom-001",
                "deny-cdn-egress-custom-002",
                "deny-cdn-egress-custom",
            ],
        )

    @patch("gcp_firewall.delete_deny_cdn_egress", return_value=True)
    def test_configure_firewall_non_interactive_deletes_deny_cdn_rule(self, mock_delete_deny):
        configure_firewall_non_interactive(
            "demo-project",
            "global/networks/default",
            delete_deny_cdn=True,
        )

        mock_delete_deny.assert_called_once_with("demo-project", network="global/networks/default")

    @patch("gcp_firewall.firewalls_client")
    @patch("gcp_firewall.delete_firewall_rule", return_value=True)
    def test_delete_deny_cdn_egress_deletes_base_and_split_rules(self, mock_delete_rule, mock_firewalls_client):
        mock_firewalls_client.return_value = SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(name="allow-all-ingress-custom"),
                SimpleNamespace(name="deny-cdn-egress-custom"),
                SimpleNamespace(name="deny-cdn-egress-custom-001"),
                SimpleNamespace(name="deny-cdn-egress-custom-extra"),
            ]
        )

        self.assertTrue(delete_deny_cdn_egress("demo-project"))

        self.assertEqual(
            mock_delete_rule.call_args_list,
            [
                call("demo-project", "deny-cdn-egress-custom"),
                call("demo-project", "deny-cdn-egress-custom-001"),
            ],
        )

    @patch("gcp_firewall.firewalls_client")
    @patch("gcp_firewall.delete_firewall_rule", return_value=True)
    def test_delete_deny_cdn_egress_filters_by_target_network(self, mock_delete_rule, mock_firewalls_client):
        mock_firewalls_client.return_value = SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(
                    name="deny-cdn-egress-custom",
                    network="https://www.googleapis.com/compute/v1/projects/demo/global/networks/default",
                ),
                SimpleNamespace(
                    name="deny-cdn-egress-custom-001",
                    network="https://www.googleapis.com/compute/v1/projects/demo/global/networks/other",
                ),
            ]
        )

        self.assertTrue(delete_deny_cdn_egress("demo-project", network="global/networks/default"))

        mock_delete_rule.assert_called_once_with("demo-project", "deny-cdn-egress-custom")

    @patch("gcp_firewall.firewalls_client")
    @patch("gcp_firewall.delete_firewall_rule", return_value=True)
    def test_delete_managed_firewall_rules_deletes_known_rules(self, mock_delete_rule, mock_firewalls_client):
        mock_firewalls_client.return_value = SimpleNamespace(
            list=lambda **_kwargs: [
                SimpleNamespace(name="allow-all-ingress-custom"),
                SimpleNamespace(name="deny-cdn-egress-custom-001"),
                SimpleNamespace(name="unmanaged-rule"),
            ]
        )

        self.assertTrue(delete_managed_firewall_rules("demo-project"))

        self.assertEqual(
            mock_delete_rule.call_args_list,
            [
                call("demo-project", "allow-all-ingress-custom"),
                call("demo-project", "deny-cdn-egress-custom-001"),
            ],
        )

    @patch("gcp_firewall.delete_managed_firewall_rules", return_value=True)
    @patch("gcp_firewall.list_instances", return_value=[])
    @patch("gcp_firewall.delete_disks_if_needed", return_value=False)
    @patch("gcp_firewall.wait_for_operation")
    @patch("gcp_firewall.delete_instance_with_retry", return_value=SimpleNamespace(name="delete-op"))
    @patch("gcp_firewall.get_instance_with_retry")
    @patch("gcp_firewall.instances_client", return_value=SimpleNamespace())
    def test_delete_free_resources_reports_disk_cleanup_failure(
        self,
        _mock_instances_client,
        mock_get_instance,
        _mock_delete_instance,
        _mock_wait_operation,
        _mock_delete_disks,
        _mock_list_instances,
        _mock_delete_firewall_rules,
    ):
        instance_info = InstanceInfo(
            name="vm-1",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="Intel Broadwell",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="35.1.2.3",
        )
        mock_get_instance.return_value = SimpleNamespace(
            disks=[SimpleNamespace(source="projects/demo/zones/us-west1-a/disks/vm-1")]
        )

        self.assertFalse(delete_free_resources("demo-project", instance_info, confirmed=True))

    @patch("gcp_firewall.delete_managed_firewall_rules", return_value=False)
    @patch("gcp_firewall.list_instances", return_value=[])
    @patch("gcp_firewall.delete_disks_if_needed", return_value=True)
    @patch("gcp_firewall.wait_for_operation")
    @patch("gcp_firewall.delete_instance_with_retry", return_value=SimpleNamespace(name="delete-op"))
    @patch("gcp_firewall.get_instance_with_retry", return_value=SimpleNamespace(disks=[]))
    @patch("gcp_firewall.instances_client", return_value=SimpleNamespace())
    def test_delete_free_resources_reports_firewall_cleanup_failure(
        self,
        _mock_instances_client,
        _mock_get_instance,
        _mock_delete_instance,
        _mock_wait_operation,
        _mock_delete_disks,
        _mock_list_instances,
        _mock_delete_firewall_rules,
    ):
        instance_info = InstanceInfo(
            name="vm-1",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="Intel Broadwell",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="35.1.2.3",
        )

        self.assertFalse(delete_free_resources("demo-project", instance_info, confirmed=True))

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

    @patch("gcp_instance.print")
    @patch("gcp_instance.build_instance_info")
    @patch("gcp_instance.get_instance_with_retry")
    @patch("gcp_instance.wait_for_operation", return_value=SimpleNamespace(error=None))
    @patch("gcp_instance.insert_instance_with_retry")
    @patch("gcp_instance.get_image_from_family_with_retry")
    @patch("gcp_instance.images_client", return_value=SimpleNamespace())
    @patch("gcp_instance.instances_client", return_value=SimpleNamespace())
    def test_create_instance_sets_default_network_field(
        self,
        _mock_instances_client,
        _mock_images_client,
        mock_get_image,
        mock_insert_instance,
        _mock_wait_operation,
        mock_get_instance,
        mock_build_instance_info,
        _mock_print,
    ):
        expected_instance = InstanceInfo(
            name="vm-1",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="AMD Rome",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="35.1.2.3",
        )
        mock_get_image.return_value = SimpleNamespace(self_link="projects/debian-cloud/global/images/debian-12")
        mock_insert_instance.return_value = SimpleNamespace(name="insert-op")
        mock_get_instance.return_value = SimpleNamespace(
            network_interfaces=[
                SimpleNamespace(
                    access_configs=[
                        SimpleNamespace(nat_i_p="35.1.2.3"),
                    ],
                )
            ]
        )
        mock_build_instance_info.return_value = expected_instance

        result = create_instance(
            "demo-project",
            "us-west1-a",
            {"name": "Debian 12", "project": "debian-cloud", "family": "debian-12"},
            instance_name="vm-1",
        )

        self.assertEqual(result, expected_instance)
        instance_resource = mock_insert_instance.call_args.args[3]
        network_interface = instance_resource.network_interfaces[0]
        self.assertEqual(network_interface.network, "global/networks/default")
        self.assertNotEqual(getattr(network_interface, "name", None), "global/networks/default")

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
            consecutive_auth_timeouts=4,
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

    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    @patch("gcp_instance.subprocess.run")
    def test_list_gcloud_accounts_via_gcloud_parses_active_flag(self, mock_run, _mock_find_gcloud):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='[{"account":"active@example.com","status":"ACTIVE"},{"account":"other@example.com","status":""}]',
            stderr="",
        )

        accounts = list_gcloud_accounts_via_gcloud()

        self.assertEqual(accounts[0]["account"], "active@example.com")
        self.assertTrue(accounts[0]["active"])
        self.assertFalse(accounts[1]["active"])

    @patch("gcp_instance.get_current_gcloud_account", return_value="old@example.com")
    @patch("gcp_instance.clear_google_cloud_client_caches")
    @patch(
        "gcp_instance.get_adc_account_email",
        side_effect=[
            ("other@example.com", ""),
            ("demo@example.com", ""),
            ("demo@example.com", ""),
        ],
    )
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    @patch("gcp_instance.subprocess.run")
    def test_switch_gcloud_account_syncs_adc_and_clears_client_cache(
        self,
        mock_run,
        _mock_find_gcloud,
        _mock_adc_account,
        mock_clear_caches,
        _mock_current_account,
    ):
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]

        switched_account = switch_gcloud_account("demo@example.com", sync_adc=True, no_browser=True)

        self.assertEqual(switched_account, "demo@example.com")
        first_cmd = mock_run.call_args_list[0].args[0]
        second_cmd = mock_run.call_args_list[1].args[0]
        self.assertEqual(first_cmd, ["gcloud", "config", "set", "account", "demo@example.com"])
        self.assertEqual(
            second_cmd,
            [
                "gcloud",
                "--account=demo@example.com",
                "auth",
                "application-default",
                "login",
                "demo@example.com",
                "--disable-quota-project",
                "--no-browser",
            ],
        )
        self.assertEqual(mock_clear_caches.call_count, 2)

    @patch("gcp_instance.print_info")
    @patch("gcp_instance.clear_google_cloud_client_caches")
    @patch("gcp_instance.get_adc_account_email", return_value=("demo@example.com", ""))
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    @patch("gcp_instance.subprocess.run")
    def test_sync_adc_account_clears_client_cache_when_already_matched(
        self,
        mock_run,
        _mock_find_gcloud,
        _mock_adc_account,
        mock_clear_caches,
        _mock_print_info,
    ):
        synced_account = sync_adc_account("demo@example.com")

        self.assertEqual(synced_account, "demo@example.com")
        mock_clear_caches.assert_called_once()
        mock_run.assert_not_called()

    @patch("gcp_instance.get_current_gcloud_account", return_value=None)
    @patch("gcp_instance.get_adc_account_email", return_value=("demo@example.com", ""))
    @patch("gcp_instance.clear_google_cloud_client_caches")
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    @patch("gcp_instance.subprocess.run")
    def test_switch_gcloud_account_handles_missing_active_account(
        self,
        mock_run,
        _mock_find_gcloud,
        _mock_clear_caches,
        _mock_adc_account,
        _mock_current_account,
    ):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        switched_account = switch_gcloud_account("demo@example.com", sync_adc=False)

        self.assertEqual(switched_account, "demo@example.com")
        first_cmd = mock_run.call_args_list[0].args[0]
        self.assertEqual(first_cmd, ["gcloud", "config", "set", "account", "demo@example.com"])

    @patch("gcp_instance.print_success")
    @patch("gcp_instance.print_info")
    @patch("gcp_instance.get_current_gcloud_account", return_value="demo@example.com")
    @patch("gcp_instance.get_adc_account_email", return_value=("demo@example.com", ""))
    @patch("gcp_instance.clear_google_cloud_client_caches")
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    @patch("gcp_instance.subprocess.run")
    def test_switch_gcloud_account_clears_caches_when_already_active(
        self,
        mock_run,
        _mock_find_gcloud,
        mock_clear_caches,
        _mock_adc_account,
        _mock_current_account,
        _mock_print_info,
        _mock_print_success,
    ):
        switched_account = switch_gcloud_account("demo@example.com", sync_adc=False)

        self.assertEqual(switched_account, "demo@example.com")
        mock_clear_caches.assert_called_once()
        mock_run.assert_not_called()

    @patch("gcp_instance.get_current_gcloud_account", return_value="new@example.com")
    @patch("gcp_instance.get_adc_account_email", return_value=("new@example.com", ""))
    @patch("gcp_instance.clear_google_cloud_client_caches")
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    @patch("gcp_instance.subprocess.run")
    def test_login_gcloud_account_uses_auth_login_with_update_adc(
        self,
        mock_run,
        _mock_find_gcloud,
        mock_clear_caches,
        _mock_adc_account,
        _mock_current_account,
    ):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        switched_account = login_gcloud_account("new@example.com", no_browser=True)

        self.assertEqual(switched_account, "new@example.com")
        first_cmd = mock_run.call_args_list[0].args[0]
        self.assertEqual(
            first_cmd,
            ["gcloud", "auth", "login", "new@example.com", "--no-browser", "--update-adc"],
        )
        mock_clear_caches.assert_called_once()

    @patch("gcp_instance.switch_gcloud_account", return_value="second@example.com")
    @patch("gcp_instance.select_gcloud_account", return_value="second@example.com")
    @patch(
        "gcp_instance.list_gcloud_accounts_via_gcloud",
        return_value=[
            {"account": "first@example.com", "status": "ACTIVE", "active": True},
            {"account": "second@example.com", "status": "UNKNOWN", "active": False},
        ],
    )
    def test_select_startup_gcloud_account_prompts_when_multiple_accounts(
        self,
        _mock_accounts,
        mock_select_account,
        mock_switch_account,
    ):
        selected = select_startup_gcloud_account()

        self.assertEqual(selected, "second@example.com")
        mock_select_account.assert_called_once()
        self.assertTrue(mock_select_account.call_args.kwargs["allow_login_new"])
        mock_switch_account.assert_called_once_with("second@example.com", sync_adc=False)

    @patch("gcp_instance.login_gcloud_account_interactive", return_value="new@example.com")
    @patch("gcp_instance.switch_gcloud_account")
    @patch("gcp_instance.select_gcloud_account", return_value=LOGIN_NEW_ACCOUNT_MARKER)
    @patch(
        "gcp_instance.list_gcloud_accounts_via_gcloud",
        return_value=[
            {"account": "first@example.com", "status": "ACTIVE", "active": True},
        ],
    )
    def test_select_startup_gcloud_account_can_login_new_account(
        self,
        _mock_accounts,
        mock_select_account,
        mock_switch_account,
        mock_login_interactive,
    ):
        selected = select_startup_gcloud_account()

        self.assertEqual(selected, "new@example.com")
        self.assertTrue(mock_select_account.call_args.kwargs["allow_login_new"])
        mock_login_interactive.assert_called_once()
        mock_switch_account.assert_not_called()

    @patch("gcp_instance.select_from_list")
    @patch(
        "gcp_instance.list_gcloud_accounts_via_gcloud",
        return_value=[
            {"account": "first@example.com", "status": "ACTIVE", "active": True},
            {"account": "second@example.com", "status": "UNKNOWN", "active": False},
        ],
    )
    def test_select_gcloud_account_offers_login_new_choice(self, _mock_accounts, mock_select_from_list):
        from gcp import select_gcloud_account

        def select_login_choice(choices, prompt_text, render_item, allow_back=False):
            self.assertEqual(prompt_text, "请选择目标账号")
            self.assertFalse(allow_back)
            rendered_choices = [render_item(item) for item in choices]
            self.assertIn("登录新账号（浏览器授权）", rendered_choices)
            return next(item for item in choices if item["account"] == LOGIN_NEW_ACCOUNT_MARKER)

        mock_select_from_list.side_effect = select_login_choice
        selected = select_gcloud_account(allow_login_new=True)

        self.assertEqual(selected, LOGIN_NEW_ACCOUNT_MARKER)

    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    @patch("gcp_instance.subprocess.run")
    def test_list_active_projects_via_gcloud_uses_selected_account(self, mock_run, _mock_find_gcloud):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='[{"projectId":"demo-project","name":"Demo","lifecycleState":"ACTIVE"}]',
            stderr="",
        )

        projects = list_active_projects_via_gcloud(account="demo@example.com")

        self.assertEqual(projects[0]["project_id"], "demo-project")
        command = mock_run.call_args.args[0]
        self.assertIn("--account=demo@example.com", command)

    @patch("gcp_menu.print_success")
    @patch("gcp_menu.select_gcp_project", return_value="demo-project")
    @patch("gcp_menu.switch_gcloud_account", return_value="second@example.com")
    @patch("gcp_menu.prompt_yes_no", return_value=False)
    @patch("gcp_menu.select_gcloud_account", return_value="second@example.com")
    def test_menu_switch_account_selects_project_for_switched_account(
        self,
        _mock_select_account,
        _mock_prompt_yes_no,
        _mock_switch_account,
        mock_select_project,
        _mock_print_success,
    ):
        context = SimpleNamespace(
            current_account="first@example.com",
            project_id="old-project",
            current_instance=object(),
            remote_config_cache={"old": object()},
        )

        menu_switch_account_action(context)

        self.assertEqual(context.current_account, "second@example.com")
        self.assertEqual(context.project_id, "demo-project")
        self.assertIsNone(context.current_instance)
        self.assertEqual(context.remote_config_cache, {})
        mock_select_project.assert_called_once_with(account="second@example.com")

    @patch("gcp_cli.get_current_adc_account", return_value="adc@example.com")
    @patch("gcp_cli.get_current_gcloud_account", return_value="gcloud@example.com")
    def test_prepare_cli_account_context_rejects_adc_mismatch_without_account(
        self,
        _mock_gcloud_account,
        _mock_adc_account,
    ):
        args = SimpleNamespace(project_id="demo-project", account=None, dry_run=False)

        with self.assertRaisesRegex(RuntimeError, "--account gcloud@example.com"):
            prepare_cli_account_context(args)

    @patch("gcp_cli.get_current_adc_account", return_value="")
    @patch("gcp_cli.get_current_gcloud_account", return_value="gcloud@example.com")
    def test_prepare_cli_account_context_rejects_unknown_adc_without_account(
        self,
        _mock_gcloud_account,
        _mock_adc_account,
    ):
        args = SimpleNamespace(project_id="demo-project", account=None, dry_run=False)

        with self.assertRaisesRegex(RuntimeError, "ADC 账号是 未确认"):
            prepare_cli_account_context(args)

    @patch("gcp_cli.get_current_adc_account", return_value="adc@example.com")
    @patch("gcp_cli.get_current_gcloud_account", return_value="")
    def test_prepare_cli_account_context_rejects_unknown_gcloud_without_account(
        self,
        _mock_gcloud_account,
        _mock_adc_account,
    ):
        args = SimpleNamespace(project_id="demo-project", account=None, dry_run=False)

        with self.assertRaisesRegex(RuntimeError, "无法确认当前 gcloud 账号"):
            prepare_cli_account_context(args)

    def test_is_target_cpu_accepts_amd_and_epyc(self):
        self.assertTrue(is_target_cpu("AMD EPYC Milan"))
        self.assertTrue(is_target_cpu("EPYC Rome"))
        self.assertFalse(is_target_cpu("Intel Broadwell"))

    def test_is_ip_target_met_requires_valid_changed_ip(self):
        self.assertFalse(is_ip_target_met("35.1.2.3", "35.1.2.3"))
        self.assertTrue(is_ip_target_met("35.1.2.3", "35.4.5.6"))
        self.assertTrue(is_ip_target_met("-", "35.4.5.6"))
        self.assertFalse(is_ip_target_met("35.1.2.3", "-"))

    def test_reroll_ip_cli_commands_parse(self):
        ip_args = parse_args([
            "reroll-ip",
            "--project-id",
            "demo-project",
            "--account",
            "demo@example.com",
            "--auth-no-browser",
            "--instance",
            "vm-1",
            "--zone",
            "us-west1-a",
            "--resume",
        ])
        ip_amd_args = parse_args([
            "reroll-ip-amd",
            "--project-id",
            "demo-project",
            "--instance",
            "vm-1",
            "--zone",
            "us-west1-a",
        ])

        self.assertIs(ip_args.handler, handle_reroll_ip_cli)
        self.assertTrue(ip_args.resume)
        self.assertEqual(ip_args.method, "restart")
        self.assertEqual(ip_args.account, "demo@example.com")
        self.assertTrue(ip_args.auth_no_browser)
        self.assertIs(ip_amd_args.handler, handle_reroll_ip_amd_cli)

    def test_switch_ip_cli_command_parses(self):
        args = parse_args([
            "switch-ip",
            "--project-id",
            "demo-project",
            "--account",
            "demo@example.com",
            "--instance",
            "vm-1",
            "--zone",
            "us-west1-a",
            "--access-config-name",
            "External NAT",
            "--network-interface",
            "nic1",
            "--network-tier",
            "PREMIUM",
            "--dry-run",
        ])

        self.assertIs(args.handler, handle_switch_ip_cli)
        self.assertEqual(args.access_config_name, "External NAT")
        self.assertEqual(args.network_interface, "nic1")
        self.assertEqual(args.network_tier, "PREMIUM")
        self.assertTrue(args.dry_run)

    def test_reroll_ip_access_config_method_parses(self):
        args = parse_args([
            "reroll-ip",
            "--project-id",
            "demo-project",
            "--instance",
            "vm-1",
            "--zone",
            "us-west1-a",
            "--method",
            "access-config",
            "--network-tier",
            "standard",
            "--dry-run",
        ])

        self.assertIs(args.handler, handle_reroll_ip_cli)
        self.assertEqual(args.method, "access-config")
        self.assertEqual(args.network_tier, "standard")
        self.assertTrue(args.dry_run)

    @patch("gcp_cli.switch_external_ip_without_restart")
    @patch("gcp_cli.get_cli_instance")
    def test_handle_switch_ip_cli_calls_access_config_switch(self, mock_get_instance, mock_switch_ip):
        instance = InstanceInfo(
            name="vm-1",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="Intel Broadwell",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="35.1.2.3",
        )
        mock_get_instance.return_value = instance
        args = parse_args([
            "switch-ip",
            "--project-id",
            "demo-project",
            "--instance",
            "vm-1",
            "--zone",
            "us-west1-a",
            "--access-config-name",
            "External NAT",
            "--network-interface",
            "nic1",
            "--network-tier",
            "PREMIUM",
            "--dry-run",
        ])

        handle_switch_ip_cli(args)

        mock_switch_ip.assert_called_once_with(
            "demo-project",
            instance,
            access_config_name="External NAT",
            network_interface="nic1",
            network_tier="PREMIUM",
            dry_run=True,
        )

    @patch("gcp_cli.switch_external_ip_without_restart")
    @patch("gcp_cli.reroll_ip_loop")
    @patch("gcp_cli.get_cli_instance")
    def test_handle_reroll_ip_cli_access_config_method_skips_restart_loop(
        self,
        mock_get_instance,
        mock_reroll_ip_loop,
        mock_switch_ip,
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
        mock_get_instance.return_value = instance
        args = parse_args([
            "reroll-ip",
            "--project-id",
            "demo-project",
            "--instance",
            "vm-1",
            "--zone",
            "us-west1-a",
            "--method",
            "access-config",
            "--dry-run",
        ])

        handle_reroll_ip_cli(args)

        mock_reroll_ip_loop.assert_not_called()
        mock_switch_ip.assert_called_once()

    def test_firewall_cli_delete_commands_parse(self):
        deny_args = parse_args([
            "firewall",
            "--project-id",
            "demo-project",
            "--delete-deny-cdn-egress",
        ])
        managed_args = parse_args([
            "firewall",
            "--project-id",
            "demo-project",
            "--delete-managed-rules",
        ])

        self.assertTrue(deny_args.delete_deny_cdn_egress)
        self.assertFalse(deny_args.delete_managed_rules)
        self.assertIsNone(deny_args.instance)
        self.assertTrue(managed_args.delete_managed_rules)

    def test_update_gcp_ip_ranges_cli_commands_parse(self):
        gcp_range_args = parse_args(["update-gcp-ip-ranges"])
        legacy_args = parse_args(["update-cdnip"])

        self.assertIs(gcp_range_args.handler, handle_update_gcp_ip_ranges_cli)
        self.assertEqual(gcp_range_args.output, "gcp_region_ips.txt")
        self.assertEqual(legacy_args.output, "gcp_region_ips.txt")

    @patch("gcp_cli.configure_firewall_non_interactive")
    @patch("gcp_cli.get_cli_instance")
    def test_firewall_cli_delete_does_not_require_instance_lookup(
        self,
        mock_get_instance,
        mock_configure_firewall,
    ):
        args = parse_args([
            "firewall",
            "--project-id",
            "demo-project",
            "--delete-deny-cdn-egress",
        ])

        handle_firewall_cli(args)

        mock_get_instance.assert_not_called()
        mock_configure_firewall.assert_called_once()
        self.assertEqual(mock_configure_firewall.call_args.args[:2], ("demo-project", "global/networks/default"))

    def test_firewall_cli_add_requires_instance_or_network(self):
        args = parse_args([
            "firewall",
            "--project-id",
            "demo-project",
            "--allow-all-ingress",
        ])

        with self.assertRaisesRegex(ValueError, "--instance 或 --network"):
            handle_firewall_cli(args)

    @patch("gcp_cli.configure_firewall_non_interactive")
    @patch("gcp_cli.get_cli_instance")
    def test_firewall_cli_add_can_use_explicit_network(
        self,
        mock_get_instance,
        mock_configure_firewall,
    ):
        args = parse_args([
            "firewall",
            "--project-id",
            "demo-project",
            "--network",
            "default",
            "--allow-all-ingress",
        ])

        handle_firewall_cli(args)

        mock_get_instance.assert_not_called()
        self.assertEqual(mock_configure_firewall.call_args.args[:2], ("demo-project", "global/networks/default"))

    def test_switch_account_cli_command_parses(self):
        args = parse_args([
            "switch-account",
            "--account",
            "demo@example.com",
            "--no-sync-adc",
        ])

        self.assertIs(args.handler, handle_switch_account_cli)
        self.assertEqual(args.account, "demo@example.com")
        self.assertTrue(args.no_sync_adc)

    def test_login_account_cli_command_parses(self):
        args = parse_args([
            "login-account",
            "--account",
            "new@example.com",
            "--no-browser",
        ])

        self.assertIs(args.handler, handle_login_account_cli)
        self.assertEqual(args.account, "new@example.com")
        self.assertTrue(args.no_browser)

    def test_root_and_3xui_cli_commands_parse(self):
        enable_args = parse_args([
            "enable-root-login",
            "--project-id",
            "demo-project",
            "--instance",
            "vm-1",
            "--zone",
            "us-west1-a",
            "--password-env",
            "ROOT_PASS",
        ])
        change_args = parse_args([
            "change-root-password",
            "--project-id",
            "demo-project",
            "--instance",
            "vm-1",
            "--zone",
            "us-west1-a",
        ])
        xui_args = parse_args([
            "install-3xui",
            "--project-id",
            "demo-project",
            "--instance",
            "vm-1",
            "--zone",
            "us-west1-a",
        ])

        self.assertIs(enable_args.handler, handle_enable_root_login_cli)
        self.assertEqual(enable_args.password_env, "ROOT_PASS")
        self.assertIs(change_args.handler, handle_change_root_password_cli)
        self.assertIs(xui_args.handler, handle_install_3xui_cli)

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
        permission_exc = RuntimeError(
            "403 GET https://compute.googleapis.com/compute/v1/projects/demo/zones/us-west1-b/instances/vm-1: "
            "Required 'compute.instances.get' permission for 'projects/demo/zones/us-west1-b/instances/vm-1'"
        )
        local_permission_exc = RuntimeError("permission denied")
        compute_permission_exc = RuntimeError(
            "403 Permission denied for https://compute.googleapis.com/compute/v1/projects/demo"
        )

        self.assertEqual(classify_reroll_exception(oauth_exc), "oauth_timeout")
        self.assertEqual(classify_reroll_exception(stop_exc), "instance_stuck")
        self.assertEqual(classify_reroll_exception(compute_exc), "compute_timeout")
        self.assertEqual(classify_reroll_exception(permission_exc), "permission_denied")
        self.assertEqual(classify_reroll_exception(compute_permission_exc), "permission_denied")
        self.assertEqual(classify_reroll_exception(local_permission_exc), "hard_failure")

    def test_oauth_host_detection_requires_exact_host_match(self):
        oauth_exc = RuntimeError("HTTPSConnectionPool(host='oauth2.googleapis.com', port=443): Read timed out")
        injected_path = RuntimeError("https://example.com/oauth2.googleapis.com/token timed out")

        self.assertTrue(is_oauth_timeout_error(oauth_exc))
        self.assertFalse(is_oauth_timeout_error(injected_path))
        self.assertTrue(message_mentions_exact_host("https://oauth2.googleapis.com/token", "oauth2.googleapis.com"))

    @patch("gcp_instance.get_adc_account_email", return_value=("adc@example.com", ""))
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    def test_get_current_adc_account_reads_adc_identity(self, _mock_find_gcloud, _mock_adc_email):
        self.assertEqual(get_current_adc_account(), "adc@example.com")

    @patch("gcp_instance.get_adc_account_email", return_value=("adc@example.com", ""))
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    def test_get_current_adc_account_uses_process_cache(self, _mock_find_gcloud, mock_adc_email):
        self.assertEqual(get_current_adc_account(), "adc@example.com")
        self.assertEqual(get_current_adc_account(), "adc@example.com")
        mock_adc_email.assert_called_once_with("gcloud")

        clear_adc_account_cache()
        self.assertEqual(get_current_adc_account(), "adc@example.com")
        self.assertEqual(mock_adc_email.call_count, 2)

    @patch(
        "gcp_instance.get_adc_account_email",
        side_effect=[("", "tokeninfo timeout"), ("adc@example.com", "")],
    )
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    def test_get_current_adc_account_does_not_cache_unknown_identity(self, _mock_find_gcloud, mock_adc_email):
        self.assertEqual(get_current_adc_account(), "")
        self.assertEqual(get_current_adc_account(), "adc@example.com")
        self.assertEqual(mock_adc_email.call_count, 2)

    @patch(
        "gcp_instance.get_adc_account_email",
        side_effect=[
            ("old@example.com", ""),
            ("", "tokeninfo timeout"),
            ("new@example.com", ""),
        ],
    )
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    def test_get_current_adc_account_refresh_clears_stale_cache(self, _mock_find_gcloud, mock_adc_email):
        self.assertEqual(get_current_adc_account(), "old@example.com")
        self.assertEqual(get_current_adc_account(refresh=True), "")
        self.assertEqual(get_current_adc_account(), "new@example.com")
        self.assertEqual(mock_adc_email.call_count, 3)

    @patch("gcp_instance.print_success")
    @patch("gcp_instance.print_info")
    @patch("gcp_instance.set_gcloud_project")
    @patch("gcp_instance.set_adc_quota_project")
    @patch("gcp_instance.get_adc_account_email", return_value=("other@example.com", ""))
    @patch("gcp_instance.get_current_gcloud_account", return_value="demo@example.com")
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    def test_switch_gcloud_account_skips_quota_project_when_adc_differs(
        self,
        _mock_find_gcloud,
        _mock_current_account,
        _mock_adc_account,
        mock_set_quota_project,
        mock_set_gcloud_project,
        _mock_print_info,
        _mock_print_success,
    ):
        switched_account = switch_gcloud_account(
            "demo@example.com",
            sync_adc=False,
            project_id="demo-project",
        )

        self.assertEqual(switched_account, "demo@example.com")
        mock_set_gcloud_project.assert_called_once_with("demo-project")
        mock_set_quota_project.assert_not_called()

    @patch("gcp_instance.get_current_gcloud_account", return_value="old@example.com")
    @patch("gcp_instance.clear_google_cloud_client_caches")
    @patch(
        "gcp_instance.get_adc_account_email",
        side_effect=[
            ("other@example.com", ""),
            ("", "tokeninfo timeout"),
        ],
    )
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    @patch("gcp_instance.subprocess.run")
    def test_switch_gcloud_account_requires_verified_adc_after_sync(
        self,
        mock_run,
        _mock_find_gcloud,
        _mock_adc_account,
        _mock_clear_caches,
        _mock_current_account,
    ):
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]

        with self.assertRaisesRegex(RuntimeError, "无法确认账号"):
            switch_gcloud_account("demo@example.com", sync_adc=True)

    @patch("gcp_instance.projects_client")
    @patch("gcp_instance.prompt_manual_project_id", return_value="manual-project")
    @patch("gcp_instance.get_current_adc_account", return_value="")
    @patch("gcp_instance.list_active_projects_via_gcloud", side_effect=RuntimeError("gcloud failed"))
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    def test_select_gcp_project_skips_resource_manager_when_selected_account_adc_unknown(
        self,
        _mock_find_gcloud,
        _mock_list_projects,
        _mock_adc_account,
        mock_manual_project,
        mock_projects_client,
    ):
        project_id = select_gcp_project(account="demo@example.com")

        self.assertEqual(project_id, "manual-project")
        mock_manual_project.assert_called_once_with(allow_back=False)
        mock_projects_client.assert_not_called()

    @patch("gcp_cli.restore_cli_project_context")
    @patch(
        "gcp_cli.snapshot_cli_project_context",
        return_value={"gcloud_project": "old-project", "adc_quota_project": "old-quota"},
    )
    @patch("gcp_cli.ensure_libraries_or_exit")
    @patch("gcp_cli.prepare_cli_account_context")
    @patch("gcp_cli.configure_runtime_logging")
    def test_run_cli_restores_project_context_when_handler_fails(
        self,
        _mock_logging,
        mock_prepare_context,
        mock_ensure_libraries,
        _mock_snapshot_context,
        mock_restore_context,
    ):
        def failing_handler(_args):
            raise RuntimeError("handler failed")

        args = SimpleNamespace(
            handler=failing_handler,
            project_id="demo-project",
            account=None,
            dry_run=False,
            log_file=None,
        )

        with self.assertRaisesRegex(RuntimeError, "handler failed"):
            run_cli(args)

        mock_prepare_context.assert_called_once_with(args)
        mock_ensure_libraries.assert_called_once()
        mock_restore_context.assert_called_once_with(
            {"gcloud_project": "old-project", "adc_quota_project": "old-quota"}
        )

    @patch("gcp_cli.restore_cli_project_context")
    @patch(
        "gcp_cli.snapshot_cli_project_context",
        return_value={
            "gcloud_account": {"read_ok": True, "value": "old@example.com"},
            "gcloud_project": {"read_ok": True, "value": "old-project"},
            "adc_credentials": {"read_ok": True, "exists": True, "content": b"{}"},
            "adc_quota_project": "old-quota",
        },
    )
    @patch("gcp_cli.prepare_cli_account_context", side_effect=RuntimeError("adc sync failed"))
    @patch("gcp_cli.configure_runtime_logging")
    def test_run_cli_restores_context_when_preflight_fails(
        self,
        _mock_logging,
        _mock_prepare_context,
        _mock_snapshot_context,
        mock_restore_context,
    ):
        args = SimpleNamespace(
            handler=Mock(),
            project_id="demo-project",
            account="demo@example.com",
            dry_run=False,
            log_file=None,
        )

        with self.assertRaisesRegex(RuntimeError, "adc sync failed"):
            run_cli(args)

        mock_restore_context.assert_called_once_with(
            {
                "gcloud_account": {"read_ok": True, "value": "old@example.com"},
                "gcloud_project": {"read_ok": True, "value": "old-project"},
                "adc_credentials": {"read_ok": True, "exists": True, "content": b"{}"},
                "adc_quota_project": "old-quota",
            }
        )

    @patch("gcp_cli.restore_gcloud_project")
    @patch("gcp_cli.restore_gcloud_account")
    @patch("gcp_cli.restore_adc_quota_project")
    @patch("gcp_cli.restore_adc_credentials")
    def test_restore_cli_project_context_restores_account_project_and_adc_file(
        self,
        mock_restore_adc_credentials,
        mock_restore_adc_quota,
        mock_restore_account,
        mock_restore_project,
    ):
        snapshot = {
            "gcloud_account": {"read_ok": True, "value": "old@example.com"},
            "gcloud_project": {"read_ok": True, "value": "old-project"},
            "adc_credentials": {"read_ok": True, "exists": True, "content": b"old"},
            "adc_quota_project": "old-quota",
        }

        gcp_cli.restore_cli_project_context(snapshot)

        mock_restore_adc_credentials.assert_called_once_with(snapshot["adc_credentials"])
        mock_restore_account.assert_called_once_with(snapshot["gcloud_account"])
        mock_restore_project.assert_called_once_with(snapshot["gcloud_project"])
        mock_restore_adc_quota.assert_not_called()

    @patch("gcp_instance.subprocess.run")
    def test_restore_gcloud_project_skips_when_snapshot_read_failed(self, mock_run):
        restored = gcp_instance.restore_gcloud_project(
            {
                "read_ok": False,
                "value": "",
                "error": "gcloud config get-value project failed",
            }
        )

        self.assertFalse(restored)
        mock_run.assert_not_called()

    @patch("gcp_instance.write_text_atomically")
    def test_restore_adc_quota_project_uses_atomic_write(self, mock_atomic_write):
        with TemporaryDirectory() as tmp_dir:
            adc_path = Path(tmp_dir, "application_default_credentials.json")
            adc_path.write_text(
                '{"client_id":"demo","quota_project_id":"new-quota"}',
                encoding="utf-8",
            )
            with patch("gcp_instance.get_adc_credentials_path", return_value=adc_path):
                restored = gcp_instance.restore_adc_quota_project("old-quota")

        self.assertTrue(restored)
        mock_atomic_write.assert_called_once()
        self.assertEqual(mock_atomic_write.call_args.args[0], adc_path)
        self.assertIn('"quota_project_id": "old-quota"', mock_atomic_write.call_args.args[1])

    def test_restore_adc_credentials_restores_full_snapshot(self):
        with TemporaryDirectory() as tmp_dir:
            adc_path = Path(tmp_dir, "application_default_credentials.json")
            original_content = '{"client_id":"old","quota_project_id":"old-quota"}\n'
            adc_path.write_text(original_content, encoding="utf-8")
            with patch("gcp_instance.get_adc_credentials_path", return_value=adc_path):
                snapshot = gcp_instance.snapshot_adc_credentials()
                adc_path.write_text(
                    '{"client_id":"new","quota_project_id":"new-quota"}\n',
                    encoding="utf-8",
                )

                restored = gcp_instance.restore_adc_credentials(snapshot)
                restored_content = adc_path.read_text(encoding="utf-8")

        self.assertTrue(restored)
        self.assertEqual(restored_content, original_content)

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
        hard_kind, _ = record_reroll_exception(stats, RuntimeError("unexpected local failure"))

        self.assertEqual(oauth_kind, "oauth_timeout")
        self.assertEqual(compute_kind, "compute_timeout")
        self.assertEqual(hard_kind, "hard_failure")
        self.assertEqual(stats.auth_timeout_count, 1)
        self.assertEqual(stats.compute_timeout_count, 1)
        self.assertEqual(stats.hard_failure_count, 1)
        self.assertEqual(get_soft_exception_count(stats), 2)
        self.assertEqual(stats.exception_count, 3)
        self.assertEqual(stats.consecutive_auth_timeouts, 0)

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

    @patch("gcp_instance.print_info")
    @patch("gcp_instance.wait_for_instance_status")
    @patch("gcp_instance.wait_for_operation")
    @patch("gcp_instance.wait_for_instance_status_change")
    @patch("gcp_instance.stop_instance_with_retry")
    @patch("gcp_instance.get_instance_with_retry")
    def test_ensure_instance_stopped_waits_when_stop_operation_already_running(
        self,
        mock_get_instance,
        mock_stop_instance,
        mock_wait_status_change,
        mock_wait_operation,
        mock_wait_for_instance_status,
        _mock_print_info,
    ):
        mock_get_instance.return_value = SimpleNamespace(status="RUNNING")
        mock_stop_instance.side_effect = RuntimeError(
            "400 POST https://compute.googleapis.com/compute/v1/projects/demo/zones/us-west1-a/"
            "instances/vm-1/stop: A stop operation is already in progress for this instance"
        )
        mock_wait_status_change.return_value = (SimpleNamespace(status="STOPPING"), "STOPPING")
        mock_wait_for_instance_status.return_value = (SimpleNamespace(status="STOPPED"), "STOPPED")

        instance, _elapsed = ensure_instance_stopped(
            instance_client=None,
            project_id="demo-project",
            zone="us-west1-a",
            instance_name="vm-1",
        )

        self.assertEqual(instance.status, "STOPPED")
        mock_wait_operation.assert_not_called()

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

    @patch("gcp_instance.print_success")
    @patch("gcp_instance.refresh_instance_info")
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    @patch("gcp_instance.subprocess.run")
    def test_switch_external_ip_without_restart_recreates_access_config(
        self,
        mock_run,
        _mock_find_gcloud,
        mock_refresh,
        _mock_print_success,
    ):
        before = InstanceInfo(
            name="vm-1",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="Intel Broadwell",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="35.1.2.3",
        )
        after = InstanceInfo(
            name="vm-1",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="Intel Broadwell",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="35.4.5.6",
        )
        mock_refresh.side_effect = [before, after]
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    '{"networkInterfaces":[{"name":"nic0","accessConfigs":['
                    '{"name":"External NAT","natIP":"35.1.2.3","type":"ONE_TO_ONE_NAT"}]}]}'
                ),
                stderr="",
            ),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]

        result = switch_external_ip_without_restart("demo-project", before)

        self.assertEqual(result.external_ip, "35.4.5.6")
        describe_cmd = mock_run.call_args_list[0].args[0]
        delete_cmd = mock_run.call_args_list[1].args[0]
        add_cmd = mock_run.call_args_list[2].args[0]
        self.assertEqual(
            describe_cmd,
            [
                "gcloud",
                "compute",
                "instances",
                "describe",
                "vm-1",
                "--project",
                "demo-project",
                "--zone",
                "us-west1-a",
                "--format=json(networkInterfaces)",
            ],
        )
        self.assertEqual(delete_cmd[:4], ["gcloud", "compute", "instances", "delete-access-config"])
        self.assertIn("--access-config-name", delete_cmd)
        self.assertIn("External NAT", delete_cmd)
        self.assertIn("--quiet", delete_cmd)
        self.assertEqual(add_cmd[:4], ["gcloud", "compute", "instances", "add-access-config"])
        self.assertIn("--network-tier", add_cmd)
        self.assertIn("STANDARD", add_cmd)

    @patch("gcp_instance.print_info")
    @patch("gcp_instance.refresh_instance_info")
    @patch("gcp_instance.find_gcloud_command", return_value="gcloud")
    @patch("gcp_instance.subprocess.run")
    def test_switch_external_ip_without_restart_adds_when_no_external_ip(
        self,
        mock_run,
        _mock_find_gcloud,
        mock_refresh,
        _mock_print_info,
    ):
        before = InstanceInfo(
            name="vm-1",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="Intel Broadwell",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="-",
        )
        after = InstanceInfo(
            name="vm-1",
            zone="us-west1-a",
            status="RUNNING",
            cpu_platform="Intel Broadwell",
            network="global/networks/default",
            internal_ip="10.0.0.2",
            external_ip="35.4.5.6",
        )
        mock_refresh.side_effect = [before, after]
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"networkInterfaces":[{"name":"nic0","accessConfigs":[]}]}',
                stderr="",
            ),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]

        result = switch_external_ip_without_restart(
            "demo-project",
            before,
            access_config_name="external-nat",
            network_interface="nic0",
            network_tier="premium",
        )

        self.assertEqual(result.external_ip, "35.4.5.6")
        commands = [call_item.args[0] for call_item in mock_run.call_args_list]
        self.assertFalse(any("delete-access-config" in command for command in commands))
        self.assertTrue(any("add-access-config" in command for command in commands))
        self.assertIn("PREMIUM", commands[-1])

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
            root_login="skip",
            dry_run=False,
        )

        handle_setup_cli(args)

        mock_reroll_cpu_loop.assert_not_called()
        self.assertEqual(mock_run_setup_remote_step.call_count, 4)

    @patch("gcp_cli.get_password_from_env_or_prompt", return_value="Secret123!")
    @patch("gcp_cli.run_setup_remote_step", side_effect=lambda _args, inst, _remote, _name, _action: inst)
    @patch("gcp_cli.build_remote_config_from_args", return_value=SimpleNamespace(method="gcloud"))
    @patch("gcp_cli.configure_firewall_non_interactive")
    @patch("gcp_cli.reroll_cpu_loop")
    @patch("gcp_cli.create_instance")
    def test_handle_setup_cli_root_login_enable_adds_root_step(
        self,
        mock_create_instance,
        mock_reroll_cpu_loop,
        _mock_configure_firewall,
        _mock_build_remote_config,
        mock_run_setup_remote_step,
        mock_get_password,
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
            root_login="enable",
            root_password_env="ROOT_PASS",
            dry_run=False,
        )

        handle_setup_cli(args)

        step_names = [call_item.args[3] for call_item in mock_run_setup_remote_step.call_args_list]
        self.assertIn("启用 root 账号和 SSH 密码登录", step_names)
        self.assertEqual(mock_run_setup_remote_step.call_count, 5)
        mock_get_password.assert_called_once_with("ROOT_PASS", "请输入要设置的 root 密码")

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
            root_login="skip",
            dry_run=False,
        )

        handle_setup_cli(args)

        mock_reroll_cpu_loop.assert_called_once()

    @patch("gcp_cli.create_instance")
    def test_handle_setup_cli_rejects_ubuntu_before_creating_resources(self, mock_create_instance):
        args = SimpleNamespace(
            project_id="demo-project",
            zone="us-west1-a",
            region="us-west1",
            os="ubuntu",
            instance_name="vm-1",
            skip_reroll=True,
            traffic_script="net_iptables",
            root_login="skip",
            dry_run=False,
        )

        with self.assertRaisesRegex(ValueError, "仅适配 Debian"):
            handle_setup_cli(args)

        mock_create_instance.assert_not_called()

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
