import pytest

from app.domain.discounts import FlatDiscount, PercentageDiscount
from app.domain.exceptions import ValidationError
from app.domain.models import CarType, Coupon, FareBreakdown, Ride
from app.strategies.pricing import PricingEngine, TieredFareStrategy
from tests.support import PICKUP, TEST_CONFIG

FARES = TEST_CONFIG.fare_strategies

# Spec example: min 50, first 2 km @10, 3-5 km @8, 6+ km @5
spec_tiers = TieredFareStrategy(min_fare=50, tiers=[(2, 10), (5, 8), (None, 5)])
no_min_tiers = TieredFareStrategy(min_fare=0, tiers=[(2, 10), (5, 8), (None, 5)])


@pytest.mark.parametrize("distance, expected", [
    (0, 0),
    (1, 10),            # within tier 1
    (2, 20),            # tier 1 boundary
    (2.1, 20.8),        # just past it: the 0.1 km is at tier 2's rate
    (3, 28),            # 2*10 + 1*8
    (5, 44),            # tier 2 boundary: 20 + 3*8
    (5.1, 44.5),        # just past it: the 0.1 km is at tier 3's rate
    (6, 49),            # 44 + 1*5
    (10, 69),           # 44 + 5*5
    (2.5, 24),          # fractional km
])
def test_tier_slabs(distance, expected):
    assert no_min_tiers.base_fare(distance) == pytest.approx(expected)


@pytest.mark.parametrize("distance, expected", [
    (0, 50), (1, 50), (5, 50),   # below minimum -> minimum fare
    (6, 50),                      # 49 still below min
    (6.2, 50),                    # 44 + 1.2*5 = 50: where the tiers reach the minimum
    (6.3, 50.5),                  # first distance that costs more than the minimum
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


def test_percentage_coupon_on_a_minimum_fare_ride():
    # The percentage applies to the minimum fare, not to the smaller distance fare (10 for 1 km).
    fare = PricingEngine(FARES).calculate(CarType.HATCHBACK, 1, coupon=Coupon("P20", PercentageDiscount(20)))
    assert (fare.base_fare, fare.discount, fare.total) == (50, 10, 40)


def test_upgrade_with_surge_and_coupon_is_billed_at_the_requested_type():
    fare = PricingEngine(FARES).price_ride(
        ride(CarType.HATCHBACK, CarType.SEDAN, 10, surge=1.5, coupon=Coupon("FLAT5", FlatDiscount(5))))
    # Hatchback 69 (the sedan would be 89), then x1.5 surge, then -5.
    assert fare == FareBreakdown(base_fare=69, surge_multiplier=1.5, surged_fare=103.5, discount=5, total=98.5)
