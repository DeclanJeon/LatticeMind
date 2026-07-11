"""Public lifecycle CLI."""
from __future__ import annotations
import argparse, hashlib, json, os, sys, tempfile
from pathlib import Path
from typing import Any
from .config import load_config, config_bytes
from .contracts import ExitCode, canonical_json
from .freshness import FreshnessError, SecurityError, scan
from .permissions import grant_for_job, require_capability
from .approval import consume, issue_challenge, reserve
from .apply import apply_file
from .state import redact
from .jobs import desired_jobs, status_jobs, render_systemd, render_launchd, render_task_scheduler, reinstall_owned_jobs, OWNER
from .update import check_update, apply_update, rollback as rollback_update, UpdateError
from .release import TrustError, ArchiveError
from .migrate import migrate_install
from .backends import ADAPTERS, probe_version


def _parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog="latticemind"); s=p.add_subparsers(dest="command",required=True)
    q=s.add_parser("status"); q.add_argument("--json",action="store_true",dest="as_json"); q.add_argument("--profile",choices=("observe","safe-write","managed-write","full","disabled"))
    q=s.add_parser("validate"); q.add_argument("--json",action="store_true",dest="as_json"); q.add_argument("--profile",choices=("observe","safe-write","managed-write","full","disabled"))
    q=s.add_parser("schedule"); q.add_argument("action",choices=("status","render","install"),default="status",nargs="?"); q.add_argument("--platform",choices=("systemd","launchd","windows"),default="systemd"); q.add_argument("--job",default=None)
    q=s.add_parser("update"); g=q.add_mutually_exclusive_group(required=True); g.add_argument("--check",action="store_true"); g.add_argument("--apply",action="store_true"); q.add_argument("--manifest",required=True); q.add_argument("--signature",required=True); q.add_argument("--asset"); q.add_argument("--install-root"); q.add_argument("--snapshot-root"); q.add_argument("--config-v1"); q.add_argument("--manifest-v1"); q.add_argument("--scheduler-export"); q.add_argument("--integration-state"); q.add_argument("--current-pointer")
    q=s.add_parser("rollback"); q.add_argument("--install-root",required=True); q.add_argument("--snapshot",required=True); q.add_argument("--manifest",required=True); q.add_argument("--signature",required=True); q.add_argument("--compatible-version")
    q=s.add_parser("migrate"); q.add_argument("--config-root",required=True); q.add_argument("--vault-root"); q.add_argument("--source"); q.add_argument("--platform",choices=("unix","windows"),default="unix")
    f=s.add_parser("freshness"); a=f.add_subparsers(dest="action",required=True); q=a.add_parser("scan"); q.add_argument("--vault"); q.add_argument("--output-dir")
    q=a.add_parser("challenge"); q.add_argument("run_id"); q.add_argument("--proposal",required=True); q.add_argument("--target",required=True); q.add_argument("--install-id")
    q=a.add_parser("apply"); q.add_argument("run_id"); q.add_argument("--proposal",required=True); q.add_argument("--target",required=True); q.add_argument("--approval-id",required=True); q.add_argument("--approve",required=True); q.add_argument("--yes",action="store_true")
    return p


def _config()->dict[str,Any]:
    explicit=os.environ.get("LATTICEMIND_CONFIG")
    if explicit:return load_config(explicit)
    path=Path(os.environ.get("XDG_CONFIG_HOME",Path.home()/".config"))/"latticemind/config-v1.json"
    return load_config(path) if path.exists() else {"schema":"config-v1","vault_path":os.environ.get("LATTICEMIND_VAULT",str(Path.home()/"Obsidian")),"profile":"observe","enabled_jobs":[]}

def _state_root()->Path:return Path(os.environ.get("LATTICEMIND_STATE_ROOT",Path(os.environ.get("XDG_DATA_HOME",Path.home()/".local/share"))/"latticemind"))
def _trusted_rollback_inputs(config: dict[str, Any]) -> tuple[dict[str, str], str]:
    """Read rollback destinations and its HMAC key only from authenticated state."""
    state_path = _state_root() / "state-v1.json"
    if not state_path.is_file():
        raise UpdateError("trusted rollback state required")
    state = _json_file(str(state_path))
    paths = state.get("component_allowlist")
    key = state.get("state_key_path")
    if not isinstance(paths, dict) or not paths or not isinstance(key, str) or not key:
        raise UpdateError("trusted rollback destinations and state key required")
    canonical = {}
    for name, value in paths.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise UpdateError("invalid trusted rollback destination")
        canonical[name] = str(Path(value).expanduser().resolve())
    return canonical, str(Path(key).expanduser().resolve())

def _json_file(path:str)->Any:return json.loads(Path(path).read_text(encoding="utf-8"))
def _cow_config(path: str) -> None:
    source = Path(path)
    data = load_config(source)
    payload = config_bytes(data)
    fd, temporary = tempfile.mkstemp(prefix=".config-v1-", dir=str(source.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, source)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def _require_callback(callback, name: str):
    result = callback()
    if result is False:
        raise UpdateError(f"{name} callback failed")
    return result
def _run_migration(path, manifest):
    source = Path(path)
    data = load_config(source)
    data["install_version"] = manifest["version"]
    data["trust"] = {
        "state": "verified",
        "key_id": manifest["key_id"],
        "manifest_sha256": hashlib.sha256(canonical_json(manifest).encode()).hexdigest(),
        "version": manifest["version"],
    }
    payload = config_bytes(data)
    fd, temporary = tempfile.mkstemp(prefix=".config-v1-", dir=str(source.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, source)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True

def _validate_lifecycle(config_path, manifest_path, scheduler_path, integration_path, pointer, expected_manifest=None):
    config = load_config(config_path)
    trust = config.get("trust", {})
    if not isinstance(trust, dict) or trust.get("state") not in {"verified", "trusted"}:
        return False
    if expected_manifest is not None:
        expected_digest = hashlib.sha256(canonical_json(expected_manifest).encode()).hexdigest()
        if (
            config.get("install_version") != expected_manifest.get("version")
            or trust.get("version") != expected_manifest.get("version")
            or trust.get("key_id") != expected_manifest.get("key_id")
            or trust.get("manifest_sha256") != expected_digest
        ):
            return False
    pointer = Path(pointer)
    if not pointer.exists() and not pointer.is_symlink():
        return False
    target = Path(os.readlink(pointer)) if pointer.is_symlink() else pointer
    if not target.is_absolute():
        target = pointer.parent / target
    if not target.exists():
        return False
    manifest = _json_file(str(manifest_path))
    if not isinstance(manifest, dict):
        return False
    export = _json_file(str(scheduler_path))
    entries = export.get("jobs", export) if isinstance(export, dict) else export
    if not isinstance(entries, list):
        return False
    effective = _json_file(str(integration_path))
    effective = effective.get("effective_jobs", effective) if isinstance(effective, dict) else effective
    if not isinstance(effective, list) or any(not isinstance(x, dict) for x in effective):
        return False
    entry_ids = {str(x.get("job_id")) for x in entries if isinstance(x, dict)}
    effective_ids = {str(x.get("job_id")) for x in effective}
    if len(entry_ids) != len(entries) or len(effective_ids) != len(effective) or entry_ids != effective_ids:
        return False
    for item in entries:
        if not isinstance(item, dict) or item.get("owner", OWNER) != OWNER:
            return False
    return True
def _status(c:dict[str,Any])->dict[str,Any]:
    root=_state_root(); p=root/"status-v1.json"; d={}
    if p.exists():
        try:d=json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:raise ValueError("corrupt status state") from e
    profile=str(c.get("profile","observe")); jobs=status_jobs(profile); desired=[j for j in jobs if j["enabled"]]
    effective_raw=d.get("effective_jobs",[])
    evidence=d.get("runs",[])
    malformed = not isinstance(effective_raw, list)
    by={str(x.get("job_id")): x for x in evidence if isinstance(x, dict) and x.get("job_id")}
    if not isinstance(evidence, list) or len(by) != len(evidence):
        malformed = True
    effective_by={}
    for item in effective_raw if isinstance(effective_raw, list) else []:
        if not isinstance(item, dict) or not item.get("job_id"):
            malformed = True
        else:
            key = str(item["job_id"])
            if key in effective_by: malformed = True
            effective_by[key] = item
    job_rows=[]; parity=True
    parity = not malformed
    if set(effective_by) != {j["job_id"] for j in desired}:
        parity = False
    for j in desired:
        x={**effective_by.get(j["job_id"],{}), **by.get(j["job_id"],{})}
        matches=all(x.get(k)==j.get(k) for k in ("job_id","mode","owner","schema"))
        present=j["job_id"] in effective_by
        parity=parity and present and matches
        job_rows.append({**j,"effective":present and matches,"last":x.get("last"),"next":x.get("next"),"duration":x.get("duration"),"exit":x.get("exit_code"),"degradation":x.get("degradation")})
    trust=c.get("trust",{"state":"unknown"}); backend=c.get("backend",{"capabilities":[]})
    if not isinstance(trust,dict): trust={"state":"unknown"}
    if not isinstance(backend,dict): backend={}
    degraded=not c.get("install_version") or trust.get("state") not in {"trusted","verified"} or not Path(str(c.get("vault_path",""))).is_dir() or not backend.get("capabilities") or not parity
    state="disabled" if c.get("profile")=="disabled" else ("degraded" if degraded else d.get("state","healthy"))
    code=int(ExitCode.DEGRADED if degraded and state!="disabled" else ExitCode.DISABLED if state=="disabled" else d.get("exit_code",0))
    d=redact(d); d.update({"schema":"status-v1","install":{"id":c.get("install_id"),"version":c.get("install_version","unknown")},"version":c.get("install_version","unknown"),"profile":profile,"trust":trust,"vault":{"read":Path(str(c.get("vault_path",""))).is_dir()},"backend":backend,"desired_jobs":job_rows,"effective_jobs":list(effective_by.values()),"next":d.get("next"),"duration":d.get("duration"),"exit_code":code,"exit_class":"degraded" if degraded else d.get("exit_class","ok"),"counts":{"desired":len(job_rows),"effective":sum(1 for j in job_rows if j["effective"]),"runs":len(evidence)},"stale":d.get("stale",False),"blocked":d.get("blocked",False),"report":d.get("report"),"log":d.get("log"),"update":d.get("update"),"snapshot":d.get("snapshot"),"migration":d.get("migration"),"state":state,"message":d.get("message","")}); return d

def _validate_diagnostics(c: dict[str, Any], profile: str | None = None) -> dict[str, Any]:
    selected = profile or str(c.get("profile", "observe"))
    vault = Path(str(c.get("vault_path", ""))).expanduser()
    state = _state_root()
    config_path = os.environ.get("LATTICEMIND_CONFIG", str(Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "latticemind/config-v1.json"))
    permissions = {}
    for name, path in (("runtime", Path(__file__).resolve().parent), ("config", Path(config_path)), ("vault", vault), ("state", state)):
        try:
            permissions[name] = {"path": str(path), "exists": path.exists(), "read": os.access(path, os.R_OK),
                                 "write": os.access(path, os.W_OK), "mode": oct(path.stat().st_mode & 0o777)}
        except OSError:
            permissions[name] = {"path": str(path), "exists": False, "read": False, "write": False, "mode": None}
    desired = [j for j in status_jobs(selected) if j.get("enabled")]
    current = {}
    status_path = state / "status-v1.json"
    if status_path.is_file():
        try:
            raw = _json_file(str(status_path))
            for item in raw.get("effective_jobs", []) if isinstance(raw, dict) else []:
                if isinstance(item, dict) and item.get("job_id"): current[str(item["job_id"])] = item
                elif isinstance(item, str): current[item] = {"job_id": item}
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    scheduler = {"desired": [j["job_id"] for j in desired], "effective": sorted(current),
                 "in_sync": all(j["job_id"] in current for j in desired)}
    trust = c.get("trust", {"state": "unknown"})
    if not isinstance(trust, dict): trust = {"state": "unknown"}
    configured = c.get("backend", {})
    names = configured.get("backends", configured.get("configured", [])) if isinstance(configured, dict) else configured
    if not isinstance(names, list) or not names: names = list(ADAPTERS)
    backends = []
    for name in names:
        adapter = ADAPTERS.get(str(name))
        row = {"backend": str(name), "verified": False, "blocked_reason": "unsupported_backend"} if adapter is None else probe_version(adapter)
        if not row["verified"]: row["remediation"] = "Install the exact certified version; support remains disabled until its observe contract is verified."
        backends.append(row)
    blocked = []
    if not permissions["vault"]["read"]: blocked.append("vault_unreadable")
    if trust.get("state") not in {"verified", "trusted"}: blocked.append("trust_unverified")
    if not any(bool(x.get("verified")) for x in backends): blocked.append("backend_version_unverified")
    if selected != "observe": blocked.append("profile_requires_explicit_confirmation")
    return {"schema": "validate-v1", "valid": not blocked, "profile": selected, "report_only": True,
            "permissions": permissions, "scheduler": scheduler, "trust": trust,
            "backends": backends, "blocked_reasons": blocked}

def main(argv:list[str]|None=None)->int:
    args=None
    try:
        args=_parser().parse_args(argv); c=_config()
        if args.command=="status":
            if args.profile: c = {**c, "profile": args.profile}
            d=_status(c); print(canonical_json(d) if args.as_json else f"{d['state']} ({d['profile']})"); return int(d["exit_code"])
        if args.command=="validate":
            d = _validate_diagnostics(c, args.profile)
            print(canonical_json(d) if args.as_json else "\n".join(f"{k}: {v}" for k, v in d.items()))
            return 0 if d["valid"] else int(ExitCode.DEGRADED)
        if args.command=="schedule":
            if args.action=="status": r=status_jobs(str(c.get("profile","observe")))
            else:
                j=next((x for x in desired_jobs(str(c.get("profile","observe"))) if args.job is None or x.job_id==args.job),None)
                if j is None: raise ValueError("unknown job")
                r=(render_systemd(j) if args.platform=="systemd" else render_launchd(j) if args.platform=="launchd" else render_task_scheduler(j))
            print(canonical_json(r) if not isinstance(r,str) else r); return 0
        if args.command=="migrate": print(canonical_json(migrate_install(args.config_root,args.vault_root,platform=args.platform,source=args.source))); return 0
        if args.command=="update":
            m=_json_file(args.manifest); sig=Path(args.signature).read_bytes(); asset=args.asset
            if args.check: r=check_update(m,sig,asset=asset,current_version=c.get("install_version"))
            else:
                required={"--asset":asset,"--install-root":args.install_root,"--snapshot-root":args.snapshot_root,
                          "--config-v1":args.config_v1,"--manifest-v1":args.manifest_v1,
                          "--scheduler-export":args.scheduler_export,"--integration-state":args.integration_state,
                          "--current-pointer":args.current_pointer}
                missing=[name for name,value in required.items() if not value]
                if missing: raise ValueError("--apply requires " + ", ".join(missing))
                components={"config-v1":args.config_v1,"manifest-v1":args.manifest_v1,
                            "scheduler-export":args.scheduler_export,"integration-state":args.integration_state}
                pointer = Path(args.current_pointer)
                pointer_target = os.readlink(pointer) if pointer.is_symlink() else str(pointer)
                r=apply_update(m,sig,asset,install_root=args.install_root,snapshot_root=args.snapshot_root,
                               install_id=c.get("install_id"), current_schema_versions=c.get("schema_versions"),
                               trusted_state_path=_state_root() / "state-v1.json",
                               component_paths=components,pointer_target=pointer_target,vault=c.get("vault_path"),
                               migrate_config=lambda: _require_callback(lambda: _run_migration(args.config_v1, m), "migration"),
                               reinstall_jobs=lambda: _require_callback(lambda: reinstall_owned_jobs(args.scheduler_export), "job reinstall"),
                               validate_lifecycle=lambda: _require_callback(lambda: _validate_lifecycle(args.config_v1, args.manifest_v1, args.scheduler_export, args.integration_state, args.current_pointer, m), "lifecycle validation"))
            print(canonical_json(r)); return 0
        if args.command=="rollback":
            allow, key = None, Path(args.install_root) / "latticemind-state.key"
            print(canonical_json(rollback_update(
                install_root=args.install_root, snapshot=args.snapshot,
                manifest=_json_file(args.manifest),
                signature=Path(args.signature).read_bytes(),
                compatible_version=args.compatible_version,
                component_allowlist=allow, state_key_path=key,
                trusted_state_path=_state_root() / "state-v1.json",
                install_id=c.get("install_id"),
            )))
            return 0
        if args.action=="scan":
            output_dir = args.output_dir or str(_state_root() / "reports")
            print(canonical_json(scan(args.vault or c["vault_path"], output_dir=output_dir)))
            return 0
        root=_state_root(); proposal=json.loads(Path(args.proposal).read_text()); pd=hashlib.sha256(canonical_json(proposal).encode()).hexdigest(); target=Path(os.path.abspath(os.path.expanduser(args.target)))
        if target.is_symlink(): raise PermissionError("target symlinks are forbidden")
        target=target.resolve(strict=True); pathd=hashlib.sha256(str(target).encode()).hexdigest(); pre=hashlib.sha256(target.read_bytes()).hexdigest(); iid=c.get("install_id") or args.install_id
        if not iid: raise PermissionError("configured install id is required")
        require_capability(grant_for_job("freshness",c.get("profile","observe")),"managed_write")
        if not sys.stdin.isatty() or not sys.stdout.isatty(): raise PermissionError("freshness actions require an interactive TTY")
        if args.action=="challenge": r,code=issue_challenge(root/"approvals",run_id=args.run_id,proposal_digest=pd,path_digest=pathd,preimage_digest=pre,install_id=iid); print(canonical_json({"approval_id":r["approval_id"],"approval_code":code,"expires_at":r["expires_at"]})); return 0
        if not args.yes and input("Apply the displayed managed metadata patch? [y/N] ").strip().lower() not in {"y","yes"}: raise PermissionError("apply cancelled")
        reserve(root/"approvals",args.approval_id,args.approve,run_id=args.run_id,proposal_digest=pd,path_digest=pathd,preimage_digest=pre,install_id=iid); r=apply_file(target,proposal,expected_preimage=pre,transaction_root=root/"transactions",vault_root=c["vault_path"]); consume(root/"approvals",args.approval_id,install_id=iid); print(canonical_json(r)); return 0
    except TrustError: code,msg=ExitCode.TRUST,"release trust verification failed"
    except SecurityError: code,msg=ExitCode.SECURITY,"security policy blocked operation"
    except PermissionError as e: code,msg=ExitCode.PERMISSION,str(e)
    except TimeoutError as e: code,msg=ExitCode.TIMEOUT,str(e)
    except (ValueError,json.JSONDecodeError) as e: code,msg=ExitCode.CORRUPT_INPUT,str(e)
    except UpdateError as e: code,msg=ExitCode.TRUST,str(e)
    except (OSError,FreshnessError) as e: code,msg=ExitCode.IO_ERROR,str(e)
    except RuntimeError as e: code,msg=ExitCode.RUNTIME_UNAVAILABLE,str(e)
    payload={"schema":"status-v1","state":"blocked","profile":"observe","exit_code":int(code),"exit_class":code.name.lower(),"message":msg}
    if args and (getattr(args,"as_json",False) or args.command in {"validate","schedule","update","rollback","migrate"}):print(canonical_json(payload))
    else:print(msg,file=sys.stderr)
    return int(code)
if __name__=="__main__":raise SystemExit(main())
