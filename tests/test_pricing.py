import pytest

from app.exceptions import ValidationError
from app.models import CarType, Coupon, DiscountType, Location
from app.pricing import (DEFAULT_FARE_STRATEGIES, GridDemandSurge, PricingEngine, TieredFareStrategy,
                         apply_coupon)

PICKUP = Location(12.97, 77.59)

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
    engine = PricingEngine(DEFAULT_FARE_STRATEGIES)
    hatch = engine.calculate(CarType.HATCHBACK, 10, PICKUP)
    sedan = engine.calculate(CarType.SEDAN, 10, PICKUP)
    assert hatch == 69           # 2*10 + 3*8 + 5*5
    assert sedan == 89           # 2*12 + 3*10 + 5*7
    assert engine.calculate(CarType.HATCHBACK, 1, PICKUP) == 50
    assert engine.calculate(CarType.SEDAN, 1, PICKUP) == 60


def test_flat_coupon():
    coupon = Coupon("FLAT20", DiscountType.FLAT, 20)
    assert apply_coupon(100, coupon) == 80


def test_percentage_coupon_with_cap():
    assert apply_coupon(200, Coupon("P10", DiscountType.PERCENTAGE, 10)) == 180
    assert apply_coupon(1000, Coupon("P50", DiscountType.PERCENTAGE, 50, max_discount=100)) == 900


def test_coupon_never_makes_fare_negative():
    assert apply_coupon(30, Coupon("BIG", DiscountType.FLAT, 100)) == 0


def test_coupon_applies_after_minimum_fare():
    engine = PricingEngine(DEFAULT_FARE_STRATEGIES)
    coupon = Coupon("FLAT10", DiscountType.FLAT, 10)
    assert engine.calculate(CarType.HATCHBACK, 1, PICKUP, coupon) == 40


def test_surge_multiplies_before_coupon():
    surge = GridDemandSurge(cap=3.0)
    surge.record_demand_supply(PICKUP, demand=15, supply=10)       # 1.5x
    engine = PricingEngine(DEFAULT_FARE_STRATEGIES, surge)
    coupon = Coupon("FLAT5", DiscountType.FLAT, 5)
    assert engine.calculate(CarType.HATCHBACK, 10, PICKUP) == pytest.approx(103.5)
    assert engine.calculate(CarType.HATCHBACK, 10, PICKUP, coupon) == pytest.approx(98.5)
    # different area: no surge
    assert engine.calculate(CarType.HATCHBACK, 10, Location(28.6, 77.2)) == 69


def test_surge_is_capped_and_never_below_one():
    surge = GridDemandSurge(cap=2.0)
    surge.record_demand_supply(PICKUP, demand=100, supply=1)
    assert surge.multiplier(PICKUP) == 2.0
    surge.record_demand_supply(PICKUP, demand=1, supply=100)
    assert surge.multiplier(PICKUP) == 1.0
    surge.record_demand_supply(PICKUP, demand=5, supply=0)
    assert surge.multiplier(PICKUP) == 2.0
