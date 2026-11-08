"""Prepare the local dashboard and open it.

The Trading 212 path calls the existing history sync and live snapshot.
It does not seed, reset, or place orders.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STREAMLIT_PORT = 8501
STREAMLIT_SCRIPT = "app/dashboard/streamlit_app.py"
DASHBOARD_URL = f"http://127.0.0.1:{STREAMLIT_PORT}"


class StartupFailure(Exception):
    def __init__(self, layer: str, detail: str) -> None:
        self.layer = layer
        self.detail = detail
        super().__init__(detail)


@dataclass
class CommandResult:
    code: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class LaunchContext:
    root: Path
    python: Path
    docker_bin: Path | None
    desktop_exe: Path | None
    data_mode: callable
    engine_ready: callable
    start_desktop: callable
    postgres_health: callable
    run: callable
    listeners: callable
    start_streamlit: callable
    http_ready: callable
    open_browser: callable
    sync_broker: callable
    create_snapshot: callable
    prepare_mock: callable
    terminate: callable = lambda pid: None
    sleep: callable = time.sleep
    now: callable = time.monotonic
    port: int = STREAMLIT_PORT
    docker_wait_seconds: float = 120
    postgres_wait_seconds: float = 90
    http_wait_seconds: float = 40
    secrets: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    def say(self, line: str) -> None:
        safe = scrub(line, self.secrets)
        self.lines.append(safe)
        print(safe, flush=True)


def scrub(text: str, secrets: list[str]) -> str:
    cleaned = text or ""
    for secret in secrets:
        if secret and len(secret) >= 8:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


def format_failure(exc: StartupFailure, secrets: list[str]) -> str:
    detail = scrub(exc.detail, secrets).strip() or "The step failed."
    return f"[FAIL] {exc.layer}\n{detail}"


def mock_plan(snapshot_count: int, live_count: int) -> str:
    """Choose the mock action. Live rows are never seeded or cleared."""
    if live_count:
        return "leave"
    if snapshot_count:
        return "keep"
    return "seed"


def is_this_dashboard(command: str | None, root: Path) -> bool:
    if not command:
        return False
    normalized = command.replace("\\", "/").lower()
    root_text = str(root).replace("\\", "/").lower()
    return "streamlit" in normalized and STREAMLIT_SCRIPT in normalized and root_text in normalized


def find_first_existing(candidates: list[Path]) -> Path | None:
    for path in candidates:
        if path and path.is_file():
            return path
    return None


def docker_cli_candidates() -> list[Path]:
    local = os.environ.get("LOCALAPPDATA", "")
    program = os.environ.get("ProgramFiles", "")
    program_x86 = os.environ.get("ProgramFiles(x86)", "")
    found = shutil.which("docker")
    candidates = [
        Path(found) if found else None,
        Path(program) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
        Path(program_x86) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
        Path(local) / "Programs" / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
        Path(local) / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe",
    ]
    return [path for path in candidates if path is not None]


def docker_desktop_candidates() -> list[Path]:
    local = os.environ.get("LOCALAPPDATA", "")
    program = os.environ.get("ProgramFiles", "")
    return [
        Path(program) / "Docker" / "Docker" / "Docker Desktop.exe",
        Path(local) / "Programs" / "Docker" / "Docker" / "Docker Desktop.exe",
        Path(local) / "Programs" / "DockerDesktop" / "Docker Desktop.exe",
    ]


def wait_until(predicate, seconds: float, interval: float, sleep, now) -> bool:
    started = now()
    while True:
        if predicate():
            return True
        if now() - started >= seconds:
            return False
        sleep(interval)


def run_startup(ctx: LaunchContext) -> int:
    ctx.say("Investment Intelligence")
    if ctx.docker_bin is None:
        raise StartupFailure("Docker CLI", "Docker is not installed. Install Docker Desktop, then try again.")
    if not ctx.engine_ready():
        if ctx.desktop_exe is None:
            raise StartupFailure("Docker engine", "Docker Desktop is not running, and the program was not found.")
        ctx.say("[..] Starting Docker Desktop")
        ctx.start_desktop()
        ready = wait_until(ctx.engine_ready, ctx.docker_wait_seconds, 3, ctx.sleep, ctx.now)
        if not ready:
            raise StartupFailure("Docker engine", "Docker Desktop did not become ready. Start it, then try again.")
    ctx.say("[OK] Docker engine")
    started = ctx.run([str(ctx.docker_bin), "compose", "up", "-d"])
    if started.code != 0:
        raise StartupFailure("PostgreSQL", _tail(started.stderr or started.stdout))
    healthy = wait_until(
        lambda: ctx.postgres_health() == "healthy",
        ctx.postgres_wait_seconds,
        2,
        ctx.sleep,
        ctx.now,
    )
    if not healthy:
        raise StartupFailure("PostgreSQL", "PostgreSQL did not become healthy.")
    ctx.say("[OK] PostgreSQL healthy")
    migrated = ctx.run([str(ctx.python), "-m", "alembic", "upgrade", "head"])
    if migrated.code != 0:
        raise StartupFailure("Database migrations", _tail(migrated.stderr or migrated.stdout))
    ctx.say("[OK] Database migrations")
    mode = str(ctx.data_mode() or "").strip().lower()
    if mode == "live":
        overall = ctx.sync_broker()
        ctx.say("[OK] Trading 212 sync")
        mark = "OK" if overall == "PASS" else "WARN"
        ctx.say(f"[{mark}] Position reconciliation: {overall}")
        created = ctx.create_snapshot()
        ctx.say("[OK] Live snapshot created" if created else "[OK] Live snapshot already current")
    elif mode == "mock":
        ctx.prepare_mock()
    else:
        raise StartupFailure("Application configuration", "DATA_MODE must be live or mock.")
    _ensure_streamlit(ctx)
    ctx.say("Opening dashboard...")
    ctx.open_browser(DASHBOARD_URL if ctx.port == STREAMLIT_PORT else f"http://127.0.0.1:{ctx.port}")
    return 0


def stop_dashboard(ctx: LaunchContext, processes: list[dict]) -> int:
    ctx.say("Investment Intelligence")
    matched = [row for row in processes if is_this_dashboard(row.get("command"), ctx.root)]
    if not matched:
        ctx.say("[OK] Streamlit was not running")
    else:
        for row in matched:
            ctx.terminate(row["pid"])
        ctx.say("[OK] Streamlit stopped")
    stopped = ctx.run([str(ctx.docker_bin), "compose", "stop"] if ctx.docker_bin else ["docker", "compose", "stop"])
    if stopped.code == 0:
        ctx.say("[OK] Project containers stopped")
    else:
        ctx.say("[WARN] Project containers were not stopped")
    return 0


def _ensure_streamlit(ctx: LaunchContext) -> None:
    listeners = ctx.listeners(ctx.port)
    if listeners:
        if not all(is_this_dashboard(row.get("command"), ctx.root) for row in listeners):
            pids = ", ".join(str(row.get("pid")) for row in listeners)
            raise StartupFailure("Streamlit", f"Port {ctx.port} is already used by another process ({pids}).")
        ctx.say("[OK] Streamlit running")
    else:
        ctx.start_streamlit()
        ctx.say("[OK] Streamlit running")
    ready = wait_until(ctx.http_ready, ctx.http_wait_seconds, 0.5, ctx.sleep, ctx.now)
    if not ready:
        raise StartupFailure("Streamlit", "Streamlit did not respond.")


def _tail(text: str) -> str:
    compact = " ".join((text or "").split())
    return compact[-300:] or "The command failed."


def _secret_values() -> list[str]:
    from urllib.parse import urlparse

    from core.config import get_settings

    settings = get_settings()
    password = urlparse(settings.database_url).password or ""
    return [settings.t212_api_key.strip(), settings.t212_api_secret.strip(), password.strip()]


def _data_mode() -> str:
    from core.config import get_settings

    return get_settings().data_mode


def _sync_broker() -> str:
    from connectors.trading212.client import Trading212Client
    from core.config import get_settings
    from db.session import session_scope
    from services.t212_sync import sync_trading212_history

    settings = get_settings()
    if not settings.t212_enabled:
        raise StartupFailure("Trading 212 sync", "Trading 212 is disabled. Set T212_ENABLED=true.")
    if not settings.t212_api_key.strip() or not settings.t212_api_secret.strip():
        raise StartupFailure("Trading 212 sync", "Trading 212 API key and secret are required.")
    if not settings.t212_read_only:
        raise StartupFailure("Trading 212 sync", "Refused because T212_READ_ONLY is not true.")
    try:
        client = Trading212Client(settings)
        with session_scope() as session:
            report = sync_trading212_history(session, client)
    except StartupFailure:
        raise
    except Exception as exc:
        raise StartupFailure("Trading 212 sync", str(exc)) from exc
    return (report.reconciliation or {}).get("overall") or "FAIL"


def _create_snapshot() -> bool:
    from connectors.trading212.client import Trading212Client
    from core.config import get_settings
    from core.enums import SnapshotSource
    from db.session import session_scope
    from services.t212_snapshot import create_live_snapshot

    settings = get_settings()
    if not settings.t212_enabled:
        raise StartupFailure("Live snapshot", "Trading 212 is disabled. Set T212_ENABLED=true.")
    if not settings.t212_api_key.strip() or not settings.t212_api_secret.strip():
        raise StartupFailure("Live snapshot", "Trading 212 API key and secret are required.")
    if not settings.t212_read_only:
        raise StartupFailure("Live snapshot", "Refused because T212_READ_ONLY is not true.")
    try:
        client = Trading212Client(settings)
        with session_scope() as session:
            report = create_live_snapshot(session, client, settings, source=SnapshotSource.MANUAL)
    except StartupFailure:
        raise
    except Exception as exc:
        raise StartupFailure("Live snapshot", str(exc)) from exc
    return bool(report.created)


def _prepare_mock(ctx: LaunchContext) -> None:
    from sqlalchemy import text

    from db.session import session_scope

    try:
        with session_scope() as session:
            live_count = session.execute(
                text("SELECT COUNT(*) FROM portfolio_snapshot WHERE data_origin = 'LIVE'")
            ).scalar_one()
            snapshot_count = session.execute(text("SELECT COUNT(*) FROM portfolio_snapshot")).scalar_one()
    except Exception as exc:
        raise StartupFailure("Mock data", str(exc)) from exc
    action = mock_plan(int(snapshot_count), int(live_count))
    if action == "leave":
        ctx.say("[OK] Mock mode. Existing live rows were left untouched.")
        return
    if action == "keep":
        ctx.say("[OK] Mock data already present")
        return
    seeded = ctx.run([str(ctx.python), "-m", "scripts.seed"])
    if seeded.code != 0:
        raise StartupFailure("Mock data", _tail(seeded.stderr or seeded.stdout))
    ctx.say("[OK] Mock data loaded")


def _run(root: Path, args: list[str], timeout: float = 180) -> CommandResult:
    try:
        completed = subprocess.run(
            args,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise StartupFailure("Command", f"{args[0]} timed out.") from exc
    return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def _engine_ready(docker_bin: Path, root: Path) -> bool:
    result = _run(root, [str(docker_bin), "info"], timeout=20)
    return result.code == 0


def _postgres_health(docker_bin: Path, root: Path) -> str:
    identity = _run(root, [str(docker_bin), "compose", "ps", "-q", "postgres"], timeout=30)
    container = identity.stdout.strip().splitlines()
    if identity.code != 0 or not container or not container[0].strip():
        return "missing"
    inspected = _run(
        root,
        [str(docker_bin), "inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", container[0].strip()],
        timeout=30,
    )
    if inspected.code != 0:
        return "missing"
    return inspected.stdout.strip() or "missing"


def _listeners(port: int, root: Path) -> list[dict]:
    net = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, check=False)
    pids: set[int] = set()
    for line in (net.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "TCP":
            continue
        if parts[1].endswith(f":{port}") and parts[3] == "LISTENING" and parts[4].isdigit():
            pids.add(int(parts[4]))
    if not pids:
        return []
    listed = ",".join(str(pid) for pid in sorted(pids))
    query = (
        "Get-CimInstance Win32_Process | "
        f"Where-Object {{ $_.ProcessId -in @({listed}) }} | "
        "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    raw = subprocess.run(
        ["powershell", "-NoProfile", "-Command", query],
        capture_output=True,
        text=True,
        check=False,
    )
    import json

    try:
        payload = json.loads(raw.stdout) if raw.stdout.strip() else []
    except json.JSONDecodeError:
        payload = []
    if isinstance(payload, dict):
        payload = [payload]
    found = {int(row["ProcessId"]): row.get("CommandLine") or "" for row in payload if "ProcessId" in row}
    return [{"pid": pid, "command": found.get(pid, "")} for pid in sorted(pids)]


def _http_ready(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def _start_streamlit(root: Path, python: Path, port: int) -> None:
    log_dir = root / "logs"
    log_dir.mkdir(exist_ok=True)
    handle = (log_dir / "streamlit.log").open("a", encoding="utf-8")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [
            str(python),
            "-m",
            "streamlit",
            "run",
            STREAMLIT_SCRIPT,
            "--server.port",
            str(port),
            "--server.address",
            "127.0.0.1",
        ],
        cwd=root,
        stdout=handle,
        stderr=handle,
        creationflags=flags,
        close_fds=True,
    )


def _processes(root: Path) -> list[dict]:
    raw = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    import json

    try:
        payload = json.loads(raw.stdout) if raw.stdout.strip() else []
    except json.JSONDecodeError:
        payload = []
    if isinstance(payload, dict):
        payload = [payload]
    return [{"pid": int(row["ProcessId"]), "command": row.get("CommandLine") or ""} for row in payload if "ProcessId" in row]


def _terminate(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True, check=False)


def build_context(root: Path, *, stop: bool = False) -> LaunchContext:
    python = root / ".venv" / "Scripts" / "python.exe"
    docker_bin = find_first_existing(docker_cli_candidates())
    desktop = find_first_existing(docker_desktop_candidates())
    ctx = LaunchContext(
        root=root,
        python=python,
        docker_bin=docker_bin,
        desktop_exe=desktop,
        data_mode=_data_mode,
        engine_ready=lambda: bool(docker_bin) and _engine_ready(docker_bin, root),
        start_desktop=lambda: subprocess.Popen([str(desktop)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        postgres_health=lambda: _postgres_health(docker_bin, root) if docker_bin else "missing",
        run=lambda args: _run(root, args),
        listeners=lambda port: _listeners(port, root),
        start_streamlit=lambda: _start_streamlit(root, python, STREAMLIT_PORT),
        http_ready=lambda: _http_ready(STREAMLIT_PORT),
        open_browser=webbrowser.open,
        sync_broker=_sync_broker,
        create_snapshot=_create_snapshot,
        prepare_mock=lambda: None,
        secrets=[],
    )
    ctx.prepare_mock = lambda: _prepare_mock(ctx)
    if stop:
        ctx.terminate = lambda pid: _terminate(pid)
    return ctx


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = ROOT
    os.chdir(root)
    stopping = "--stop" in args
    ctx = build_context(root, stop=stopping)
    try:
        ctx.secrets = [value for value in _secret_values() if value]
    except Exception:
        ctx.secrets = []
    try:
        if stopping:
            if ctx.docker_bin is None:
                raise StartupFailure("Docker CLI", "Docker is not installed, so project containers were not stopped.")
            return stop_dashboard(ctx, _processes(root))
        return run_startup(ctx)
    except StartupFailure as exc:
        print(format_failure(exc, ctx.secrets), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
