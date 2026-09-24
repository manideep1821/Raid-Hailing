from app.domain.models import CarType, Driver
from app.strategies.matching import MATCHING_STRATEGIES, HighestRatedDriverStrategy, NearestDriverStrategy
from tests.support import PICKUP, north_of


def driver(driver_id: str, km: float, rating: float) -> Driver:
    return Driver(driver_id, driver_id, "900", CarType.SEDAN, north_of(km), rating)


CANDIDATES = [driver("far-best", 4, 4.9), driver("near-poor", 0.5, 3.9), driver("mid-best", 2, 4.9)]


def ids(drivers):
    return [d.id for d in drivers]


def test_nearest_orders_by_distance_to_pickup():
    assert ids(NearestDriverStrategy().rank(CANDIDATES, PICKUP)) == ["near-poor", "mid-best", "far-best"]


def test_highest_rated_orders_by_rating_then_distance():
    assert ids(HighestRatedDriverStrategy().rank(CANDIDATES, PICKUP)) == ["mid-best", "far-best", "near-poor"]


def test_ranking_does_not_modify_the_input():
    before = ids(CANDIDATES)
    NearestDriverStrategy().rank(CANDIDATES, PICKUP)
    assert ids(CANDIDATES) == before


def test_registry_names_match_the_config_values():
    assert set(MATCHING_STRATEGIES) == {"nearest", "highest_rated"}
