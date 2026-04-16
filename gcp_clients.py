import os

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


def ensure_google_cloud_libraries():
    if compute_v1 and resourcemanager_v3 and google_exceptions:
        return
    raise RuntimeError(IMPORT_ERROR_MESSAGE)


def _transport(env_name, default_value):
    return os.getenv(env_name, default_value)


def projects_client():
    ensure_google_cloud_libraries()
    return resourcemanager_v3.ProjectsClient(
        transport=_transport("GCP_FREE_RESOURCEMANAGER_TRANSPORT", "rest")
    )


def instances_client():
    ensure_google_cloud_libraries()
    return compute_v1.InstancesClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", "rest")
    )


def images_client():
    ensure_google_cloud_libraries()
    return compute_v1.ImagesClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", "rest")
    )


def zones_client():
    ensure_google_cloud_libraries()
    return compute_v1.ZonesClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", "rest")
    )


def zone_operations_client():
    ensure_google_cloud_libraries()
    return compute_v1.ZoneOperationsClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", "rest")
    )


def global_operations_client():
    ensure_google_cloud_libraries()
    return compute_v1.GlobalOperationsClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", "rest")
    )


def firewalls_client():
    ensure_google_cloud_libraries()
    return compute_v1.FirewallsClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", "rest")
    )


def disks_client():
    ensure_google_cloud_libraries()
    return compute_v1.DisksClient(
        transport=_transport("GCP_FREE_COMPUTE_TRANSPORT", "rest")
    )
