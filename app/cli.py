import argparse
import shlex
import sys
from typing import Dict, List

from app.config import ConfigError, load_config
from app.container import Container, build_container
from app.discounts import DISCOUNT_TYPES, build_discount
from app.exceptions import RideHailingError
from app.matching import MATCHING_STRATEGIES
from app.models import CarType, Location, Ride


def format_ride(r: Ride) -> str:
    car = r.assigned_car_type.value
    if r.upgraded:
        car += f" (upgraded from {r.requested_car_type.value}, billed as {r.requested_car_type.value})"
    lines = [f"ride {r.id} [{r.status.value}]  user={r.user_id}  driver={r.driver_id}  car={car}",
             f"  booked {r.booked_at:%Y-%m-%d %H:%M:%S}"]
    if r.picked_up_at:
        lines.append(f"  picked up {r.picked_up_at:%Y-%m-%d %H:%M:%S}")
    if r.surge_multiplier != 1:
        lines.append(f"  surge x{r.surge_multiplier:g} (locked at booking)")
    if r.coupon:
        lines.append(f"  coupon {r.coupon.code}")
    if r.fare:
        f = r.fare
        lines.append(f"  distance {r.distance_km:.2f} km  base fare ₹{f.base_fare:.2f}")
        if f.surge_multiplier != 1:
            lines.append(f"  with surge x{f.surge_multiplier:g}: ₹{f.surged_fare:.2f}")
        if f.discount:
            lines.append(f"  coupon discount -₹{f.discount:.2f}")
        lines.append(f"  total ₹{f.total:.2f}")
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

    s = sub.add_parser("update-location", help="update a cab's location (extends the route once the rider is on board)")
    s.add_argument("driver_id")
    loc(s)

    s = sub.add_parser("book")
    s.add_argument("--user", required=True)
    s.add_argument("--car-type", choices=[t.value for t in CarType], required=True)
    s.add_argument("--radius", type=float, help="search radius in km (default: from config)")
    s.add_argument("--coupon")
    s.add_argument("--strategy", choices=list(MATCHING_STRATEGIES), help="default: from config")
    loc(s)

    s = sub.add_parser("start", help="rider picked up; distance is measured from here")
    s.add_argument("ride_id")

    s = sub.add_parser("end", help="end a ride; --lat/--lng is the drop point")
    s.add_argument("ride_id")
    loc(s, required=False)

    s = sub.add_parser("cancel", help="cancel a ride before pickup")
    s.add_argument("ride_id")

    s = sub.add_parser("user-rides")
    s.add_argument("user_id")

    s = sub.add_parser("driver-rides")
    s.add_argument("driver_id")

    s = sub.add_parser("add-coupon")
    s.add_argument("code")
    s.add_argument("--type", choices=list(DISCOUNT_TYPES), required=True)
    s.add_argument("--value", type=float, required=True)
    s.add_argument("--max-discount", type=float)

    s = sub.add_parser("delete-coupon")
    s.add_argument("code")

    s = sub.add_parser("shell", help="interactive prompt; with --memory, no Postgres needed")
    s.add_argument("--memory", action="store_true", help="keep state in memory for this session only")
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
    if a.command == "start":
        return "started " + format_ride(c.rides.start(a.ride_id))
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
        params = {"value": a.value}
        if a.max_discount is not None:
            params["max_discount"] = a.max_discount
        cp = c.coupons.add(a.code, build_discount(a.type, params))
        details = ", ".join(f"{k}={v:g}" for k, v in cp.discount.params().items() if v is not None)
        return f"added coupon {cp.code} ({cp.discount.kind}: {details})"
    if a.command == "delete-coupon":
        c.coupons.delete(a.code)
        return f"deleted coupon {a.code.upper()}"
    raise AssertionError(f"unhandled command {a.command}")


def execute(c: Container, a: argparse.Namespace) -> int:
    try:
        print(run(c, a))
        return 0
    except RideHailingError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def shell(c: Container) -> int:
    """One container for the whole session, so in-memory state survives between commands."""
    parser = build_parser()
    interactive = sys.stdin.isatty()
    while True:
        try:
            line = input("rides> " if interactive else "")
        except EOFError:
            return 0
        try:
            argv = shlex.split(line, comments=True)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            continue
        if not argv:
            continue
        if argv[0] in ("exit", "quit"):
            return 0
        if not interactive:
            print(f"rides> {line}", flush=True)  # keep the echo ahead of any error on stderr
        try:
            args = parser.parse_args(argv)
        except SystemExit:  # argparse has already printed the usage error
            continue
        if args.command == "shell":
            print("error: already in a shell", file=sys.stderr)
            continue
        execute(c, args)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    if args.command == "shell" and args.memory:
        return shell(build_container(config))

    from psycopg import Error as PostgresError
    from psycopg_pool import PoolTimeout

    from app.postgres import SchemaOutOfDateError, apply_schema, open_pool, postgres_repositories
    try:
        pool = open_pool(config.database_url, max_size=config.db_pool_size)
    except PoolTimeout:
        print(f"error: cannot reach Postgres at {config.database_url}; run `docker compose up -d`", file=sys.stderr)
        return 2
    try:
        try:
            apply_schema(pool)
        except (PostgresError, SchemaOutOfDateError) as e:
            print(f"error: the database schema is out of date ({e}); reset it with "
                  "`docker compose down -v && docker compose up -d --wait`", file=sys.stderr)
            return 2
        c = build_container(config, postgres_repositories(pool))
        return shell(c) if args.command == "shell" else execute(c, args)
    finally:
        pool.close()


if __name__ == "__main__":
    sys.exit(main())
