"""Opportunity evaluation and recording only; no execution mechanism."""
from .economics import EconomicEvaluator
from .models import CapitalLimits, Opportunity, PortfolioState
from .risk import RiskGovernor
from ..persistence.sqlite import Repository

class Phase1Engine:
    def __init__(self, repository: Repository):
        self.repository = repository
        self.economics = EconomicEvaluator()
        self.risk = RiskGovernor()

    def process(self, opportunity: Opportunity, limits: CapitalLimits, state: PortfolioState):
        evaluation = self.economics.evaluate(opportunity)
        decision = self.risk.evaluate(opportunity, limits, state, evaluation)
        self.repository.record(opportunity, evaluation, decision)
        return evaluation, decision
