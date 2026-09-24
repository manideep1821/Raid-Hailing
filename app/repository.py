from typing import Dict, Generic, List, Optional, TypeVar

from app.exceptions import NotFoundError
from app.models import CarType, Coupon, Driver, DriverStatus, Location, Ride, RideStatus, User

T = TypeVar("T")


class InMemoryRepository(Generic[T]):
    entity_name = "entity"

    def __init__(self):
        self._items: Dict[str, T] = {}

    def save(self, key: str, item: T) -> T:
        self._items[key] = item
        return item

    def find(self, key: str) -> Optional[T]:
        return self._items.get(key)

    def get(self, key: str) -> T:
        item = self._items.get(key)
        if item is None:
            raise NotFoundError(f"{self.entity_name} '{key}' not found")
        return item

    def delete(self, key: str) -> None:
        self.get(key)
        del self._items[key]

    def all(self) -> List[T]:
        return list(self._items.values())


class UserRepository(InMemoryRepository[User]):
    entity_name = "user"


class CouponRepository(InMemoryRepository[Coupon]):
    entity_name = "coupon"


class DriverRepository(InMemoryRepository[Driver]):
    entity_name = "driver"

    def find_available(self, pickup: Location, radius_km: float, car_type: CarType) -> List[Driver]:
        return [
            d for d in self._items.values()
            if d.status == DriverStatus.AVAILABLE
            and d.car_type == car_type
            and d.location.distance_km(pickup) <= radius_km
        ]


class RideRepository(InMemoryRepository[Ride]):
    entity_name = "ride"

    def for_user(self, user_id: str) -> List[Ride]:
        return [r for r in self._items.values() if r.user_id == user_id]

    def for_driver(self, driver_id: str) -> List[Ride]:
        return [r for r in self._items.values() if r.driver_id == driver_id]

    def ongoing_for_driver(self, driver_id: str) -> Optional[Ride]:
        return next((r for r in self.for_driver(driver_id) if r.status == RideStatus.ONGOING), None)
