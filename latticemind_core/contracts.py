"""Stable, dependency-free control-plane contracts."""
from __future__ import annotations

from dataclasses import dataclass, asdict, fields
from enum import Enum, IntEnum
import json
from typing import Any, Mapping


def canonical_json(value: Any) -> str:
    """Return deterministic JSON (UTF-8 text, no insignificant whitespace)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class PermissionProfile(str, Enum):
    OBSERVE = "observe"
    SAFE_WRITE = "safe-write"
    MANAGED_WRITE = "managed-write"
    FULL = "full"


class ExitCode(IntEnum):
    OK = 0
    DEGRADED = 2
    DISABLED = 3
    USAGE = 64
    CORRUPT_INPUT = 65
    RUNTIME_UNAVAILABLE = 69
    BLOCKED = 69
    PERMISSION = 73
    IO_ERROR = 74
    LOCKED = 75
    TRUST = 76
    SECURITY = 77
    MIGRATION = 78
    FAILED = 78
    TIMEOUT = 124


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    APPLIED = "applied"


def _strict_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


@dataclass(frozen=True)
class JobDefinition:
    job_id: str
    mode: str
    enabled: bool = True
    timeout_seconds: int = 900

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not self.job_id or not isinstance(self.mode, str) or not self.mode:
            raise ValueError("job_id and mode must be non-empty strings")
        if type(self.enabled) is not bool or type(self.timeout_seconds) is not int or self.timeout_seconds <= 0:
            raise ValueError("invalid job definition types")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    status: RunStatus
    mode: str
    source: str = "manual"
    timestamp: str = ""
    exit_code: int = int(ExitCode.OK)
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id or not isinstance(self.mode, str) or not self.mode:
            raise ValueError("run_id and mode must be non-empty strings")
        object.__setattr__(self, "status", RunStatus(self.status))
        if type(self.exit_code) is not int:
            raise ValueError("exit_code must be an integer")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result


@dataclass(frozen=True)
class Status:
    schema: str = "status-v1"
    state: str = "healthy"
    profile: PermissionProfile = PermissionProfile.OBSERVE
    exit_code: int = int(ExitCode.OK)
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", PermissionProfile(self.profile))
        if type(self.exit_code) is not int:
            raise ValueError("exit_code must be an integer")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["profile"] = self.profile.value
        return result


def validate_schema(value: Any, schema: str, allowed: set[str]) -> dict[str, Any]:
    obj = _strict_dict(value, schema)
    if obj.get("schema") != schema:
        raise ValueError(f"unsupported schema: expected {schema}")
    unknown = set(obj) - allowed
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(sorted(unknown))}")
    return obj
