from app.domain.models import CarType
from app.services.rides import upgrade_chain


def test_no_upgrade_path_means_only_the_requested_type():
    assert upgrade_chain(CarType.SEDAN, {}) == [CarType.SEDAN]


def test_follows_the_configured_path():
    assert upgrade_chain(CarType.HATCHBACK, {CarType.HATCHBACK: CarType.SEDAN}) == [CarType.HATCHBACK, CarType.SEDAN]


def test_chains_and_stops_on_a_cycle():
    # Plain strings stand in for car types: the chain logic doesn't depend on the enum.
    assert upgrade_chain("hatchback", {"hatchback": "sedan", "sedan": "suv"}) == ["hatchback", "sedan", "suv"]
    assert upgrade_chain("suv", {"hatchback": "sedan", "sedan": "suv"}) == ["suv"]
    assert upgrade_chain("a", {"a": "b", "b": "a"}) == ["a", "b"]
