from pathlib import Path
import sys
from typing import Optional

# 远程脚本映射：CLI 和菜单都通过这个表选择本地脚本文件。
LOCAL_SCRIPT_FILES = {
    "apt": "apt.sh",
    "dae": "dae.sh",
    "net_iptables": "net_iptables.sh",
    "net_shutdown": "net_shutdown.sh",
}

# 流量监控默认限额，单位 GB；远程上传脚本前会用这个值覆盖脚本里的兜底默认值。
TRAFFIC_LIMIT_GB = 180

# GCP Compute Engine 免费层支持的区域（e2-micro 免费）
FREE_TIER_REGIONS = [
    {"name": "俄勒冈 (Oregon) [美国] - 免费层", "region": "us-west1", "default_zone": "us-west1-b"},
    {"name": "爱荷华 (Iowa) [美国] - 免费层", "region": "us-central1", "default_zone": "us-central1-f"},
    {"name": "南卡罗来纳 (South Carolina) [美国] - 免费层", "region": "us-east1", "default_zone": "us-east1-b"},
]

# 付费区域（同样使用 e2-micro，但会产生费用）
PAID_REGIONS = [
    {"name": "悉尼 (Sydney) [澳洲] - 付费", "region": "australia-southeast1", "default_zone": "australia-southeast1-a"},
    {"name": "墨尔本 (Melbourne) [澳洲] - 付费", "region": "australia-southeast2", "default_zone": "australia-southeast2-a"},
    {"name": "新加坡 (Singapore) [亚洲] - 付费", "region": "asia-southeast1", "default_zone": "asia-southeast1-a"},
    {"name": "东京 (Tokyo) [亚洲] - 付费", "region": "asia-northeast1", "default_zone": "asia-northeast1-a"},
    {"name": "大阪 (Osaka) [亚洲] - 付费", "region": "asia-northeast2", "default_zone": "asia-northeast2-a"},
    {"name": "首尔 (Seoul) [亚洲] - 付费", "region": "asia-northeast3", "default_zone": "asia-northeast3-a"},
    {"name": "香港 (Hong Kong) [亚洲] - 付费", "region": "asia-east2", "default_zone": "asia-east2-a"},
    {"name": "台湾 (Taiwan) [亚洲] - 付费", "region": "asia-east1", "default_zone": "asia-east1-a"},
    {"name": "孟买 (Mumbai) [印度] - 付费", "region": "asia-south1", "default_zone": "asia-south1-a"},
    {"name": "雅加达 (Jakarta) [印尼] - 付费", "region": "asia-southeast2", "default_zone": "asia-southeast2-a"},
    {"name": "洛杉矶 (Los Angeles) [美国] - 付费", "region": "us-west2", "default_zone": "us-west2-a"},
    {"name": "盐湖城 (Salt Lake City) [美国] - 付费", "region": "us-west3", "default_zone": "us-west3-a"},
    {"name": "拉斯维加斯 (Las Vegas) [美国] - 付费", "region": "us-west4", "default_zone": "us-west4-a"},
    {"name": "北弗吉尼亚 (N. Virginia) [美国] - 付费", "region": "us-east4", "default_zone": "us-east4-a"},
    {"name": "法兰克福 (Frankfurt) [欧洲] - 付费", "region": "europe-west3", "default_zone": "europe-west3-a"},
    {"name": "伦敦 (London) [欧洲] - 付费", "region": "europe-west2", "default_zone": "europe-west2-a"},
    {"name": "荷兰 (Netherlands) [欧洲] - 付费", "region": "europe-west4", "default_zone": "europe-west4-a"},
]

# 所有区域的合并列表（向后兼容）
REGION_OPTIONS = FREE_TIER_REGIONS + PAID_REGIONS

# 当前支持的系统镜像选项。
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
DEFAULT_REROLL_IP_STATE_FILE = "reroll_ip_state.json"
DEFAULT_REROLL_IP_AMD_STATE_FILE = "reroll_ip_amd_state.json"

# GCP operation 等待参数：用于 start/stop/create/delete 等长操作。
OPERATION_WAIT_TIMEOUT = 300
OPERATION_POLL_INTERVAL = 3
OPERATION_WAIT_REQUEST_TIMEOUT = 20
OPERATION_GET_REQUEST_TIMEOUT = 10

# 长时间停顿检测：用于标记进程可能被冻结、远程会话挂起，或单次 API 调用异常阻塞。
LONG_PAUSE_WARNING_THRESHOLD = 30

# 通用 API 重试参数：这里只兜底瞬时错误和资源冲突，避免高频请求把 502/409 顶得更高。
INSTANCE_API_MAX_RETRIES = 4
INSTANCE_API_RETRY_BASE_DELAY = 3
INSTANCE_CONFLICT_RETRY_DELAY = 5
INSTANCE_GET_REQUEST_TIMEOUT = 10
INSTANCE_MUTATION_REQUEST_TIMEOUT = 20
RESOURCE_LIST_REQUEST_TIMEOUT = 20
RESOURCE_READ_REQUEST_TIMEOUT = 15

# 实例状态轮询参数：这里控制 RUNNING/STOPPED 等状态切换的观察频率。
# 目前偏激进，优先追求刷 CPU 周期更短；如果后续 429/502 明显增多，再适当调大。
INSTANCE_STATUS_WAIT_TIMEOUT = 180
INSTANCE_STATUS_POLL_INTERVAL = 0.5
INSTANCE_STATUS_HEARTBEAT_INTERVAL = 5
INSTANCE_TRANSITION_CONFIRM_TIMEOUT = 6
INSTANCE_TRANSITION_CONFIRM_POLL_INTERVAL = 0.5

# CPU 平台同步参数：实例刚 RUNNING 时 cpu_platform 可能还没同步出来。
CPU_PLATFORM_WAIT_TIMEOUT = 120
CPU_PLATFORM_POLL_INTERVAL = 0.5

# 刷 CPU 冷却参数：
# - 正常轮次尽量短，追求更快重刷
# - 异常轮次保守一些，给 GCP 后端一点恢复时间
# - 如果本轮已经在 STOPPING/STOPPED 上耗了较久，就直接进入下一轮，不再额外睡眠
REROLL_LOOP_COOLDOWN = 1
REROLL_ERROR_COOLDOWN = 6
REROLL_POST_STOP_FAST_COOLDOWN = 0
REROLL_STOP_WAIT_THRESHOLD = 4

# OAuth 熔断参数：如果认证链路连续多轮超时，就暂停更久再试，
# 避免每轮都把 4 次重试额度耗光并污染异常统计。
OAUTH_CIRCUIT_BREAKER_THRESHOLD = 3
OAUTH_CIRCUIT_BREAKER_BASE_COOLDOWN = 60
OAUTH_CIRCUIT_BREAKER_STEP_COOLDOWN = 30
OAUTH_CIRCUIT_BREAKER_MAX_COOLDOWN = 180

# 抖动参数：避免所有请求都精确打在固定秒数上，减少和后端限流节奏“撞车”。
RETRY_JITTER_RATIO = 0.15
RETRY_JITTER_CAP = 2
COOLDOWN_JITTER_RATIO = 0.1
COOLDOWN_JITTER_CAP = 1
REROLL_RECENT_HISTORY_LIMIT = 8

# 远程连接参数：控制 SSH 就绪探测、上传和远程执行的超时。
REMOTE_READY_TIMEOUT = 180
REMOTE_READY_POLL_INTERVAL = 5
REMOTE_PROBE_TIMEOUT = 20
REMOTE_UPLOAD_TIMEOUT = 300
REMOTE_COMMAND_TIMEOUT = 1800
REMOTE_CONFIG_APPLY_TIMEOUT = 300

# SSH 稳定性参数：本地直连和 gcloud passthrough 都会共用。
SSH_CONNECT_TIMEOUT = 10
SSH_SERVER_ALIVE_INTERVAL = 15
SSH_SERVER_ALIVE_COUNT_MAX = 3
SSH_STRICT_HOST_KEY_CHECKING = "accept-new"

# 子进程错误摘要上限：控制日志可读性，避免整屏 stderr 直接灌出来。
SUBPROCESS_ERROR_SUMMARY_LIMIT = 600
SUBPROCESS_ERROR_LINE_LIMIT = 8

# 默认强制走 REST transport，避开部分 Windows / 本地环境下的 gRPC 连接问题。
DEFAULT_COMPUTE_TRANSPORT = "rest"
DEFAULT_RESOURCEMANAGER_TRANSPORT = "rest"


def get_region_config(region: str) -> Optional[dict]:
    for config in REGION_OPTIONS:
        if config["region"] == region:
            return config
    return None


def resolve_project_path(root_dir: Path | str, *parts: str) -> Path:
    return Path(root_dir, *parts)


def get_bundle_root() -> Path:
    # PyInstaller onefile 运行时，静态资源会先解包到 _MEIPASS。
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def get_runtime_root() -> Path:
    # 日志、状态文件和用户可覆盖的配置，统一落在 exe 所在目录。
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resolve_asset_path(*parts: str) -> Path:
    # 优先读取 exe 同目录下的外部资源，便于用户直接替换模板或配置。
    runtime_path = get_runtime_root().joinpath(*parts)
    if runtime_path.exists():
        return runtime_path
    return get_bundle_root().joinpath(*parts)
