import pytest

from app.domain.discounts import FlatDiscount, PercentageDiscount, build_discount
from app.domain.exceptions import ValidationError


def test_flat_discount_is_its_value():
    assert FlatDiscount(20).amount(100) == 20


def test_percentage_discount_with_and_without_cap():
    assert PercentageDiscount(10).amount(200) == 20
    assert PercentageDiscount(50, max_discount=100).amount(1000) == 100
    assert PercentageDiscount(50, max_discount=100).amount(100) == 50      # under the cap


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
