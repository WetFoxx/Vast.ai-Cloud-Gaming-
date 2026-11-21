"""Build a support report from known codes, never from log text or event parameters."""

from collections import Counter, deque
import json
from pathlib import Path
import re
import tempfile
import threading


STAGES = frozenset("vast tailscale restore ready shutdown verify deleted".split())
MACHINE = frozenset("none many vast_error vast tailscale boot_error restore sync_error syncing ready".split())
VAST = frozenset("created loading running exited offline stopped unknown scheduling starting".split())
STATES = frozenset("up down ok err wait login other running closed launch download busy ready".split())
EVENTS = frozenset("""
    busy progress.lost progress.resuming progress.resume_gone
    core.fail_stopped core.fail_state core.fail_host core.fail_timeout core.fail_nvenc core.fail_unreachable
    core.rent_failed core.offer_gone core.no_instances core.many_instances core.no_template core.no_node
    core.start_cancelled core.start_failed core.tries_exhausted_docker core.vast_retry core.running
    core.ts_blocked core.ts_approve core.tailscale_slow core.tailscale_timeout core.tailscale_online
    core.mac_cant_reach core.ssh_failed core.restore_timeout core.restore_no_answer core.boot_not_ok
    core.no_status_page core.no_status_after core.old_script core.finish_answer core.finish_failed
    core.finish_machine_changed core.finish_timeout core.finished_offline core.upload_unconfirmed
    core.uploaded core.uploaded_none core.all_uploaded core.shutdown_failed core.shutdown_timeout
    core.self_deleting core.self_delete_failed core.deleting core.deleted core.delete_pending core.delete_failed
    core.delete_uncertain core.emergency_aborted core.instance_unconfirmed
    wizard.install.moonlight wizard.install.steamlink wizard.install.tailscale
    wizard.finish.template wizard.finish.sshkey wizard.finish.google wizard.finish.autostart
    moonlight.missing pair.no_machine pair.cert pair.creds_failed
    steamlink.missing steamlink.pin.invalid steamlink.pin.nologin steamlink.pin.nosteam steamlink.pin.failed
    setup.err.install_failed setup.err.template setup.err.ts_not_ready
    update.err.nokey update.err.signature update.err.manifest update.err.fetch update.err.not_newer
    update.err.space update.err.size update.err.download update.err.checksum update.err.unpack
    update.err.app update.err.codesign update.err.selftest
""".split()) | {f"stage.{stage}" for stage in STAGES}
ACTIONS = frozenset(("moonlight_open", "steamlink_open", "steamlink_pair", "steamlink_check", "machine_status"))
STEAM_LOG_CODES = frozenset(("not_available", "no_known_error", "user_namespaces", "missing_library"))
SERVICES = ("boot", "sunshine", "play", "finish", "shutdown")


def choice(value, allowed):
    return value if isinstance(value, str) and value in allowed else "unknown"


def error_code(error):
    return choice(error.get("key"), EVENTS) if isinstance(error, dict) else "unknown"


def moonlight_log_code(launched_at, platform):
    if launched_at is None:
        return "not_observed"
    directory = Path(tempfile.gettempdir() if platform == "win32" else "/tmp")
    try:
        logs = []
        for path in directory.glob("Moonlight-*.log"):
            if not path.is_symlink() and path.is_file():
                modified = path.stat().st_mtime
                if modified >= launched_at:
                    logs.append((modified, path))
        if not logs:
            return "not_available"
        with max(logs)[1].open("rb") as log:
            log.seek(0, 2)
            log.seek(max(0, log.tell() - 16_384))
            tail = log.read(16_384).lower()
    except OSError:
        return "not_available"
    if b"no video received" in tail:
        return "no_video_received"
    if b"connection timed out" in tail or b"failed to connect" in tail:
        return "connection_failed"
    if b"rtsp handshake failed" in tail:
        return "rtsp_handshake_failed"
    return "no_known_error"


def operation(job):
    if not isinstance(job, dict):
        return None
    archives = job.get("archives")
    counts = Counter(choice(row[0], STATES) for row in (archives.values() if isinstance(archives, dict) else ())
                     if isinstance(row, (list, tuple)) and row)
    events = job.get("events")
    args = job.get("args")
    docker = args.get("docker") if isinstance(args, dict) else None
    recent = deque(maxlen=16)
    for event in events[-80:] if isinstance(events, list) else ():
        if isinstance(event, dict):
            code = choice(event.get("key"), EVENTS)
            if code != "unknown" and (not recent or recent[-1] != code):
                recent.append(code)
    return {
        "kind": choice(job.get("kind"), ("play", "stop", "replace", "update", "games", "setup")),
        "stage": choice(job.get("stage"), STAGES),
        "rental_mode": "docker" if docker is True else "kvm" if docker is False else "unknown",
        "outcome": "error" if job.get("error") else "done" if job.get("done") else "pending",
        "error": error_code(job["error"]) if job.get("error") else None,
        "events": list(recent),
        "archive_counts": dict(counts),
    }


class SessionReport:
    def __init__(self):
        self._lock = threading.Lock()
        self._machine = None
        self._machine_id = None  # Only used locally to clear observations when a rental changes.
        self._actions = deque(maxlen=12)
        self._wolf = None
        self._moonlight_started = None

    def observe_machine(self, status):
        machine = status.get("machine") or {}
        with self._lock:
            iid = machine.get("id")
            if iid is not None and iid != self._machine_id:
                self._machine_id = iid
                self._actions.clear()
                self._wolf = None
                self._moonlight_started = None
            self._machine = {
                "state": choice(status.get("kind"), MACHINE),
                "provider_state": choice(status.get("vast_status"), VAST),
                "wolf_status_received": bool(status.get("links")),
                "game_state": choice((status.get("play") or {}).get("state"), STATES),
            }

    def observe_action(self, name, result):
        if name not in ACTIONS:
            return
        result = result if isinstance(result, dict) else {}
        if result.get("busy"):
            return
        item = {"action": name}
        if result.get("error"):
            item.update(outcome="error", error=error_code(result["error"]))
        elif result.get("ok") is True:
            item["outcome"] = ("pin_required" if result.get("need_pin") is True else
                               "accepted" if name in ("steamlink_pair", "steamlink_check") else "launch_requested")
        else:
            item["outcome"] = "unknown"
        with self._lock:
            if not self._actions or self._actions[-1] != item:
                self._actions.append(item)

    def moonlight_requested(self, when):
        with self._lock:
            self._moonlight_started = when

    def export(self, version, platform, job, wolf=None, local=None):
        present = isinstance(wolf, dict) and type(wolf.get("schema")) is int and wolf["schema"] == 1
        with self._lock:
            if present:
                services = wolf.get("services")
                self._wolf = {"source": "current", "steam_log": choice(wolf.get("steam_log"), STEAM_LOG_CODES),
                              "services": {key: choice(services[key], STATES) for key in SERVICES if key in services}
                              if isinstance(services, dict) else {}}
            remote = self._wolf
            if remote and not present:
                remote = {**remote, "source": "last_check"}
            report = {
                "report": "VastGame support", "schema": 1,
                "version": version if isinstance(version, str) and re.fullmatch(
                    r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", version) else "unknown",
                "platform": choice(platform, ("darwin", "win32", "linux")),
                "machine": self._machine, "operation": operation(job),
                "desktop_operation": operation(local),
                "actions": list(self._actions), "wolf": remote,
            }
            moonlight_started = self._moonlight_started
        report["moonlight_log"] = moonlight_log_code(moonlight_started, platform)
        return json.dumps(report, ensure_ascii=False, indent=2)
