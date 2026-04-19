from __future__ import annotations

from gcp_common import (
    Any,
    INSTANCE_API_MAX_RETRIES,
    INSTANCE_API_RETRY_BASE_DELAY,
    INSTANCE_CONFLICT_RETRY_DELAY,
    INSTANCE_GET_REQUEST_TIMEOUT,
    INSTANCE_MUTATION_REQUEST_TIMEOUT,
    OPERATION_GET_REQUEST_TIMEOUT,
    OPERATION_POLL_INTERVAL,
    OPERATION_WAIT_REQUEST_TIMEOUT,
    OPERATION_WAIT_TIMEOUT,
    RESOURCE_LIST_REQUEST_TIMEOUT,
    RESOURCE_READ_REQUEST_TIMEOUT,
    global_operations_client,
    google_exceptions,
    requests_exceptions,
    time,
    urllib3_exceptions,
    zone_operations_client,
)
from gcp_utils import (
    apply_jitter,
    format_seconds,
    print_info,
    print_warning,
    summarize_exception,
    warn_if_long_pause,
)

__all__ = [
    'is_transient_gcp_error',
    'is_operation_in_progress_error',
    'extract_operation_error',
    'ensure_operation_success',
    'wait_for_operation_result',
    'wait_for_operation',
    'wait_for_global_operation',
    'call_with_retries',
    'get_instance_with_retry',
    'start_instance_with_retry',
    'stop_instance_with_retry',
    'insert_instance_with_retry',
    'delete_instance_with_retry',
    'get_image_from_family_with_retry',
    'insert_firewall_with_retry',
    'delete_firewall_with_retry',
    'delete_disk_with_retry',
    'search_projects_with_retry',
    'list_zones_with_retry',
    'aggregated_list_instances_with_retry',
]

def is_transient_gcp_error(exc: Any) -> Any:
    transient_error_types = []
    if google_exceptions:
        transient_error_types.extend(
            [
                google_exceptions.BadGateway,
                google_exceptions.DeadlineExceeded,
                google_exceptions.GatewayTimeout,
                google_exceptions.ServiceUnavailable,
                google_exceptions.TooManyRequests,
            ]
        )
    if requests_exceptions:
        transient_error_types.extend(
            [
                requests_exceptions.ConnectionError,
                requests_exceptions.Timeout,
                requests_exceptions.ConnectTimeout,
                requests_exceptions.ReadTimeout,
                requests_exceptions.ChunkedEncodingError,
            ]
        )
    if urllib3_exceptions:
        transient_error_types.extend(
            [
                urllib3_exceptions.HTTPError,
                urllib3_exceptions.MaxRetryError,
                urllib3_exceptions.ProtocolError,
                urllib3_exceptions.ReadTimeoutError,
                urllib3_exceptions.ConnectTimeoutError,
                urllib3_exceptions.NewConnectionError,
            ]
        )

    if transient_error_types and isinstance(exc, tuple(transient_error_types)):
        return True

    message = str(exc).lower()
    transient_markers = [
        " 429 ",
        " 502 ",
        " 503 ",
        " 504 ",
        "try again in 30 seconds",
        "httpsconnectionpool",
        "max retries exceeded",
        "failed to establish a new connection",
        "connection aborted",
        "connection reset by peer",
        "remote end closed connection",
        "read timed out",
        "connect timeout",
        "connection broken",
        "temporary failure in name resolution",
        "name or service not known",
        "nodename nor servname provided",
    ]
    return any(marker in message for marker in transient_markers)

def is_operation_in_progress_error(exc: Any) -> Any:
    if google_exceptions and isinstance(exc, google_exceptions.Conflict):
        return True

    message = str(exc).lower()
    conflict_markers = [
        " 409 ",
        "already in progress",
        "already being used by an operation",
        "operation is already in progress",
        "operationinprogress",
        "resource not ready",
        "resource_not_ready",
        "resourceinusebyanotherresource",
    ]
    return any(marker in message for marker in conflict_markers)

def extract_operation_error(operation: Any) -> Any:
    operation_error = getattr(operation, "error", None)
    if operation_error and getattr(operation_error, "errors", None):
        error_messages = []
        for item in operation_error.errors:
            code = getattr(item, "code", "") or "UNKNOWN"
            message = getattr(item, "message", "") or "未知错误"
            error_messages.append(f"{code}: {message}")
        if error_messages:
            return "; ".join(error_messages)

    http_status = getattr(operation, "http_error_status_code", None)
    http_message = getattr(operation, "http_error_message", None)
    if http_status or http_message:
        return f"{http_status or ''} {http_message or ''}".strip()

    return ""

def ensure_operation_success(operation: Any,  operation_desc: Any) -> Any:
    error_message = extract_operation_error(operation)
    if error_message:
        raise RuntimeError(f"{operation_desc}失败: {error_message}")

def wait_for_operation_result( operation_client: Any,  operation_desc: Any,  timeout: Any=OPERATION_WAIT_TIMEOUT,  poll_interval: Any=OPERATION_POLL_INTERVAL,  **kwargs: Any,  ) -> Any:
    deadline = time.time() + timeout
    last_error = None
    last_activity_time = time.time()

    try:
        wait_started_at = time.time()
        operation = operation_client.wait(timeout=OPERATION_WAIT_REQUEST_TIMEOUT, **kwargs)
        last_activity_time = warn_if_long_pause(wait_started_at, f"{operation_desc} 的 wait 接口调用")
        ensure_operation_success(operation, operation_desc)
        return operation
    except Exception as exc:
        last_activity_time = warn_if_long_pause(
            last_activity_time,
            f"{operation_desc} 的 wait 接口返回异常前",
        )
        if not is_transient_gcp_error(exc):
            raise
        last_error = exc
        print_warning(
            f"{operation_desc} 的 wait 接口暂时不可用，改用轮询继续等待: {summarize_exception(exc)}"
        )

    while time.time() < deadline:
        last_activity_time = warn_if_long_pause(last_activity_time, f"等待 {operation_desc} 完成")
        try:
            get_started_at = time.time()
            operation = operation_client.get(timeout=OPERATION_GET_REQUEST_TIMEOUT, **kwargs)
            last_activity_time = warn_if_long_pause(get_started_at, f"{operation_desc} 状态轮询")
        except Exception as exc:
            last_activity_time = warn_if_long_pause(
                last_activity_time,
                f"{operation_desc} 状态轮询异常前",
            )
            if not is_transient_gcp_error(exc):
                raise
            last_error = exc
            sleep_time = apply_jitter(poll_interval)
            print_warning(
                f"{operation_desc} 轮询状态时遇到临时错误，约 {format_seconds(sleep_time)} 秒后重试: "
                f"{summarize_exception(exc)}"
            )
            time.sleep(sleep_time)
            continue

        if getattr(operation, "status", None) == "DONE":
            ensure_operation_success(operation, operation_desc)
            return operation

        time.sleep(poll_interval)

    if last_error:
        raise TimeoutError(f"{operation_desc}等待超时，最后一次错误: {summarize_exception(last_error)}") from last_error
    raise TimeoutError(f"{operation_desc}等待超时。")

def wait_for_operation(project_id: Any,  zone: Any,  operation_name: Any,  operation_desc: Any="区域操作") -> Any:
    operation_client = zone_operations_client()
    return wait_for_operation_result(
        operation_client,
        operation_desc,
        project=project_id,
        zone=zone,
        operation=operation_name,
    )

def wait_for_global_operation(project_id: Any,  operation_name: Any,  operation_desc: Any="全局操作") -> Any:
    operation_client = global_operations_client()
    return wait_for_operation_result(
        operation_client,
        operation_desc,
        project=project_id,
        operation=operation_name,
    )

def call_with_retries( action_desc: Any,  func: Any,  max_retries: Any=INSTANCE_API_MAX_RETRIES,  base_delay: Any=INSTANCE_API_RETRY_BASE_DELAY,  ) -> Any:
    last_error = None

    for attempt in range(max_retries):
        try:
            return func()
        except Exception as exc:
            if not is_transient_gcp_error(exc) and not is_operation_in_progress_error(exc):
                raise

            last_error = exc
            current_try = attempt + 1
            if current_try >= max_retries:
                break

            if is_operation_in_progress_error(exc):
                sleep_time = apply_jitter(INSTANCE_CONFLICT_RETRY_DELAY)
                print_warning(
                    f"{action_desc} 遇到资源冲突，可能已有操作正在进行，准备重试 "
                    f"({current_try}/{max_retries}): {summarize_exception(exc)}"
                )
            else:
                sleep_time = apply_jitter(base_delay * current_try)
                print_warning(
                    f"{action_desc} 遇到临时错误，准备重试 ({current_try}/{max_retries}): "
                    f"{summarize_exception(exc)}"
                )
            print_info(f"等待约 {format_seconds(sleep_time)} 秒后继续 {action_desc}...")
            time.sleep(sleep_time)

    raise RuntimeError(
        f"{action_desc} 在 {max_retries} 次尝试后仍失败: {summarize_exception(last_error)}"
    ) from last_error

def get_instance_with_retry(instance_client: Any,  project_id: Any,  zone: Any,  instance_name: Any) -> Any:
    return call_with_retries(
        f"获取实例 {instance_name} 状态",
        lambda: instance_client.get(
            project=project_id,
            zone=zone,
            instance=instance_name,
            timeout=INSTANCE_GET_REQUEST_TIMEOUT,
        ),
    )

def start_instance_with_retry(instance_client: Any,  project_id: Any,  zone: Any,  instance_name: Any) -> Any:
    return call_with_retries(
        f"启动虚拟机 {instance_name}",
        lambda: instance_client.start(
            project=project_id,
            zone=zone,
            instance=instance_name,
            timeout=INSTANCE_MUTATION_REQUEST_TIMEOUT,
        ),
    )

def stop_instance_with_retry(instance_client: Any,  project_id: Any,  zone: Any,  instance_name: Any) -> Any:
    return call_with_retries(
        f"关停虚拟机 {instance_name}",
        lambda: instance_client.stop(
            project=project_id,
            zone=zone,
            instance=instance_name,
            timeout=INSTANCE_MUTATION_REQUEST_TIMEOUT,
        ),
    )

def insert_instance_with_retry(instance_client: Any,  project_id: Any,  zone: Any,  instance_resource: Any) -> Any:
    instance_name = getattr(instance_resource, "name", "未命名实例")
    return call_with_retries(
        f"创建实例 {instance_name}",
        lambda: instance_client.insert(
            project=project_id,
            zone=zone,
            instance_resource=instance_resource,
            timeout=INSTANCE_MUTATION_REQUEST_TIMEOUT,
        ),
    )

def delete_instance_with_retry(instance_client: Any,  project_id: Any,  zone: Any,  instance_name: Any) -> Any:
    return call_with_retries(
        f"删除实例 {instance_name}",
        lambda: instance_client.delete(
            project=project_id,
            zone=zone,
            instance=instance_name,
            timeout=INSTANCE_MUTATION_REQUEST_TIMEOUT,
        ),
    )

def get_image_from_family_with_retry(images_client: Any,  project: Any,  family: Any) -> Any:
    return call_with_retries(
        f"获取镜像族 {project}/{family}",
        lambda: images_client.get_from_family(
            project=project,
            family=family,
            timeout=RESOURCE_READ_REQUEST_TIMEOUT,
        ),
    )

def insert_firewall_with_retry(firewall_client: Any,  project_id: Any,  firewall_rule: Any) -> Any:
    rule_name = getattr(firewall_rule, "name", "未命名规则")
    return call_with_retries(
        f"创建防火墙规则 {rule_name}",
        lambda: firewall_client.insert(
            project=project_id,
            firewall_resource=firewall_rule,
            timeout=INSTANCE_MUTATION_REQUEST_TIMEOUT,
        ),
    )

def delete_firewall_with_retry(firewall_client: Any,  project_id: Any,  rule_name: Any) -> Any:
    return call_with_retries(
        f"删除防火墙规则 {rule_name}",
        lambda: firewall_client.delete(
            project=project_id,
            firewall=rule_name,
            timeout=INSTANCE_MUTATION_REQUEST_TIMEOUT,
        ),
    )

def delete_disk_with_retry(disk_client: Any,  project_id: Any,  zone: Any,  disk_name: Any) -> Any:
    return call_with_retries(
        f"删除磁盘 {disk_name}",
        lambda: disk_client.delete(
            project=project_id,
            zone=zone,
            disk=disk_name,
            timeout=INSTANCE_MUTATION_REQUEST_TIMEOUT,
        ),
    )

def search_projects_with_retry(projects_client: Any,  request: Any) -> Any:
    return call_with_retries(
        "扫描 GCP 项目列表",
        lambda: list(
            projects_client.search_projects(
                request=request,
                timeout=RESOURCE_LIST_REQUEST_TIMEOUT,
            )
        ),
    )

def list_zones_with_retry(zones_client: Any,  project_id: Any) -> Any:
    return call_with_retries(
        f"获取项目 {project_id} 的可用区列表",
        lambda: list(
            zones_client.list(
                project=project_id,
                timeout=RESOURCE_LIST_REQUEST_TIMEOUT,
            )
        ),
    )

def aggregated_list_instances_with_retry(instance_client: Any,  request: Any,  project_id: Any) -> Any:
    return call_with_retries(
        f"扫描项目 {project_id} 的实例列表",
        lambda: list(
            instance_client.aggregated_list(
                request=request,
                timeout=RESOURCE_LIST_REQUEST_TIMEOUT,
            )
        ),
    )
