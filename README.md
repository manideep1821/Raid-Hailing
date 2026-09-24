# Ride Hailing Service — Backend (Python CLI + Postgres)

## Run it

```bash
docker compose up -d --wait            # Postgres 16 on localhost:5433 (dbs: rides, rides_test)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest -q                    # 160 tests; Postgres cases skip if the DB is down
.venv/bin/python -m app.demo           # scripted walkthrough of every edge case, in memory

alias rides=".venv/bin/python -m app.cli"
rides register-user --name Asha --phone 9000000001
rides register-driver --name Sam --phone 9111111111 --car-type sedan --lat 12.975 --lng 77.59 --rating 4.6
rides add-coupon SAVE20 --type percentage --value 20 --max-discount 30     # or --type flat --value 25
rides book --user U-xxxx --car-type hatchback --lat 12.9716 --lng 77.5946 [--radius 5] [--coupon SAVE20] [--strategy nearest|highest_rated]
rides update-location D-xxxx --lat 12.972 --lng 77.594     # driver on the way: not billed
rides start R-xxxx                                          # rider picked up
rides update-location D-xxxx --lat 13.00 --lng 77.60       # now extends the billed route
rides end R-xxxx --lat 13.05 --lng 77.62
rides cancel R-xxxx                                         # only before pickup
rides user-rides U-xxxx     |   rides driver-rides D-xxxx
rides delete-coupon SAVE20
rides shell --memory                                        # one session, no Postgres needed
```

There are no migrations: `schema.sql` carries a version, and if the CLI reports an out-of-date schema, reset the local DB with `docker compose down -v && docker compose up -d --wait`. The test DB is rebuilt on every test run.

## Configuration

All business values live in [`config.toml`](config.toml). None are hard-coded in the app, which has no fallback defaults:

| Setting | Key |
|---|---|
| Fare tiers + minimum fare per car type | `[pricing.<car_type>]` `min_fare`, `tiers = [{ up_to_km, rate_per_km }, …, { rate_per_km }]` |
| Free-upgrade path | `[booking.upgrades]` e.g. `hatchback = "sedan"`; entries chain (`sedan = "suv"` makes hatchback → sedan → suv); remove to disable |
| Default search radius | `booking.default_radius_km` (overridable per booking with `--radius`) |
| Default matching strategy | `booking.default_matching_strategy` (overridable per booking with `--strategy`) |
| Driver offline timeout | `drivers.offline_after_minutes`: no location update for this long → not matched, not surge supply |
| Cancellation window + fee | `cancellation.grace_period_minutes`, `cancellation.fee` |
| Surge | `surge.enabled`, `surge.area_radius_km`, `surge.window_minutes`, `surge.cap` |
| Database | `database.url`, `database.pool_size` |

The config is validated at startup, and a bad value fails fast with a precise message. Examples: unknown car type, a car type with no pricing, non-increasing tiers, a last tier that isn't open-ended, a negative rate, an unknown strategy, an upgrade cycle, or a missing key.

Environment overrides: `RIDES_CONFIG=/path/other.toml` selects a different file, and `DATABASE_URL` overrides `database.url`. For the tests: `TEST_DATABASE_URL`. For docker-compose: `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_PORT`.

The tests use their own pinned [`tests/config.test.toml`](tests/config.test.toml), so tuning prices in `config.toml` never breaks them.

## Layout

| Layer | File | Responsibility |
|---|---|---|
| Domain | `app/models.py` | Entities, ride lifecycle, `FareBreakdown`, haversine distance, running ride distance |
| Pricing | `app/pricing.py` | `FareStrategy` (tiered, per car type), `PricingEngine` (breakdown; bills the requested type) |
| Discounts | `app/discounts.py` | `Discount` types (flat, percentage with cap) and their registry |
| Surge | `app/surge.py` | `SurgeStrategy`: none, or demand/supply near the pickup |
| Matching | `app/matching.py` | `MatchingStrategy`: nearest, highest-rated |
| Cancellation | `app/cancellation.py` | `CancellationPolicy`: free inside a grace window, flat fee after |
| Services | `app/services.py` | Use cases: register, book (with upgrade), end, cancel, history, coupons |
| Storage | `app/repository.py`, `app/postgres.py`, `app/schema.sql` | Repository contracts + in-memory and Postgres implementations |
| Config | `config.toml`, `app/config.py` | All tunable values; loaded + validated into typed `AppConfig` |
| Wiring / UI | `app/container.py`, `app/cli.py`, `app/demo.py` | Builds services from `AppConfig`, argparse CLI + shell, scripted demo |

## Assumptions

- **Tiers are slabs, cumulative**: km 0–2 @₹10, km 2–5 @₹8, km 5+ @₹5 ("3–5 km" in the spec read as "the 3rd to 5th km").
- **Fare order**: tiered fare → minimum fare → × surge → − coupon. So the minimum is surged too, and a coupon can take a fare below the minimum (never below ₹0).
- **Rates** (shipped `config.toml`): Hatchback uses the spec example exactly (min ₹50, 10/8/5). Sedan: min ₹60, 12/10/7.
- **Upgrade** only goes Hatchback → Sedan, never downward. An upgraded ride is **priced at the requested type's rate**, and the ride records both requested and assigned car types.
- **Ride lifecycle**: `booked` (driver assigned, heading to the pickup) → `ongoing` (`start`: rider on board) → `completed`, or `booked` → `cancelled`. Ride history shows booked and ongoing rides together under "ongoing".
- **Distance** is the sum of the legs along the ride's route: the pickup point, every `update-location` sent after `start`, and the optional drop point. It is kept as a running total (plus the last point), not as a stored route. The driver's approach to the pickup is not billed. Distances are straight-line (haversine), not road distances.
- **Drivers go offline by silence**: a driver with no location update for `drivers.offline_after_minutes` isn't matched or counted as surge supply. Any update (and ending a ride) counts as a heartbeat. The shipped 30 minutes suits manual CLI use; an app sending heartbeats every few seconds would use about a minute.
- **Coupons** are validated when the ride is **booked**, not at `start` (the spec says "when starting a ride"), so the rider knows before a driver is committed whether the code works; it is applied to the final fare. The coupon is snapshotted onto the ride, so deleting it mid-ride doesn't change what the rider was promised. Discounts are flat or percentage with an optional cap, never below ₹0, and applied after surge. Codes are case-insensitive, and coupons are unlimited-use with no expiry.
- A user can have at most one ongoing ride. The shipped default search radius is 5 km.
- A phone number registers at most one user and at most one driver (one person can be both). Coordinates must be valid: latitude within ±90, longitude within ±180.
- **Driver ratings** are set at registration and never change (there is no rating step after a ride), so "highest-rated" matching ranks on that fixed value.
- Cancellation is only possible before pickup. It is free within 2 minutes of booking and ₹25 after that (shipped config). A cancelled ride leaves the driver at their last reported location.
- **Surge** is computed once, at booking, and locked onto the ride like the coupon. Demand = this rider plus the *distinct other* riders who booked within 2 km of the pickup in the last 15 minutes; supply = online available drivers of any type within 2 km, counted as at least 1. Multiplier = demand / supply within [1, cap]. So a rider with no competition is never surged (even when the only driver is outside the 2 km area but inside the booking radius), and cancelling and rebooking can't raise your own price.
- No auth, payments, driver acceptance step, or explicit driver on/offline toggle.

## Key design decisions & trade-offs

- **Strategies at each variation point.** Fare per car type, surge, matching, cancellation and coupon discounts are all interfaces injected into the services. Adding an SUV means one `CarType` value plus a `[pricing.suv]` section in `config.toml`, with no booking code touched; the config loader refuses to start if a car type has no pricing. The matching strategy is set by config and can be overridden per booking (`--strategy`).
- **Upgrades are data** (`[booking.upgrades]`), not an `if` inside booking logic. Booking walks the chain; the config loader rejects cycles.
- **Discount types are a registry.** A coupon stores `(kind, params)` as JSON, so a new type (e.g. "₹X off above ₹Y") is one dataclass in `app/discounts.py` plus its registry entry, with no schema change. Each type validates its own parameters.
- **The fare is a breakdown** (base, surge, discount, total) stored on the ride, so the receipt explains itself. The rule "an upgrade is billed at the requested type" lives in `PricingEngine.price_ride`, next to the rest of the pricing.
- **Config has no code fallbacks.** Services receive every value through their constructors, and `config.toml` is the single source of truth. Tests pin their own config file.
- **Concurrency is enforced in storage, not in an app lock.** A CLI runs each command in a new process, so a `threading.Lock` would protect nothing. Instead:
  - **a driver's status follows their ride, in the same transaction.** `RideRepository.create` claims the driver (`UPDATE drivers SET status='on_ride' WHERE id=? AND status='available'`) and inserts the ride together. Whoever loses the claim moves on to the next ranked driver, and if the insert fails the claim rolls back with it. Closing a ride releases the driver in the ride's transaction too. A crash at any point therefore can't leave a driver stuck `on_ride` with no ride;
  - every change to a ride (start, a location update's leg, end, cancel) goes through `RideRepository.modify(ride_id, change)`: a `SELECT … FOR UPDATE` row lock, the change, and the write, in one transaction. The service passes the change as a function, so the rules (status checks, pricing) stay in the service while the storage decides how to make it atomic. So a ride can't be started or closed twice, and a location update lands either before `end` (and is billed) or after it (and is ignored), never lost in between;
  - a completed ride moves the driver to its drop point only if the driver hasn't reported a newer location since, so ending a ride never overwrites a fresher position;
  - partial unique indexes guarantee one active (booked or ongoing) ride per user and per driver.

  The in-memory repository implements the same contract with one lock shared by drivers and rides (its version of a transaction), and hands out **copies** so it behaves like a database. The ride test suite runs against both backends, including a 20-thread race for 3 drivers, location updates racing `end`, and a change that fails halfway.
- **Running distance, not a stored route.** Each location update adds one leg to `distance_km` and moves `last_location`: constant work per update, no growing column. The trade-off is that the route itself isn't kept; see Scaling.
- **Surge reads live state** from the repositories rather than keeping its own, so it works across CLI processes.
- Money is stored as `float`, rounded to 2 decimal places at the pricing boundary.

## Scaling: where this breaks first, and the fix

| Bottleneck today | Fix |
|---|---|
| `find_available` loads every available driver of a type and checks the radius in Python: cost grows with all drivers, in every city. | PostGIS `geography` column + GiST index: `ST_DWithin` to filter and `ORDER BY location <-> pickup LIMIT k` for nearest. At higher scale, live positions in Redis `GEOSEARCH` or H3 cells, with Postgres as the record of rides. |
| Driver location updates are Postgres writes; at a heartbeat every few seconds per driver, that is most of the write load. | Live positions in Redis; Postgres only for ride legs. Keep the full route, if needed for disputes, in an append-only `ride_points` table or a stream. |
| `count_booked_near` scans every ride booked in the last 15 minutes, in all cities, on each booking. | Per-area counters (H3 cell × minute bucket, with a TTL) incremented on booking; a background job computes each cell's smoothed multiplier every few seconds; booking just reads it. Count unique riders so rebooking can't inflate demand. |
| Hot spots: every booking ranks the same nearest driver first, and the losers of the claim retry down the list. | Pick and claim in one statement: `UPDATE … WHERE id = (SELECT … ORDER BY distance LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *`. Works where the matching strategy can be expressed as an `ORDER BY`. |
| Ride history returns every ride a user has ever taken. | Index `(user_id, booked_at DESC)` and cursor pagination; partition `rides` by month and serve history from a read replica. |
| One process per CLI command, each opening a pool. | The same services behind an HTTP API (FastAPI), stateless instances behind a load balancer; shard or partition by city, since rides rarely cross cities. |
| A mobile client retrying `book` after a timeout can book twice. | An idempotency key on `book` and `end`, stored with a unique constraint. |

## With more time

- `Decimal` / integer paise for money.
- Coupon expiry, usage limits and per-user limits (a `coupon_redemptions` table with a unique `(coupon, user)`).
- A driver acceptance/timeout step before `booked`, with ride events published through an outbox for notifications and billing.
- Migrations (Alembic) instead of a versioned `schema.sql` that must be reset when it changes.
- Metrics: match rate, no-driver rate, booking latency, surge by area.

## How AI was used

<!-- TODO: rewrite in your own words before submitting -->
- Prompted for: the domain model, the pricing slab algorithm, the strategy interfaces, test cases for the boundaries (tier edges, minimum fare, upgrade pricing, coupon caps), and the Postgres repositories.
- Rejected / rewrote:
  - The first version was in-memory behind FastAPI and used a single `threading.Lock` around driver assignment. After switching to a CLI with Postgres, that lock was useless across processes, so atomicity moved into conditional `UPDATE`s and partial unique indexes.
  - The in-memory repositories originally returned shared object references, which let service code "save" state just by mutating objects. That would have hidden bugs in the Postgres version, so the repositories now return copies, and the tests re-read state rather than trusting stale objects.
  - A coupon was first looked up again when the ride ended. It is now snapshotted at booking, because deleting a coupon mid-ride shouldn't change the price.
- Verified by: hand-computing the tier boundaries in the parametrised tests, running the ride suite against both backends, and repeatedly running the concurrency race.
