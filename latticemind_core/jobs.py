"""Job-definition-v1 lifecycle contract and deterministic native renderers."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import hashlib, json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo
import os
import contextlib
import fcntl

SCHEMA = "job-definition-v1"
OWNER = "latticemind-job-v1"
CATCH_UP_WINDOW_SECONDS = 21600
KILL_GRACE_SECONDS = 10

@dataclass(frozen=True)
class JobDefinition:
    job_id: str
    mode: str
    calendar: str
    timezone: str
    dstPolicy: str
    catchUpWindowSeconds: int
    jitterSeconds: int
    overlapPolicy: str
    lockScope: str
    timeoutSeconds: int
    killGraceSeconds: int = KILL_GRACE_SECONDS
    retry: Mapping[str, int] = field(default_factory=lambda: {"maxAttempts": 1})
    networkPolicy: str = "none"
    permissionCapability: str = "observe"
    executable: str = "latticemind-maintain"
    argv: tuple[str, ...] = ()
    environmentAllowlist: tuple[str, ...] = ()
    ownershipMarker: str = OWNER
    enabledByDefault: bool = False
    # scheduling decomposition retained as explicit, canonical fields
    weekday: int | None = None
    hour: int = 0
    minute: int = 0

    def __post_init__(self):
        if self.ownershipMarker != OWNER or self.dstPolicy != "first-valid": raise ValueError("invalid ownership or DST policy")
        if self.timezone != "local" or self.overlapPolicy != "skip" or self.lockScope != "user-install": raise ValueError("unsupported scheduler policy")
        if self.catchUpWindowSeconds != CATCH_UP_WINDOW_SECONDS or self.killGraceSeconds != KILL_GRACE_SECONDS: raise ValueError("invalid catch-up or kill grace")
        if self.retry.get("maxAttempts") != 1 or self.timeoutSeconds <= 0 or self.jitterSeconds < 0: raise ValueError("invalid retry or timeout")
        if self.weekday is not None and self.weekday not in range(7): raise ValueError("weekday must be Monday=0..Sunday=6")
        if self.hour not in range(24) or self.minute not in range(60): raise ValueError("invalid local time")
        object.__setattr__(self, "argv", tuple(self.argv) or (self.mode, "--slot-state", "{slot_state}"))

    @property
    def enabled(self): return self.enabledByDefault
    @property
    def timeout_seconds(self): return self.timeoutSeconds
    @property
    def kill_grace_seconds(self): return self.killGraceSeconds
    @property
    def jitter_seconds(self): return self.jitterSeconds
    @property
    def overlap_policy(self): return self.overlapPolicy
    @property
    def owner(self): return self.ownershipMarker
    @property
    def schema(self): return SCHEMA
    def to_dict(self): return asdict(self)


def _job(job_id, mode, calendar, weekday, hour, minute, enabled, jitter, network, capability):
    return JobDefinition(job_id, mode, calendar, "local", "first-valid", CATCH_UP_WINDOW_SECONDS, jitter, "skip", "user-install", 900, networkPolicy=network, permissionCapability=capability, argv=(mode, "--slot-state", "{slot_state}"), enabledByDefault=enabled, weekday=weekday, hour=hour, minute=minute)

JOB_DEFINITIONS = (
    _job("morning", "morning", "daily", None, 8, 7, False, 0, "none", "scheduled-write:morning"),
    _job("nightly", "nightly", "daily", None, 22, 17, False, 0, "none", "scheduled-write:nightly"),
    _job("weekly", "weekly", "weekly", 4, 18, 17, False, 0, "none", "scheduled-write:weekly"),
    _job("freshness", "freshness", "weekly", 6, 19, 17, True, 0, "research", "observe"),
    _job("health", "health", "weekly", 6, 21, 17, True, 0, "none", "observe"),
)

def get_job(job_id): return next(j for j in JOB_DEFINITIONS if j.job_id == job_id)
def desired_jobs(profile="observe", grants=None):
    grants = grants or {}; return tuple(j for j in JOB_DEFINITIONS if j.enabledByDefault or (profile != "observe" and grants.get(j.mode, False)))
def _seed(j, d): return int.from_bytes(hashlib.sha256(f"{j.job_id}:{d.isoformat()}".encode()).digest()[:8], "big")
def seeded_jitter(j, d): return _seed(j, d) % (j.jitterSeconds + 1) if j.jitterSeconds else 0
def slot_identity(j, d): return f"{SCHEMA}:{j.job_id}:{d.isoformat()}:{j.hour:02d}:{j.minute:02d}"
def _resolve_local(naive, tz):
    for i in range(181):
        c = (naive + timedelta(minutes=i)).replace(tzinfo=tz, fold=0)
        if c.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) == c.replace(tzinfo=None):
            return c
    raise ValueError("local time cannot be resolved")

def scheduled_occurrence(j, when, timezone_name=None):
    """Return the canonical local scheduled occurrence at or before *when*."""
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("scheduled time must be timezone-aware")
    tz = ZoneInfo(timezone_name or (getattr(when.tzinfo, "key", None) or "UTC"))
    local = when.astimezone(tz)
    for i in range(8):
        d = local.date() - timedelta(days=i)
        if j.weekday is not None and d.weekday() != j.weekday:
            continue
        occurrence = _resolve_local(datetime.combine(d, time(j.hour, j.minute)), tz)
        occurrence += timedelta(seconds=seeded_jitter(j, d))
        if occurrence <= local:
            return occurrence
    raise RuntimeError("no scheduled occurrence")

def next_run(j, now, timezone_name=None):
    tz = ZoneInfo(timezone_name or (getattr(now.tzinfo, "key", None) or "UTC"))
    local = now.astimezone(tz)
    for i in range(8):
        d = local.date() + timedelta(days=i)
        if j.weekday is not None and d.weekday() != j.weekday:
            continue
        c = _resolve_local(datetime.combine(d, time(j.hour, j.minute)), tz) + timedelta(seconds=seeded_jitter(j, d))
        if c > local:
            return c
    raise RuntimeError("no next run")

def catch_up_expired(scheduled, now, j):
    if scheduled.tzinfo is None or now.tzinfo is None:
        raise ValueError("scheduled and now must be timezone-aware")
    return now - scheduled > timedelta(seconds=j.catchUpWindowSeconds)

def result_mapping(exit_code, timed_out=False):
    if timed_out or exit_code == 124:
        return "timed_out"
    return {0: "succeeded", 2: "degraded", 3: "disabled", 69: "blocked"}.get(exit_code, "failed")

def should_run_slot(j, slot, completed_slots, running=False, scheduled=None, now=None):
    if j.lockScope != "user-install" or running or slot in completed_slots:
        return False
    return scheduled is None or now is None or not catch_up_expired(scheduled, now, j)

@dataclass
class SlotState:
    completed: set[str] = field(default_factory=set)
    running: set[str] = field(default_factory=set)
    results: dict[str, str] = field(default_factory=dict)

    def load(self, path):
        p = Path(path)
        if p.exists():
            obj = json.loads(p.read_text())
            self.completed = set(obj.get("completed", []))
            self.running = set(obj.get("running", []))
            self.results = dict(obj.get("results", {}))

    def save(self, path):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps({"completed": sorted(self.completed), "running": sorted(self.running), "results": self.results}, sort_keys=True) + "\n"
        fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", dir=p.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, p)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)

def run_slot(j, slot, state, execute, scheduled=None, now=None):
    now = now or datetime.now(timezone.utc)
    if not should_run_slot(j, slot, state.completed, bool(state.running), scheduled, now):
        return "expired" if scheduled is not None and catch_up_expired(scheduled, now, j) else "skipped"
    state.running.add(slot)
    try:
        result = execute()
        result = result_mapping(result) if isinstance(result, int) else result
        state.results[slot] = result
        if result in {"succeeded", "degraded", "disabled", "blocked", "failed", "timed_out"}:
            state.completed.add(slot)
        return result
    except TimeoutError:
        state.results[slot] = "timed_out"
        state.completed.add(slot)
        return "timed_out"
    finally:
        state.running.discard(slot)

def run_persisted_slot(path, j, slot, execute, scheduled=None, now=None):
    p = Path(path)
    lock_path = p.with_name(p.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = SlotState()
        state.load(p)
        result = run_slot(j, slot, state, execute, scheduled=scheduled, now=now)
        state.save(p)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return result

def status_jobs(profile="observe", grants=None):
    enabled = {j.job_id for j in desired_jobs(profile, grants)}
    return [{"job_id": j.job_id, "mode": j.mode, "enabled": j.job_id in enabled, "owner": j.ownershipMarker, "schema": SCHEMA} for j in JOB_DEFINITIONS]

def ownership_collision(existing):
    return existing is not None and existing != OWNER

def _argv(j):
    return " ".join(("$HOME/.local/bin/" + j.executable, *j.argv, "--scheduled-at", "{scheduled_at}", "--slot-id", "{slot_id}")).replace("{slot_state}", "$HOME/.local/state/latticemind/slots.json")
def render_systemd(j):
    day="*-*-*" if j.weekday is None else ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][j.weekday]
    return f"# owner={OWNER} schema={SCHEMA} calendar={j.calendar} timezone={j.timezone} dstPolicy={j.dstPolicy} lockScope={j.lockScope} networkPolicy={j.networkPolicy} permissionCapability={j.permissionCapability} retry.maxAttempts={j.retry['maxAttempts']}\n[Service]\nType=oneshot\nExecStart={_argv(j)}\nTimeoutStartSec={j.timeoutSeconds}\nTimeoutStopSec={j.killGraceSeconds}\n[Timer]\nOnCalendar={day} {j.hour:02d}:{j.minute:02d}:00\nPersistent=true\nRandomizedDelaySec={j.jitterSeconds}s\n# enabled={str(j.enabledByDefault).lower()} overlap={j.overlapPolicy} catchup={j.catchUpWindowSeconds} result=timed_out\n"
def render_launchd(j):
    weekday="" if j.weekday is None else f"<key>Weekday</key><integer>{(j.weekday + 1) % 7 + 1}</integer>"
    disabled="false" if j.enabledByDefault else "true"
    args="".join(f"<string>{x.replace('{slot_state}', '$HOME/.local/state/latticemind/slots.json')}</string>" for x in (j.argv))
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<!-- owner={OWNER} schema={SCHEMA} --><plist version="1.0"><dict><key>Label</key><string>com.latticemind.{j.job_id}</string><key>ProgramArguments</key><array><string>$HOME/.local/bin/{j.executable}</string>{args}</array><key>StartCalendarInterval</key><dict><key>Hour</key><integer>{j.hour}</integer><key>Minute</key><integer>{j.minute}</integer>{weekday}</dict><key>TimeOut</key><integer>{j.timeoutSeconds}</integer><key>ThrottleInterval</key><integer>{j.killGraceSeconds}</integer><key>Disabled</key><{disabled}/></dict></plist>\n'
def render_task_scheduler(j):
    """Render a native Scheduled Task XML definition."""
    import xml.etree.ElementTree as ET

    ns = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}Task", {"version": "1.4"})
    root.append(ET.Comment(f"timeoutSeconds={j.timeoutSeconds} killGraceSeconds={j.killGraceSeconds}"))
    registration = ET.SubElement(root, f"{{{ns}}}RegistrationInfo")
    ET.SubElement(registration, f"{{{ns}}}Description").text = (
        f"{OWNER} schema={SCHEMA} mode={j.mode}"
    )
    ET.SubElement(registration, f"{{{ns}}}URI").text = f"\\LatticeMind\\{j.job_id}"
    principals = ET.SubElement(root, f"{{{ns}}}Principals")
    principal = ET.SubElement(principals, f"{{{ns}}}Principal", {"id": "Author"})
    ET.SubElement(principal, f"{{{ns}}}LogonType").text = "InteractiveToken"
    ET.SubElement(principal, f"{{{ns}}}RunLevel").text = "LeastPrivilege"
    settings = ET.SubElement(root, f"{{{ns}}}Settings")
    ET.SubElement(settings, f"{{{ns}}}MultipleInstancesPolicy").text = "IgnoreNew"
    ET.SubElement(settings, f"{{{ns}}}StartWhenAvailable").text = "true"
    ET.SubElement(settings, f"{{{ns}}}ExecutionTimeLimit").text = f"PT{j.timeoutSeconds}S"
    ET.SubElement(settings, f"{{{ns}}}AllowHardTerminate").text = "true"
    ET.SubElement(settings, f"{{{ns}}}Enabled").text = str(j.enabledByDefault).lower()
    triggers = ET.SubElement(root, f"{{{ns}}}Triggers")
    trigger_name = "CalendarTrigger"
    trigger = ET.SubElement(triggers, f"{{{ns}}}{trigger_name}")
    ET.SubElement(trigger, f"{{{ns}}}Enabled").text = "true"
    schedule = ET.SubElement(trigger, f"{{{ns}}}ScheduleByWeek" if j.weekday is not None else f"{{{ns}}}ScheduleByDay")
    if j.weekday is None:
        ET.SubElement(schedule, f"{{{ns}}}DaysInterval").text = "1"
    else:
        ET.SubElement(schedule, f"{{{ns}}}WeeksInterval").text = "1"
        ET.SubElement(schedule, f"{{{ns}}}DaysOfWeek").append(
            ET.Element(f"{{{ns}}}{['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][j.weekday]}")
        )
    ET.SubElement(trigger, f"{{{ns}}}StartBoundary").text = f"2000-01-01T{j.hour:02d}:{j.minute:02d}:00"
    actions = ET.SubElement(root, f"{{{ns}}}Actions", {"Context": "Author"})
    exec_node = ET.SubElement(actions, f"{{{ns}}}Exec")
    ET.SubElement(exec_node, f"{{{ns}}}Command").text = r"%USERPROFILE%\.local\bin\latticemind-maintain.exe"
    ET.SubElement(exec_node, f"{{{ns}}}Arguments").text = (
        f"-NoProfile -NonInteractive -Mode {j.mode} "
        r"-SlotState %USERPROFILE%\.local\state\latticemind\slots.json"
    )
    return ET.tostring(root, encoding="unicode") + "\n"
def reinstall_owned_jobs(export_path, *, platform=None):
    """Render, install, activate, and verify an ownership export using native tools."""
    payload = json.loads(Path(export_path).read_text(encoding="utf-8"))
    entries = payload.get("jobs", payload) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("invalid scheduler ownership export")
    prepared = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("owner") != OWNER or entry.get("schema") != SCHEMA:
            raise ValueError("scheduler ownership mismatch")
        job = get_job(str(entry.get("job_id")))
        target = entry.get("path")
        if not target:
            raise ValueError("scheduler export missing path")
        backend = platform or entry.get("platform", "systemd")
        if backend in {"windows", "task-scheduler"}:
            destination = Path(target)
            if not destination.is_file():
                raise ValueError(f"Windows scheduler export missing: {destination}")
            rendered = destination.read_text(encoding="utf-8")
            import xml.etree.ElementTree as ET
            try:
                tree = ET.fromstring(rendered)
            except ET.ParseError as exc:
                raise ValueError(f"invalid Windows scheduler XML: {destination}") from exc
            values = " ".join(text for text in tree.itertext() if text)
            marker = f"{OWNER} schema={SCHEMA} mode={job.mode}"
            if marker not in values or f"\\LatticeMind\\{job.job_id}" not in values:
                raise ValueError(f"ownership collision: {destination}")
        else:
            rendered = (render_systemd(job) if backend == "systemd" else
                        render_launchd(job) if backend == "launchd" else
                        (_ for _ in ()).throw(ValueError("unsupported scheduler platform")))
            destination = Path(target)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                current = destination.read_text(encoding="utf-8", errors="replace")
                if f"owner={OWNER}" not in current or f"schema={SCHEMA}" not in current:
                    raise ValueError(f"ownership collision: {destination}")
            fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
            os.close(fd)
            temporary = Path(name)
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(destination)
        expected_hash = entry.get("sha256") or entry.get("hash")
        if expected_hash and hashlib.sha256(rendered.encode("utf-8")).hexdigest() != expected_hash:
            raise ValueError(f"scheduler export hash mismatch: {destination}")
        prepared.append((entry, job, backend, destination))
    
    def run(argv, check=True):
        return subprocess.run(argv, check=check, capture_output=True, text=True)
    
    reloaded = set()
    for entry, job, backend, destination in prepared:
        enabled = bool(entry.get("enabled", job.enabledByDefault))
        if backend == "systemd":
            if destination.name.endswith(".service"):
                continue
            if backend not in reloaded:
                run(["systemctl", "--user", "daemon-reload"])
                reloaded.add(backend)
            unit = destination.name
            run(["systemctl", "--user", "enable" if enabled else "disable", "--now", unit])
            result = run(["systemctl", "--user", "is-enabled", unit], check=False)
            actual = result.stdout.strip() == ("enabled" if enabled else "disabled")
        elif backend == "launchd":
            label = entry.get("label", f"com.latticemind.{job.job_id}")
            domain = f"gui/{os.getuid()}"
            run(["launchctl", "bootout", domain, str(destination)], check=False)
            if enabled:
                run(["launchctl", "bootstrap", domain, str(destination)])
                run(["launchctl", "enable", f"{domain}/{label}"])
            result = run(["launchctl", "print", f"{domain}/{label}"], check=False)
            actual = result.returncode == 0 if enabled else result.returncode != 0
        else:
            command = ("$d=Get-Content -Raw -LiteralPath $args[0]; "
                       "Register-ScheduledTask -TaskPath '\\LatticeMind\\' "
                       f"-TaskName '{job.job_id}' -Xml $d -Force | Out-Null; "
                       f"$x=Export-ScheduledTask -TaskPath '\\LatticeMind\\' -TaskName '{job.job_id}'; "
                       f"if ($x -notmatch '{OWNER}' -or $x -notmatch '{SCHEMA}') {{ exit 23 }}; "
                       f"{'Enable' if enabled else 'Disable'}-ScheduledTask -TaskPath '\\LatticeMind\\' "
                       f"-TaskName '{job.job_id}'")
            run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command, str(destination)])
            result = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                          f"(Get-ScheduledTask -TaskPath '\\LatticeMind\\' -TaskName '{job.job_id}').State"], check=False)
            actual = result.returncode == 0 and (
                result.stdout.strip() in {"Ready", "Running"} if enabled
                else result.stdout.strip() in {"Disabled", "Queued"})
        if not actual:
            raise RuntimeError(f"native scheduler state verification failed for {job.job_id}")
    return True