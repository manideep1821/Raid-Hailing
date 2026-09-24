from app.domain.discounts import Discount
from app.domain.exceptions import InvalidCouponError
from app.domain.models import Coupon
from app.services.common import require
from app.storage.base import CouponRepository


class CouponService:
    def __init__(self, coupons: CouponRepository):
        self.coupons = coupons

    def add(self, code: str, discount: Discount) -> Coupon:
        code = code.strip().upper()
        require(bool(code), "coupon code is required")
        return self.coupons.add(Coupon(code, discount))

    def delete(self, code: str) -> None:
        self.coupons.delete(code.strip().upper())

    def validate(self, code: str) -> Coupon:
        coupon = self.coupons.find(code.strip().upper())
        if coupon is None:
            raise InvalidCouponError(f"coupon '{code}' is invalid or expired")
        return coupon
