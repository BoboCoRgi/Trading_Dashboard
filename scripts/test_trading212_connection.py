"""Check the Trading 212 read connection and print a short summary.

Does not write to the database and does not print credentials or account ids.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from connectors.trading212.client import Trading212Client, Trading212Error, sanitised_connection_summary
from core.config import get_settings
from core.logging import configure_logging


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.t212_enabled:
        print("Trading 212 connection: disabled. Set T212_ENABLED=true to test.")
        return 1
    try:
        client = Trading212Client(settings)
        summary = client.get_account_summary()
        positions = client.get_positions()
    except Trading212Error as exc:
        print(f"Trading 212 connection: failed ({exc})")
        return 1
    print(sanitised_connection_summary(summary, positions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
