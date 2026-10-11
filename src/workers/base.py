from abc import ABC, abstractmethod
from typing import Iterable
from src.core.models import Opportunity


class OpportunityWorker(ABC):
    name: str

    @abstractmethod
    def discover(self) -> Iterable[Opportunity]:
        raise NotImplementedError
