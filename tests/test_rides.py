import threading
from pathlib import Path
from datetime import datetime, timedelta

import pytest

from app.config import load_config
from app.container import build_container
from app.exceptions import (InvalidCouponError, InvalidRideStateError, NoDriverAvailableError, NotFoundError,
                            ValidationError)
from app.matching import HighestRatedDriverStrategy
from app.models import CarType, DiscountType, DriverStatus, Location, RideStatus

PICKUP = Location(12.9716, 77.5946)
KM_PER_DEG_LAT = 111.19492664455873
CONFIG = load_config(Path(__file__).with_name("config.test.toml"))


def north_of(loc: Location, km: float) -> Location:
    return Location(loc.lat + km / KM_PER_DEG_LAT, loc.lng)


def fare(car_type: CarType, km: float) -> float:
    return round(CONFIG.fare_strategies[car_type].base_fare(km), 2)


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, 10, 0)

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def c(repos, clock):
    return build_container(CONFIG, repos, clock=clock)


@pytest.fixture
def user(c):
    return c.users.register("Asha", "9000000001")


def add_driver(c, car_type, km_away=1.0, rating=4.5, name="D"):
    return c.drivers.register(name, "9111111111", car_type, north_of(PICKUP, km_away), rating)


# --- booking & matching ---------------------------------------------------------

def test_book_assigns_nearest_driver_of_requested_type(c, user):
    far = add_driver(c, CarType.SEDAN, km_away=3)
    near = add_driver(c, CarType.SEDAN, km_away=1)
    add_driver(c, CarType.HATCHBACK, km_away=0.5)

    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)

    assert ride.driver_id == near.id
    assert ride.status == RideStatus.ONGOING
    assert c.drivers.get(near.id).status == DriverStatus.ON_RIDE
    assert c.drivers.get(far.id).status == DriverStatus.AVAILABLE


def test_no_driver_within_radius(c, user):
    add_driver(c, CarType.SEDAN, km_away=8)
    with pytest.raises(NoDriverAvailableError):
        c.rides.book(user.id, PICKUP, CarType.SEDAN, radius_km=5)


def test_busy_driver_is_not_rebooked(c, user):
    add_driver(c, CarType.SEDAN)
    c.rides.book(user.id, PICKUP, CarType.SEDAN)
    other = c.users.register("Ravi", "9000000002")
    with pytest.raises(NoDriverAvailableError):
        c.rides.book(other.id, PICKUP, CarType.SEDAN)


def test_user_cannot_have_two_ongoing_rides(c, user):
    add_driver(c, CarType.SEDAN)
    add_driver(c, CarType.SEDAN)
    c.rides.book(user.id, PICKUP, CarType.SEDAN)
    with pytest.raises(InvalidRideStateError):
        c.rides.book(user.id, PICKUP, CarType.SEDAN)


def test_highest_rated_strategy_is_switchable_per_booking(c, user):
    add_driver(c, CarType.SEDAN, km_away=0.5, rating=3.9)
    best = add_driver(c, CarType.SEDAN, km_away=4, rating=4.9)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN, matching=HighestRatedDriverStrategy())
    assert ride.driver_id == best.id


def test_unknown_user_rejected(c):
    with pytest.raises(NotFoundError):
        c.rides.book("U-missing", PICKUP, CarType.SEDAN)


def test_invalid_radius_rejected(c, user):
    with pytest.raises(ValidationError):
        c.rides.book(user.id, PICKUP, CarType.SEDAN, radius_km=0)


# --- upgrade ----------------------------------------------------------------------

def test_hatchback_upgraded_to_sedan_at_hatchback_price(c, user):
    sedan_driver = add_driver(c, CarType.SEDAN)
    ride = c.rides.book(user.id, PICKUP, CarType.HATCHBACK)

    assert ride.driver_id == sedan_driver.id
    assert ride.requested_car_type == CarType.HATCHBACK
    assert ride.assigned_car_type == CarType.SEDAN
    assert ride.upgraded

    ended = c.rides.end(ride.id, drop=north_of(PICKUP, 10))
    assert ended.fare == pytest.approx(fare(CarType.HATCHBACK, 10), abs=0.05)
    assert ended.fare < fare(CarType.SEDAN, 10)


def test_hatchback_preferred_when_available(c, user):
    add_driver(c, CarType.SEDAN, km_away=0.5)
    hatch = add_driver(c, CarType.HATCHBACK, km_away=2)
    ride = c.rides.book(user.id, PICKUP, CarType.HATCHBACK)
    assert ride.driver_id == hatch.id and not ride.upgraded


def test_sedan_is_never_downgraded(c, user):
    add_driver(c, CarType.HATCHBACK)
    with pytest.raises(NoDriverAvailableError):
        c.rides.book(user.id, PICKUP, CarType.SEDAN)


# --- ending rides / location updates ---------------------------------------------

def test_end_ride_minimum_fare_and_driver_released(c, user):
    driver = add_driver(c, CarType.HATCHBACK)
    ride = c.rides.book(user.id, PICKUP, CarType.HATCHBACK)
    drop = north_of(PICKUP, 1)

    ended = c.rides.end(ride.id, drop=drop)

    assert ended.status == RideStatus.COMPLETED
    assert ended.fare == 50
    assert c.drivers.get(driver.id).status == DriverStatus.AVAILABLE
    assert c.drivers.get(driver.id).location == drop


def test_distance_accumulates_from_location_updates(c, user):
    driver = add_driver(c, CarType.SEDAN, km_away=0)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    # Drive 4 km north, then 4 km back: 8 km travelled though drop == pickup.
    c.drivers.update_location(driver.id, north_of(PICKUP, 4))
    c.drivers.update_location(driver.id, PICKUP)

    ended = c.rides.end(ride.id)

    assert ended.distance_km == pytest.approx(8, abs=0.01)
    assert ended.fare == pytest.approx(fare(CarType.SEDAN, 8), abs=0.05)


def test_location_update_when_idle_does_not_touch_rides(c, user):
    driver = add_driver(c, CarType.SEDAN, km_away=3)
    new_loc = north_of(PICKUP, 0.2)
    c.drivers.update_location(driver.id, new_loc)
    assert c.drivers.get(driver.id).location == new_loc
    assert c.rides.book(user.id, PICKUP, CarType.SEDAN).route == [PICKUP]


def test_cannot_end_ride_twice(c, user):
    add_driver(c, CarType.SEDAN)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    c.rides.end(ride.id)
    with pytest.raises(InvalidRideStateError):
        c.rides.end(ride.id)


# --- coupons ----------------------------------------------------------------------

def test_valid_coupon_discounts_fare(c, user):
    c.coupons.add("save20", DiscountType.PERCENTAGE, 20)
    add_driver(c, CarType.HATCHBACK)
    ride = c.rides.book(user.id, PICKUP, CarType.HATCHBACK, coupon_code="SAVE20")
    ended = c.rides.end(ride.id, drop=north_of(PICKUP, 10))
    assert ended.fare == pytest.approx(fare(CarType.HATCHBACK, 10) * 0.8, abs=0.05)


def test_invalid_coupon_rejects_booking_without_holding_driver(c, user):
    driver = add_driver(c, CarType.SEDAN)
    with pytest.raises(InvalidCouponError):
        c.rides.book(user.id, PICKUP, CarType.SEDAN, coupon_code="NOPE")
    assert c.drivers.get(driver.id).status == DriverStatus.AVAILABLE


def test_deleted_coupon_is_invalid_for_new_bookings_but_honoured_mid_ride(c, user):
    c.coupons.add("FLAT10", DiscountType.FLAT, 10)
    add_driver(c, CarType.HATCHBACK)
    ride = c.rides.book(user.id, PICKUP, CarType.HATCHBACK, coupon_code="FLAT10")
    c.coupons.delete("FLAT10")

    assert c.rides.end(ride.id).fare == 40
    with pytest.raises(InvalidCouponError):
        c.coupons.validate("FLAT10")


@pytest.mark.parametrize("code, dtype, value, max_discount", [
    ("", DiscountType.FLAT, 10, None),
    ("X", DiscountType.FLAT, 0, None),
    ("X", DiscountType.PERCENTAGE, 150, None),
    ("X", DiscountType.PERCENTAGE, 10, -1),
])
def test_invalid_coupon_definitions(c, code, dtype, value, max_discount):
    with pytest.raises(ValidationError):
        c.coupons.add(code, dtype, value, max_discount)


def test_duplicate_coupon_and_missing_delete(c):
    c.coupons.add("DUP", DiscountType.FLAT, 10)
    with pytest.raises(ValidationError):
        c.coupons.add("dup", DiscountType.FLAT, 5)
    with pytest.raises(NotFoundError):
        c.coupons.delete("GONE")


# --- history ----------------------------------------------------------------------

def test_history_for_user_and_driver(c, user, clock):
    driver = add_driver(c, CarType.SEDAN)
    first = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    c.rides.end(first.id, drop=north_of(PICKUP, 3))
    clock.now += timedelta(minutes=30)
    second = c.rides.book(user.id, PICKUP, CarType.SEDAN)

    for history in (c.rides.history_for_user(user.id), c.rides.history_for_driver(driver.id)):
        assert [r.id for r in history["ongoing"]] == [second.id]
        assert [r.id for r in history["completed"]] == [first.id]
        assert history["cancelled"] == []


# --- cancellation -----------------------------------------------------------------

def test_cancel_within_grace_is_free(c, user, clock):
    driver = add_driver(c, CarType.SEDAN)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    clock.now += timedelta(minutes=1)
    cancelled = c.rides.cancel(ride.id)
    assert cancelled.status == RideStatus.CANCELLED
    assert cancelled.cancellation_fee == 0
    assert c.drivers.get(driver.id).status == DriverStatus.AVAILABLE


def test_cancel_leaves_driver_where_they_are(c, user):
    driver = add_driver(c, CarType.SEDAN, km_away=3)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    c.rides.cancel(ride.id)
    # Regression: the driver used to be "released" at the pickup point they never reached.
    assert c.drivers.get(driver.id).location == driver.location
    other = c.users.register("Ravi", "9000000002")
    with pytest.raises(NoDriverAvailableError):
        c.rides.book(other.id, PICKUP, CarType.SEDAN, radius_km=1)


def test_cancel_after_grace_charges_fee(c, user, clock):
    add_driver(c, CarType.SEDAN)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    clock.now += timedelta(minutes=5)
    assert c.rides.cancel(ride.id).cancellation_fee == 25
    with pytest.raises(InvalidRideStateError):
        c.rides.end(ride.id)


# --- concurrency ------------------------------------------------------------------

def test_concurrent_bookings_never_share_a_driver(c):
    # All users rank the same nearest driver first; losers must fall through to the next one.
    drivers = [add_driver(c, CarType.SEDAN, km_away=k) for k in (0.5, 1, 2)]
    users = [c.users.register(f"u{i}", str(i)) for i in range(20)]
    results, errors = [], []
    barrier = threading.Barrier(len(users))

    def attempt(u):
        barrier.wait()
        try:
            results.append(c.rides.book(u.id, PICKUP, CarType.SEDAN))
        except NoDriverAvailableError as e:
            errors.append(e)

    threads = [threading.Thread(target=attempt, args=(u,)) for u in users]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(r.driver_id for r in results) == sorted(d.id for d in drivers)
    assert len(errors) == len(users) - len(drivers)
