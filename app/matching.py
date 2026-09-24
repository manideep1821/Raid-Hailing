from abc import ABC, abstractmethod
from typing import List

from app.models import Driver, Location


class MatchingStrategy(ABC):
    """Orders eligible drivers by preference; booking assigns the first."""

    @abstractmethod
    def rank(self, candidates: List[Driver], pickup: Location) -> List[Driver]: ...


class NearestDriverStrategy(MatchingStrategy):
    def rank(self, candidates: List[Driver], pickup: Location) -> List[Driver]:
        return sorted(candidates, key=lambda d: d.location.distance_km(pickup))


class HighestRatedDriverStrategy(MatchingStrategy):
    def rank(self, candidates: List[Driver], pickup: Location) -> List[Driver]:
        return sorted(candidates, key=lambda d: (-d.rating, d.location.distance_km(pickup)))


MATCHING_STRATEGIES = {
    "nearest": NearestDriverStrategy(),
    "highest_rated": HighestRatedDriverStrategy(),
}
