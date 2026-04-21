import os
from functools import lru_cache
from typing import Any

from gcp_config import DEFAULT_COMPUTE_TRANSPORT, DEFAULT_RESOURCEMANAGER_TRANSPORT

IMPORT_ERROR_MESSAGE = (
    "【错误】缺少必要的 Python 库。\n"
    "请先在终端运行以下命令安装：\n"
    "pip install google-cloud-compute google-cloud-resource-manager"
)

try:
    from google.cloud import compute_v1
    from google.cloud import resourcemanager_v3
    from google.api_core import exceptions as google_exceptions
except ImportError:
    compute_v1 = None
    resourcemanager_v3 = None
    google_exceptions = None


def ensure_google_cloud_libraries() -> None:
    if compute_v1 and resourcemanager_v3 and google_exceptions:
        return
    raise RuntimeError(IMPORT_ERROR_MESSAGE)


def _transport(env_name: str, default_value: str) -> str:
    return os.getenv(env_name, default_value)


@lru_cache(maxsize=1)
def projects_client() -> Any:
    ensure_google_cloud_libraries()
    return resourcemanager_v3.ProjectsClient(
        transport=_transport("GCP_FREE_RESOURCEMANAGER_TRANSPORT", DEFAULT_RESOURCEMANAGER_TRANSPORT)
    )


@lru_cache(maxsize=1)
def instances_client() -> Any:
    ensure_google_cloud_libraries()
    return compute_v1.InstancesClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", DEFAULT_COMPUTE_TRANSPORT)
    )


@lru_cache(maxsize=1)
def images_client() -> Any:
    ensure_google_cloud_libraries()
    return compute_v1.ImagesClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", DEFAULT_COMPUTE_TRANSPORT)
    )


@lru_cache(maxsize=1)
def zones_client() -> Any:
    ensure_google_cloud_libraries()
    return compute_v1.ZonesClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", DEFAULT_COMPUTE_TRANSPORT)
    )


@lru_cache(maxsize=1)
def zone_operations_client() -> Any:
    ensure_google_cloud_libraries()
    return compute_v1.ZoneOperationsClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", DEFAULT_COMPUTE_TRANSPORT)
    )


@lru_cache(maxsize=1)
def global_operations_client() -> Any:
    ensure_google_cloud_libraries()
    return compute_v1.GlobalOperationsClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", DEFAULT_COMPUTE_TRANSPORT)
    )


@lru_cache(maxsize=1)
def firewalls_client() -> Any:
    ensure_google_cloud_libraries()
    return compute_v1.FirewallsClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", DEFAULT_COMPUTE_TRANSPORT)
    )


@lru_cache(maxsize=1)
def disks_client() -> Any:
    ensure_google_cloud_libraries()
    return compute_v1.DisksClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", DEFAULT_COMPUTE_TRANSPORT)
    )


def clear_google_cloud_client_caches() -> None:
    projects_client.cache_clear()
    instances_client.cache_clear()
    images_client.cache_clear()
    zones_client.cache_clear()
    zone_operations_client.cache_clear()
    global_operations_client.cache_clear()
    firewalls_client.cache_clear()
    disks_client.cache_clear()
