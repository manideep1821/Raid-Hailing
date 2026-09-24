"""Loads config.toml into typed, validated settings. All tunable business values live
in the config file; code carries no fallback defaults, so there is one source of truth."""
import os
import tomllib
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from app.domain.exceptions import ValidationError
from app.domain.models import CarType
from app.strategies.matching import MATCHING_STRATEGIES
from app.strategies.pricing import FareStrategy, TieredFareStrategy

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class SurgeConfig:
    enabled: bool
    area_radius_km: float
    window: timedelta
    cap: float


@dataclass(frozen=True)
class AppConfig:
    fare_strategies: Dict[CarType, FareStrategy]
    upgrade_path: Dict[CarType, CarType]
    default_radius_km: float
    default_matching_strategy: str
    driver_timeout: timedelta
    cancellation_grace: timedelta
    cancellation_fee: float
    surge: SurgeConfig
    database_url: str
    db_pool_size: int


def load_config(path: Optional[Path] = None) -> AppConfig:
    path = Path(path or os.environ.get("RIDES_CONFIG") or DEFAULT_CONFIG_PATH)
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}")
    try:
        return parse_config(raw)
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}")


def parse_config(raw: Dict[str, Any]) -> AppConfig:
    try:
        return _parse(raw)
    except KeyError as e:
        raise ConfigError(f"missing key {e}")
    except (TypeError, ValueError) as e:
        raise ConfigError(str(e))


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def _car_type(name: str) -> CarType:
    try:
        return CarType(name)
    except ValueError:
        raise ConfigError(f"unknown car type '{name}' (known: {', '.join(t.value for t in CarType)})")


def _fare_strategy(name: str, spec: Dict[str, Any]) -> FareStrategy:
    _check(spec["min_fare"] >= 0, f"pricing.{name}.min_fare must be >= 0")
    tiers = []
    for tier in spec["tiers"]:
        _check(tier["rate_per_km"] >= 0, f"pricing.{name}: rate_per_km must be >= 0")
        tiers.append((tier.get("up_to_km"), tier["rate_per_km"]))
    try:
        return TieredFareStrategy(spec["min_fare"], tiers)
    except ValidationError as e:
        raise ConfigError(f"pricing.{name}: {e}")


def _check_no_upgrade_cycle(upgrades: Dict[CarType, CarType]) -> None:
    for start in upgrades:
        path = [start]
        while path[-1] in upgrades:
            path.append(upgrades[path[-1]])
            _check(path[-1] not in path[:-1], f"upgrade cycle: {' -> '.join(t.value for t in path)}")


def _parse(raw: Dict[str, Any]) -> AppConfig:
    booking, cancellation, surge, db = raw["booking"], raw["cancellation"], raw["surge"], raw["database"]

    fares = {_car_type(name): _fare_strategy(name, spec) for name, spec in raw["pricing"].items()}
    missing = [t.value for t in CarType if t not in fares]
    _check(not missing, f"pricing missing for car type(s): {', '.join(missing)}")

    upgrades = {_car_type(k): _car_type(v) for k, v in booking.get("upgrades", {}).items()}
    _check_no_upgrade_cycle(upgrades)

    _check(booking["default_radius_km"] > 0, "booking.default_radius_km must be > 0")
    strategy = booking["default_matching_strategy"]
    _check(strategy in MATCHING_STRATEGIES,
           f"unknown matching strategy '{strategy}' (known: {', '.join(MATCHING_STRATEGIES)})")

    _check(raw["drivers"]["offline_after_minutes"] > 0, "drivers.offline_after_minutes must be > 0")
    _check(cancellation["grace_period_minutes"] >= 0, "cancellation.grace_period_minutes must be >= 0")
    _check(cancellation["fee"] >= 0, "cancellation.fee must be >= 0")

    _check(isinstance(surge["enabled"], bool), "surge.enabled must be true or false")
    _check(surge["area_radius_km"] > 0, "surge.area_radius_km must be > 0")
    _check(surge["window_minutes"] > 0, "surge.window_minutes must be > 0")
    _check(surge["cap"] >= 1, "surge.cap must be >= 1")

    _check(db["pool_size"] >= 1, "database.pool_size must be >= 1")

    return AppConfig(
        fare_strategies=fares,
        upgrade_path=upgrades,
        default_radius_km=float(booking["default_radius_km"]),
        default_matching_strategy=strategy,
        driver_timeout=timedelta(minutes=raw["drivers"]["offline_after_minutes"]),
        cancellation_grace=timedelta(minutes=cancellation["grace_period_minutes"]),
        cancellation_fee=float(cancellation["fee"]),
        surge=SurgeConfig(surge["enabled"], float(surge["area_radius_km"]),
                          timedelta(minutes=surge["window_minutes"]), float(surge["cap"])),
        database_url=os.environ.get("DATABASE_URL") or db["url"],
        db_pool_size=int(db["pool_size"]),
    )
