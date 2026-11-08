"""Create one current Trading 212 portfolio snapshot.

Safe to rerun. A second run in the same snapshot interval keeps the existing
row. The client is read-only. This does not change the mock dashboard.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from connectors.trading212.client import Trading212Client, Trading212Error
from core.config import get_settings
from core.enums import SnapshotSource
from core.logging import configure_logging
from db.session import session_scope
from services.t212_snapshot import create_live_snapshot


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.t212_enabled:
        print("Trading 212 live snapshot: disabled. Set T212_ENABLED=true.")
        return 1
    if not settings.t212_api_key.strip() or not settings.t212_api_secret.strip():
        print("Trading 212 live snapshot: API key and secret are required.")
        return 1
    if not settings.t212_read_only:
        print("Trading 212 live snapshot: refused because T212_READ_ONLY is not true.")
        return 1
    try:
        client = Trading212Client(settings)
        with session_scope() as session:
            report = create_live_snapshot(session, client, settings, source=SnapshotSource.MANUAL)
    except Trading212Error as exc:
        print(f"Trading 212 live snapshot: failed ({exc})")
        return 1
    except ValueError as exc:
        print(f"Trading 212 live snapshot: failed ({exc})")
        return 1
    print(report.render())
    if report.position_reconciliation.get("overall") == "FAIL" or report.integrity.get("status") == "FAIL":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
