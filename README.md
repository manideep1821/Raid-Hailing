# Ride Hailing Service — Backend (Python CLI + Postgres)

## Run it

```bash
docker compose up -d --wait            # Postgres 16 on localhost:5433 (dbs: rides, rides_test)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q                    # 103 tests; Postgres cases skip if the DB is down

alias rides=".venv/bin/python -m app.cli"
rides register-user --name Asha --phone 9000000001
rides register-driver --name Sam --phone 9111111111 --car-type sedan --lat 12.975 --lng 77.59 --rating 4.6
rides add-coupon SAVE20 --type percentage --value 20 --max-discount 30
rides book --user U-xxxx --car-type hatchback --lat 12.9716 --lng 77.5946 [--radius 5] [--coupon SAVE20] [--strategy nearest|highest_rated]
rides update-location D-xxxx --lat 13.00 --lng 77.60
rides end R-xxxx --lat 13.05 --lng 77.62
rides cancel R-xxxx
rides user-rides U-xxxx     |   rides driver-rides D-xxxx
rides delete-coupon SAVE20
```

## Configuration

All business values live in [`config.toml`](config.toml). None are hard-coded in the app, which has no fallback defaults:

| Setting | Key |
|---|---|
| Fare tiers + minimum fare per car type | `[pricing.<car_type>]` `min_fare`, `tiers = [{ up_to_km, rate_per_km }, …, { rate_per_km }]` |
| Free-upgrade path | `[booking.upgrades]` e.g. `hatchback = "sedan"` (remove to disable) |
| Default search radius | `booking.default_radius_km` (overridable per booking with `--radius`) |
| Default matching strategy | `booking.default_matching_strategy` (overridable per booking with `--strategy`) |
| Cancellation window + fee | `cancellation.grace_period_minutes`, `cancellation.fee` |
| Surge | `surge.enabled`, `surge.cell_size_deg`, `surge.cap` |
| Database | `database.url`, `database.pool_size` |

The config is validated at startup, and a bad value fails fast with a precise message. Examples: unknown car type, a car type with no pricing, non-increasing tiers, a last tier that isn't open-ended, a negative rate, an unknown strategy, a self-upgrade, or a missing key.

Environment overrides: `RIDES_CONFIG=/path/other.toml` selects a different file, and `DATABASE_URL` overrides `database.url`. For the tests: `TEST_DATABASE_URL`. For docker-compose: `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_PORT`.

The tests use their own pinned [`tests/config.test.toml`](tests/config.test.toml), so tuning prices in `config.toml` never breaks them.

## Layout

| Layer | File | Responsibility |
|---|---|---|
| Domain | `app/models.py` | Entities, enums, haversine distance, route distance |
| Pricing | `app/pricing.py` | `FareStrategy` (tiered, per car type), `SurgeStrategy`, coupon discount, `PricingEngine` |
| Matching | `app/matching.py` | `MatchingStrategy`: nearest, highest-rated |
| Cancellation | `app/cancellation.py` | `CancellationPolicy`: free inside a grace window, flat fee after |
| Services | `app/services.py` | Use cases: register, book (with upgrade), end, cancel, history, coupons |
| Storage | `app/repository.py`, `app/postgres.py`, `app/schema.sql` | Repository contracts + in-memory and Postgres implementations |
| Config | `config.toml`, `app/config.py` | All tunable values; loaded + validated into typed `AppConfig` |
| Wiring / UI | `app/container.py`, `app/cli.py` | Builds services from `AppConfig`, argparse CLI |

## Assumptions

- **Tiers are slabs, cumulative**: km 0–2 @₹10, km 2–5 @₹8, km 5+ @₹5 ("3–5 km" in the spec read as "the 3rd to 5th km"). Minimum fare applies to the pre-discount total.
- **Rates** (shipped `config.toml`): Hatchback uses the spec example exactly (min ₹50, 10/8/5). Sedan: min ₹60, 12/10/7.
- **Upgrade** only goes Hatchback → Sedan, never downward. An upgraded ride is **priced at the requested type's rate**, and the ride records both requested and assigned car types.
- **Distance** is the sum of the legs along the ride's route: the pickup point, every `update-location` sent while the ride is ongoing, and the optional drop point. Distances are straight-line (haversine), not road distances.
- **Coupons** are validated when the ride is **booked** (the spec says "when starting a ride") and applied to the final fare. The coupon is snapshotted onto the ride, so deleting it mid-ride doesn't change what the rider was promised. Discounts are flat or percentage with an optional cap, never below ₹0, and applied after surge. Codes are case-insensitive, and coupons are unlimited-use with no expiry.
- A user can have at most one ongoing ride. The shipped default search radius is 5 km.
- Cancellation is free within 2 minutes of booking and ₹25 after that (shipped config). A cancelled ride leaves the driver at their last reported location.
- No auth, payments, driver acceptance step, or driver on/offline toggle.

## Key design decisions & trade-offs

- **Strategies at each variation point.** Fare per car type, surge, matching and cancellation are all interfaces injected into the services. Adding an SUV means one `CarType` value plus a `[pricing.suv]` section in `config.toml`, with no booking code touched; the config loader refuses to start if a car type has no pricing. The matching strategy is set by config and can be overridden per booking (`--strategy`).
- **Upgrades are data** (`[booking.upgrades]`), not an `if` inside booking logic.
- **Config has no code fallbacks.** Services receive every value through their constructors, and `config.toml` is the single source of truth. Tests pin their own config file.
- **Concurrency is enforced in storage, not in an app lock.** A CLI runs each command in a new process, so a `threading.Lock` would protect nothing. Instead:
  - booking claims a driver with `UPDATE drivers SET status='on_ride' WHERE id=? AND status='available'`. Whoever loses the claim moves on to the next ranked driver;
  - end/cancel use `UPDATE ... WHERE status='ongoing'`, so a ride can't be closed twice;
  - partial unique indexes guarantee one ongoing ride per user and per driver.

  The in-memory repository implements the same contract with locks, and hands out **copies** so it behaves like a database. The ride test suite runs against both backends, including a 20-thread race for 3 drivers.
- **Driver search** reads the available drivers of the requested type from SQL and applies the radius check in Python. That's fine at this scale; see below for the scalable version.
- **Surge** (`GridDemandSurge`) is implemented and tested at the library level, but its state is in-process, so the CLI runs without surge (`NoSurge`).
- Money is stored as `float`, rounded to 2 decimal places at the pricing boundary.

## With more time

- PostGIS / geohash index for radius search instead of filtering in the application.
- `Decimal` / integer paise for money; store the fare breakdown (base, surge, discount) on each ride.
- Persist surge cells and recompute them from live demand/supply; add coupon expiry, usage limits and per-user limits.
- Driver availability toggle, a driver acceptance/timeout flow, and a ride state machine (requested → accepted → started → completed).
- Migrations (Alembic) instead of an idempotent `schema.sql` applied on startup.
- An HTTP API over the same services (they're framework-agnostic).

## How AI was used

<!-- TODO: rewrite in your own words before submitting -->
- Prompted for: the domain model, the pricing slab algorithm, the strategy interfaces, test cases for the boundaries (tier edges, minimum fare, upgrade pricing, coupon caps), and the Postgres repositories.
- Rejected / rewrote:
  - The first version was in-memory behind FastAPI and used a single `threading.Lock` around driver assignment. After switching to a CLI with Postgres, that lock was useless across processes, so atomicity moved into conditional `UPDATE`s and partial unique indexes.
  - The in-memory repositories originally returned shared object references, which let service code "save" state just by mutating objects. That would have hidden bugs in the Postgres version, so the repositories now return copies, and the tests re-read state rather than trusting stale objects.
  - A coupon was first looked up again when the ride ended. It is now snapshotted at booking, because deleting a coupon mid-ride shouldn't change the price.
- Verified by: hand-computing the tier boundaries in the parametrised tests, running the ride suite against both backends, and repeatedly running the concurrency race.
