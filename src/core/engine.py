"""Phase 1 orchestration: discover -> evaluate -> risk-gate -> persist."""
from uuid import uuid4
from .economics import EconomicEvaluator
from .models import CapitalLimits, Decision, PortfolioState
from .risk import RiskGovernor


class Phase1Engine:
    def __init__(self, repository):
        self.repository = repository
        self.evaluator = EconomicEvaluator()
        self.risk = RiskGovernor()

    def process(self, opportunity, limits: CapitalLimits, state: PortfolioState | None = None):
        evaluation = self.evaluator.evaluate(opportunity)
        decision = self.risk.evaluate(opportunity, limits, state, evaluation)
        with self.repository.conn:
            self.repository.save_opportunity(opportunity)
            self.repository.save_decision(opportunity.id, decision)
            self.repository.audit(str(uuid4()), "opportunity_decision", opportunity.id, {
                "decision": decision.decision.value,
                "reason": decision.reason,
                "expected_value": str(evaluation.expected_value),
                "risk_adjusted_score": str(evaluation.risk_adjusted_score),
            })
        return evaluation, decision
