from decimal import Decimal
from .base import OpportunityWorker
from src.core.models import Opportunity, OpportunityType


class ExamplePaperWorker(OpportunityWorker):
    name = "example-paper-worker"

    def discover(self):
        yield Opportunity(
            id="example-001",
            type=OpportunityType.OTHER,
            title="Example opportunity — replace with real discovery adapter",
            required_capital=Decimal("5"),
            expected_revenue=Decimal("8"),
            expected_cost=Decimal("1"),
            estimated_probability=Decimal("0.70"),
            confidence=Decimal("0.20"),
        )
