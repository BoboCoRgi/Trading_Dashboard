"""Read-only broker boundary.

Adapters may fetch and normalise. The interface has no method that submits,
modifies, or cancels an order. Do not add one.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from core.contracts import NormalisedEvent, RawEventDraft
from core.enums import Broker


class BrokerReadConnector(ABC):
    broker: Broker

    @abstractmethod
    def fetch_events(self, since: datetime | None = None) -> list[RawEventDraft]:
        """Return events the broker already recorded. Never place an order."""

    @abstractmethod
    def normalise(self, event: RawEventDraft) -> NormalisedEvent:
        """Map one raw payload into the canonical draft. Do not call the network."""
