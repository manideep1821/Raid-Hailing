from pathlib import Path

import pytest

from app.config import load_config
from app.discounts import FlatDiscount, PercentageDiscount, build_discount
from app.exceptions import ValidationError
from app.models import CarType, Coupon, FareBreakdown, Location, Ride
from app.pricing import PricingEngine, TieredFareStrategy

PICKUP = Location(12.97, 77.59)
FARES = load_config(Path(__file__).with_name("config.test.toml")).fare_strategies

# Spec example: min 50, first 2 km @10, 3-5 km @8, 6+ km @5
spec_tiers = TieredFareStrategy(min_fare=50, tiers=[(2, 10), (5, 8), (None, 5)])
no_min_tiers = TieredFareStrategy(min_fare=0, tiers=[(2, 10), (5, 8), (None, 5)])


@pytest.mark.parametrize("distance, expected", [
    (0, 0),
    (1, 10),            # within tier 1
    (2, 20),            # tier 1 boundary
    (3, 28),            # 2*10 + 1*8
    (5, 44),            # tier 2 boundary: 20 + 3*8
    (6, 49),            # 44 + 1*5
    (10, 69),           # 44 + 5*5
    (2.5, 24),          # fractional km
])
def test_tier_slabs(distance, expected):
    assert no_min_tiers.base_fare(distance) == pytest.approx(expected)


@pytest.mark.parametrize("distance, expected", [
    (0, 50), (1, 50), (5, 50),   # below minimum -> minimum fare
    (6, 50),                      # 49 still below min
    (7, 54),                      # 54 exceeds min
])
def test_minimum_fare(distance, expected):
    assert spec_tiers.base_fare(distance) == pytest.approx(expected)


@pytest.mark.parametrize("tiers", [
    [(2, 10), (5, 8)],                 # not open-ended
    [(5, 10), (2, 8), (None, 5)],      # decreasing bounds
    [],
])
def test_invalid_tier_config_rejected(tiers):
    with pytest.raises(ValidationError):
        TieredFareStrategy(min_fare=50, tiers=tiers)


def test_car_types_have_different_rates():
    engine = PricingEngine(FARES)
    assert engine.calculate(CarType.HATCHBACK, 10).total == 69     # 2*10 + 3*8 + 5*5
    assert engine.calculate(CarType.SEDAN, 10).total == 89         # 2*12 + 3*10 + 5*7
    assert engine.calculate(CarType.HATCHBACK, 1).total == 50
    assert engine.calculate(CarType.SEDAN, 1).total == 60


def ride(requested: CarType, assigned: CarType, km: float, surge: float = 1.0, coupon=None) -> Ride:
    return Ride("R-1", "U-1", "D-1", requested, assigned, PICKUP, PICKUP, coupon=coupon,
                surge_multiplier=surge, distance_km=km)


def test_free_upgrade_is_billed_at_the_requested_type():
    engine = PricingEngine(FARES)
    upgraded = engine.price_ride(ride(CarType.HATCHBACK, CarType.SEDAN, 10))
    assert upgraded.total == 69                                     # hatchback rate, not sedan's 89
    assert engine.price_ride(ride(CarType.SEDAN, CarType.SEDAN, 10)).total == 89
    assert engine.price_ride(ride(CarType.HATCHBACK, CarType.SEDAN, 1)).total == 50   # hatchback minimum


def test_flat_coupon():
    engine = PricingEngine(FARES)
    fare = engine.calculate(CarType.HATCHBACK, 10, coupon=Coupon("FLAT20", FlatDiscount(20)))
    assert fare == FareBreakdown(base_fare=69, surge_multiplier=1.0, surged_fare=69, discount=20, total=49)


def test_percentage_coupon_with_cap():
    assert PercentageDiscount(10).amount(200) == 20
    assert PercentageDiscount(50, max_discount=100).amount(1000) == 100
    engine = PricingEngine(FARES)
    assert engine.calculate(CarType.SEDAN, 10, coupon=Coupon("P50", PercentageDiscount(50, 30))).total == 59


def test_coupon_never_makes_fare_negative():
    fare = PricingEngine(FARES).calculate(CarType.HATCHBACK, 1, coupon=Coupon("BIG", FlatDiscount(100)))
    assert fare.discount == 50 and fare.total == 0


def test_coupon_applies_after_minimum_fare():
    engine = PricingEngine(FARES)
    assert engine.calculate(CarType.HATCHBACK, 1, coupon=Coupon("FLAT10", FlatDiscount(10))).total == 40


def test_surge_multiplies_after_minimum_and_before_coupon():
    engine = PricingEngine(FARES)
    fare = engine.calculate(CarType.HATCHBACK, 10, surge_multiplier=1.5, coupon=Coupon("FLAT5", FlatDiscount(5)))
    assert fare == FareBreakdown(base_fare=69, surge_multiplier=1.5, surged_fare=103.5, discount=5, total=98.5)
    assert engine.calculate(CarType.HATCHBACK, 1, surge_multiplier=2).total == 100    # 2 x minimum fare


def test_ride_is_priced_with_its_locked_surge_and_coupon():
    fare = PricingEngine(FARES).price_ride(
        ride(CarType.SEDAN, CarType.SEDAN, 10, surge=2, coupon=Coupon("P10", PercentageDiscount(10))))
    assert (fare.surged_fare, fare.discount, fare.total) == (178, 17.8, 160.2)


@pytest.mark.parametrize("kind, params", [
    ("flat", {"value": 0}),
    ("flat", {"value": 10, "max_discount": 5}),     # flat takes no cap
    ("percentage", {"value": 150}),
    ("percentage", {"value": 10, "max_discount": -1}),
    ("percentage", {"value": "ten"}),
    ("bogo", {"value": 1}),
])
def test_invalid_discounts_rejected(kind, params):
    with pytest.raises(ValidationError):
        build_discount(kind, params)


def test_discount_round_trips_through_its_params():
    for discount in (FlatDiscount(25), PercentageDiscount(20, max_discount=30), PercentageDiscount(5)):
        assert build_discount(discount.kind, discount.params()) == discount
