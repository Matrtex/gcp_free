import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from gcp_ips import (
    DEFAULT_GCP_IP_RANGES_FILE,
    merge_gcp_ipv4_ranges,
    update_cdnip_file,
    update_gcp_ip_ranges_file,
)


class FakeUrlopenResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class GcpIpsTestCase(unittest.TestCase):
    def test_merge_gcp_ipv4_ranges_filters_regions_and_collapses(self):
        data = {
            "prefixes": [
                {"scope": "us-west1", "ipv4Prefix": "10.0.0.0/25"},
                {"scope": "us-west1", "ipv4Prefix": "10.0.0.128/25"},
                {"scope": "europe-west1", "ipv4Prefix": "192.0.2.0/24"},
                {"scope": "us-central1", "ipv6Prefix": "2001:db8::/32"},
            ]
        }

        self.assertEqual(merge_gcp_ipv4_ranges(data), ["10.0.0.0/24"])

    @patch("gcp_ips.urllib.request.urlopen")
    def test_update_gcp_ip_ranges_file_uses_standard_library_fetch(self, mock_urlopen):
        mock_urlopen.return_value = FakeUrlopenResponse(
            {
                "prefixes": [
                    {"scope": "us-west1", "ipv4Prefix": "10.0.0.0/24"},
                    {"scope": "us-east1", "ipv4Prefix": "10.0.1.0/24"},
                ]
            }
        )
        with TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir, "gcp_region_ips.txt")
            ranges = update_gcp_ip_ranges_file(output_path=str(output_path))
            content = output_path.read_text(encoding="utf-8")

        self.assertEqual(ranges, ["10.0.0.0/23"])
        self.assertEqual(content, "10.0.0.0/23\n")

    def test_default_output_does_not_target_cdnip(self):
        self.assertEqual(DEFAULT_GCP_IP_RANGES_FILE, "gcp_region_ips.txt")
        self.assertNotEqual(update_cdnip_file.__defaults__[0], "cdnip.txt")


if __name__ == "__main__":
    unittest.main()
