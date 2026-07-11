"""Fail-closed release manifest and archive verification."""
from __future__ import annotations
import base64, hashlib, json, os, shutil, stat, tarfile, unicodedata, zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from . import trust_root

class TrustError(ValueError): pass
class ArchiveError(TrustError): pass
MAX_MEMBERS=10000
MAX_FILE_SIZE=512*1024*1024
MAX_TOTAL_SIZE=2*1024*1024*1024
MAX_RATIO=200
_MANIFEST_FIELDS = {
    "schema", "key_id", "repository", "channel", "version", "tag", "full_sha",
    "upstream", "bounds", "bootstrap", "payload", "assets", "runtimes",
    "scheduler_assets", "previous_compatible_version", "activation", "revocation",
}
_ED_P = 2**255 - 19
_ED_L = 2**252 + 27742317777372353535851937790883648493
_ED_D = (-121665 * pow(121666, _ED_P - 2, _ED_P)) % _ED_P
_ED_ID = (0, 1)
_ED_B_Y = (4 * pow(5, _ED_P - 2, _ED_P)) % _ED_P
_ED_B_X = pow((_ED_B_Y * _ED_B_Y - 1) * pow(_ED_D * _ED_B_Y * _ED_B_Y + 1, _ED_P - 2, _ED_P) % _ED_P, (_ED_P + 3) // 8, _ED_P)
if (_ED_B_X * _ED_B_X - ((_ED_B_Y * _ED_B_Y - 1) * pow(_ED_D * _ED_B_Y * _ED_B_Y + 1, _ED_P - 2, _ED_P))) % _ED_P:
    _ED_B_X = (_ED_B_X * pow(2, (_ED_P - 1) // 4, _ED_P)) % _ED_P
if _ED_B_X & 1:
    _ED_B_X = _ED_P - _ED_B_X
_ED_B = (_ED_B_X, _ED_B_Y)

def _ed_decode(raw: bytes) -> tuple[int, int]:
    if len(raw) != 32: raise ValueError("invalid point length")
    sign = raw[31] >> 7
    y = int.from_bytes(raw, "little") & ((1 << 255) - 1)
    if y >= _ED_P: raise ValueError("non-canonical point")
    xx = (y * y - 1) * pow(_ED_D * y * y + 1, _ED_P - 2, _ED_P) % _ED_P
    x = pow(xx, (_ED_P + 3) // 8, _ED_P)
    if (x * x - xx) % _ED_P: x = x * pow(2, (_ED_P - 1) // 4, _ED_P) % _ED_P
    if (x * x - xx) % _ED_P or (x == 0 and sign): raise ValueError("invalid point")
    if (x & 1) != sign: x = _ED_P - x
    return x, y

def _ed_add(p: tuple[int, int], q: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = p; x2, y2 = q
    t = _ED_D * x1 * x2 * y1 * y2 % _ED_P
    return ((x1 * y2 + y1 * x2) * pow(1 + t, _ED_P - 2, _ED_P) % _ED_P,
            (y1 * y2 + x1 * x2) * pow(1 - t, _ED_P - 2, _ED_P) % _ED_P)

def _ed_mul(n: int, p: tuple[int, int]) -> tuple[int, int]:
    out = _ED_ID
    while n:
        if n & 1: out = _ed_add(out, p)
        p = _ed_add(p, p); n >>= 1
    return out

def _verify_fallback(message: bytes, signature: bytes, key: bytes) -> None:
    if len(signature) != 64 or len(key) != 32: raise ValueError("invalid signature")
    rraw, sraw = signature[:32], signature[32:]
    s = int.from_bytes(sraw, "little")
    if s >= _ED_L: raise ValueError("non-canonical scalar")
    a = _ed_decode(key); r = _ed_decode(rraw)
    if a == _ED_ID or r == _ED_ID or _ed_mul(_ED_L, a) != _ED_ID or _ed_mul(_ED_L, r) != _ED_ID:
        raise ValueError("non-canonical point")
    k = int.from_bytes(hashlib.sha512(rraw + key + message).digest(), "little") % _ED_L
    if _ed_mul(s, _ED_B) != _ed_add(r, _ed_mul(k, a)): raise ValueError("signature verification failed")
_HEX64=lambda v:isinstance(v,str) and len(v)==64 and all(c in '0123456789abcdefABCDEF' for c in v)
_SHA=lambda v:isinstance(v,str) and len(v) in (40,64) and all(c in '0123456789abcdefABCDEF' for c in v)

def canonical_manifest(value: Mapping[str,Any])->bytes:
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')

def _sig_bytes(value: bytes|str)->bytes:
    if isinstance(value,bytes): return value
    for fn in (lambda:base64.b64decode(value,validate=True),lambda:bytes.fromhex(value)):
        try:return fn()
        except Exception:pass
    raise TrustError('invalid signature encoding')
def _parse_signature(value: bytes|str|os.PathLike[str]|Mapping[str,Any]) -> tuple[Mapping[str,Any]|None, bytes|str]:
    if isinstance(value, Mapping):
        raw = value.get('signature', value.get('value'))
        if raw is None: raise TrustError('missing signature value')
        return value, raw
    if isinstance(value, os.PathLike):
        value = Path(value).read_bytes()
    if isinstance(value, bytes):
        try: value = json.loads(value.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError): return None, value
    elif isinstance(value, str):
        try: value = json.loads(value)
        except json.JSONDecodeError: return None, value
    if isinstance(value, Mapping): return _parse_signature(value)
    raise TrustError('invalid signature envelope')

def _verify(message:bytes,signature:bytes,key:bytes)->None:
    if len(signature)!=64 or len(key)!=32: raise TrustError('invalid signature')
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(key).verify(signature,message)
    except ImportError:
        try: _verify_fallback(message,signature,key)
        except Exception as exc: raise TrustError('signature verification failed') from exc
    except Exception as exc: raise TrustError('signature verification failed') from exc

def _strmap(m:Any,name:str)->Mapping[str,Any]:
    if not isinstance(m,Mapping): raise TrustError(f'missing {name}')
    return m

def verify_manifest(manifest:Mapping[str,Any],signature:bytes|str|os.PathLike[str]|Mapping[str,Any],*,asset:os.PathLike[str]|str|None=None,
                    repository:str=trust_root.REPOSITORY,channel:str='stable',version:str|None=None,
                    tag:str|None=None,commit:str|None=None)->dict[str,Any]:
    envelope, signature_value = _parse_signature(signature)
    signature_key_id = envelope.get('key_id',trust_root.KEY_ID) if envelope else trust_root.KEY_ID
    signature_epoch = envelope.get('epoch',trust_root.KEY_EPOCH) if envelope else trust_root.KEY_EPOCH
    if not isinstance(signature_key_id,str) or not isinstance(signature_epoch,int) or isinstance(signature_epoch,bool) or signature_epoch<0: raise TrustError('invalid signature envelope')
    if envelope and set(envelope)-{'key_id','epoch','signature','value'}: raise TrustError('invalid signature envelope fields')
    if not isinstance(manifest,Mapping) or manifest.get('schema')!='release-manifest-v1': raise TrustError('unsupported schema')
    if set(manifest) != _MANIFEST_FIELDS: raise TrustError('invalid manifest fields')
    if manifest.get('key_id') not in (trust_root.KEY_ID,trust_root.RECOVERY_KEY_ID) or manifest.get('key_id') != signature_key_id or manifest.get('repository')!=repository or manifest.get('channel')!=channel or channel not in trust_root.CHANNELS: raise TrustError('release identity mismatch')
    ver,tg,sha=manifest.get('version'),manifest.get('tag'),manifest.get('full_sha',manifest.get('commit'))
    if not isinstance(ver,str) or not ver or not isinstance(tg,str) or tg != ver or not _SHA(sha): raise TrustError('version/tag/full SHA invalid')
    if version is not None and ver!=version: raise TrustError('version mismatch')
    if tag is not None and tg!=tag: raise TrustError('tag mismatch')
    if commit is not None and sha!=commit: raise TrustError('SHA mismatch')
    bounds=_strmap(manifest.get('bounds',manifest.get('compatibility',{})),'compatibility bounds')
    for name in ('bootstrap','config','state'):
        bound=bounds.get(name)
        if not isinstance(bound,Mapping): raise TrustError('missing compatibility bound')
        minimum=bound.get('min',bound.get('minimum')); maximum=bound.get('max',bound.get('maximum'))
        if (isinstance(minimum,bool) or not isinstance(minimum,int) or isinstance(maximum,bool) or
            not isinstance(maximum,int) or minimum>maximum):
            raise TrustError('invalid compatibility bound')
    upstream=_strmap(manifest.get('upstream'),'upstream')
    if (not isinstance(upstream.get('url'),str) or not upstream.get('url').strip() or
        not _SHA(upstream.get('full_commit',upstream.get('commit'))) or
        upstream.get('full_commit',upstream.get('commit'))==sha):
        raise TrustError('upstream must be distinct and pinned')
    assets=manifest.get('assets')
    if not isinstance(assets,list) or not assets: raise TrustError('assets must be a list')
    names=set()
    for a in assets:
        a=_strmap(a,'asset')
        if (not isinstance(a.get('name'),str) or not a['name'].strip() or a['name'] in names or
            isinstance(a.get('size'),bool) or not isinstance(a.get('size'),int) or a['size']<0 or
            not _HEX64(a.get('sha256')) or not isinstance(a.get('rid'),str) or not a['rid'].strip()):
            raise TrustError('invalid asset metadata')
        names.add(a['name'])
    payload=_strmap(manifest.get('payload'),'payload')
    if payload.get('root')!='upstream' or payload.get('members')!=['dist','scaffolds','windows','VERSION'] or set(payload)!={'root','members'}:
        raise TrustError('invalid payload contract')
    bootstrap=_strmap(manifest.get('bootstrap'),'bootstrap')
    if bootstrap: raise TrustError('invalid bootstrap contract')
    runtimes=_strmap(manifest.get('runtimes'),'runtimes')
    if set(runtimes) != {'windows-x64','windows-arm64'}: raise TrustError('invalid runtime set')
    for rid,r in runtimes.items():
        if (not isinstance(r,Mapping) or not isinstance(r.get('url'),str) or not r.get('url').strip() or
            isinstance(r.get('size'),bool) or not isinstance(r.get('size'),int) or r.get('size')<0 or
            not _HEX64(r.get('sha256')) or r.get('rid',rid)!=rid):
            raise TrustError('invalid runtime metadata')
    sched=_strmap(manifest.get('scheduler_assets'),'scheduler assets')
    if any(not isinstance(v,Mapping) or not _HEX64(v.get('sha256')) for v in sched.values()): raise TrustError('invalid scheduler asset hash')
    if not isinstance(manifest.get('previous_compatible_version'),(str,type(None))): raise TrustError('invalid previous version')
    activation=_strmap(manifest.get('activation',{}),'activation'); rev=_strmap(manifest.get('revocation',{}),'revocation')
    activation_epoch=activation.get('epoch',0)
    if (isinstance(activation_epoch,bool) or not isinstance(activation_epoch,int) or
        activation.get('min_bootstrap') is None or activation_epoch<trust_root.KEY_EPOCH):
        raise TrustError('bootstrap activation constraint')
    floor=trust_root.RECOVERY_EPOCH if signature_key_id==trust_root.RECOVERY_KEY_ID else trust_root.KEY_EPOCH
    if signature_epoch<floor or signature_epoch!=activation_epoch or signature_epoch<=trust_root.REVOCATION_EPOCH:
        raise TrustError('stale signing epoch')
    revoked_epoch=rev.get('revoked_epoch',-1)
    if (rev.get('revoked_key_id')==signature_key_id and
        (isinstance(revoked_epoch,bool) or not isinstance(revoked_epoch,int) or revoked_epoch>=signature_epoch)):
        raise TrustError('revoked signing key')
    if rev.get('recovery_signature') is not None:
        if not isinstance(rev.get('recovery_signature'),Mapping): raise TrustError('invalid recovery revocation')
        recovery=rev['recovery_signature']
        _verify(canonical_manifest({'schema':manifest['schema'],'full_sha':sha,'revoked_key_id':rev.get('revoked_key_id'),'revoked_epoch':rev.get('revoked_epoch')}),_sig_bytes(recovery.get('signature',recovery.get('value'))),trust_root.RECOVERY_PUBLIC_KEY)
    if rev.get('bootstrap_failed') is True: raise TrustError('upgrade bootstrap failed')
    key=trust_root.RECOVERY_PUBLIC_KEY if signature_key_id==trust_root.RECOVERY_KEY_ID else trust_root.PUBLIC_KEY
    if signature_key_id not in (trust_root.KEY_ID,trust_root.RECOVERY_KEY_ID): raise TrustError('unknown signing key')
    _verify(canonical_manifest(manifest),_sig_bytes(signature_value),key)
    if asset is not None:
        raw=Path(asset).read_bytes(); matches=[a for a in assets if a.get('name')==Path(asset).name]
        if len(matches)!=1 or matches[0]['size']!=len(raw) or matches[0]['sha256']!=hashlib.sha256(raw).hexdigest(): raise TrustError('asset mismatch')
    return dict(manifest)

def _safe(name:str)->str:
    if not isinstance(name,str) or not name or '\\' in name: raise ArchiveError('invalid archive name')
    p=PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts: raise ArchiveError('archive traversal')
    return unicodedata.normalize('NFC',name).casefold()
def _safe_target(root:Path, name:str)->Path:
    resolved_root = root.resolve()
    target=(root/name).resolve()
    if resolved_root not in target.parents and target != resolved_root: raise ArchiveError(f'escape: {name} resolves to {target} outside {resolved_root}')
    cur=root
    for part in PurePosixPath(name).parts[:-1]:
        cur=cur/part
        if cur.is_symlink(): raise ArchiveError('symlink parent')
    return root/name

def _preflight(entries,src_size:int):
    if len(entries)>MAX_MEMBERS: raise ArchiveError('member quota exceeded')
    keys=set(); total=0
    for e in entries:
        name=e.filename if hasattr(e,'filename') else e.name
        key=_safe(name)
        if key in keys: raise ArchiveError('duplicate or colliding member')
        keys.add(key)
        size=getattr(e,'file_size',getattr(e,'size',0))
        if size>MAX_FILE_SIZE: raise ArchiveError('file quota exceeded')
        total+=size
        if total>MAX_TOTAL_SIZE: raise ArchiveError('total quota exceeded')
        if size and src_size and size/max(1,src_size)>MAX_RATIO: raise ArchiveError('compression ratio exceeded')

def validate_archive(path:os.PathLike[str]|str,destination:os.PathLike[str]|str,*,expected_names:set[str]|None=None)->None:
    src,dst=Path(path),Path(destination)
    if dst.exists() and any(dst.iterdir()): raise ArchiveError('extraction root must be newly owned')
    dst.mkdir(parents=True,exist_ok=True); root=dst.resolve(); src_size=src.stat().st_size
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            es=z.infolist(); _preflight(es,src_size)
            for e in es:
                mode=(e.external_attr>>16)&0o170000
                if mode and mode not in (stat.S_IFREG,stat.S_IFDIR): raise ArchiveError('links or special files')
                target=_safe_target(dst,e.filename)
                if root not in target.resolve().parents and target.resolve()!=root: raise ArchiveError('escape')
            for e in es:
                p=_safe_target(dst,e.filename)
                if root not in p.resolve().parents and p.resolve()!=root: raise ArchiveError('escape')
                if e.is_dir():p.mkdir(parents=True,exist_ok=True)
                else:
                    p.parent.mkdir(parents=True,exist_ok=True)
                    with z.open(e) as inp,p.open('xb') as out: shutil.copyfileobj(inp,out)
    else:
        try: tf=tarfile.open(src,'r:*')
        except Exception as exc: raise ArchiveError('unsupported archive') from exc
        with tf:
            es=tf.getmembers(); _preflight(es,src_size)
            for e in es:
                if e.issym() or e.islnk() or not(e.isfile() or e.isdir()): raise ArchiveError('links or special files')
                target=_safe_target(dst,e.name)
                if root not in target.resolve().parents and target.resolve()!=root: raise ArchiveError('escape')
            for e in es:
                p=_safe_target(dst,e.name)
                if root not in p.resolve().parents and p.resolve()!=root: raise ArchiveError('escape')
                if e.isdir():p.mkdir(parents=True,exist_ok=True)
                else:
                    p.parent.mkdir(parents=True,exist_ok=True); inp=tf.extractfile(e)
                    if inp is None: raise ArchiveError('invalid member')
                    with inp,p.open('xb') as out: shutil.copyfileobj(inp,out)
    if expected_names is not None and {p.name for p in dst.iterdir()}!=expected_names: raise ArchiveError('unexpected payload')

def verify_release(*a,**k): return verify_manifest(*a,**k)
def verify_release_manifest(*a,**k): return verify_manifest(*a,**k)
validate_release=verify_manifest
def _main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(prog="python -m latticemind_core.release")
    sub=parser.add_subparsers(dest="command",required=True)
    verify=sub.add_parser("verify")
    verify.add_argument("--manifest",required=True); verify.add_argument("--signature",required=True)
    verify.add_argument("--asset"); verify.add_argument("--extract")
    args=parser.parse_args(argv)
    manifest=json.loads(Path(args.manifest).read_text())
    result=verify_manifest(manifest,Path(args.signature).read_bytes(),asset=args.asset)
    if args.extract:
        if not args.asset: raise TrustError("extract requires asset")
        validate_archive(args.asset,args.extract)
    print(json.dumps({"verified":True,"version":result["version"]},sort_keys=True))
    return 0

if __name__=="__main__":
    raise SystemExit(_main())
