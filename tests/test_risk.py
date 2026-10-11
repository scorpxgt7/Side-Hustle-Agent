from decimal import Decimal
from src.core.models import CapitalLimits, Opportunity, OpportunityType, Decision
from src.core.risk import RiskGovernor


def limits():
    return CapitalLimits(
        total_capital=Decimal("100"),
        reserve_ratio=Decimal("0.50"),
        max_single_allocation=Decimal("10"),
        max_daily_loss=Decimal("5"),
        max_drawdown=Decimal("10"),
    )


def test_rejects_over_allocation():
    opp = Opportunity("1", OpportunityType.OTHER, "x", Decimal("11"), Decimal("20"), Decimal("1"), Decimal("0.9"), confidence=Decimal("1"))
    assert RiskGovernor().evaluate(opp, limits()).decision == Decision.REJECT


def test_paper_tests_positive_expected_value():
    opp = Opportunity("2", OpportunityType.OTHER, "x", Decimal("5"), Decimal("10"), Decimal("1"), Decimal("0.9"), confidence=Decimal("1"))
    assert RiskGovernor().evaluate(opp, limits()).decision == Decision.PAPER_TEST


def test_rejects_negative_expected_value():
    opp = Opportunity("3", OpportunityType.OTHER, "x", Decimal("5"), Decimal("2"), Decimal("5"), Decimal("0.9"), confidence=Decimal("1"))
    assert RiskGovernor().evaluate(opp, limits()).decision == Decision.REJECT
