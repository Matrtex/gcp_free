from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from gcp_config import resolve_asset_path


class ConfigPathTestCase(unittest.TestCase):
    def test_resolve_asset_path_prefers_runtime_override(self):
        with TemporaryDirectory() as runtime_dir, TemporaryDirectory() as bundle_dir:
            runtime_root = Path(runtime_dir)
            bundle_root = Path(bundle_dir)
            runtime_file = runtime_root / "config.dae"
            bundle_file = bundle_root / "config.dae"
            runtime_file.write_text("runtime", encoding="utf-8")
            bundle_file.write_text("bundle", encoding="utf-8")

            with patch("gcp_config.get_runtime_root", return_value=runtime_root), patch(
                "gcp_config.get_bundle_root", return_value=bundle_root
            ):
                self.assertEqual(resolve_asset_path("config.dae"), runtime_file)

    def test_resolve_asset_path_falls_back_to_bundle_root(self):
        with TemporaryDirectory() as runtime_dir, TemporaryDirectory() as bundle_dir:
            runtime_root = Path(runtime_dir)
            bundle_root = Path(bundle_dir)
            bundle_file = bundle_root / "config.dae"
            bundle_file.write_text("bundle", encoding="utf-8")

            with patch("gcp_config.get_runtime_root", return_value=runtime_root), patch(
                "gcp_config.get_bundle_root", return_value=bundle_root
            ):
                self.assertEqual(resolve_asset_path("config.dae"), bundle_file)


if __name__ == "__main__":
    unittest.main()
