import itertools
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.config import SurgeConfig, load_config
from app.container import build_container
from app.models import CarType, Location
from app.surge import DemandSupplySurge

PICKUP = Location(12.9716, 77.5946)
KM_PER_DEG_LAT = 111.19492664455873
CONFIG = load_config(Path(__file__).with_name("config.test.toml"))
PHONES = itertools.count(9000000000)  # phone numbers are unique per user and per driver


def north_of(km: float) -> Location:
    return Location(PICKUP.lat + km / KM_PER_DEG_LAT, PICKUP.lng)


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
def surge(repos, clock):
    return DemandSupplySurge(repos.drivers, repos.rides, area_radius_km=2, window=timedelta(minutes=15),
                             cap=2.0, driver_timeout=timedelta(minutes=60), clock=clock)


def add_drivers(c, n, km=0.5, car_type=CarType.SEDAN):
    return [c.drivers.register(f"D{i}", str(next(PHONES)), car_type, north_of(km), 4.5) for i in range(n)]


def book(c, n):
    rides = []
    for i in range(n):
        user = c.users.register(f"u{i}", str(next(PHONES)))
        rides.append(c.rides.book(user.id, PICKUP, CarType.SEDAN))
    return rides


NEW_RIDER = "U-new"   # someone asking for a price who hasn't booked yet


def test_no_recent_demand_means_no_surge(c, surge):
    add_drivers(c, 3)
    assert surge.multiplier(PICKUP, NEW_RIDER) == 1.0


def test_demand_over_supply_sets_the_multiplier(c, surge):
    add_drivers(c, 4)
    book(c, 2)                                   # 2 recent riders + this one = 3; 2 drivers left
    assert surge.multiplier(PICKUP, NEW_RIDER) == 1.5


def test_multiplier_is_capped(c, surge):
    add_drivers(c, 3)
    book(c, 2)                                   # demand 3, supply 1 -> 3.0, capped at 2.0
    assert surge.multiplier(PICKUP, NEW_RIDER) == 2.0
    book(c, 1)                                   # demand 4, supply 0 (counted as 1) -> capped
    assert surge.multiplier(PICKUP, NEW_RIDER) == 2.0


def test_lone_rider_is_never_surged_even_with_no_driver_in_the_area(c, surge):
    # Regression: the only driver is 3 km away, outside the 2 km surge area but inside the 5 km
    # booking radius. Zero supply used to mean the cap; nobody is competing, so it's 1.0.
    add_drivers(c, 1, km=3)
    assert surge.multiplier(PICKUP, NEW_RIDER) == 1.0


def test_supply_counts_every_car_type_in_the_area(c, surge):
    add_drivers(c, 2, car_type=CarType.HATCHBACK)
    add_drivers(c, 1, car_type=CarType.SEDAN)
    book(c, 1)                                   # demand 2, supply 2
    assert surge.multiplier(PICKUP, NEW_RIDER) == 1.0


def test_offline_drivers_are_not_supply(c, surge, clock):
    drivers = add_drivers(c, 3)
    clock.now += timedelta(minutes=61)
    for d in drivers[:2]:                                      # the third goes quiet: offline
        c.drivers.update_location(d.id, north_of(0.5))
    book(c, 1)
    # demand 2 (1 recent rider + this one); supply is the 1 online driver left. Counting the
    # offline driver would make supply 2 and the multiplier 1.0.
    assert surge.multiplier(PICKUP, NEW_RIDER) == 2.0


def test_old_or_far_bookings_are_not_demand(c, surge, clock):
    add_drivers(c, 3)
    book(c, 2)
    # 10 km away there are no drivers (supply counted as 1): counting these 2 riders would cap it.
    assert surge.multiplier(north_of(10), NEW_RIDER) == 1.0
    clock.now += timedelta(minutes=16)           # both bookings fall out of the window
    assert surge.multiplier(PICKUP, NEW_RIDER) == 1.0


def test_demand_counts_riders_not_bookings(c, surge):
    add_drivers(c, 3)
    user = c.users.register("Asha", str(next(PHONES)))
    for _ in range(3):                           # the same rider books and cancels three times
        c.rides.cancel(c.rides.book(user.id, PICKUP, CarType.SEDAN).id)
    assert surge.multiplier(PICKUP, user.id) == 1.0       # their own bookings don't count
    assert surge.multiplier(PICKUP, NEW_RIDER) == 1.0     # demand 2 (Asha + new), supply 3


def test_booking_locks_the_surge_onto_the_ride_and_the_fare(repos, clock):
    config = replace(CONFIG, surge=SurgeConfig(enabled=True, area_radius_km=2, window=timedelta(minutes=15), cap=2.0))
    c = build_container(config, repos, clock=clock)
    add_drivers(c, 2)
    first, second = book(c, 2)                   # second: demand 2, supply 1
    assert (first.surge_multiplier, second.surge_multiplier) == (1.0, 2.0)

    c.rides.start(second.id)
    clock.now += timedelta(minutes=30)           # demand has gone by the time the ride ends
    ended = c.rides.end(second.id, drop=north_of(10))
    assert ended.fare.surge_multiplier == 2.0
    assert ended.fare.total == pytest.approx(2 * 89, abs=0.1)
