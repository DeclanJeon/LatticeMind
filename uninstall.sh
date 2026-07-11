#!/usr/bin/env bash
set -euo pipefail
PURGE=0; [[ "${1:-}" == "--purge-state" ]] && PURGE=1
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/latticemind"
CONFIG_FILE="$CONFIG_DIR/config-v1.json"
[[ -r "$CONFIG_FILE" ]] || { printf 'LatticeMind is not installed.\n'; exit 0; }
python3 - "$CONFIG_FILE" "$PURGE" <<'PY'
import json,sys,hashlib,os,subprocess,shutil
from pathlib import Path
# Resolve package root: explicit PYTHONPATH entries, then script parent
for entry in sys.path:
    candidate = Path(entry) / "latticemind_core"
    if candidate.is_dir():
        break
else:
    script_dir = Path(__file__).resolve().parent if __file__ != "-" else Path.cwd()
    candidate = script_dir / "latticemind_core"
    if not candidate.is_dir():
        parent_candidate = script_dir.parent / "latticemind_core"
        if parent_candidate.is_dir():
            sys.path.insert(0, str(script_dir.parent))
        else:
            raise SystemExit("latticemind_core package not found")
    else:
        sys.path.insert(0, str(script_dir))
from latticemind_core.config import load_config
cfg=load_config(sys.argv[1]); purge=sys.argv[2]=='1'; config=Path(sys.argv[1])
data=Path(os.environ.get('LATTICEMIND_STATE_ROOT',Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'latticemind'))
manifest=Path(cfg.get('manifest_path', data/'manifest-v1.json'))
if not manifest.is_absolute(): manifest=data/manifest
if not manifest.exists(): raise SystemExit('manifest-v1.json is required')
records=json.loads(manifest.read_text(encoding="utf-8"))
if not isinstance(records,dict) or records.get('schema')!='manifest-v1' or not isinstance(records.get('owned'),list): raise SystemExit('invalid manifest-v1')
schedulers=[]
for rec in records['owned']:
    if not isinstance(rec,dict) or rec.get('owner') not in ('latticemind','latticemind-job-v1'): raise SystemExit('unowned manifest collision')
    p=Path(rec.get('output',rec.get('path','')))
    if not p.is_absolute(): raise SystemExit('invalid manifest path')
    typ=rec.get('type',rec.get('kind','file'))
    if typ=='scheduler': schedulers.append(rec); continue
    if typ=='managed-block': continue
    if typ=='symlink':
        if p.exists() or p.is_symlink():
            if not p.is_symlink() or os.readlink(p)!=rec.get('target'): raise SystemExit(f'unsafe owned symlink: {p}')
    elif typ=='managed-block': continue
    elif p.exists() and (p.is_symlink() or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest().lower()!=str(rec.get('sha256','')).lower()):
        raise SystemExit(f'modified owned path: {p}')
for rec in schedulers:
    p=Path(rec.get('output',rec.get('path','')))
    if p.exists() and (not p.is_file() or p.is_symlink() or rec.get('marker') not in p.read_text(errors='ignore')): raise SystemExit(f'marker mismatch: {p}')
for rec in records['owned']:
    p=Path(rec.get('output',rec.get('path',''))); typ=rec.get('type',rec.get('kind','file'))
    if typ=='scheduler': continue
    if typ=='managed-block':
        if p.exists():
            text=p.read_text()
            marker=rec.get('marker','<!-- LATTICEMIND:START -->')
            start=marker
            end=marker.replace('START','END')
            if start not in text or end not in text: raise SystemExit(f'marker mismatch: {p}')
            before, rest=text.split(start,1)
            _, after=rest.split(end,1)
            p.write_text(before.rstrip()+after.lstrip())
        continue
    if typ=='symlink':
        if p.exists() or p.is_symlink(): p.unlink()
    elif p.exists() and rec.get('created',not rec.get('backup')):
        p.unlink()
    elif p.exists() and rec.get('backup'):
        b=Path(rec['backup'])
        if not b.is_file() or rec.get('backup_sha256') != hashlib.sha256(b.read_bytes()).hexdigest(): raise SystemExit(f'invalid backup: {b}')
        p.unlink(); p.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(b,p)
for rec in schedulers:
    p=Path(rec.get('output',rec.get('path','')))
    if not p.exists(): continue
    if rec.get('sha256') != hashlib.sha256(p.read_bytes()).hexdigest(): raise SystemExit(f'scheduler identity mismatch: {p}')
    job=rec.get('job_id') or rec.get('identity',{}).get('job_id') or Path(p).stem
    platform=rec.get('platform') or rec.get('identity',{}).get('platform')
    if platform == 'systemd':
        subprocess.run(['systemctl','--user','disable','--now',f'latticemind-{job}.timer'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    elif platform == 'launchd':
        subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}',str(p)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    p.unlink()
if any((rec.get('platform') or rec.get('identity',{}).get('platform')) == 'systemd' for rec in schedulers):
    subprocess.run(['systemctl','--user','daemon-reload'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
config.unlink(missing_ok=True)
if purge and data.exists(): shutil.rmtree(data)
PY
printf 'LatticeMind integration removed; vault, backups, and state preserved by default.\n'
[[ "$PURGE" -eq 1 ]] && printf 'State purged.\n'
