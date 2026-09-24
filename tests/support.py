"""Helpers shared by the unit and integration tests."""
import itertools
from datetime import datetime
from pathlib import Path

from app.config import load_config
from app.domain.models import Location

TEST_CONFIG_PATH = Path(__file__).with_name("config.test.toml")
TEST_CONFIG = load_config(TEST_CONFIG_PATH)  # pinned values the assertions rely on

PICKUP = Location(12.9716, 77.5946)
KM_PER_DEG_LAT = 111.19492664455873           # haversine km per degree of latitude (Earth radius 6371 km)

_phones = itertools.count(7000000000)  # clear of the fixed 9xxxxxxxxx numbers some tests use


def north_of(km: float, origin: Location = PICKUP) -> Location:
    """A point exactly `km` due north of `origin`, so expected distances are exact."""
    return Location(origin.lat + km / KM_PER_DEG_LAT, origin.lng)


def unique_phone() -> str:
    """Phone numbers must be unique per user and per driver."""
    return str(next(_phones))


class FakeClock:
    """Injected in place of `datetime.now`; tests move time by assigning `now`."""

    def __init__(self):
        self.now = datetime(2026, 1, 1, 10, 0)

    def __call__(self) -> datetime:
        return self.now
