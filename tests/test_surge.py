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
    return [c.drivers.register(f"D{i}", "911", car_type, north_of(km), 4.5) for i in range(n)]


def book(c, n):
    rides = []
    for i in range(n):
        user = c.users.register(f"u{i}", "900")
        rides.append(c.rides.book(user.id, PICKUP, CarType.SEDAN))
    return rides


def test_no_recent_demand_means_no_surge(c, surge):
    add_drivers(c, 3)
    assert surge.multiplier(PICKUP) == 1.0


def test_demand_over_supply_sets_the_multiplier(c, surge):
    add_drivers(c, 4)
    book(c, 2)                                   # 2 recent bookings + this request = 3; 2 drivers left
    assert surge.multiplier(PICKUP) == 1.5


def test_multiplier_is_capped_and_no_supply_means_cap(c, surge):
    add_drivers(c, 3)
    book(c, 2)                                   # demand 3, supply 1 -> 3.0, capped at 2.0
    assert surge.multiplier(PICKUP) == 2.0
    book(c, 1)                                   # supply 0
    assert surge.multiplier(PICKUP) == 2.0


def test_supply_counts_every_car_type_in_the_area(c, surge):
    add_drivers(c, 2, car_type=CarType.HATCHBACK)
    add_drivers(c, 1, car_type=CarType.SEDAN)
    book(c, 1)                                   # demand 2, supply 2
    assert surge.multiplier(PICKUP) == 1.0


def test_offline_drivers_are_not_supply(c, surge, clock):
    drivers = add_drivers(c, 3)
    clock.now += timedelta(minutes=61)
    for d in drivers[:2]:                                      # the third goes quiet: offline
        c.drivers.update_location(d.id, north_of(0.5))
    book(c, 1)
    # demand 2 (1 recent + this); supply is the 1 online driver left. Counting the offline
    # driver would make supply 2 and the multiplier 1.0.
    assert surge.multiplier(PICKUP) == 2.0


def test_old_or_far_bookings_are_not_demand(c, surge, clock):
    add_drivers(c, 3)
    book(c, 2)
    clock.now += timedelta(minutes=16)           # both bookings fall out of the window
    assert surge.multiplier(PICKUP) == 1.0
    assert surge.multiplier(north_of(10)) == 2.0  # 10 km away: no drivers there, so cap


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
