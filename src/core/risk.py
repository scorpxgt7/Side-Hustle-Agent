"""Deterministic risk governor. LLM output cannot override these checks."""
from decimal import Decimal
from .economics import EconomicEvaluation, EconomicEvaluator
from .models import CapitalLimits, Decision, Opportunity, PortfolioState, RiskDecision


class RiskGovernor:
    def evaluate(
        self,
        opportunity: Opportunity,
        limits: CapitalLimits,
        state: PortfolioState | None = None,
        evaluation: EconomicEvaluation | None = None,
    ) -> RiskDecision:
        state = state or PortfolioState()
        canonical = EconomicEvaluator().evaluate(opportunity)
        checks = {
            "evaluation_matches": evaluation is None or evaluation == canonical,
            "capital_positive": opportunity.required_capital > 0,
            "within_single_allocation": opportunity.required_capital <= limits.max_single_allocation,
            "within_deployable_capital": state.allocated_today + opportunity.required_capital <= limits.deployable_capital,
            "probability_valid": Decimal("0") <= opportunity.estimated_probability <= Decimal("1"),
            "confidence_valid": Decimal("0") <= opportunity.confidence <= Decimal("1"),
            "liquidity_valid": Decimal("0") <= opportunity.liquidity_score <= Decimal("1"),
            "daily_loss_limit": state.realized_daily_pnl > -limits.max_daily_loss,
            "drawdown_limit": state.drawdown < limits.max_drawdown,
            "leverage_limit": opportunity.leverage <= limits.max_leverage,
            "positive_expected_value": opportunity.expected_value > 0,
        }
        checks["positive_risk_adjusted_score"] = canonical.risk_adjusted_score > 0

        if not all(checks.values()):
            failed = [name for name, ok in checks.items() if not ok]
            return RiskDecision(Decision.REJECT, f"Failed hard checks: {', '.join(failed)}", opportunity.id, checks)

        return RiskDecision(
            Decision.PAPER_TEST,
            "Passed deterministic checks; live execution remains disabled.",
            opportunity.id,
            checks,
        )
