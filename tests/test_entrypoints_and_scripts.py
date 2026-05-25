from pathlib import Path
import os
import shutil
import subprocess
import sys
import unittest

from scripts.build_exe import validate_package_component


ROOT_DIR = Path(__file__).resolve().parents[1]


class EntrypointsAndScriptsTestCase(unittest.TestCase):
    def test_gcp_py_returns_nonzero_when_cli_handler_fails(self):
        result = subprocess.run(
            [
                sys.executable,
                "gcp.py",
                "show-reroll-state",
                "--state-file",
                "missing-state-for-test.json",
            ],
            cwd=ROOT_DIR,
            encoding="utf-8",
            errors="backslashreplace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(
            "未找到可显示的刷 CPU 状态" in result.stdout
            or r"\u672a\u627e\u5230\u53ef\u663e\u793a\u7684\u5237 CPU \u72b6\u6001" in result.stdout
        )

    def test_start_sh_forwards_arguments_to_python_entrypoint(self):
        content = Path(ROOT_DIR, "start.sh").read_text(encoding="utf-8")

        self.assertIn('exec python -u gcp.py "$@"', content)
        self.assertNotIn("gcloud services enable", content)

    def test_start_ps1_propagates_python_exit_code(self):
        content = Path(ROOT_DIR, "start.ps1").read_text(encoding="utf-8")

        self.assertIn("exit $LASTEXITCODE", content)
        self.assertIn("Failed to install Python dependencies.", content)

    def test_net_shutdown_uses_monthly_tx_field(self):
        content = Path(ROOT_DIR, "scripts", "net_shutdown.sh").read_text(encoding="utf-8")

        self.assertIn("第 10 个字段", content)
        self.assertIn("cut -d ';' -f 10", content)
        self.assertNotIn("cut -d ';' -f 5", content)

    def test_net_iptables_blocks_new_outbound_connections_after_limit(self):
        content = Path(ROOT_DIR, "scripts", "net_iptables.sh").read_text(encoding="utf-8")
        limit_marker = 'if [ \\$(echo "\\$TX_GB >= \\$LIMIT"'
        limit_block = content[
            content.index(limit_marker) : content.index(
                "else",
                content.index(limit_marker),
            )
        ]

        self.assertIn("apply_limit_rules", limit_block)
        self.assertIn('LIMIT_CHAIN="GCP_FREE_LIMIT"', content)
        self.assertIn('iptables -A "\\$chain" -j REJECT --reject-with icmp-port-unreachable', content)
        self.assertIn('iptables -I OUTPUT 1 -j "\\$chain"', content)
        self.assertNotIn("iptables -P INPUT DROP", limit_block)
        self.assertNotIn("iptables -P FORWARD DROP", limit_block)
        self.assertNotIn("iptables -P OUTPUT DROP", limit_block)
        self.assertNotIn("iptables -P OUTPUT ACCEPT", limit_block)

    def test_net_iptables_reset_only_removes_managed_chain(self):
        content = Path(ROOT_DIR, "scripts", "net_iptables.sh").read_text(encoding="utf-8")
        reset_block = content[content.index("remove_limit_rules()") : content.index("# 3. 重置 vnStat 数据库")]

        self.assertIn('while iptables -D OUTPUT -j "\\$chain"', reset_block)
        self.assertIn('iptables -F "\\$chain" 2>/dev/null || true', reset_block)
        self.assertIn('iptables -X "\\$chain" 2>/dev/null || true', reset_block)
        self.assertNotIn("iptables -P INPUT ACCEPT", reset_block)
        self.assertNotIn("iptables -P OUTPUT ACCEPT", reset_block)
        self.assertNotIn("iptables -P FORWARD ACCEPT", reset_block)

    def test_net_shutdown_preserves_usage_after_limit(self):
        content = Path(ROOT_DIR, "scripts", "net_shutdown.sh").read_text(encoding="utf-8")
        limit_marker = 'if [ \\$(echo "\\$TX_GB >= \\$LIMIT"'
        limit_block = content[
            content.index(limit_marker) : content.index(
                "else",
                content.index(limit_marker),
            )
        ]

        self.assertIn("保留 vnStat 统计和日志", limit_block)
        self.assertNotIn("vnstat --remove --force", limit_block)
        self.assertNotIn('rm -f "\\$LOG_FILE"', limit_block)

    def test_default_dae_config_keeps_insecure_tls_disabled(self):
        content = Path(ROOT_DIR, "config.dae").read_text(encoding="utf-8")

        self.assertIn("allow_insecure: false", content)
        self.assertNotIn("allow_insecure: true", content)

    def test_config_dae_is_not_gitignored(self):
        content = Path(ROOT_DIR, ".gitignore").read_text(encoding="utf-8")

        self.assertNotIn("config.dae", content.splitlines())

    def test_traffic_scripts_fail_fast_but_allow_missing_crontab(self):
        for script_name in ("net_iptables.sh", "net_shutdown.sh"):
            with self.subTest(script_name=script_name):
                content = Path(ROOT_DIR, "scripts", script_name).read_text(encoding="utf-8")

                self.assertIn("set -euo pipefail", content)
                self.assertIn("crontab -l > /tmp/cron_bk 2>/dev/null || true", content)

    def test_apt_script_contains_separate_debian_and_ubuntu_sources(self):
        content = Path(ROOT_DIR, "scripts", "apt.sh").read_text(encoding="utf-8")

        self.assertIn("debian)", content)
        self.assertIn("ubuntu)", content)
        self.assertIn("debian.sources", content)
        self.assertIn("ubuntu.sources", content)
        self.assertIn("ubuntu-archive-keyring.gpg", content)
        self.assertIn("backup_existing_apt_sources", content)
        self.assertIn("/etc/apt/sources.list", content)
        self.assertIn("find /etc/apt/sources.list.d", content)
        self.assertNotIn("\nbackup_existing_apt_sources\n\ncase", content)
        unsupported_block = content[content.index("    *)") :]
        self.assertNotIn("backup_existing_apt_sources", unsupported_block)
        debian_block = content[content.index("    debian)") : content.index("    ubuntu)")]
        ubuntu_block = content[content.index("    ubuntu)") : content.index("    *)")]
        self.assertIn("backup_existing_apt_sources", debian_block)
        self.assertIn("backup_existing_apt_sources", ubuntu_block)

    @unittest.skipIf(shutil.which("bash") is None or os.name == "nt", "bash 不可用或当前为 Windows，跳过 shell 语法检查")
    def test_shell_scripts_have_valid_bash_syntax(self):
        for script_name in ("start.sh", "apt.sh", "dae.sh", "net_iptables.sh", "net_shutdown.sh"):
            with self.subTest(script_name=script_name):
                result = subprocess.run(
                    ["bash", "-n", str(Path(ROOT_DIR, "scripts", script_name) if script_name != "start.sh" else Path(ROOT_DIR, script_name))],
                    cwd=ROOT_DIR,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_release_command_workflow_passes_outputs_through_environment(self):
        content = Path(ROOT_DIR, ".github", "workflows", "release-command.yml").read_text(encoding="utf-8")

        self.assertIn("process.env.BUILD_VERSION", content)
        self.assertIn("refs/pull/${context.issue.number}/head", content)
        self.assertIn("workflow_id: 'pr-build-exe.yml'", content)
        self.assertIn("source_ref: sourceRef", content)
        self.assertIn("source_sha: sourceSha", content)
        self.assertIn("source_label: sourceLabel", content)
        self.assertIn("PR 评论不支持 /release-exe", content)
        self.assertNotIn("workflow_id: 'release-exe.yml'", content)
        self.assertNotIn("create_release", content)
        self.assertNotIn("process.env.CREATE_RELEASE", content)
        self.assertNotIn("version: '${{ steps.parse.outputs.version }}'", content)
        self.assertNotIn("const version = '${{ steps.parse.outputs.version }}';", content)

    def test_pr_build_exe_workflow_is_read_only_and_unsigned(self):
        content = Path(ROOT_DIR, ".github", "workflows", "pr-build-exe.yml").read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch", content)
        self.assertIn("contents: read", content)
        self.assertNotIn("contents: write", content)
        self.assertIn("ref: ${{ inputs.source_ref }}", content)
        self.assertIn("persist-credentials: false", content)
        self.assertIn("SOURCE_SHA: ${{ inputs.source_sha }}", content)
        self.assertIn("python scripts/build_exe.py --clean --version $env:BUILD_VERSION", content)
        self.assertNotIn("WINDOWS_CODESIGN", content)
        self.assertNotIn("SIGN_PFX_PASSWORD", content)
        self.assertNotIn("softprops/action-gh-release", content)

    def test_release_exe_workflow_rejects_external_source_inputs(self):
        content = Path(ROOT_DIR, ".github", "workflows", "release-exe.yml").read_text(encoding="utf-8")

        self.assertIn("INPUT_VERSION: ${{ inputs.version }}", content)
        self.assertIn("$inputVersion = $env:INPUT_VERSION", content)
        self.assertIn("拒绝外部源码输入", content)
        self.assertIn("正式发布 workflow 只能构建当前默认分支或 tag 的可信代码", content)
        self.assertIn("ref: ${{ github.ref }}", content)
        self.assertIn("persist-credentials: false", content)
        self.assertIn("target_commitish: ${{ github.sha }}", content)
        self.assertIn("GIT_SHA: ${{ github.sha }}", content)
        self.assertIn("GITHUB_SHA: ${{ github.sha }}", content)
        self.assertNotIn("ref: ${{ inputs.source_ref || github.ref }}", content)
        self.assertNotIn("SOURCE_REF: ${{ inputs.source_ref }}", content)
        self.assertNotIn("process.env.SOURCE_REF", content)
        self.assertNotIn("target_commitish: ${{ inputs.source_sha || github.sha }}", content)
        self.assertNotIn("core.getInput('source_ref')", content)
        self.assertNotIn("跳过默认分支自动检查门禁", content)
        self.assertIn("$version -notmatch", content)
        self.assertIn("steps.version.outputs.build_version", content)
        self.assertNotIn('$inputVersion = "${{ inputs.version }}"', content)
        version_block = content[
            content.index("$eventName = $env:GITHUB_EVENT_NAME") : content.index("      - name: 构建 EXE")
        ]
        self.assertNotIn("${{ inputs.release_notes }}", version_block)
        test_index = content.index("      - name: 运行测试")
        signing_index = content.index("      - name: 准备代码签名证书")
        build_index = content.index("      - name: 构建 EXE")
        self.assertLess(test_index, signing_index)
        self.assertLess(signing_index, build_index)
        self.assertIn("SIGN_PFX_PASSWORD: ${{ secrets.WINDOWS_CODESIGN_CERT_PASSWORD }}", content)
        self.assertNotIn("SIGN_PFX_PASSWORD=$env:WINDOWS_CODESIGN_CERT_PASSWORD", content)

    def test_build_exe_rejects_unsafe_package_components(self):
        self.assertEqual(validate_package_component("v1.2.3", "version"), "v1.2.3")
        with self.assertRaises(ValueError):
            validate_package_component("../release", "version")
        with self.assertRaises(ValueError):
            validate_package_component("v1\nEVIL=1", "version")

    def test_dae_script_parses_arguments_before_default_install(self):
        content = Path(ROOT_DIR, "scripts", "dae.sh").read_text(encoding="utf-8")

        parse_index = content.index("while [ $# != 0 ]; do")
        default_index = content.index('if [ "$no_args" = ')
        install_index = content.index("should_we_install_dae", default_index)
        self.assertLess(parse_index, default_index)
        self.assertLess(default_index, install_index)
        self.assertNotIn('if [ "$1" = "" ] || [ "$1" = "use-cdn" ]; then', content)


if __name__ == "__main__":
    unittest.main()
