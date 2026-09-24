import copy
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.config import DEFAULT_CONFIG_PATH, ConfigError, load_config, parse_config
from app.container import build_container
from app.exceptions import NoDriverAvailableError
from app.matching import HighestRatedDriverStrategy
from app.models import CarType, Location

TEST_CONFIG_PATH = Path(__file__).with_name("config.test.toml")
BASE = tomllib.loads(TEST_CONFIG_PATH.read_text())
PICKUP = Location(12.9716, 77.5946)


def with_changes(mutate) -> dict:
    raw = copy.deepcopy(BASE)
    mutate(raw)
    return raw


def test_shipped_config_is_valid_and_prices_every_car_type():
    config = load_config(DEFAULT_CONFIG_PATH)
    assert set(config.fare_strategies) == set(CarType)


def test_values_are_mapped():
    config = load_config(TEST_CONFIG_PATH)
    assert config.default_radius_km == 5.0
    assert config.default_matching_strategy == "nearest"
    assert config.upgrade_path == {CarType.HATCHBACK: CarType.SEDAN}
    assert config.cancellation_grace == timedelta(minutes=2)
    assert config.cancellation_fee == 25
    assert config.fare_strategies[CarType.HATCHBACK].base_fare(10) == 69
    assert config.surge.enabled is False


def test_rides_config_env_var_selects_file(monkeypatch, tmp_path):
    custom = tmp_path / "custom.toml"
    custom.write_text(TEST_CONFIG_PATH.read_text().replace("default_radius_km = 5.0", "default_radius_km = 9.0"))
    monkeypatch.setenv("RIDES_CONFIG", str(custom))
    assert load_config().default_radius_km == 9.0


def test_database_url_env_overrides_file(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://elsewhere/db")
    assert load_config(TEST_CONFIG_PATH).database_url == "postgresql://elsewhere/db"


def test_missing_or_malformed_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[booking\n")
    with pytest.raises(ConfigError):
        load_config(bad)


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["pricing"].update(suv=r["pricing"]["sedan"]), "unknown car type 'suv'"),
    (lambda r: r["pricing"].pop("sedan"), "pricing missing for car type\\(s\\): sedan"),
    (lambda r: r["pricing"]["sedan"]["tiers"].reverse(), "pricing.sedan"),
    (lambda r: r["pricing"]["sedan"]["tiers"][-1].update(up_to_km=9), "open-ended"),
    (lambda r: r["pricing"]["sedan"]["tiers"][0].update(rate_per_km=-1), "rate_per_km must be >= 0"),
    (lambda r: r["pricing"]["sedan"].update(min_fare=-5), "min_fare must be >= 0"),
    (lambda r: r["pricing"]["sedan"]["tiers"][0].update(rate_per_km="ten"), "not supported"),
    (lambda r: r["booking"].update(default_radius_km=0), "default_radius_km must be > 0"),
    (lambda r: r["booking"].update(default_matching_strategy="cheapest"), "unknown matching strategy"),
    (lambda r: r["booking"]["upgrades"].update(sedan="sedan"), "cannot upgrade to itself"),
    (lambda r: r["booking"]["upgrades"].update(hatchback="limo"), "unknown car type 'limo'"),
    (lambda r: r["cancellation"].update(fee=-1), "cancellation.fee"),
    (lambda r: r["cancellation"].pop("grace_period_minutes"), "missing key 'grace_period_minutes'"),
    (lambda r: r["surge"].update(cap=0.5), "surge.cap"),
    (lambda r: r["surge"].update(enabled="yes"), "surge.enabled"),
    (lambda r: r["database"].update(pool_size=0), "pool_size"),
])
def test_invalid_config_rejected(mutate, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(with_changes(mutate))


# --- config drives behaviour ---------------------------------------------------------

def container_with(mutate, clock=datetime.now):
    return build_container(parse_config(with_changes(mutate)), clock=clock)


def setup(c, car_type=CarType.SEDAN, km_north=3.0, rating=4.5):
    user = c.users.register("Asha", "900")
    driver = c.drivers.register("Sam", "911", car_type, Location(PICKUP.lat + km_north / 111.195, PICKUP.lng), rating)
    return user, driver


def test_default_radius_from_config():
    c = container_with(lambda r: r["booking"].update(default_radius_km=1.0))
    user, _ = setup(c, km_north=3)
    with pytest.raises(NoDriverAvailableError, match="1.0 km"):
        c.rides.book(user.id, PICKUP, CarType.SEDAN)
    assert c.rides.book(user.id, PICKUP, CarType.SEDAN, radius_km=4).status.value == "ongoing"


def test_default_matching_strategy_from_config():
    c = container_with(lambda r: r["booking"].update(default_matching_strategy="highest_rated"))
    user, _ = setup(c, km_north=0.5, rating=3.0)
    best = c.drivers.register("Top", "912", CarType.SEDAN, Location(PICKUP.lat + 0.03, PICKUP.lng), 4.9)
    assert isinstance(c.rides.matching, HighestRatedDriverStrategy)
    assert c.rides.book(user.id, PICKUP, CarType.SEDAN).driver_id == best.id


def test_upgrade_can_be_disabled_in_config():
    c = container_with(lambda r: r["booking"].pop("upgrades"))
    user, _ = setup(c, car_type=CarType.SEDAN, km_north=1)
    with pytest.raises(NoDriverAvailableError):
        c.rides.book(user.id, PICKUP, CarType.HATCHBACK)


def test_cancellation_window_and_fee_from_config():
    now = [datetime(2026, 1, 1, 10, 0)]
    c = container_with(lambda r: r["cancellation"].update(grace_period_minutes=10, fee=40),
                       clock=lambda: now[0])
    user, _ = setup(c, km_north=1)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    now[0] += timedelta(minutes=5)
    assert c.rides.cancel(ride.id).cancellation_fee == 0          # inside 10-min window

    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    now[0] += timedelta(minutes=11)
    assert c.rides.cancel(ride.id).cancellation_fee == 40


def test_prices_from_config():
    def cheap_sedan(r):
        r["pricing"]["sedan"] = {"min_fare": 10, "tiers": [{"rate_per_km": 1}]}
    c = container_with(cheap_sedan)
    user, driver = setup(c, km_north=0)
    ride = c.rides.book(user.id, PICKUP, CarType.SEDAN)
    ended = c.rides.end(ride.id, drop=Location(PICKUP.lat + 20 / 111.195, PICKUP.lng))
    assert ended.fare == pytest.approx(20, abs=0.05)
