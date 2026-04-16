from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class InstanceInfo:
    name: str
    zone: str
    status: str
    cpu_platform: str
    network: str
    internal_ip: str
    external_ip: str

    @classmethod
    def from_api_instance(cls, instance, zone):
        network = None
        internal_ip = "-"
        external_ip = "-"
        if getattr(instance, "network_interfaces", None):
            network = instance.network_interfaces[0].network
            internal_ip = instance.network_interfaces[0].network_i_p
            access_configs = instance.network_interfaces[0].access_configs
            if access_configs:
                external_ip = access_configs[0].nat_i_p or "-"
        return cls(
            name=instance.name,
            zone=zone,
            status=instance.status,
            cpu_platform=instance.cpu_platform or "Unknown CPU Platform",
            network=network or "global/networks/default",
            internal_ip=internal_ip,
            external_ip=external_ip,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        return cls(
            name=data["name"],
            zone=data["zone"],
            status=data.get("status", "UNKNOWN"),
            cpu_platform=data.get("cpu_platform", "Unknown CPU Platform"),
            network=data.get("network", "global/networks/default"),
            internal_ip=data.get("internal_ip", "-"),
            external_ip=data.get("external_ip", "-"),
        )

    def to_dict(self):
        return {
            "name": self.name,
            "zone": self.zone,
            "status": self.status,
            "cpu_platform": self.cpu_platform,
            "network": self.network,
            "internal_ip": self.internal_ip,
            "external_ip": self.external_ip,
        }


@dataclass
class RemoteConfig:
    method: str
    user: str = ""
    port: str = "22"
    key: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        return cls(
            method=data["method"],
            user=data.get("user", ""),
            port=str(data.get("port", "22")),
            key=data.get("key", ""),
        )

    def to_dict(self):
        return {
            "method": self.method,
            "user": self.user,
            "port": self.port,
            "key": self.key,
        }


@dataclass
class RerollStats:
    project_id: str
    instance_name: str
    zone: str
    start_time: float
    attempts: int = 0
    exception_count: int = 0
    cpu_counter: Dict[str, int] = field(default_factory=dict)
    recent_results: List[str] = field(default_factory=list)
    recent_errors: List[str] = field(default_factory=list)
    success_cpu: Optional[str] = None
    last_cpu: Optional[str] = None
    last_error: Optional[str] = None
    last_updated: Optional[float] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        return cls(
            project_id=data["project_id"],
            instance_name=data["instance_name"],
            zone=data["zone"],
            start_time=float(data.get("start_time") or 0),
            attempts=int(data.get("attempts") or 0),
            exception_count=int(data.get("exception_count") or 0),
            cpu_counter=dict(data.get("cpu_counter") or {}),
            recent_results=list(data.get("recent_results") or []),
            recent_errors=list(data.get("recent_errors") or []),
            success_cpu=data.get("success_cpu"),
            last_cpu=data.get("last_cpu"),
            last_error=data.get("last_error"),
            last_updated=data.get("last_updated"),
        )

    def to_dict(self):
        return {
            "project_id": self.project_id,
            "instance_name": self.instance_name,
            "zone": self.zone,
            "start_time": self.start_time,
            "attempts": self.attempts,
            "exception_count": self.exception_count,
            "cpu_counter": dict(self.cpu_counter),
            "recent_results": list(self.recent_results),
            "recent_errors": list(self.recent_errors),
            "success_cpu": self.success_cpu,
            "last_cpu": self.last_cpu,
            "last_error": self.last_error,
            "last_updated": self.last_updated,
        }


@dataclass
class DoctorCheck:
    name: str
    status: str
    message: str


@dataclass
class ActionSpec:
    key: str
    menu_label: str
    cli_name: Optional[str]
    description: str
    handler_name: str


@dataclass
class RuntimeContext:
    project_id: str
    current_instance: Optional[InstanceInfo] = None
    remote_config_cache: Dict[str, RemoteConfig] = field(default_factory=dict)
