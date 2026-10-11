from decimal import Decimal
from src.core.economics import EconomicEvaluator
from src.core.engine import Phase1Engine
from src.core.models import CapitalLimits, Opportunity, OpportunityType, PortfolioState, Decision
from src.core.risk import RiskGovernor
from src.persistence.sqlite import Repository


def opp(**kwargs):
    base = dict(id="x", type=OpportunityType.OTHER, title="test", required_capital=Decimal("10"),
                expected_revenue=Decimal("20"), expected_cost=Decimal("2"),
                estimated_probability=Decimal("0.8"), confidence=Decimal("0.8"), liquidity_score=Decimal("1"))
    base.update(kwargs)
    return Opportunity(**base)


def limits():
    return CapitalLimits(Decimal("100"), Decimal("0.5"), Decimal("20"), Decimal("5"), Decimal("10"))


def test_economic_evaluator_uses_decimal_and_costs():
    result = EconomicEvaluator().evaluate(opp())
    assert result.expected_net == Decimal("18")
    assert result.expected_value == Decimal("14.0")
    assert result.viable


def test_risk_rejects_daily_loss_breach():
    state = PortfolioState(realized_daily_pnl=Decimal("-5.01"))
    result = RiskGovernor().evaluate(opp(), limits(), state, EconomicEvaluator().evaluate(opp()))
    assert result.decision == Decision.REJECT
    assert not result.checks["daily_loss_limit"]


def test_risk_rejects_drawdown_breach():
    state = PortfolioState(peak_equity=Decimal("100"), current_equity=Decimal("89"))
    result = RiskGovernor().evaluate(opp(), limits(), state, EconomicEvaluator().evaluate(opp()))
    assert result.decision == Decision.REJECT
    assert not result.checks["drawdown_limit"]


def test_engine_persists_decision_and_audit(tmp_path):
    repo = Repository(tmp_path / "agent.db")
    evaluation, decision = Phase1Engine(repo).process(opp(), limits())
    assert evaluation.viable
    assert decision.decision == Decision.PAPER_TEST
    assert repo.conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1
    assert repo.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert repo.conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 1
    repo.close()
