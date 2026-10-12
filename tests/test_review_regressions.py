"""Regression coverage for the five PR #2 review findings."""
from dataclasses import asdict, replace
from decimal import Decimal as D, Inexact, Rounded, ROUND_DOWN, ROUND_UP, localcontext
import json
from types import SimpleNamespace

import pytest

from test_phase1 import limits, opp
from src.adapters.paper import PaperLedger
from src.core.arithmetic import ARITHMETIC_POLICY_VERSION
from src.core.economics import EconomicEvaluator
from src.core.engine import Phase1Engine
from src.core.models import CapitalLimits, Decision, Opportunity, OpportunityType, PortfolioState
from src.core.risk import RISK_RULE_VERSION, RiskGovernor
from src.persistence.sqlite import Repository


@pytest.mark.parametrize("budget,pnl,expected", [
    ("0", "-1", Decision.REJECT),
    ("0", "0", Decision.REJECT),
    ("0", "1", Decision.REJECT),
    ("5", "-5.01", Decision.REJECT),
    ("5", "-5", Decision.REJECT),
    ("5", "-4.99", Decision.PAPER_TEST),
])
def test_daily_loss_budget_and_boundary_persist(tmp_path, budget, pnl, expected):
    repo = Repository(tmp_path / "loss.db")
    _, decision = Phase1Engine(repo).process(
        opp(), replace(limits(), max_daily_loss=D(budget)),
        PortfolioState(realized_daily_pnl=D(pnl)))
    assert decision.decision == expected
    assert decision.checks["daily_loss_limit"] == (expected == Decision.PAPER_TEST)
    row = repo.conn.execute("SELECT decision FROM decisions").fetchone()
    assert row[0] == expected.value
    repo.close()


@pytest.mark.parametrize("precision,rounding", [(2, ROUND_DOWN), (2, ROUND_UP), (7, ROUND_UP)])
def test_caller_context_cannot_change_economics_risk_or_balances(precision, rounding):
    candidate = opp(required_capital=D("3"), liquidity_score=D("0.7"))
    evaluator = EconomicEvaluator()
    expected_evaluation = evaluator.evaluate(candidate)
    capital = replace(limits(), total_capital=D("100"), reserve_ratio=D("0"))
    oversized = opp(required_capital=D("2"))
    state = PortfolioState(allocated_today=D("99"))
    expected_decision = RiskGovernor().evaluate(oversized, capital, state)
    assert expected_decision.decision == Decision.REJECT
    drawdown = PortfolioState(peak_equity=D("100.01"), current_equity=D("100"))
    with localcontext() as ctx:
        ctx.prec = precision
        ctx.rounding = rounding
        ctx.Emax = 2
        ctx.Emin = -2
        ctx.traps[Inexact] = True
        ctx.traps[Rounded] = True
        ctx.clear_flags()
        assert evaluator.evaluate(candidate) == expected_evaluation
        assert RiskGovernor().evaluate(oversized, capital, state) == expected_decision
        assert replace(capital, reserve_ratio=D("0.333")).deployable_capital == D("66.7")
        assert drawdown.drawdown == D("0.01")
        loss = RiskGovernor().evaluate(
            opp(), replace(limits(), max_daily_loss=D("5.06")),
            PortfolioState(realized_daily_pnl=D("-5.08")))
        assert not loss.checks["daily_loss_limit"]
        paper = PaperLedger(D("99"))
        paper.credit(D("2"))
        assert paper.balance == D("101")
        paper.debit(D("1.01"))
        assert paper.balance == D("99.99")
        assert not any(ctx.flags.values())


def test_exact_comparison_does_not_lose_small_allocation_at_default_precision():
    capital = replace(limits(), total_capital=D("1e28"), reserve_ratio=D("0"))
    result = RiskGovernor().evaluate(opp(required_capital=D("0.01")), capital,
                                    PortfolioState(allocated_today=D("1e28")))
    assert result.decision == Decision.REJECT
    assert not result.checks["within_deployable_capital"]


def test_exact_drawdown_equality_is_rejected_at_default_precision():
    state = PortfolioState(peak_equity=D("10000000000000000000000000000.01"),
                           current_equity=D("1e28"))
    result = RiskGovernor().evaluate(opp(), replace(limits(), max_drawdown=D("0.01")), state)
    assert state.drawdown == D("0.01")
    assert result.decision == Decision.REJECT
    assert not result.checks["drawdown_limit"]


def test_extreme_supported_positive_score_is_not_rounded_to_zero():
    candidate = opp(required_capital=D("1e128"), expected_revenue=D("1e-128"),
                    expected_cost=D("0"), estimated_probability=D("1e-128"),
                    confidence=D("1e-128"), liquidity_score=D("1e-128"))
    result = EconomicEvaluator().evaluate(candidate)
    assert result.expected_value == D("1e-256")
    assert result.return_on_capital == D("1e-384")
    assert result.risk_adjusted_score == D("1e-640")
    assert result.viable


@pytest.mark.parametrize("value", [D("1e129"), D("1e-129"), D("1" * 65)])
def test_unsupported_numeric_sizes_reject_without_mutation(tmp_path, value):
    with pytest.raises(ValueError):
        opp(expected_revenue=value)
    with pytest.raises(ValueError):
        replace(limits(), total_capital=value)
    with pytest.raises(ValueError):
        PortfolioState(realized_daily_pnl=value)
    paper = PaperLedger(D("1"))
    with pytest.raises(ValueError):
        paper.credit(value)
    assert paper.balance == D("1")
    repo = Repository(tmp_path / "bounds.db")
    with pytest.raises(ValueError), repo.conn:
        repo.ledger("oversized", "credit", value)
    assert repo.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0
    repo.close()


def test_paper_output_bound_failure_preserves_balance():
    paper = PaperLedger(D("5e128"))
    with pytest.raises(ValueError):
        paper.credit(D("5e128"))
    assert paper.balance == D("5e128")


@pytest.mark.parametrize("identifier", ["", " ", "\t", " leading", "trailing ", "two words", "a\x00b", None, 42])
def test_invalid_opportunity_ids_reject_at_model_and_persistence(tmp_path, identifier):
    with pytest.raises(ValueError):
        opp(id=identifier)
    repo = Repository(tmp_path / "ids.db")
    bypassed = SimpleNamespace(**asdict(opp()))
    bypassed.id = identifier
    with pytest.raises(ValueError), repo.conn:
        repo.save_opportunity(bypassed)
    for table in ("opportunities", "opportunity_outcomes", "decisions", "audit_events"):
        assert repo.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    repo.close()


@pytest.mark.parametrize("kind,amount", [("credit", D("-10")), ("debit", D("-10")), ("adjustment", D("10"))])
def test_invalid_persisted_ledger_operations_roll_back(tmp_path, kind, amount):
    repo = Repository(tmp_path / "ledger.db")
    with pytest.raises(ValueError), repo.conn:
        repo.ledger("valid-first", "credit", D("1"))
        repo.ledger("invalid", kind, amount)
    assert repo.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0
    with repo.conn:
        repo.ledger("valid", "debit", D("0"))
    assert repo.conn.execute("SELECT amount FROM ledger").fetchone()[0] == "0"
    repo.close()


@pytest.mark.parametrize("identifier", ["", "two words", None])
def test_invalid_event_ids_never_persist(tmp_path, identifier):
    repo = Repository(tmp_path / "events.db")
    with pytest.raises(ValueError), repo.conn:
        repo.ledger(identifier, "credit", D("1"))
    with pytest.raises(ValueError), repo.conn:
        repo.audit(identifier, "event", "x", {})
    assert repo.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0
    assert repo.conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    repo.close()


def test_decision_cannot_reference_a_different_opportunity(tmp_path):
    repo = Repository(tmp_path / "reference.db")
    decision = RiskGovernor().evaluate(opp(), limits())
    with pytest.raises(ValueError), repo.conn:
        repo.save_decision("different", decision)
    assert repo.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    repo.close()


@pytest.mark.parametrize("state", [None, PortfolioState(allocated_today=D("41"),
                                                       realized_daily_pnl=D("-1.01"),
                                                       peak_equity=D("100"), current_equity=D("99"))])
def test_audit_reconstructs_effective_inputs_and_decision_after_restart(tmp_path, state):
    path = tmp_path / "audit.db"
    repo = Repository(path)
    evaluation, decision = Phase1Engine(repo).process(opp(failure_cost=D("1.01")), limits(), state)
    repo.close()
    repo = Repository(path)
    row = repo.conn.execute("SELECT * FROM decisions").fetchone()
    payload = json.loads(repo.conn.execute("SELECT payload_json FROM audit_events").fetchone()[0])
    assert payload["schema_version"] == 1
    assert payload["decision_id"] == row["id"]
    assert payload["rule_version"] == RISK_RULE_VERSION
    assert payload["arithmetic_policy"] == ARITHMETIC_POLICY_VERSION
    assert payload["checks"] == json.loads(row["checks_json"])
    assert payload["decision"] == row["decision"]
    assert payload["limits"]["reserve_ratio"] == "0.5"
    assert payload["portfolio_state"]["allocated_today"] == str((state or PortfolioState()).allocated_today)
    reconstructed = dict(payload["opportunity"])
    for field in ("required_capital", "expected_revenue", "expected_cost", "estimated_probability",
                  "liquidity_score", "confidence", "failure_revenue", "failure_cost", "leverage"):
        assert isinstance(reconstructed[field], str)
        reconstructed[field] = D(reconstructed[field])
    reconstructed["type"] = OpportunityType(reconstructed["type"])
    restored_opportunity = Opportunity(**reconstructed)
    restored_limits = CapitalLimits(**{key: D(value) for key, value in payload["limits"].items()})
    restored_state = PortfolioState(**{key: D(value) for key, value in payload["portfolio_state"].items()})
    assert EconomicEvaluator().evaluate(restored_opportunity) == evaluation
    assert RiskGovernor().evaluate(restored_opportunity, restored_limits, restored_state) == decision
    assert payload["evaluation"]["expected_value"] == str(evaluation.expected_value)
    assert payload["evaluation"]["risk_adjusted_score"] == str(evaluation.risk_adjusted_score)
    repo.close()
