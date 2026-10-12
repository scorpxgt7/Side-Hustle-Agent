"""Phase 1 orchestration: discover -> evaluate -> risk-gate -> persist."""
from uuid import uuid4
from dataclasses import asdict
from .arithmetic import ARITHMETIC_POLICY_VERSION
from .economics import EconomicEvaluator
from .models import CapitalLimits, PortfolioState
from .risk import RISK_RULE_VERSION, RiskGovernor


class Phase1Engine:
    def __init__(self, repository):
        self.repository = repository
        self.evaluator = EconomicEvaluator()
        self.risk = RiskGovernor()

    def process(self, opportunity, limits: CapitalLimits, state: PortfolioState | None = None):
        state = state if state is not None else PortfolioState()
        evaluation = self.evaluator.evaluate(opportunity)
        decision = self.risk.evaluate(opportunity, limits, state, evaluation)
        with self.repository.conn:
            self.repository.save_opportunity(opportunity)
            decision_id = self.repository.save_decision(opportunity.id, decision)
            self.repository.audit(str(uuid4()), "opportunity_decision", opportunity.id, {
                "schema_version": 1,
                "rule_version": RISK_RULE_VERSION,
                "arithmetic_policy": ARITHMETIC_POLICY_VERSION,
                "decision_id": decision_id,
                "decision": decision.decision.value,
                "reason": decision.reason,
                "checks": decision.checks,
                "opportunity": asdict(opportunity),
                "limits": asdict(limits),
                "portfolio_state": asdict(state),
                "evaluation": asdict(evaluation),
                "expected_value": str(evaluation.expected_value),
                "risk_adjusted_score": str(evaluation.risk_adjusted_score),
            })
        return evaluation, decision
