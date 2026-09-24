from datetime import datetime, timedelta

import pytest

from app.domain.models import CarType, Ride
from app.strategies.cancellation import GracePeriodCancellationPolicy
from tests.support import PICKUP

BOOKED_AT = datetime(2026, 1, 1, 10, 0)
POLICY = GracePeriodCancellationPolicy(grace=timedelta(minutes=2), fee_amount=25)


@pytest.mark.parametrize("after, fee", [
    (timedelta(0), 0),
    (timedelta(minutes=2), 0),                   # the boundary is still inside the grace period
    (timedelta(minutes=2, seconds=1), 25),
    (timedelta(hours=1), 25),                    # flat fee, not time-based
])
def test_fee_depends_only_on_time_since_booking(after, fee):
    ride = Ride("R-1", "U-1", "D-1", CarType.SEDAN, CarType.SEDAN, PICKUP, PICKUP, booked_at=BOOKED_AT)
    assert POLICY.fee(ride, BOOKED_AT + after) == fee
