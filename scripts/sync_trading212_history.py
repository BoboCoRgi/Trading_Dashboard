"""Import Trading 212 history and reconcile reconstructed positions.

Safe to rerun. Does not place orders and does not write portfolio snapshots.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from connectors.trading212.client import Trading212Client, Trading212Error
from core.config import get_settings
from core.logging import configure_logging
from db.session import session_scope
from services.t212_sync import sync_trading212_history


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.t212_enabled:
        print("Trading 212 historical sync: disabled. Set T212_ENABLED=true.")
        return 1
    if not settings.t212_api_key.strip() or not settings.t212_api_secret.strip():
        print("Trading 212 historical sync: API key and secret are required.")
        return 1
    if not settings.t212_read_only:
        print("Trading 212 historical sync: refused because T212_READ_ONLY is not true.")
        return 1
    try:
        client = Trading212Client(settings)
        with session_scope() as session:
            report = sync_trading212_history(session, client)
    except Trading212Error as exc:
        print(f"Trading 212 historical sync: failed ({exc})")
        return 1
    print(report.render())
    if report.reconciliation.get("overall") == "FAIL":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
