from dataclasses import dataclass
from decimal import Decimal
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
    def evaluate(self, o: Opportunity) -> EconomicEvaluation:
        values = (o.required_capital, o.expected_revenue, o.expected_cost,
                  o.failure_loss, o.estimated_probability, o.confidence, o.liquidity_score)
        if any(not isinstance(v, Decimal) or not v.is_finite() for v in values):
            raise ValueError("Financial values must be finite Decimal objects")
        if o.required_capital <= 0 or min(o.expected_revenue, o.expected_cost, o.failure_loss) < 0:
            raise ValueError("Capital must be positive; revenues and costs nonnegative")
        if any(not Decimal("0") <= x <= Decimal("1") for x in
               (o.estimated_probability, o.confidence, o.liquidity_score)):
            raise ValueError("Probability, confidence and liquidity must be within [0, 1]")
        if not o.id.strip() or not o.title.strip() or not o.source.strip():
            raise ValueError("ID, title and source are required")
        ev = o.expected_value
        roc = ev / o.required_capital
        score = roc * o.confidence * o.liquidity_score
        return EconomicEvaluation(o.id, o.expected_revenue, o.expected_cost,
                                  o.expected_net, ev, roc, score, ev > 0 and score > 0)
