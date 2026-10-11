"""Deterministic paper-only risk governor."""
from decimal import Decimal
from .economics import EconomicEvaluation
from .models import CapitalLimits, Decision, Opportunity, PortfolioState, RiskDecision

class RiskGovernor:
    def evaluate(self, o: Opportunity, limits: CapitalLimits,
                 state: PortfolioState, economic: EconomicEvaluation | None = None) -> RiskDecision:
        metrics = (limits.total_capital, limits.reserve_ratio, limits.max_single_allocation,
                   limits.max_daily_loss, limits.max_drawdown, limits.max_leverage,
                   state.allocated_today, state.realized_daily_pnl, state.peak_equity,
                   state.current_equity)
        valid = all(isinstance(x, Decimal) and x.is_finite() for x in metrics)
        checks = {
            "valid_numeric_state": valid,
            "valid_limits": valid and limits.total_capital > 0 and
                Decimal("0") <= limits.reserve_ratio < Decimal("1") and
                limits.max_single_allocation > 0 and limits.max_daily_loss >= 0 and
                limits.max_drawdown >= 0 and limits.max_leverage >= 1,
            "valid_portfolio": valid and state.allocated_today >= 0 and
                state.peak_equity >= 0 and state.current_equity >= 0 and
                state.peak_equity >= state.current_equity,
            "within_single_limit": valid and o.required_capital <= limits.max_single_allocation,
            "within_deployable_limit": valid and
                state.allocated_today + o.required_capital <= limits.deployable_capital,
            "within_daily_loss_limit": valid and state.realized_daily_pnl >= -limits.max_daily_loss,
            "within_drawdown_limit": valid and state.drawdown <= limits.max_drawdown,
            "positive_expected_value": economic.viable if economic else o.expected_value > 0,
        }
        failed = [key for key, passed in checks.items() if not passed]
        if failed:
            return RiskDecision(Decision.REJECT, "Risk checks failed: " + ", ".join(failed), o.id, checks)
        return RiskDecision(Decision.PAPER_TEST, "Paper evaluation only; live execution disabled", o.id, checks)
