"""Build a reproducible, signed LatticeMind release bundle."""
from __future__ import annotations
import argparse, base64, hashlib, json, os, shutil, subprocess, tempfile, zipfile
from pathlib import Path, PurePosixPath
from typing import Any
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from latticemind_core.release import canonical_manifest
from latticemind_core import trust_root

_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

def _validate_archive_member_name(raw: str) -> str:
    if not raw or "\\" in raw or raw.startswith("/") or raw.endswith("/"):
        raise ValueError("invalid runtime archive member")
    parts = PurePosixPath(raw).parts
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("invalid runtime archive member")
    for part in parts:
        if part.endswith((" ", ".")) or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
            raise ValueError("Windows-equivalent runtime archive member")
    return "/".join(parts)
RUNTIME_RIDS = ("windows-x64", "windows-arm64")
SCAFFOLDS = ("default", "builder", "executive", "creator", "researcher")
DEFAULT_CONTENT = ("latticemind_core", "bin", "windows", "schemas", "bootstrap")
MAX_RUNTIME_FILES = 10000
MAX_RUNTIME_BYTES = 512 * 1024 * 1024
_ALLOWED_PAYLOAD_TOPS = frozenset({
    "latticemind_core", "bin", "windows", "schemas", "bootstrap",
    "latticemind", "install.sh", "install.ps1", "uninstall.sh",
    "README.md", "CHANGELOG.md", "dist", "scripts", "scaffolds", "VERSION",
})


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _safe_relative(root: Path, path: Path) -> str:
    if path.is_symlink():
        raise ValueError(f"symlink is not permitted: {path}")
    rel = path.relative_to(root).as_posix()
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"path traversal: {rel}")
    return rel


def _add_tree(archive: zipfile.ZipFile, root: Path, source: Path, prefix: str = "") -> list[str]:
    if source.is_symlink(): raise ValueError(f"symlink is not permitted: {source}")
    if not source.exists(): raise FileNotFoundError(source)
    files = []
    for candidate in source.rglob("*"):
        if candidate.is_symlink(): raise ValueError(f"symlink is not permitted: {candidate}")
        if candidate.is_file() and "__pycache__" not in candidate.parts and candidate.suffix != ".pyc":
            files.append(candidate)
    names = []
    for path in sorted(files, key=lambda p: _safe_relative(source, p)):
        rel = _safe_relative(source, path)
        name = f"{prefix.rstrip('/')}/{rel}" if prefix else rel
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED; info.external_attr = 0o100644 << 16
        archive.writestr(info, path.read_bytes()); names.append(name)
    return names
def _add_file(archive: zipfile.ZipFile, source: Path, name: str) -> str:
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"required release file is unavailable: {source}")
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, source.read_bytes())
    return name



def _runtime_metadata(runtime_dir: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for rid in RUNTIME_RIDS:
        candidates = [runtime_dir / f"{rid}.zip"] if runtime_dir.exists() and (runtime_dir / f"{rid}.zip").exists() else []
        if len(candidates) != 1 or not candidates[0].is_file() or candidates[0].is_symlink(): raise ValueError(f"exactly one official runtime archive required for {rid}")
        path = candidates[0]
        result[rid] = {"url": os.environ.get(f"LATTICEMIND_{rid.upper().replace('-', '_')}_URL", ""), "size": path.stat().st_size, "sha256": _sha(path), "rid": rid}
        if not result[rid]["url"]: raise ValueError(f"missing pinned URL for {rid}")
    return result


def _git_commit(root: Path) -> str:
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def _add_runtime(archive: zipfile.ZipFile, path: Path, prefix: str, names: set[str]) -> None:
    total = count = 0
    with zipfile.ZipFile(path) as source:
        entries = sorted(source.infolist(), key=lambda i: i.filename)
        for entry in entries:
            raw = entry.filename.replace("\\", "/")
            if not raw or raw.endswith("/"): continue
            raw = _validate_archive_member_name(raw)
            parts = PurePosixPath(raw).parts
            if PurePosixPath(raw).is_absolute() or ".." in parts: raise ValueError("runtime path traversal")
            mode = (entry.external_attr >> 16) & 0o170000
            if mode == 0o120000 or entry.is_dir(): raise ValueError("runtime symlink or invalid entry")
            if len(parts) == 0: raise ValueError("invalid runtime entry")
            name = f"{prefix}/{raw}"
            folded = name.casefold().rstrip(" .")
            if any(existing.casefold().rstrip(" .") == folded for existing in names): raise ValueError(f"runtime collision: {name}")
            count += 1; total += entry.file_size
            if count > MAX_RUNTIME_FILES or total > MAX_RUNTIME_BYTES: raise ValueError("runtime archive exceeds safety quota")
            data = source.read(entry)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED; info.external_attr = 0o100644 << 16
            archive.writestr(info, data); names.add(name)


def _scaffolds(upstream_scripts: Path, target: Path) -> None:
    module_path = upstream_scripts / "bootstrap_vault.py"
    if not module_path.is_file() or module_path.is_symlink(): raise FileNotFoundError(module_path)
    import importlib.util
    spec = importlib.util.spec_from_file_location("release_bootstrap_vault", module_path)
    if spec is None or spec.loader is None: raise ValueError("cannot load pinned bootstrap generator")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    if not callable(getattr(module, "bootstrap", None)): raise ValueError("pinned bootstrap generator has no bootstrap()")
    for preset in SCAFFOLDS:
        destination = target / preset; destination.mkdir(parents=True)
        module.bootstrap(destination, "LatticeMind", preset, "personal", "", [], False)


def build(args: argparse.Namespace) -> Path:
    root = Path(args.root).resolve(); out = Path(args.output).resolve(); out.mkdir(parents=True, exist_ok=True)
    commit = args.commit or _git_commit(root)
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit.lower()): raise ValueError("full repository commit is required")
    version = args.version; tag = args.tag or version
    if not version or tag != version: raise ValueError("tag must equal version")
    upstream_commit = args.upstream_commit or os.environ.get("LATTICEMIND_UPSTREAM_COMMIT", "")
    if len(upstream_commit) not in (40, 64) or any(c not in "0123456789abcdef" for c in upstream_commit.lower()): raise ValueError("full upstream commit is required")
    upstream_url = args.upstream_url or os.environ.get("LATTICEMIND_UPSTREAM_URL", "")
    if not upstream_url: raise ValueError("upstream URL is required")
    upstream = Path(args.upstream_dir).resolve()
    if not upstream.is_dir(): raise ValueError("upstream directory is required")
    actual_upstream = _git_commit(upstream)
    if actual_upstream.lower() != upstream_commit.lower(): raise ValueError("upstream HEAD does not match upstream commit")
    runtime_dir = Path(args.runtimes); bundle = out / "latticemind-dist.zip"; names: set[str] = set()
    with tempfile.TemporaryDirectory() as td:
        scaffolds = Path(td) / "scaffolds"; _scaffolds(upstream / "scripts", scaffolds)
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for item in DEFAULT_CONTENT: names.update(_add_tree(archive, root, root / item, f"upstream/{item}"))
            names.update(_add_tree(archive, root, root / "scripts", "upstream/latticemind/scripts"))
            for standalone in ("install.sh", "install.ps1", "uninstall.sh", "README.md", "CHANGELOG.md"):
                names.add(_add_file(archive, root / standalone, f"upstream/{standalone}"))
            names.update(_add_tree(archive, upstream, upstream / "dist", "upstream/dist"))
            names.update(_add_tree(archive, upstream, upstream / "scripts", "upstream/scripts"))
            if "upstream/windows/latticemind.ps1" not in names:
                raise ValueError("Windows CLI wrapper missing from signed payload")
            names.update(_add_tree(archive, scaffolds.parent, scaffolds, "upstream/scaffolds"))
            info = zipfile.ZipInfo("upstream/VERSION", date_time=(1980, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED; archive.writestr(info, version + "\n"); names.add("upstream/VERSION")
            for rid, folder in zip(RUNTIME_RIDS, ("python-x64", "python-arm64")):
                _add_runtime(archive, runtime_dir / f"{rid}.zip", f"upstream/windows/{folder}", names)
            allowed = {n.split("/", 2)[1] for n in names if n.startswith("upstream/") and "/" in n}
            if not allowed <= _ALLOWED_PAYLOAD_TOPS:
                raise ValueError("payload namespace violation")
    required = {"upstream/VERSION", "upstream/dist", "upstream/scaffolds", "upstream/windows"}
    if not all(any(n == p or n.startswith(p + "/") for n in names) for p in required): raise ValueError("incomplete upstream payload")
    assets = [{"name": bundle.name, "size": bundle.stat().st_size, "sha256": _sha(bundle), "rid": "any"}]
    scheduler_assets = {}
    for relative in ("scripts/install-systemd.sh", "scripts/install-launchd.sh", "windows/register-tasks.ps1", "windows/uninstall.ps1"):
        path = root / relative
        if path.is_file() and not path.is_symlink(): scheduler_assets[relative] = {"sha256": _sha(path)}
    manifest = {"schema":"release-manifest-v1","key_id":trust_root.KEY_ID,"repository":trust_root.REPOSITORY,"channel":"stable","version":version,"tag":tag,"full_sha":commit,"upstream":{"url":upstream_url,"full_commit":upstream_commit},"bounds":{"bootstrap":{"min":1,"max":1},"config":{"min":1,"max":1},"state":{"min":1,"max":1}},"bootstrap":{},"payload":{"root":"upstream","members":["dist","scaffolds","windows","VERSION"]},"assets":assets,"runtimes":_runtime_metadata(runtime_dir),"scheduler_assets":scheduler_assets,"previous_compatible_version":args.previous_version,"activation":{"min_bootstrap":1,"epoch":trust_root.KEY_EPOCH},"revocation":{"revoked_epoch":trust_root.REVOCATION_EPOCH}}
    try:
        import jsonschema
    except ImportError as exc:
        raise RuntimeError("jsonschema is required to validate release manifests") from exc
    schema = json.loads((root / "schemas/release-manifest-v1.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(manifest)
    manifest_path = out / "release-manifest-v1.json"; manifest_path.write_bytes(canonical_manifest(manifest) + b"\n")
    raw_key = os.environ.get("LATTICEMIND_SIGNING_KEY_B64", "")
    if not raw_key: raise ValueError("LATTICEMIND_SIGNING_KEY_B64 is required; refusing unsigned publish")
    try: key = base64.b64decode(raw_key, validate=True)
    except Exception as exc: raise ValueError("invalid signing key encoding") from exc
    if len(key) != 32: raise ValueError("signing key must be 32 bytes")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        signature = Ed25519PrivateKey.from_private_bytes(key).sign(canonical_manifest(manifest))
    except ImportError as exc: raise RuntimeError("cryptography is required for release signing") from exc
    envelope = {"key_id":trust_root.KEY_ID,"epoch":trust_root.KEY_EPOCH,"signature":base64.b64encode(signature).decode("ascii")}; (out / "release-manifest-v1.sig").write_bytes(canonical_manifest(envelope) + b"\n")
    fresh_artifacts = [bundle, manifest_path, out / "release-manifest-v1.sig"]
    checks = "".join(f"{_sha(p)}  {p.name}\n" for p in fresh_artifacts); (out / "SHA256SUMS").write_text(checks, encoding="ascii")
    return bundle


def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--root",default="."); p.add_argument("--output",required=True); p.add_argument("--runtimes",required=True); p.add_argument("--upstream-dir",required=True); p.add_argument("--version",required=True); p.add_argument("--tag"); p.add_argument("--commit"); p.add_argument("--upstream-url"); p.add_argument("--upstream-commit"); p.add_argument("--previous-version",default=None)
    args=p.parse_args(); build(args); return 0
if __name__ == "__main__": raise SystemExit(main())
