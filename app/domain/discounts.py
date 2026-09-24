"""Coupon discount types. Adding one is a dataclass here plus an entry in DISCOUNT_TYPES:
storage keeps (kind, params) as JSON, so no schema change is needed."""
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, fields
from typing import Any, ClassVar, Dict, Optional, Type

from app.domain.exceptions import ValidationError


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


class Discount(ABC):
    kind: ClassVar[str]

    @abstractmethod
    def amount(self, fare: float) -> float:
        """Amount taken off `fare`. The pricing engine never lets the total go below zero."""

    def params(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FlatDiscount(Discount):
    kind: ClassVar[str] = "flat"
    value: float

    def __post_init__(self):
        _require(self.value > 0, "discount value must be positive")

    def amount(self, fare: float) -> float:
        return self.value


@dataclass(frozen=True)
class PercentageDiscount(Discount):
    kind: ClassVar[str] = "percentage"
    value: float
    max_discount: Optional[float] = None

    def __post_init__(self):
        _require(0 < self.value <= 100, "percentage must be between 0 and 100")
        _require(self.max_discount is None or self.max_discount > 0, "max_discount must be positive")

    def amount(self, fare: float) -> float:
        discount = fare * self.value / 100
        return discount if self.max_discount is None else min(discount, self.max_discount)


DISCOUNT_TYPES: Dict[str, Type[Discount]] = {cls.kind: cls for cls in (FlatDiscount, PercentageDiscount)}


def build_discount(kind: str, params: Dict[str, Any]) -> Discount:
    cls = DISCOUNT_TYPES.get(kind)
    _require(cls is not None, f"unknown discount type '{kind}' (known: {', '.join(DISCOUNT_TYPES)})")
    unknown = set(params) - {f.name for f in fields(cls)}
    _require(not unknown, f"{kind} discount does not take: {', '.join(sorted(unknown))}")
    try:
        return cls(**params)
    except TypeError as e:
        raise ValidationError(f"invalid {kind} discount parameters: {e}")
