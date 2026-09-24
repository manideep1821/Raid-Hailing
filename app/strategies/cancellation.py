from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from app.domain.models import Ride


class CancellationPolicy(ABC):
    @abstractmethod
    def fee(self, ride: Ride, cancelled_at: datetime) -> float: ...


class FreeCancellationPolicy(CancellationPolicy):
    """Cancelling before pickup never costs anything."""

    def fee(self, ride: Ride, cancelled_at: datetime) -> float:
        return 0.0


class GracePeriodCancellationPolicy(CancellationPolicy):
    """Free within the grace window after booking, flat fee afterwards (only a booked ride can be cancelled)."""

    def __init__(self, grace: timedelta, fee_amount: float):
        self.grace = grace
        self.fee_amount = fee_amount

    def fee(self, ride: Ride, cancelled_at: datetime) -> float:
        return 0.0 if cancelled_at - ride.booked_at <= self.grace else self.fee_amount
