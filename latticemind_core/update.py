"""Immutable versioned updates and transactional lifecycle component recovery."""
from __future__ import annotations
import hashlib, hmac, json, os, re, shutil, stat, tempfile, uuid, subprocess
from pathlib import Path
from typing import Callable, Mapping, Any
from .release import verify_manifest, validate_archive, TrustError, ArchiveError

class UpdateError(RuntimeError): pass

def _validate_transition(manifest: Mapping[str, Any], current: Mapping[str, Any] | None = None) -> None:
    bounds = manifest.get("bounds")
    if not isinstance(bounds, Mapping): raise UpdateError("compatibility bounds unavailable")
    if not isinstance(current, Mapping):
        raise UpdateError("current schema versions unavailable")
    for name in ("bootstrap", "config", "state"):
        bound = bounds.get(name)
        if not isinstance(bound, Mapping): raise UpdateError("compatibility bounds unavailable")
        minimum, maximum = bound.get("min", bound.get("minimum")), bound.get("max", bound.get("maximum"))
        if (isinstance(minimum, bool) or not isinstance(minimum, int) or
            isinstance(maximum, bool) or not isinstance(maximum, int) or minimum > maximum):
            raise UpdateError(f"invalid {name} schema range")
        if bound.get("compatible") is False or bound.get("irreversible") is True:
            raise UpdateError(f"irreversible {name} transition")
        value = current.get(name, current.get(f"{name}_version"))
        if isinstance(value, bool) or not isinstance(value, int):
            raise UpdateError(f"current {name} schema version unavailable")
        if value < minimum or value > maximum:
            raise UpdateError(f"unsupported {name} schema version")

def _windows_acl(path: Path) -> None:
    if os.name != "nt": return
    username = os.environ.get("USERNAME")
    if not username: raise UpdateError("snapshot authentication ACL unavailable")
    domain = os.environ.get("USERDOMAIN", ".")
    principal = f"{domain}\\{username}"
    try:
        subprocess.run(["icacls", str(path), "/inheritance:r", "/remove:g", "*S-1-1-0", "/grant:r", f"{principal}:F", "SYSTEM:F"], check=True, shell=False, capture_output=True, text=True)
        result = subprocess.run(["icacls", str(path)], check=True, shell=False, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc: raise UpdateError("snapshot authentication ACL unavailable") from exc
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    if "Everyone" in output or "BUILTIN\\Users" in output or "*S-1-1-0" in output:
        raise UpdateError("snapshot authentication ACL is permissive")
    for line in output.splitlines():
        for match in re.finditer(r"(?<![A-Za-z0-9_])([A-Za-z0-9_.-]+\\[A-Za-z0-9_.-]+|SYSTEM):\(", line):
            if match.group(1) not in {principal, "SYSTEM"}:
                raise UpdateError("snapshot authentication ACL contains unknown grant")

def _state_key(path: os.PathLike[str]|str) -> bytes:
    p=Path(path).expanduser()
    if p.is_symlink() or not p.is_file(): raise UpdateError("snapshot authentication key unavailable")
    if os.name == "nt": _windows_acl(p)
    elif p.stat().st_mode & 0o077: raise UpdateError("snapshot authentication key permissions unsafe")
    key=p.read_bytes()
    if len(key)<32: raise UpdateError("snapshot authentication key invalid")
    return key

def _root(value):
    p=Path(value).expanduser().resolve(); p.mkdir(parents=True,exist_ok=True); return p

def _digest(path: Path) -> str:
    if path.is_file(): return hashlib.sha256(path.read_bytes()).hexdigest()
    if not path.is_dir() or path.is_symlink(): raise UpdateError("component must be regular file or directory")
    h=hashlib.sha256()
    for item in sorted(path.rglob("*"),key=lambda x:str(x.relative_to(path))):
        rel=str(item.relative_to(path)).replace(os.sep,"/")
        if item.is_symlink(): raise UpdateError("component contains symlink")
        h.update(rel.encode()); h.update(b"\0")
        if item.is_file(): h.update(item.read_bytes())
    return h.hexdigest()

def check_update(manifest, signature, *, asset=None, current_version=None, **kwargs):
    m=verify_manifest(manifest,signature,asset=asset,**kwargs)
    return {"available":current_version!=m["version"],"version":m["version"],"tag":m["tag"],"commit":m.get("full_sha",m.get("commit")),"manifest":m}

def _preflight_component(source: Path, vault: Path | None = None) -> None:
    if source.is_symlink() or not source.exists():
        raise UpdateError("missing lifecycle component")
    for base, dirs, files in os.walk(source, topdown=True, followlinks=False):
        for name in dirs + files:
            item = Path(base) / name
            st = os.lstat(item)
            if stat.S_ISLNK(st.st_mode):
                raise UpdateError("component contains symlink")
            if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                raise UpdateError("component contains special file")
            if vault is not None:
                absolute = item.absolute()
                if absolute == vault or vault in absolute.parents:
                    raise UpdateError("vault cannot be snapshotted")


def _copy_component(source,destination,vault=None):
    _preflight_component(source, vault)
    if vault and (source.absolute()==vault or vault in source.absolute().parents): raise UpdateError("vault cannot be snapshotted")
    if source.is_dir():
        shutil.copytree(source,destination,symlinks=True)
        _digest(destination)
    else:
        destination.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,destination)

def snapshot_install(snapshot_root, *, components, pointer_target=None, runtime=None, vault=None, version=None, state_key_path=None, key_path=None):
    root=_root(snapshot_root); out=root/("snapshot-"+uuid.uuid4().hex); out.mkdir(); vr=Path(vault).expanduser().absolute() if vault else None; records={}
    try:
        for name,raw in components.items():
            if not name or name in (".","..","pointer","runtime") or "/" in name or "\\" in name: raise UpdateError("invalid component name")
            src=Path(raw).expanduser().absolute(); dst=out/"components"/name; _copy_component(src,dst,vr); records[name]={"path":str(src),"stored":f"components/{name}","sha256":_digest(src)}
        if runtime is not None:
            src=Path(runtime).expanduser().absolute(); _copy_component(src,out/"runtime",vr); records["runtime"]={"path":str(src),"stored":"runtime","sha256":_digest(src)}
        records["pointer"]={"target":pointer_target}; payload={"schema":"latticemind-lifecycle-snapshot-v1","version":version,"components":records,"vault_excluded":True}; canonical=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
        (out/"receipt.json").write_bytes(canonical+b"\n"); (out/"receipt.sha256").write_text(hashlib.sha256(canonical).hexdigest()+"\n")
        kp=Path(state_key_path or key_path or (root.parent/"latticemind-state.key")).expanduser(); kp.parent.mkdir(parents=True,exist_ok=True)
        try:
            fd=os.open(kp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,"wb") as f: f.write(os.urandom(32)); f.flush(); os.fsync(f.fileno())
        except FileExistsError: pass
        os.chmod(kp,0o600)
        if os.name == "nt": _windows_acl(kp)
        (out/"receipt.hmac").write_text(hmac.new(_state_key(kp),canonical,hashlib.sha256).hexdigest()+"\n")
        return out
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise

def _activation_layout(manifest: Mapping[str, Any], payload: Path, platform: str) -> Path:
    """Materialize the authenticated archive into the public version layout."""
    spec = manifest.get("payload")
    if not isinstance(spec, Mapping) or spec.get("root") != "upstream":
        raise UpdateError("production payload metadata unavailable")
    members = spec.get("members")
    if not isinstance(members, list) or not all(isinstance(x, str) for x in members):
        raise UpdateError("production payload metadata unavailable")
    required = ("dist", "scaffolds", "windows", "VERSION")
    if not all(x in members for x in required):
        raise UpdateError("production payload metadata incomplete")
    out = payload.parent / (".activation-" + uuid.uuid4().hex)
    out.mkdir()
    target = out / "payload" if platform == "nt" else out
    try:
        if platform == "nt":
            windows = payload / "windows"
            core = payload / "latticemind_core"
            if not windows.is_dir() or windows.is_symlink() or not core.is_dir() or core.is_symlink():
                raise UpdateError("production payload member missing")
            target.mkdir()
            for source in windows.iterdir():
                if source.is_symlink():
                    raise UpdateError("production payload member is linked")
                destination = target / source.name
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
            shutil.copytree(core, target / "latticemind_core")
        else:
            sources = ("bin/latticemind", "bin/latticemind-maintain", "bin/latticemind-status",
                       "uninstall.sh", "latticemind_core")
            for relative in sources:
                source = payload / relative
                if not source.exists() or source.is_symlink():
                    raise UpdateError("production payload member missing")
                destination = target / (Path(relative).name if relative.startswith("bin/") else relative)
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
        return out
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise

def _remove_pointer(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "rmdir", str(path)], check=False, shell=False, capture_output=True)
            if result.returncode != 0:
                raise OSError("junction removal failed")
        else:
            shutil.rmtree(path)

def _pointer(root,name,target):
    target = Path(target).resolve()
    try:
        if os.stat(root).st_dev != os.stat(target).st_dev:
            raise UpdateError("activation must remain on one volume")
    except OSError as exc:
        raise UpdateError("activation volume unavailable") from exc
    if target.anchor != root.resolve().anchor:
        raise UpdateError("activation must remain on one volume")
    tmp=root/(name+".new-"+uuid.uuid4().hex)
    backup=root/(name+".old-"+uuid.uuid4().hex)
    try:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(tmp), str(target)],
                           check=True, shell=False, capture_output=True, text=True)
        else:
            tmp.symlink_to(target, target_is_directory=True)
        destination = root/name
        if os.name == "nt" and (destination.exists() or destination.is_symlink()):
            os.replace(destination, backup)
        os.replace(tmp, destination)
        if backup.exists() or backup.is_symlink(): _remove_pointer(backup)
    except (OSError, subprocess.CalledProcessError) as exc:
        _remove_pointer(tmp)
        if backup.exists() or backup.is_symlink():
            os.replace(backup, root/name)
        raise UpdateError("activation pointer creation failed") from exc

def _component_transaction(receipt,snapshot):
    entries=[]
    for name,rec in receipt["components"].items():
        if name=="pointer": continue
        src=snapshot/rec["stored"]; dest=Path(rec["path"])
        if not src.exists() or _digest(src)!=rec["sha256"]: raise UpdateError("snapshot component verification failed")
        entries.append((src,dest))
    backups=[]; created=[]
    try:
        for src,dest in entries:
            backup=dest.parent/("."+dest.name+".backup-"+uuid.uuid4().hex)
            if dest.exists() or dest.is_symlink(): shutil.move(str(dest),str(backup)); backups.append((backup,dest))
        for src,dest in entries: _copy_component(src,dest); created.append(dest)
    except Exception as exc: _rollback_transaction(backups,created); raise UpdateError("component restoration failed") from exc
    return backups,created

def _rollback_transaction(backups,created):
    for dest in created:
        if dest.exists() or dest.is_symlink(): shutil.rmtree(dest) if dest.is_dir() and not dest.is_symlink() else dest.unlink()
    for backup,dest in reversed(backups):
        if backup.exists() or backup.is_symlink():
            if dest.exists() or dest.is_symlink(): shutil.rmtree(dest) if dest.is_dir() and not dest.is_symlink() else dest.unlink()
            shutil.move(str(backup),str(dest))

def _verify_snapshot(snapshot: Path, key_path: Path, allowlist: Mapping[str, Any] | None = None) -> dict:
    raw = (snapshot / "receipt.json").read_bytes().rstrip(b"\n")
    if hashlib.sha256(raw).hexdigest() != (snapshot / "receipt.sha256").read_text().strip():
        raise UpdateError("tampered snapshot receipt")
    if not hmac.compare_digest(hmac.new(_state_key(key_path), raw, hashlib.sha256).hexdigest(),
                               (snapshot / "receipt.hmac").read_text().strip()):
        raise UpdateError("snapshot authentication failed")
    receipt = json.loads(raw)
    if receipt.get("schema") != "latticemind-lifecycle-snapshot-v1":
        raise UpdateError("invalid snapshot receipt")
    for name,item in receipt.get("components",{}).items():
        if name == "pointer": continue
        if allowlist is not None and (name not in allowlist or
            Path(item.get("path","")).expanduser().resolve() != Path(allowlist[name]).resolve()):
            raise UpdateError("component destination not allowed")
    return receipt
def _commit_transaction(backups):
    # Cleanup is post-commit and best-effort; retained backups are recovery evidence.
    for backup,_ in backups:
        try:
            if backup.exists() or backup.is_symlink():
                shutil.rmtree(backup) if backup.is_dir() and not backup.is_symlink() else backup.unlink()
        except OSError:
            continue

def _restore_components(receipt, snapshot, key_path=None, allowlist=None):
    if key_path is not None:
        receipt = _verify_snapshot(snapshot, Path(key_path), allowlist)
    backups, created = _component_transaction(receipt, snapshot)
    _commit_transaction(backups)

def _atomic_json(path,payload):
    fd,tmp=tempfile.mkstemp(prefix="."+path.name+"-",dir=str(path.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(payload,f,sort_keys=True,separators=(",",":")); f.write("\n"); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def _trusted_state(root, install_id, components, key_path, snap, pointer, manifest):
    raw=(snap/"receipt.json").read_bytes().rstrip(b"\n"); receipt_hmac=(snap/"receipt.hmac").read_text().strip()
    state={"schema":"trusted-state-v1","install_id":install_id,"component_allowlist":{k:str(Path(v).expanduser().resolve()) for k,v in components.items()},"state_key_path":str(Path(key_path).expanduser().resolve()),"snapshot_path":str(snap.resolve()),"snapshot_id":snap.name,"receipt_hmac":receipt_hmac,"current_pointer":str((root/pointer).absolute()),"manifest_digest":hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()}
    state["state_hmac"]=hmac.new(_state_key(key_path),json.dumps(state,sort_keys=True,separators=(",",":")).encode(),hashlib.sha256).hexdigest()
    return state
def _verify_trusted_state(state, root, snapshot, key_path, expected_install_id=None, pointer_name="current"):
    if not isinstance(state, Mapping) or state.get("schema") != "trusted-state-v1":
        raise UpdateError("trusted rollback state unavailable")
    supplied = state.get("state_hmac")
    body = dict(state); body.pop("state_hmac", None)
    expected = hmac.new(_state_key(key_path), json.dumps(body,sort_keys=True,separators=(",",":")).encode(), hashlib.sha256).hexdigest()
    if not isinstance(supplied,str) or not hmac.compare_digest(supplied, expected):
        raise UpdateError("trusted rollback state authentication failed")
    if Path(state.get("snapshot_path","")).resolve() != snapshot.resolve() or state.get("snapshot_id") != snapshot.name:
        raise UpdateError("trusted rollback state binding mismatch")
    if expected_install_id is not None and state.get("install_id") != expected_install_id:
        raise UpdateError("trusted rollback install identity mismatch")
    expected_pointer = str((Path(root) / pointer_name).absolute())
    if state.get("current_pointer") != expected_pointer:
        raise UpdateError("trusted rollback pointer mismatch")
    return body

def apply_update(manifest, signature, archive, *, install_root, current_name="current", snapshot_root=None, snapshots=None,
                 component_paths=None, pointer_target=None, runtime_path=None, vault=None, migrate_config=None,
                 reinstall_jobs=None, validate_lifecycle=None, fail_after_switch=None, install_id=None,
                 trusted_state_path=None, current_schema_versions=None, state_key_path=None, key_path=None,
                 verify_options=None):
    archive=Path(archive); m=verify_manifest(manifest,signature,asset=archive,**(verify_options or {}))
    root=_root(install_root); versions=root/"versions"; versions.mkdir(exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix=".stage-",dir=root)); journal=root/"update.journal"; old=None; snap=None; switched=False
    components=component_paths or {}
    if snapshot_root is None or not components: raise UpdateError("complete lifecycle snapshot is required")
    previous_state_path=Path(trusted_state_path or root/"state-v1.json")
    previous_state=previous_state_path.read_bytes() if previous_state_path.is_file() else None
    try:
        validate_archive(archive,stage); children=list(stage.iterdir()); _validate_transition(m, current_schema_versions)
        if snapshots: raise UpdateError("explicit lifecycle component paths required")
        payload=children[0] if len(children)==1 and children[0].is_dir() else stage
        payload=_activation_layout(m, payload, os.name)
        final=versions/m["version"]
        if final.exists(): raise UpdateError("version already installed")
        old=pointer_target
        if old is None: old=os.readlink(root/current_name) if (root/current_name).is_symlink() else None
        if old is not None and not os.path.isabs(old): old=str((root/old).resolve())
        canonical_key = (root / "latticemind-state.key").resolve()
        supplied_key = state_key_path or key_path
        if supplied_key is not None and Path(supplied_key).expanduser().resolve() != canonical_key:
            raise UpdateError("snapshot authentication key must use canonical install path")
        key_path = canonical_key
        snap=snapshot_install(snapshot_root,components=components,pointer_target=old,runtime=runtime_path,vault=vault,version=m.get("previous_compatible_version"),state_key_path=key_path)
        os.replace(payload, final)
        install_id=install_id or m.get("install_id")
        if not isinstance(install_id,str) or not install_id: raise UpdateError("install identity required")
        trusted=_trusted_state(root,install_id,components,key_path,snap,current_name,m)
        _atomic_json(journal,{"schema":"latticemind-update-journal-v1","phase":"staged","version":m["version"],"old":old,"snapshot":str(snap),"trusted_state":trusted})
        _atomic_json(previous_state_path,trusted)
        _pointer(root,current_name,final)
        switched=True
        if migrate_config and migrate_config() is False: raise UpdateError("migration callback failed")
        if reinstall_jobs and reinstall_jobs() is False: raise UpdateError("job reinstall callback failed")
        if validate_lifecycle and validate_lifecycle() is False: raise UpdateError("lifecycle validation callback failed")
        if fail_after_switch: fail_after_switch()
        journal.unlink(missing_ok=True)
        return {"status":"updated","version":m["version"],"path":str(root/current_name),"manifest":m,"state":trusted}
    except Exception as exc:
        try:
            if snap: _restore_components(json.loads((snap/"receipt.json").read_bytes()),snap,key_path=key_path,allowlist=trusted.get("component_allowlist") if "trusted" in locals() else None)
            if switched and old is not None: _pointer(root,current_name,Path(old))
            elif switched: _remove_pointer(root/current_name)
            if "final" in locals() and final.exists(): shutil.rmtree(final)
            if previous_state is None: previous_state_path.unlink(missing_ok=True)
            else: previous_state_path.write_bytes(previous_state)
        except Exception as restore_exc: raise UpdateError("update failed and restoration failed") from restore_exc
        if isinstance(exc,(TrustError,ArchiveError,UpdateError)): raise
        raise UpdateError("update failed and was rolled back") from exc
    finally:
        if stage.exists(): shutil.rmtree(stage,ignore_errors=True)

def rollback(*,install_root,snapshot,current_name="current",compatible_version=None,manifest=None,signature=None,
             component_allowlist=None,allowed_components=None,state_key_path=None,key_path=None,
             trusted_state_path=None,verify_options=None,install_id=None):
    root=_root(install_root); src=Path(snapshot).resolve(); state_path=Path(trusted_state_path or root/"state-v1.json")
    if not state_path.is_file(): raise UpdateError("trusted rollback state unavailable")
    try: state=json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise UpdateError("trusted rollback state unavailable") from exc
    kp=(root/"latticemind-state.key").resolve()
    supplied_key=state_key_path or key_path
    if supplied_key is not None and Path(supplied_key).expanduser().resolve()!=kp:
        raise UpdateError("trusted rollback key mismatch")
    body=_verify_trusted_state(state,root,src,kp,expected_install_id=install_id,pointer_name=current_name)
    allow=body.get("component_allowlist")
    if not isinstance(allow,Mapping) or not allow: raise UpdateError("component destination allowlist required")
    supplied=component_allowlist or allowed_components
    if supplied is not None and {k:str(Path(v).expanduser().resolve()) for k,v in supplied.items()} != dict(allow):
        raise UpdateError("component destination allowlist mismatch")
    receipt=src/"receipt.json"; digest=src/"receipt.sha256"; hfile=src/"receipt.hmac"
    if not src.is_dir() or src.is_symlink() or not receipt.is_file() or not digest.is_file() or not hfile.is_file(): raise UpdateError("invalid snapshot")
    raw=receipt.read_bytes().rstrip(b"\n")
    if hashlib.sha256(raw).hexdigest()!=digest.read_text().strip(): raise UpdateError("tampered snapshot receipt")
    rec=json.loads(raw)
    if rec.get("schema") != "latticemind-lifecycle-snapshot-v1" or (compatible_version is not None and rec.get("version") != compatible_version):
        raise UpdateError("incompatible snapshot")
    if state.get("receipt_hmac") != hfile.read_text().strip(): raise UpdateError("trusted rollback receipt binding mismatch")
    if not hmac.compare_digest(hfile.read_text().strip(),hmac.new(_state_key(kp),raw,hashlib.sha256).hexdigest()): raise UpdateError("snapshot authentication failed")
    for name,item in rec.get("components",{}).items():
        if name=="pointer": continue
        stored=item.get("stored")
        if name not in allow or Path(item.get("path","")).expanduser().resolve()!=Path(allow[name]).resolve(): raise UpdateError("component destination not allowed")
        if not isinstance(stored,str) or ".." in Path(stored).parts or not (src/stored).resolve().is_relative_to(src) or not (src/stored).exists(): raise UpdateError("invalid component record")
    if manifest is None or signature is None: raise UpdateError("rollback release signature required")
    verified=verify_manifest(manifest,signature,**(verify_options or {}))
    manifest_compatible=verified.get("previous_compatible_version")
    if compatible_version is None: compatible_version=manifest_compatible
    if not compatible_version or manifest_compatible != compatible_version or rec.get("version") != compatible_version: raise UpdateError("rollback compatibility mismatch")
    expected_digest=hashlib.sha256(json.dumps(verified,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    if body.get("manifest_digest") != expected_digest: raise UpdateError("trusted rollback manifest mismatch")
    backups,created=_component_transaction(rec,src); oldlink=root/current_name; oldtarget=os.readlink(oldlink) if oldlink.is_symlink() else None
    try:
        target=rec.get("components",{}).get("pointer",{}).get("target")
        if target:
            target_path=Path(target); target_path=root/target_path if not target_path.is_absolute() else target_path
            if not target_path.is_relative_to(root): raise UpdateError("pointer destination not allowed")
            _pointer(root,current_name,target_path)
        _commit_transaction(backups)
    except Exception as exc:
        _rollback_transaction(backups,created)
        try:
            if oldlink.is_symlink() or oldlink.exists(): _remove_pointer(oldlink)
            if oldtarget is not None: _pointer(root,current_name,Path(oldtarget))
        except Exception: pass
        raise UpdateError("rollback failed and was restored") from exc
    return {"status":"rolled_back","path":str(root/current_name),"version":rec.get("version")}
