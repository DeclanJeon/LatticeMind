"""Capability policy for jobs and backend processes."""
from __future__ import annotations
from dataclasses import dataclass
from .contracts import PermissionProfile

@dataclass(frozen=True)
class JobGrant:
    job_id: str
    profile: PermissionProfile
    allow_vault_read: bool = False
    allow_metadata_write: bool = False
    allow_create: bool = False
    allow_delete: bool = False
    allow_managed_write: bool = False
    scheduled: bool = False

_PROFILES = {
    PermissionProfile.OBSERVE: (True, False, False, False, False),
    PermissionProfile.SAFE_WRITE: (True, False, True, False, False),
    PermissionProfile.MANAGED_WRITE: (True, True, True, False, True),
    PermissionProfile.FULL: (True, True, True, False, True),
}

def validate_profile(value: str | PermissionProfile) -> PermissionProfile:
    try: return PermissionProfile(value)
    except (ValueError, TypeError) as exc: raise ValueError("invalid permission profile") from exc

def grant_for_job(job_id: str, profile: str | PermissionProfile, *, scheduled: bool = False) -> JobGrant:
    if not isinstance(job_id, str) or not job_id or any(c in job_id for c in "\\/\x00"):
        raise ValueError("invalid job id")
    p = validate_profile(profile)
    read, metadata, create, delete, managed = _PROFILES[p]
    if scheduled and p is not PermissionProfile.OBSERVE:
        raise PermissionError("scheduled jobs require observe profile")
    return JobGrant(job_id, p, read, metadata, create, delete, managed, scheduled)

def require_capability(grant: JobGrant, capability: str) -> None:
    if capability not in {"vault_read", "metadata_write", "create", "delete", "managed_write"}:
        raise ValueError("unknown capability")
    if not getattr(grant, "allow_" + capability):
        raise PermissionError(f"capability denied: {capability}")

def observe_allowed(grant: JobGrant) -> bool:
    return (
        grant.profile is PermissionProfile.OBSERVE
        and grant.scheduled
        and grant.allow_vault_read
        and not any((grant.allow_metadata_write, grant.allow_create, grant.allow_delete, grant.allow_managed_write))
    )
