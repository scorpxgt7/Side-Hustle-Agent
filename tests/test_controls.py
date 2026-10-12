from dataclasses import replace
from decimal import Decimal as D
import sqlite3
import pytest
from test_phase1 import opp, limits
from src.core.economics import EconomicEvaluator
from src.core.engine import Phase1Engine
from src.core.models import CapitalLimits, PortfolioState, Decision
from src.core.risk import RiskGovernor
from src.persistence.sqlite import Repository
from src.adapters.paper import PaperLedger


def test_outcome_ev_includes_sunk_cost_and_failure_loss():
    o = opp(expected_revenue=D("20"), expected_cost=D("12"),
            estimated_probability=D("0.5"), failure_cost=D("3"))
    assert o.expected_value == D("-3.5")
    assert RiskGovernor().evaluate(o, limits()).decision == Decision.REJECT
    assert replace(o, estimated_probability=D("0")).expected_value == D("-15")
    assert replace(o, estimated_probability=D("1")).expected_value == D("8")
    assert replace(o, failure_revenue=D("4")).expected_value == D("-1.5")


@pytest.mark.parametrize("value", [D("NaN"), D("Infinity"), D("-Infinity"), 1.5])
@pytest.mark.parametrize("field", ["required_capital", "expected_cost", "estimated_probability", "confidence", "failure_cost", "leverage"])
def test_invalid_opportunity_numbers(field, value):
    with pytest.raises(ValueError):
        opp(**{field: value})


def test_invalid_configuration_and_state():
    for field, value in [("reserve_ratio", D("1.1")), ("max_daily_loss", D("-1")), ("total_capital", D("NaN"))]:
        with pytest.raises(ValueError):
            replace(limits(), **{field: value})
    with pytest.raises(ValueError):
        PortfolioState(allocated_today=D("-1"))
    with pytest.raises(ValueError):
        PortfolioState(current_equity=D("1"))


def test_risk_boundaries_and_forged_evaluation():
    risk = RiskGovernor()
    for state in [PortfolioState(realized_daily_pnl=D("-5")),
                  PortfolioState(peak_equity=D("100"), current_equity=D("90")),
                  PortfolioState(allocated_today=D("41"))]:
        assert risk.evaluate(opp(), limits(), state).decision == Decision.REJECT
    assert risk.evaluate(opp(leverage=D("2")), limits()).decision == Decision.REJECT
    assert risk.evaluate(opp(confidence=D("0")), limits()).decision == Decision.REJECT
    forged = replace(EconomicEvaluator().evaluate(opp()), expected_value=D("999"))
    assert risk.evaluate(opp(), limits(), evaluation=forged).decision == Decision.REJECT
    assert risk.evaluate(opp(), limits(), PortfolioState(allocated_today=D("40"))).decision == Decision.PAPER_TEST


def test_atomic_decision_rollback_and_immutable_id(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "test.db")
    engine = Phase1Engine(repo)
    original = repo.audit
    def fail(*args):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(repo, "audit", fail)
    with pytest.raises(RuntimeError):
        engine.process(opp(), limits())
    for table in ["opportunities", "opportunity_outcomes", "decisions", "audit_events"]:
        assert repo.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    monkeypatch.setattr(repo, "audit", original)
    engine.process(opp(failure_cost=D("1")), limits())
    with pytest.raises(sqlite3.IntegrityError):
        engine.process(opp(expected_revenue=D("999")), limits())
    assert repo.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert repo.conn.execute("SELECT failure_cost FROM opportunity_outcomes").fetchone()[0] == "1"
    repo.close()


def test_ledger_duplicate_and_invalid_amount(tmp_path):
    repo = Repository(tmp_path / "test.db")
    with repo.conn:
        repo.ledger("one", "credit", D("1.01"))
    with pytest.raises(sqlite3.IntegrityError), repo.conn:
        repo.ledger("one", "credit", D("1.01"))
    with pytest.raises(ValueError):
        repo.ledger("two", "credit", D("NaN"))
    paper = PaperLedger(D("1.01"))
    with pytest.raises(ValueError):
        paper.debit(D("1.02"))
    with pytest.raises(ValueError):
        paper.credit(D("NaN"))
    paper.debit(D("0.01"))
    assert paper.balance == D("1.00")
    repo.close()


def test_live_configuration_disabled():
    from pathlib import Path
    config = Path("config/default.yaml").read_text()
    for flag in ["live_enabled", "withdrawals_enabled", "card_access_enabled"]:
        assert f"{flag}: false" in config
    assert "mode: PAPER" in config
