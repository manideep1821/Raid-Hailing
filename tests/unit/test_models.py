import pytest

from app.domain.exceptions import ValidationError
from app.domain.models import CarType, Location, Ride, RideStatus
from tests.support import PICKUP, north_of


def make_ride(**changes) -> Ride:
    fields = dict(id="R-1", user_id="U-1", driver_id="D-1", requested_car_type=CarType.HATCHBACK,
                  assigned_car_type=CarType.HATCHBACK, pickup=PICKUP, last_location=PICKUP)
    return Ride(**{**fields, **changes})


@pytest.mark.parametrize("lat, lng", [(91, 0), (-90.5, 0), (0, 180.1), (200, 77)])
def test_invalid_coordinates_rejected(lat, lng):
    with pytest.raises(ValidationError, match="invalid coordinates"):
        Location(lat, lng)


def test_coordinate_bounds_are_valid():
    assert Location(90, 180) and Location(-90, -180)


def test_haversine_distance_is_symmetric_and_zero_to_itself():
    ten_km = north_of(10)
    assert PICKUP.distance_km(ten_km) == pytest.approx(10, abs=1e-9)
    assert ten_km.distance_km(PICKUP) == pytest.approx(PICKUP.distance_km(ten_km))
    assert PICKUP.distance_km(PICKUP) == 0


def test_move_to_accumulates_legs_not_displacement():
    ride = make_ride()
    ride.move_to(north_of(4))
    ride.move_to(PICKUP)                     # back where it started: 8 km driven, 0 km displaced
    assert ride.distance_km == pytest.approx(8, abs=1e-9)
    assert ride.last_location == PICKUP


def test_upgraded_and_active_flags():
    assert not make_ride().upgraded
    assert make_ride(requested_car_type=CarType.HATCHBACK, assigned_car_type=CarType.SEDAN).upgraded
    assert make_ride(status=RideStatus.BOOKED).is_active and make_ride(status=RideStatus.ONGOING).is_active
    assert not make_ride(status=RideStatus.COMPLETED).is_active
    assert not make_ride(status=RideStatus.CANCELLED).is_active
