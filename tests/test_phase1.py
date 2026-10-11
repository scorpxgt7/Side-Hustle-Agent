from decimal import Decimal as D
import pytest
from src.core.economics import EconomicEvaluator
from src.core.engine import Phase1Engine
from src.core.models import CapitalLimits, Decision, Opportunity, OpportunityType, PortfolioState
from src.core.risk import RiskGovernor
from src.persistence.sqlite import Repository

def opportunity(**changes):
    args = dict(id="o1",type=OpportunityType.OTHER,title="Paper scenario",required_capital=D(100),
                expected_revenue=D(100),expected_cost=D(10),failure_loss=D(60),
                estimated_probability=D("0.4"),liquidity_score=D("0.9"),
                confidence=D("0.8"),source="test fixture")
    args.update(changes)
    return Opportunity(**args)

def limits(**changes):
    args = dict(total_capital=D(1000),reserve_ratio=D("0.2"),max_single_allocation=D(200),
                max_daily_loss=D(100),max_drawdown=D(250),max_leverage=D(1))
    args.update(changes)
    return CapitalLimits(**args)

def state(**changes):
    args = dict(allocated_today=D(0),realized_daily_pnl=D(0),peak_equity=D(1000),current_equity=D(1000))
    args.update(changes)
    return PortfolioState(**args)

def test_probability_weighted_failure_loss():
    result = EconomicEvaluator().evaluate(opportunity())
    assert result.expected_value == D(-6)
    assert not result.viable

def test_positive_paper_only():
    o = opportunity(failure_loss=D(0))
    e = EconomicEvaluator().evaluate(o)
    assert RiskGovernor().evaluate(o, limits(), state(), e).decision is Decision.PAPER_TEST

def test_daily_loss_rejects():
    o = opportunity(failure_loss=D(0))
    assert RiskGovernor().evaluate(o, limits(), state(realized_daily_pnl=D(-101))).decision is Decision.REJECT

def test_drawdown_rejects():
    o = opportunity(failure_loss=D(0))
    assert RiskGovernor().evaluate(o, limits(), state(current_equity=D(700))).decision is Decision.REJECT

def test_combined_allocation_rejects():
    o = opportunity(failure_loss=D(0))
    assert RiskGovernor().evaluate(o, limits(), state(allocated_today=D(750))).decision is Decision.REJECT

def test_nonfinite_is_rejected():
    with pytest.raises(ValueError):
        EconomicEvaluator().evaluate(opportunity(estimated_probability=D("NaN")))

def test_invalid_reserve_rejects():
    o = opportunity(failure_loss=D(0))
    assert RiskGovernor().evaluate(o, limits(reserve_ratio=D("1.1")), state()).decision is Decision.REJECT

def test_transactional_persistence(tmp_path):
    repo = Repository(str(tmp_path / "paper.db"))
    o = opportunity(failure_loss=D(0))
    _, decision = Phase1Engine(repo).process(o, limits(), state())
    assert decision.decision is Decision.PAPER_TEST
    for table in ("opportunities", "decisions", "audit_events"):
        assert repo.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
    repo.close()
