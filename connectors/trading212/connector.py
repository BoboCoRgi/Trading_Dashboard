from datetime import datetime

from connectors.base import BrokerReadConnector
from connectors.trading212.normalise import normalise_trading212, trading212_events
from core.contracts import NormalisedEvent, RawEventDraft
from core.enums import Broker


class Trading212ReadConnector(BrokerReadConnector):
    """Mock Trading 212 adapter. Read-only. No live session and no orders."""

    broker = Broker.TRADING212

    def fetch_events(self, since: datetime | None = None) -> list[RawEventDraft]:
        events = trading212_events()
        if since is None:
            return events
        return [event for event in events if event.occurred_at >= since]

    def normalise(self, event: RawEventDraft) -> NormalisedEvent:
        return normalise_trading212(event)
