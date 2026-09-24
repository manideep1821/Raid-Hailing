from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from app.exceptions import ValidationError
from app.models import CarType, Coupon, FareBreakdown, Ride


class FareStrategy(ABC):
    @abstractmethod
    def base_fare(self, distance_km: float) -> float: ...


class TieredFareStrategy(FareStrategy):
    """Slab pricing: each km is charged at the rate of the slab it falls in.

    tiers are (cumulative_upper_bound_km, rate_per_km); the last bound is None (open-ended).
    [(2, 10), (5, 8), (None, 5)] => km 0-2 @10, km 2-5 @8, km 5+ @5.
    """

    def __init__(self, min_fare: float, tiers: List[Tuple[Optional[float], float]]):
        if not tiers or tiers[-1][0] is not None:
            raise ValidationError("last tier must be open-ended (upper bound None)")
        bounds = [b for b, _ in tiers[:-1]]
        if bounds != sorted(bounds) or len(set(bounds)) != len(bounds):
            raise ValidationError("tier bounds must be strictly increasing")
        self.min_fare = min_fare
        self.tiers = tiers

    def base_fare(self, distance_km: float) -> float:
        cost, lower = 0.0, 0.0
        for upper, rate in self.tiers:
            if distance_km <= lower:
                break
            span = distance_km - lower if upper is None else min(distance_km, upper) - lower
            cost += span * rate
            lower = upper if upper is not None else lower
        return max(cost, self.min_fare)


class PricingEngine:
    """Fare = car-type tiered fare (minimum applied) x surge, minus the coupon discount."""

    def __init__(self, fare_strategies: Dict[CarType, FareStrategy]):
        self.fare_strategies = fare_strategies

    def calculate(self, car_type: CarType, distance_km: float, surge_multiplier: float = 1.0,
                  coupon: Optional[Coupon] = None) -> FareBreakdown:
        base = round(self.fare_strategies[car_type].base_fare(distance_km), 2)
        surged = round(base * surge_multiplier, 2)
        discount = round(min(coupon.discount.amount(surged), surged), 2) if coupon else 0.0
        return FareBreakdown(base, surge_multiplier, surged, discount, round(surged - discount, 2))

    def price_ride(self, ride: Ride) -> FareBreakdown:
        """Bills the *requested* car type, so an upgrade is free for the rider."""
        return self.calculate(ride.requested_car_type, ride.distance_km, ride.surge_multiplier, ride.coupon)
