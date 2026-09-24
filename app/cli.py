import argparse
import sys
from typing import Dict, List

from app.config import ConfigError, load_config
from app.container import Container, build_container
from app.exceptions import RideHailingError
from app.matching import MATCHING_STRATEGIES
from app.models import CarType, DiscountType, Location, Ride


def format_ride(r: Ride) -> str:
    car = r.assigned_car_type.value
    if r.upgraded:
        car += f" (upgraded from {r.requested_car_type.value}, billed as {r.requested_car_type.value})"
    lines = [f"ride {r.id} [{r.status.value}]  user={r.user_id}  driver={r.driver_id}  car={car}",
             f"  started {r.started_at:%Y-%m-%d %H:%M:%S}"]
    if r.coupon:
        lines.append(f"  coupon {r.coupon.code}")
    if r.fare is not None:
        lines.append(f"  distance {r.distance_km:.2f} km  fare ₹{r.fare:.2f}")
    if r.cancellation_fee is not None:
        lines.append(f"  cancellation fee ₹{r.cancellation_fee:.2f}")
    return "\n".join(lines)


def format_history(history: Dict[str, List[Ride]]) -> str:
    out = []
    for status, rides in history.items():
        out.append(f"{status.upper()} ({len(rides)})")
        out.extend(format_ride(r) for r in rides)
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rides", description="Ride hailing service CLI")
    sub = p.add_subparsers(dest="command", required=True)

    def loc(sp, required=True):
        sp.add_argument("--lat", type=float, required=required)
        sp.add_argument("--lng", type=float, required=required)

    s = sub.add_parser("register-user")
    s.add_argument("--name", required=True)
    s.add_argument("--phone", required=True)

    s = sub.add_parser("register-driver")
    s.add_argument("--name", required=True)
    s.add_argument("--phone", required=True)
    s.add_argument("--car-type", choices=[t.value for t in CarType], required=True)
    s.add_argument("--rating", type=float, default=5.0)
    loc(s)

    s = sub.add_parser("update-location", help="update a cab's location (extends the route if on a ride)")
    s.add_argument("driver_id")
    loc(s)

    s = sub.add_parser("book")
    s.add_argument("--user", required=True)
    s.add_argument("--car-type", choices=[t.value for t in CarType], required=True)
    s.add_argument("--radius", type=float, help="search radius in km (default: from config)")
    s.add_argument("--coupon")
    s.add_argument("--strategy", choices=list(MATCHING_STRATEGIES), help="default: from config")
    loc(s)

    s = sub.add_parser("end", help="end a ride; --lat/--lng is the drop point")
    s.add_argument("ride_id")
    loc(s, required=False)

    s = sub.add_parser("cancel")
    s.add_argument("ride_id")

    s = sub.add_parser("user-rides")
    s.add_argument("user_id")

    s = sub.add_parser("driver-rides")
    s.add_argument("driver_id")

    s = sub.add_parser("add-coupon")
    s.add_argument("code")
    s.add_argument("--type", choices=[t.value for t in DiscountType], required=True)
    s.add_argument("--value", type=float, required=True)
    s.add_argument("--max-discount", type=float)

    s = sub.add_parser("delete-coupon")
    s.add_argument("code")
    return p


def run(c: Container, a: argparse.Namespace) -> str:
    if a.command == "register-user":
        u = c.users.register(a.name, a.phone)
        return f"registered user {u.id} ({u.name})"
    if a.command == "register-driver":
        d = c.drivers.register(a.name, a.phone, CarType(a.car_type), Location(a.lat, a.lng), a.rating)
        return f"registered driver {d.id} ({d.name}, {d.car_type.value}, rating {d.rating})"
    if a.command == "update-location":
        d = c.drivers.update_location(a.driver_id, Location(a.lat, a.lng))
        return f"driver {d.id} now at ({d.location.lat}, {d.location.lng}) [{d.status.value}]"
    if a.command == "book":
        ride = c.rides.book(a.user, Location(a.lat, a.lng), CarType(a.car_type), a.radius, a.coupon,
                            MATCHING_STRATEGIES[a.strategy] if a.strategy else None)
        return "booked " + format_ride(ride)
    if a.command == "end":
        drop = Location(a.lat, a.lng) if a.lat is not None and a.lng is not None else None
        return "ended " + format_ride(c.rides.end(a.ride_id, drop))
    if a.command == "cancel":
        return "cancelled " + format_ride(c.rides.cancel(a.ride_id))
    if a.command == "user-rides":
        return format_history(c.rides.history_for_user(a.user_id))
    if a.command == "driver-rides":
        return format_history(c.rides.history_for_driver(a.driver_id))
    if a.command == "add-coupon":
        cp = c.coupons.add(a.code, DiscountType(a.type), a.value, a.max_discount)
        cap = f", max ₹{cp.max_discount}" if cp.max_discount else ""
        return f"added coupon {cp.code} ({cp.discount_type.value} {cp.value}{cap})"
    if a.command == "delete-coupon":
        c.coupons.delete(a.code)
        return f"deleted coupon {a.code.upper()}"
    raise AssertionError(f"unhandled command {a.command}")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    from psycopg_pool import PoolTimeout

    from app.postgres import open_pool, postgres_repositories
    try:
        pool = open_pool(config.database_url, max_size=config.db_pool_size)
    except PoolTimeout:
        print(f"error: cannot reach Postgres at {config.database_url}; run `docker compose up -d`", file=sys.stderr)
        return 2
    try:
        print(run(build_container(config, postgres_repositories(pool)), args))
        return 0
    except RideHailingError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        pool.close()


if __name__ == "__main__":
    sys.exit(main())
