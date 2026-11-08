from datetime import datetime

from connectors.base import BrokerReadConnector
from connectors.ibkr.normalise import ibkr_events, normalise_ibkr
from core.contracts import NormalisedEvent, RawEventDraft
from core.enums import Broker


class IBKRReadConnector(BrokerReadConnector):
    """Mock IBKR adapter. Read-only. No live gateway and no orders."""

    broker = Broker.IBKR

    def fetch_events(self, since: datetime | None = None) -> list[RawEventDraft]:
        events = ibkr_events()
        if since is None:
            return events
        return [event for event in events if event.occurred_at >= since]

    def normalise(self, event: RawEventDraft) -> NormalisedEvent:
        return normalise_ibkr(event)
