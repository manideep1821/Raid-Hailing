"""Scripted walkthrough of the main flows and edge cases, in memory (no Postgres needed):

    python -m app.cli.demo

Each scenario runs in a different city so that surge demand in one doesn't leak into another.
"""
from datetime import datetime, timedelta
from typing import Callable

from app.cli.formatting import format_history, format_ride
from app.config import load_config
from app.container import build_container
from app.domain.discounts import PercentageDiscount
from app.domain.exceptions import RideHailingError
from app.domain.models import CarType, Location, Ride
from app.strategies.matching import HighestRatedDriverStrategy

KM_PER_DEG_LAT = 111.195


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, 9, 0)

    def __call__(self) -> datetime:
        return self.now


def north_of(loc: Location, km: float) -> Location:
    return Location(loc.lat + km / KM_PER_DEG_LAT, loc.lng)


def heading(title: str) -> None:
    print(f"\n=== {title}")


def show(action: Callable[[], Ride]) -> None:
    try:
        print(format_ride(action()))
    except RideHailingError as e:
        print(f"error: {e}")


def main() -> None:
    clock = Clock()
    c = build_container(load_config(), clock=clock)
    rides, drivers = c.rides, c.drivers

    heading("No driver within the search radius")
    city = Location(12.9716, 77.5946)
    user = c.users.register("Asha", "9000000001")
    drivers.register("Far", "9100000001", CarType.HATCHBACK, north_of(city, 8))
    show(lambda: rides.book(user.id, city, CarType.HATCHBACK))

    heading("Minimum fare: a 1 km hatchback ride")
    city = Location(19.0760, 72.8777)
    user = c.users.register("Ravi", "9000000002")
    drivers.register("Near", "9100000002", CarType.HATCHBACK, north_of(city, 0.5))
    ride = rides.book(user.id, city, CarType.HATCHBACK)
    rides.start(ride.id)
    show(lambda: rides.end(ride.id, drop=north_of(city, 1)))

    heading("Hatchback requested, none free: upgraded to a sedan, billed as a hatchback")
    city = Location(28.6139, 77.2090)
    user = c.users.register("Meera", "9000000003")
    drivers.register("Sedan", "9100000003", CarType.SEDAN, north_of(city, 1))
    ride = rides.book(user.id, city, CarType.HATCHBACK)
    print(format_ride(ride))
    rides.start(ride.id)
    show(lambda: rides.end(ride.id, drop=north_of(city, 10)))

    heading("Coupons: an invalid code is rejected, a valid one discounts the fare")
    city = Location(13.0827, 80.2707)
    user = c.users.register("Kiran", "9000000004")
    drivers.register("Hatch", "9100000004", CarType.HATCHBACK, north_of(city, 1))
    c.coupons.add("SAVE20", PercentageDiscount(20, max_discount=30))
    show(lambda: rides.book(user.id, city, CarType.HATCHBACK, coupon_code="NOPE"))
    ride = rides.book(user.id, city, CarType.HATCHBACK, coupon_code="save20")
    rides.start(ride.id)
    show(lambda: rides.end(ride.id, drop=north_of(city, 10)))

    heading("Cancellation: free inside the grace period, fee after it, not allowed after pickup")
    city = Location(17.3850, 78.4867)
    user = c.users.register("Dev", "9000000005")
    drivers.register("Sedan", "9100000005", CarType.SEDAN, north_of(city, 1))
    ride = rides.book(user.id, city, CarType.SEDAN)
    clock.now += timedelta(minutes=1)
    show(lambda: rides.cancel(ride.id))
    ride = rides.book(user.id, city, CarType.SEDAN)
    clock.now += timedelta(minutes=5)
    show(lambda: rides.cancel(ride.id))
    ride = rides.book(user.id, city, CarType.SEDAN)
    rides.start(ride.id)
    show(lambda: rides.cancel(ride.id))
    rides.end(ride.id, drop=north_of(city, 3))

    heading("Ride history for that user (newest first)")
    print(format_history(rides.history_for_user(user.id)))

    heading("Surge: three bookings against three nearby drivers")
    city = Location(22.5726, 88.3639)
    for i in range(3):
        drivers.register(f"S{i}", f"91000001{i}", CarType.SEDAN, north_of(city, 0.5 + i * 0.1))
    for i in range(3):
        user = c.users.register(f"Rider{i}", f"90000001{i}")
        ride = rides.book(user.id, city, CarType.SEDAN)
        print(f"booking {i + 1}: surge x{ride.surge_multiplier:g}")
    rides.start(ride.id)
    show(lambda: rides.end(ride.id, drop=north_of(city, 10)))

    heading("Matching strategy switched per booking: highest-rated beats nearest")
    city = Location(18.5204, 73.8567)
    user = c.users.register("Nia", "9000000006")
    drivers.register("Close", "9100000006", CarType.SEDAN, north_of(city, 0.5), rating=3.9)
    best = drivers.register("Best", "9100000007", CarType.SEDAN, north_of(city, 4), rating=4.9)
    ride = rides.book(user.id, city, CarType.SEDAN, matching=HighestRatedDriverStrategy())
    print(f"assigned {ride.driver_id} (highest rated is {best.id})")


if __name__ == "__main__":
    main()
