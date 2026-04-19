import ipaddress
import json
import urllib.request
from typing import Any, Sequence


GCP_IP_RANGES_URL = "https://www.gstatic.com/ipranges/cloud.json"
DEFAULT_TARGET_REGIONS = ("us-west1", "us-central1", "us-east1")


def fetch_gcp_ip_ranges(url: str = GCP_IP_RANGES_URL, timeout: int = 20) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def merge_gcp_ipv4_ranges(
    data: dict[str, Any],
    target_regions: Sequence[str] = DEFAULT_TARGET_REGIONS,
) -> list[str]:
    target_region_set = set(target_regions)
    networks = []
    for prefix in data.get("prefixes", []):
        if prefix.get("scope") not in target_region_set:
            continue
        ipv4_prefix = prefix.get("ipv4Prefix")
        if ipv4_prefix:
            networks.append(ipaddress.IPv4Network(ipv4_prefix))
    return [str(network) for network in ipaddress.collapse_addresses(networks)]


def get_gcp_ips_merged(
    target_regions: Sequence[str] = DEFAULT_TARGET_REGIONS,
    url: str = GCP_IP_RANGES_URL,
    timeout: int = 20,
) -> list[str]:
    data = fetch_gcp_ip_ranges(url=url, timeout=timeout)
    return merge_gcp_ipv4_ranges(data, target_regions=target_regions)


def update_cdnip_file(
    output_path: str = "cdnip.txt",
    target_regions: Sequence[str] = DEFAULT_TARGET_REGIONS,
) -> list[str]:
    merged_ranges = get_gcp_ips_merged(target_regions=target_regions)
    with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(merged_ranges))
        fh.write("\n")
    return merged_ranges


def main() -> None:
    print("正在获取并计算合并 IP 段...")
    merged_ranges = get_gcp_ips_merged()
    print(f"合并后段数: {len(merged_ranges)}\n")
    for network in merged_ranges:
        print(network)


if __name__ == "__main__":
    main()
