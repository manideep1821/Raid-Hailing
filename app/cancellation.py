from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from app.models import Ride


class CancellationPolicy(ABC):
    @abstractmethod
    def fee(self, ride: Ride, cancelled_at: datetime) -> float: ...


class GracePeriodCancellationPolicy(CancellationPolicy):
    """Free within the grace window after booking, flat fee afterwards."""

    def __init__(self, grace: timedelta = timedelta(minutes=2), fee_amount: float = 25.0):
        self.grace = grace
        self.fee_amount = fee_amount

    def fee(self, ride: Ride, cancelled_at: datetime) -> float:
        return 0.0 if cancelled_at - ride.started_at <= self.grace else self.fee_amount
