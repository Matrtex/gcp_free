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
        self.assertIn("process.env.CREATE_RELEASE", content)
        self.assertIn("refs/pull/${context.issue.number}/head", content)
        self.assertIn("source_ref: sourceRef", content)
        self.assertIn("source_sha: sourceSha", content)
        self.assertNotIn("version: '${{ steps.parse.outputs.version }}'", content)
        self.assertNotIn("const version = '${{ steps.parse.outputs.version }}';", content)

    def test_release_exe_workflow_reads_dispatch_inputs_from_environment(self):
        content = Path(ROOT_DIR, ".github", "workflows", "release-exe.yml").read_text(encoding="utf-8")

        self.assertIn("INPUT_VERSION: ${{ inputs.version }}", content)
        self.assertIn("$inputVersion = $env:INPUT_VERSION", content)
        self.assertIn("ref: ${{ inputs.source_ref || github.ref }}", content)
        self.assertIn("SOURCE_REF: ${{ inputs.source_ref }}", content)
        self.assertIn("process.env.SOURCE_REF", content)
        self.assertNotIn("core.getInput('source_ref')", content)
        self.assertIn("跳过默认分支自动检查门禁", content)
        self.assertIn("$version -notmatch", content)
        self.assertIn("steps.version.outputs.build_version", content)
        self.assertNotIn('$inputVersion = "${{ inputs.version }}"', content)
        version_block = content[
            content.index("$eventName = $env:GITHUB_EVENT_NAME") : content.index("      - name: 构建 EXE")
        ]
        self.assertNotIn("${{ inputs.release_notes }}", version_block)

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
