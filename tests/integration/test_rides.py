import itertools
import threading
from datetime import timedelta

import pytest

from app.domain.discounts import FlatDiscount, PercentageDiscount
from app.domain.exceptions import (InvalidCouponError, InvalidRideStateError, NoDriverAvailableError, NotFoundError,
                                   ValidationError)
from app.domain.models import CarType, DriverStatus, Ride, RideStatus
from app.strategies.matching import HighestRatedDriverStrategy
from tests.support import PICKUP, TEST_CONFIG, north_of, unique_phone


def fare(car_type: CarType, km: float) -> float:
    return round(TEST_CONFIG.fare_strategies[car_type].base_fare(km), 2)


@pytest.fixture
def user(c):
    return c.users.register("Asha", "9000000001")


def add_driver(c, car_type, km_away=1.0, rating=4.5, name="D"):
    return c.drivers.register(name, unique_phone(), car_type, north_of(km_away), rating)


def complete(c, ride_id, drop=None):
    c.rides.start(ride_id)
    return c.rides.end(ride_id, drop=drop)


# --- booking & matching ---------------------------------------------------------

def test_book_assigns_nearest_driver_of_requested_type(c, user):
    far = add_driver(c, CarType.SEDAN, km_away=3)
    near = add_driver(c, CarType.SEDAN, km_away=1)
    add_driver(c, CarType.HATCHBACK, km_away=0.5)

    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)

    assert ride.driver_id == near.id
    assert ride.status == RideStatus.BOOKED
    assert c.drivers.get(near.id).status == DriverStatus.ON_RIDE
    assert c.drivers.get(far.id).status == DriverStatus.AVAILABLE


def test_no_driver_within_radius(c, user):
    add_driver(c, CarType.SEDAN, km_away=8)
    with pytest.raises(NoDriverAvailableError):
        c.rides.book(user.id, PICKUP, CarType.SEDAN, radius_km=5)


def test_driver_without_recent_location_is_treated_as_offline(c, user, clock):
    driver = add_driver(c, CarType.SEDAN)
    clock.now += TEST_CONFIG.driver_timeout + timedelta(seconds=1)
    with pytest.raises(NoDriverAvailableError):
        c.rides.book(user.id, PICKUP, CarType.SEDAN)
    c.drivers.update_location(driver.id, north_of(1))     # heartbeat: back online
    assert c.rides.book(user.id, PICKUP, CarType.SEDAN).driver_id == driver.id


def test_ending_a_ride_counts_as_the_driver_being_seen(c, user, clock):
    driver = add_driver(c, CarType.SEDAN)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    c.rides.start(ride.id)
    clock.now += TEST_CONFIG.driver_timeout      # a long ride with no updates
    c.rides.end(ride.id, drop=north_of(2))
    assert c.drivers.get(driver.id).last_seen_at == clock.now
    other = c.users.register("Ravi", "9000000002")
    assert c.rides.book(other.id, PICKUP, CarType.SEDAN).driver_id == driver.id


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


def test_phone_number_is_registered_once_per_user_and_per_driver(c, user):
    with pytest.raises(ValidationError, match="already registered"):
        c.users.register("Asha again", user.phone)
    driver = add_driver(c, CarType.SEDAN)
    with pytest.raises(ValidationError, match="already registered"):
        c.drivers.register("Sam again", driver.phone, CarType.SEDAN, PICKUP)
    assert c.drivers.register("Asha drives too", user.phone, CarType.SEDAN, PICKUP)   # separate roles


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

    ended = complete(c, ride.id, drop=north_of(10))
    assert ended.fare.total == pytest.approx(fare(CarType.HATCHBACK, 10), abs=0.05)
    assert ended.fare.total < fare(CarType.SEDAN, 10)


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
    drop = north_of(1)

    ended = complete(c, ride.id, drop=drop)

    assert ended.status == RideStatus.COMPLETED
    assert ended.fare.total == 50
    assert c.drivers.get(driver.id).status == DriverStatus.AVAILABLE
    assert c.drivers.get(driver.id).location == drop


def test_distance_accumulates_from_location_updates(c, user):
    driver = add_driver(c, CarType.SEDAN, km_away=0)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    c.rides.start(ride.id)
    # Drive 4 km north, then 4 km back: 8 km travelled though drop == pickup.
    c.drivers.update_location(driver.id, north_of(4))
    c.drivers.update_location(driver.id, PICKUP)

    ended = c.rides.end(ride.id)

    assert ended.distance_km == pytest.approx(8, abs=0.01)
    assert ended.fare.total == pytest.approx(fare(CarType.SEDAN, 8), abs=0.05)


def test_driver_approach_to_pickup_is_not_billed(c, user):
    # Regression: updates sent on the way to the pickup used to be added to the rider's route.
    driver = add_driver(c, CarType.SEDAN, km_away=3)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    c.drivers.update_location(driver.id, north_of(1.5))
    c.drivers.update_location(driver.id, PICKUP)
    c.rides.start(ride.id)
    ended = c.rides.end(ride.id, drop=north_of(10))
    assert ended.distance_km == pytest.approx(10, abs=0.01)


def test_ride_must_be_started_before_it_is_ended(c, user):
    add_driver(c, CarType.SEDAN)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    with pytest.raises(InvalidRideStateError, match="not ongoing"):
        c.rides.end(ride.id)
    started = c.rides.start(ride.id)
    assert started.status == RideStatus.ONGOING and started.picked_up_at is not None
    with pytest.raises(InvalidRideStateError, match="not booked"):
        c.rides.start(ride.id)


def test_no_location_update_is_lost_or_billed_after_the_ride_ends(c, repos, user):
    # A driver keeps sending updates while the ride is ended. Each update either lands before
    # the end (and is billed) or after it (and is ignored); none is half-applied or dropped.
    driver = add_driver(c, CarType.SEDAN, km_away=0)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    c.rides.start(ride.id)
    applied = [PICKUP]
    first_update_done, stop = threading.Event(), threading.Event()

    def drive():
        for i in itertools.count():
            if stop.is_set():
                return
            point = north_of(0.5 * (i % 4 + 1))
            if repos.rides.modify_ongoing_for_driver(driver.id, lambda r: r.move_to(point)) is not None:
                applied.append(point)
            first_update_done.set()

    t = threading.Thread(target=drive)
    t.start()
    first_update_done.wait()
    ended = c.rides.end(ride.id)
    stop.set()
    t.join()

    expected_km = sum(a.distance_km(b) for a, b in zip(applied, applied[1:]))
    assert len(applied) > 1
    assert ended.distance_km == pytest.approx(expected_km, abs=0.001)
    assert repos.rides.get(ride.id).distance_km == ended.distance_km
    assert ended.fare.total == pytest.approx(fare(CarType.SEDAN, ended.distance_km), abs=0.01)


def test_location_update_when_idle_does_not_touch_rides(c, user):
    driver = add_driver(c, CarType.SEDAN, km_away=3)
    new_loc = north_of(0.2)
    c.drivers.update_location(driver.id, new_loc)
    assert c.drivers.get(driver.id).location == new_loc
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    assert (ride.last_location, ride.distance_km) == (PICKUP, 0)


def test_cannot_end_ride_twice(c, user):
    add_driver(c, CarType.SEDAN)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    complete(c, ride.id)
    with pytest.raises(InvalidRideStateError):
        c.rides.end(ride.id)


# --- coupons ----------------------------------------------------------------------

def test_valid_coupon_discounts_fare(c, user):
    c.coupons.add("save20", PercentageDiscount(20))
    add_driver(c, CarType.HATCHBACK)
    ride = c.rides.book(user.id, PICKUP, CarType.HATCHBACK, coupon_code="SAVE20")
    ended = complete(c, ride.id, drop=north_of(10))
    assert ended.fare.total == pytest.approx(fare(CarType.HATCHBACK, 10) * 0.8, abs=0.05)


def test_invalid_coupon_rejects_booking_without_holding_driver(c, user):
    driver = add_driver(c, CarType.SEDAN)
    with pytest.raises(InvalidCouponError):
        c.rides.book(user.id, PICKUP, CarType.SEDAN, coupon_code="NOPE")
    assert c.drivers.get(driver.id).status == DriverStatus.AVAILABLE


def test_deleted_coupon_is_invalid_for_new_bookings_but_honoured_mid_ride(c, user):
    c.coupons.add("FLAT10", FlatDiscount(10))
    add_driver(c, CarType.HATCHBACK)
    ride = c.rides.book(user.id, PICKUP, CarType.HATCHBACK, coupon_code="FLAT10")
    c.coupons.delete("FLAT10")

    assert complete(c, ride.id).fare.total == 40
    with pytest.raises(InvalidCouponError):
        c.coupons.validate("FLAT10")


def test_coupon_code_is_required(c):
    with pytest.raises(ValidationError):
        c.coupons.add("  ", FlatDiscount(10))


def test_coupon_discount_is_stored_and_found(c):
    c.coupons.add("cap30", PercentageDiscount(20, max_discount=30))
    assert c.coupons.validate("CAP30").discount == PercentageDiscount(20, max_discount=30)


def test_duplicate_coupon_and_missing_delete(c):
    c.coupons.add("DUP", FlatDiscount(10))
    with pytest.raises(ValidationError):
        c.coupons.add("dup", FlatDiscount(5))
    with pytest.raises(NotFoundError):
        c.coupons.delete("GONE")


# --- history ----------------------------------------------------------------------

def test_history_for_user_and_driver(c, user, clock):
    driver = add_driver(c, CarType.SEDAN)
    first = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    complete(c, first.id, drop=north_of(3))
    clock.now += timedelta(minutes=30)
    cancelled = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    c.rides.cancel(cancelled.id)
    clock.now += timedelta(minutes=1)
    booked = c.rides.book(user.id, PICKUP, CarType.SEDAN)

    for history in (c.rides.history_for_user(user.id), c.rides.history_for_driver(driver.id)):
        assert [r.id for r in history["ongoing"]] == [booked.id]      # booked, not yet picked up
        assert [r.id for r in history["completed"]] == [first.id]
        assert [r.id for r in history["cancelled"]] == [cancelled.id]

    c.rides.start(booked.id)
    assert [r.id for r in c.rides.history_for_user(user.id)["ongoing"]] == [booked.id]


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
        c.rides.start(ride.id)


def test_cannot_cancel_after_pickup(c, user):
    driver = add_driver(c, CarType.SEDAN)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    c.rides.start(ride.id)
    with pytest.raises(InvalidRideStateError, match="not booked"):
        c.rides.cancel(ride.id)
    assert c.drivers.get(driver.id).status == DriverStatus.ON_RIDE


# --- atomicity: a ride and its driver change together, or not at all -------------------

def test_failed_ride_insert_does_not_leave_the_driver_claimed(c, repos, user):
    add_driver(c, CarType.SEDAN)
    c.rides.book(user.id, PICKUP, CarType.SEDAN)
    free = add_driver(c, CarType.SEDAN)
    second = Ride("R-dup", user.id, free.id, CarType.SEDAN, CarType.SEDAN, PICKUP, PICKUP)
    with pytest.raises(InvalidRideStateError):      # the user already has an active ride
        repos.rides.create(second)
    assert c.drivers.get(free.id).status == DriverStatus.AVAILABLE


def test_failed_ride_change_leaves_ride_and_driver_untouched(c, repos, user):
    driver = add_driver(c, CarType.SEDAN)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    c.rides.start(ride.id)

    def crash_while_ending(r):
        r.status = RideStatus.COMPLETED
        raise RuntimeError("process died")

    with pytest.raises(RuntimeError):
        repos.rides.modify(ride.id, crash_while_ending)
    assert c.rides.history_for_user(user.id)["ongoing"][0].status == RideStatus.ONGOING
    assert c.drivers.get(driver.id).status == DriverStatus.ON_RIDE


def test_end_keeps_a_driver_location_newer_than_the_ride(c, repos, user, clock):
    # Regression: end used to move the driver to the drop point after closing the ride, which
    # overwrote any location the driver had reported in between.
    driver = add_driver(c, CarType.SEDAN, km_away=0)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    c.rides.start(ride.id)
    newer = north_of(7)
    repos.drivers.update_location(driver.id, newer, clock.now + timedelta(seconds=1))
    c.rides.end(ride.id, drop=north_of(5))
    assert c.drivers.get(driver.id).location == newer


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
