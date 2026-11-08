"""Launcher decisions. No Docker, database, or Trading 212 credentials."""

from pathlib import Path

from app.dashboard.system_status import (
    LIVE_DATABASE_MESSAGE,
    MOCK_DATABASE_MESSAGE,
    database_unavailable_message,
    system_status_markup,
)
from scripts.dashboard_launch import (
    CommandResult,
    LaunchContext,
    StartupFailure,
    find_first_existing,
    format_failure,
    is_this_dashboard,
    mock_plan,
    run_startup,
    scrub,
    stop_dashboard,
)


ROOT = Path(r"C:\dash\TRD")
SECRET = "live-api-secret-value"


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, amount: float) -> None:
        self.t += amount


def _ctx(**overrides) -> tuple[LaunchContext, dict]:
    clock = Clock()
    state = {
        "commands": [],
        "sync": 0,
        "snapshot": 0,
        "mock": 0,
        "started_streamlit": 0,
        "browser": [],
        "desktop": 0,
        "terminated": [],
        "health": "healthy",
        "engine": True,
        "mode": "live",
        "listeners": [],
        "http": True,
    }

    def run(args):
        state["commands"].append(list(args))
        if args[-2:] == ["-m", "scripts.seed"]:
            state["seeded"] = state.get("seeded", 0) + 1
        return CommandResult(0, "", "")

    ctx = LaunchContext(
        root=ROOT,
        python=ROOT / ".venv" / "Scripts" / "python.exe",
        docker_bin=Path(r"C:\docker\docker.exe"),
        desktop_exe=Path(r"C:\docker\Docker Desktop.exe"),
        data_mode=lambda: state["mode"],
        engine_ready=lambda: state["engine"],
        start_desktop=lambda: state.__setitem__("desktop", state["desktop"] + 1),
        postgres_health=lambda: state["health"],
        run=run,
        listeners=lambda port: list(state["listeners"]),
        start_streamlit=lambda: state.__setitem__("started_streamlit", state["started_streamlit"] + 1),
        http_ready=lambda: state["http"],
        open_browser=state["browser"].append,
        sync_broker=lambda: state.__setitem__("sync", state["sync"] + 1) or "PASS",
        create_snapshot=lambda: state.__setitem__("snapshot", state["snapshot"] + 1) or True,
        prepare_mock=lambda: state.__setitem__("mock", state["mock"] + 1),
        sleep=clock.sleep,
        now=clock.now,
        secrets=[SECRET],
        docker_wait_seconds=6,
        postgres_wait_seconds=4,
        http_wait_seconds=1,
    )
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx, state


def test_live_startup_syncs_and_never_seeds():
    ctx, state = _ctx()
    assert run_startup(ctx) == 0
    assert state["sync"] == 1
    assert state["snapshot"] == 1
    assert state["mock"] == 0
    assert state.get("seeded", 0) == 0
    assert any(line == "[OK] Trading 212 sync" for line in ctx.lines)
    assert any("Position reconciliation: PASS" in line for line in ctx.lines)
    assert not any("scripts.seed" in " ".join(command) for command in state["commands"])


def test_mock_startup_uses_mock_path_and_skips_broker_sync():
    ctx, state = _ctx()
    state["mode"] = "mock"
    assert run_startup(ctx) == 0
    assert state["mock"] == 1
    assert state["sync"] == 0
    assert state["snapshot"] == 0
    assert state.get("seeded", 0) == 0


def test_mock_plan_seeds_only_an_empty_book_and_never_clears_live_rows():
    assert mock_plan(0, 0) == "seed"
    assert mock_plan(3, 0) == "keep"
    assert mock_plan(4, 2) == "leave"


def test_docker_engine_failure_stops_before_postgres_or_seed():
    ctx, state = _ctx()
    state["engine"] = False
    try:
        run_startup(ctx)
    except StartupFailure as exc:
        text = format_failure(exc, ctx.secrets)
    else:
        raise AssertionError("docker failure should stop startup")
    assert text.startswith("[FAIL] Docker engine")
    assert state["commands"] == []
    assert state["sync"] == 0
    assert state.get("seeded", 0) == 0
    assert state["desktop"] == 1


def test_unhealthy_postgres_does_not_migrate():
    ctx, state = _ctx()
    state["health"] = "starting"
    try:
        run_startup(ctx)
    except StartupFailure as exc:
        assert exc.layer == "PostgreSQL"
    else:
        raise AssertionError("unhealthy postgres should fail")
    assert state["commands"] == [[str(ctx.docker_bin), "compose", "up", "-d"]]
    assert state["sync"] == 0


def test_healthy_postgres_is_reused_with_one_compose_up():
    ctx, state = _ctx()
    run_startup(ctx)
    compose = [command for command in state["commands"] if "compose" in command]
    assert compose == [[str(ctx.docker_bin), "compose", "up", "-d"]]
    assert any(command[-3:] == ["alembic", "upgrade", "head"] for command in state["commands"])
    assert not any("down" in command or "rm" in command for command in state["commands"])


def test_existing_dashboard_streamlit_is_not_started_again():
    ctx, state = _ctx()
    state["listeners"] = [{"pid": 42, "command": rf"{ROOT}\.venv\Scripts\python.exe -m streamlit run {ROOT}\app\dashboard\streamlit_app.py"}]
    run_startup(ctx)
    assert state["started_streamlit"] == 0
    assert state["browser"] == [f"http://127.0.0.1:8501"]
    assert any(line == "[OK] Streamlit running" for line in ctx.lines)


def test_unrelated_port_owner_is_left_alone():
    ctx, state = _ctx()
    state["listeners"] = [{"pid": 77, "command": r"C:\other\python.exe -m http.server 8501"}]
    try:
        run_startup(ctx)
    except StartupFailure as exc:
        assert exc.layer == "Streamlit"
        assert "77" in exc.detail
    else:
        raise AssertionError("occupied port should fail")
    assert state["started_streamlit"] == 0
    assert state["browser"] == []
    assert state["terminated"] == []


def test_stop_terminates_only_this_dashboard():
    ctx, state = _ctx()
    processes = [
        {"pid": 5, "command": rf"{ROOT}\.venv\Scripts\python.exe -m streamlit run app/dashboard/streamlit_app.py"},
        {"pid": 9, "command": r"C:\other\streamlit.exe run app/dashboard/streamlit_app.py"},
    ]
    ctx.terminate = state["terminated"].append
    stop_dashboard(ctx, processes)
    assert state["terminated"] == [5]
    assert state["commands"][-1][1:] == ["compose", "stop"]


def test_failure_log_does_not_contain_credentials():
    text = format_failure(StartupFailure("Trading 212 sync", f"rejected {SECRET}"), [SECRET])
    assert SECRET not in text
    assert "[redacted]" in text
    assert SECRET not in scrub(f"header {SECRET}", [SECRET])


def test_dashboard_identity_requires_this_project_path():
    command = rf"{ROOT}\.venv\Scripts\python.exe -m streamlit run app/dashboard/streamlit_app.py"
    assert is_this_dashboard(command, ROOT)
    assert not is_this_dashboard(r"C:\other\python.exe -m streamlit run app/dashboard/streamlit_app.py", ROOT)


def test_live_database_message_does_not_mention_seed():
    assert "seed" not in database_unavailable_message("live").lower()
    assert database_unavailable_message("live") == LIVE_DATABASE_MESSAGE
    assert "scripts.seed" in database_unavailable_message("mock")
    assert database_unavailable_message("mock") == MOCK_DATABASE_MESSAGE


def test_status_markup_has_no_secret_material():
    markup = system_status_markup(
        {
            "mode": "live",
            "database": "Connected",
            "broker": "Connected",
            "last_sync": None,
            "snapshot": None,
            "reconciliation": "PASS",
        }
    )
    assert "LIVE · Trading 212" in markup
    assert "Database: Connected" in markup
    assert "Position reconciliation: PASS" in markup
    assert "api" not in markup.lower()
    assert SECRET not in markup


def test_docker_executable_search_uses_the_first_real_file(tmp_path):
    present = tmp_path / "docker.exe"
    present.write_text("", encoding="utf-8")
    missing = tmp_path / "missing.exe"
    assert find_first_existing([missing, present]) == present
