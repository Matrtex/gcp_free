from pathlib import Path

LOCAL_SCRIPT_FILES = {
    "apt": "apt.sh",
    "dae": "dae.sh",
    "net_iptables": "net_iptables.sh",
    "net_shutdown": "net_shutdown.sh",
}

FIREWALL_RULES_TO_CLEAN = [
    "allow-all-ingress-custom",
    "deny-cdn-egress-custom",
]

REGION_OPTIONS = [
    {"name": "俄勒冈 (Oregon) [推荐]", "region": "us-west1", "default_zone": "us-west1-b"},
    {"name": "爱荷华 (Iowa)", "region": "us-central1", "default_zone": "us-central1-f"},
    {"name": "南卡罗来纳 (South Carolina)", "region": "us-east1", "default_zone": "us-east1-b"},
]

OS_IMAGE_OPTIONS = [
    {"name": "Debian 12 (Bookworm)", "project": "debian-cloud", "family": "debian-12"},
    {"name": "Ubuntu 22.04 LTS", "project": "ubuntu-os-cloud", "family": "ubuntu-2204-lts"},
]

DEPENDENCY_PACKAGES = [
    "google-cloud-compute",
    "google-cloud-resource-manager",
]

REQUIREMENTS_FILE = "requirements.txt"
DEPS_HASH_FILE = ".deps.sha256"
LOG_DIR_NAME = ".gcp_free_logs"
STATE_DIR_NAME = ".gcp_free_state"
DEFAULT_REROLL_STATE_FILE = "reroll_state.json"

OPERATION_WAIT_TIMEOUT = 300
OPERATION_POLL_INTERVAL = 3
INSTANCE_API_MAX_RETRIES = 4
INSTANCE_API_RETRY_BASE_DELAY = 5
INSTANCE_CONFLICT_RETRY_DELAY = 10
INSTANCE_STATUS_WAIT_TIMEOUT = 180
INSTANCE_STATUS_POLL_INTERVAL = 3
CPU_PLATFORM_WAIT_TIMEOUT = 120
CPU_PLATFORM_POLL_INTERVAL = 2
REROLL_LOOP_COOLDOWN = 15
RETRY_JITTER_RATIO = 0.2
RETRY_JITTER_CAP = 3
COOLDOWN_JITTER_RATIO = 0.15
COOLDOWN_JITTER_CAP = 4
REROLL_RECENT_HISTORY_LIMIT = 8
REMOTE_READY_TIMEOUT = 180
REMOTE_READY_POLL_INTERVAL = 5
REMOTE_PROBE_TIMEOUT = 20
REMOTE_UPLOAD_TIMEOUT = 300
REMOTE_COMMAND_TIMEOUT = 1800
REMOTE_CONFIG_APPLY_TIMEOUT = 300
SSH_CONNECT_TIMEOUT = 10
SSH_SERVER_ALIVE_INTERVAL = 15
SSH_SERVER_ALIVE_COUNT_MAX = 3
SSH_STRICT_HOST_KEY_CHECKING = "accept-new"
SUBPROCESS_ERROR_SUMMARY_LIMIT = 600
SUBPROCESS_ERROR_LINE_LIMIT = 8

DEFAULT_COMPUTE_TRANSPORT = "rest"
DEFAULT_RESOURCEMANAGER_TRANSPORT = "rest"


def get_region_config(region):
    for config in REGION_OPTIONS:
        if config["region"] == region:
            return config
    return None


def resolve_project_path(root_dir, *parts):
    return Path(root_dir, *parts)
