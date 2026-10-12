"""Deterministic economic evaluation for candidate opportunities."""
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from .arithmetic import rounded_ratio
from .models import Opportunity


@dataclass(frozen=True)
class EconomicEvaluation:
    opportunity_id: str
    gross_revenue: Decimal
    total_cost: Decimal
    expected_net: Decimal
    expected_value: Decimal
    return_on_capital: Decimal
    risk_adjusted_score: Decimal
    viable: bool


class EconomicEvaluator:
    """Calculate economics without using floating point or model judgment."""

    def evaluate(self, opportunity: Opportunity) -> EconomicEvaluation:
        if not Decimal("0") <= opportunity.estimated_probability <= Decimal("1"):
            raise ValueError("estimated_probability must be between 0 and 1")
        if opportunity.required_capital <= 0:
            raise ValueError("required_capital must be greater than zero")
        if opportunity.expected_revenue < 0 or opportunity.expected_cost < 0:
            raise ValueError("revenue and cost must be non-negative")

        net = opportunity.expected_net
        ev = opportunity.expected_value
        exact_roc = Fraction(ev) / Fraction(opportunity.required_capital)
        roc = rounded_ratio(exact_roc)
        # Confidence and liquidity reduce the score but never create profitability.
        exact_score = exact_roc * Fraction(opportunity.confidence) * Fraction(opportunity.liquidity_score)
        score = rounded_ratio(exact_score)
        viable = ev > 0 and exact_score > 0
        return EconomicEvaluation(
            opportunity.id,
            opportunity.expected_revenue,
            opportunity.expected_cost,
            net,
            ev,
            roc,
            score,
            viable,
        )
