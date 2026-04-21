from __future__ import annotations

# 集中管理职责模块共享依赖，避免拆分后的重复 import 块在多个文件中同时维护。
# 这里有意重导出较多符号，职责模块按需显式导入；因此对未直接使用的导入关闭 F401。

import argparse
from collections import Counter
import getpass
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from argparse import Namespace
from typing import Any, Optional

try:
    from requests import exceptions as requests_exceptions
except ImportError:
    requests_exceptions = None

try:
    from urllib3 import exceptions as urllib3_exceptions
except ImportError:
    urllib3_exceptions = None

from gcp_clients import (
    IMPORT_ERROR_MESSAGE,
    clear_google_cloud_client_caches,
    compute_v1,
    disks_client,
    ensure_google_cloud_libraries as ensure_client_libraries,
    firewalls_client,
    global_operations_client,
    google_exceptions,
    images_client,
    instances_client,
    projects_client,
    resourcemanager_v3,
    zone_operations_client,
    zones_client,
)
from gcp_config import (
    COOLDOWN_JITTER_CAP,
    COOLDOWN_JITTER_RATIO,
    CPU_PLATFORM_POLL_INTERVAL,
    CPU_PLATFORM_WAIT_TIMEOUT,
    DEFAULT_REROLL_STATE_FILE,
    DEFAULT_REROLL_IP_AMD_STATE_FILE,
    DEFAULT_REROLL_IP_STATE_FILE,
    FIREWALL_RULES_TO_CLEAN,
    INSTANCE_API_MAX_RETRIES,
    INSTANCE_API_RETRY_BASE_DELAY,
    INSTANCE_CONFLICT_RETRY_DELAY,
    INSTANCE_GET_REQUEST_TIMEOUT,
    INSTANCE_MUTATION_REQUEST_TIMEOUT,
    INSTANCE_TRANSITION_CONFIRM_POLL_INTERVAL,
    INSTANCE_TRANSITION_CONFIRM_TIMEOUT,
    INSTANCE_STATUS_HEARTBEAT_INTERVAL,
    INSTANCE_STATUS_POLL_INTERVAL,
    INSTANCE_STATUS_WAIT_TIMEOUT,
    LOCAL_SCRIPT_FILES,
    LOG_DIR_NAME,
    LONG_PAUSE_WARNING_THRESHOLD,
    OPERATION_GET_REQUEST_TIMEOUT,
    OPERATION_POLL_INTERVAL,
    OPERATION_WAIT_REQUEST_TIMEOUT,
    OPERATION_WAIT_TIMEOUT,
    OAUTH_CIRCUIT_BREAKER_BASE_COOLDOWN,
    OAUTH_CIRCUIT_BREAKER_MAX_COOLDOWN,
    OAUTH_CIRCUIT_BREAKER_STEP_COOLDOWN,
    OAUTH_CIRCUIT_BREAKER_THRESHOLD,
    OS_IMAGE_OPTIONS,
    REGION_OPTIONS,
    REMOTE_COMMAND_TIMEOUT,
    REMOTE_CONFIG_APPLY_TIMEOUT,
    REMOTE_PROBE_TIMEOUT,
    REMOTE_READY_POLL_INTERVAL,
    REMOTE_READY_TIMEOUT,
    REMOTE_UPLOAD_TIMEOUT,
    REQUIREMENTS_FILE,
    RESOURCE_LIST_REQUEST_TIMEOUT,
    RESOURCE_READ_REQUEST_TIMEOUT,
    RETRY_JITTER_CAP,
    RETRY_JITTER_RATIO,
    REROLL_ERROR_COOLDOWN,
    REROLL_LOOP_COOLDOWN,
    REROLL_POST_STOP_FAST_COOLDOWN,
    REROLL_RECENT_HISTORY_LIMIT,
    REROLL_STOP_WAIT_THRESHOLD,
    SSH_CONNECT_TIMEOUT,
    SSH_SERVER_ALIVE_COUNT_MAX,
    SSH_SERVER_ALIVE_INTERVAL,
    SSH_STRICT_HOST_KEY_CHECKING,
    STATE_DIR_NAME,
    SUBPROCESS_ERROR_LINE_LIMIT,
    SUBPROCESS_ERROR_SUMMARY_LIMIT,
    TRAFFIC_LIMIT_GB,
    get_region_config as get_region_config_from_config,
    get_runtime_root,
    resolve_asset_path,
)
from gcp_doctor import find_gcloud_command, run_doctor
from gcp_ips import update_cdnip_file
from gcp_logging import configure_logger, get_logger
from gcp_models import ActionSpec, DoctorCheck, InstanceInfo, RemoteConfig, RerollStats, RuntimeContext
from gcp_state import load_json_state, save_json_state

LOGGER = get_logger()
