"""Paper reservations: independent expected balances, concurrency and crash recovery."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal as D, Inexact, Rounded, localcontext
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier

import pytest

from src.core.engine import Phase1Engine
from src.core.models import CapitalLimits, Opportunity, OpportunityType
from src.core.paper_accounts import PaperAccountService, PaperActionSpec, RequestConflictError
from src.persistence.sqlite import Repository


def profile(**changes):
    return replace(CapitalLimits(D("200"), D(".5"), D("100"), D("500"), D("500")), **changes)


def candidate(identifier="a", **changes):
    base = Opportunity(identifier, OpportunityType.OTHER, "synthetic fixture", D("60"),
                       D("120"), D("0"), D(".8"), confidence=D("1"))
    return replace(base, **changes)


def action(amount="60", loss=None, currency="PHP", treatment="refundable"):
    return PaperActionSpec(currency, D(amount), D(loss or amount), treatment)


@pytest.fixture
def account(tmp_path):
    repo = Repository(tmp_path / "paper.db")
    service = PaperAccountService(repo)
    service.create_account("demo", profile())
    yield repo, service
    repo.close()


def counts(repo):
    return {table: repo.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("opportunities", "opportunity_outcomes", "decisions", "audit_events",
                          "paper_observation_currencies", "paper_requests", "paper_orders", "capital_reservations")}


def test_genesis_and_reservations_survive_reopen_and_old_dates(account):
    repo, service = account
    first = service.submit("demo", "one", candidate(), action())
    second = service.submit("demo", "two", candidate("b"), action())
    assert first.status == "RESERVED" and second.status == "REJECTED"
    assert not second.checks["cash_available"]
    state = service.get_account("demo")
    assert (state.cash, state.reserved_cash, state.reserved_loss, state.available_cash) == (D("200"), D("60"), D("60"), D("40"))
    assert state.version == 1
    with repo.transaction():
        repo.conn.execute("UPDATE capital_reservations SET created_at='2000-01-01 00:00:00'")
    path = repo.path
    repo.close()
    reopened = Repository(path)
    assert PaperAccountService(reopened).get_account("demo") == state
    assert reopened.conn.execute("SELECT status FROM paper_orders").fetchone()[0] == "RESERVED"
    assert reopened.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0
    reopened.close()


def test_equivalent_retry_returns_stored_result_and_conflicts_have_no_effect(account):
    repo, service = account
    first = service.submit("demo", "one", candidate(), action())
    before = counts(repo)
    assert service.submit("demo", "one", candidate(), action("60.00", "60.0")) == first
    assert counts(repo) == before
    with pytest.raises(RequestConflictError):
        service.submit("demo", "one", candidate(), action("61"))
    with pytest.raises(RequestConflictError):
        service.submit("demo", "another", candidate(expected_revenue=D("121")), action())
    assert counts(repo) == before
    duplicate = service.submit("demo", "another", candidate(), action())
    assert duplicate.status == "REJECTED"
    assert not duplicate.checks["observation_not_reserved"]
    assert repo.conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1


def test_rejected_request_retry_stays_rejected(account):
    repo, service = account
    rejected = service.submit("demo", "large", candidate(required_capital=D("101")), action("101"))
    before = counts(repo)
    assert rejected.status == "REJECTED"
    assert service.submit("demo", "large", candidate(required_capital=D("101")), action("101")) == rejected
    assert counts(repo) == before
    assert service.get_account("demo").reserved_cash == 0


@pytest.mark.parametrize("same_request", [False, True])
def test_two_connections_cannot_double_reserve(tmp_path, same_request):
    path = tmp_path / "concurrent.db"
    repo = Repository(path)
    PaperAccountService(repo).create_account("demo", profile())
    repo.close()
    barrier = Barrier(2)

    def submit(index):
        connection = Repository(path, timeout=2)
        try:
            barrier.wait(timeout=5)
            identifier = "same" if same_request else str(index)
            return PaperAccountService(connection).submit("demo", identifier, candidate(identifier), action())
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(submit, (1, 2)))
    if same_request:
        assert results[0] == results[1]
        assert results[0].status == "RESERVED"
    else:
        assert sorted(result.status for result in results) == ["REJECTED", "RESERVED"]
    repo = Repository(path)
    state = PaperAccountService(repo).get_account("demo")
    assert state.reserved_cash == state.reserved_loss == D("60")
    assert repo.conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
    repo.close()


@pytest.mark.parametrize("boundary", ["paper_requests", "paper_orders", "capital_reservations"])
def test_sql_failure_rolls_back_every_submission_record(account, boundary):
    repo, service = account
    before = counts(repo)
    with repo.transaction():
        repo.conn.execute(f"CREATE TRIGGER injected_failure BEFORE INSERT ON {boundary} BEGIN SELECT RAISE(ABORT,'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.submit("demo", "one", candidate(), action())
    assert counts(repo) == before
    assert not repo.conn.in_transaction
    assert service.get_account("demo").reserved_cash == 0


def test_audit_failure_rolls_back_account_order_and_reservation(account, monkeypatch):
    repo, service = account
    before = counts(repo)
    def fail(*args):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(repo, "audit", fail)
    with pytest.raises(RuntimeError):
        service.submit("demo", "one", candidate(), action())
    assert counts(repo) == before
    assert service.get_account("demo").version == 0


def test_failed_genesis_creates_no_account(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "genesis.db")
    def fail(*args):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(repo, "audit", fail)
    with pytest.raises(RuntimeError):
        PaperAccountService(repo).create_account("demo", profile())
    assert repo.conn.execute("SELECT COUNT(*) FROM paper_accounts").fetchone()[0] == 0
    assert repo.conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0
    repo.close()


@pytest.mark.parametrize("limit,loss", [("max_daily_loss", "60"), ("max_drawdown", "60")])
def test_prospective_loss_equality_rejects(tmp_path, limit, loss):
    repo = Repository(tmp_path / "risk.db")
    service = PaperAccountService(repo)
    service.create_account("demo", profile(**{limit: D(loss)}))
    result = service.submit("demo", "one", candidate(), action())
    assert result.status == "REJECTED"
    check = "prospective_daily_loss" if limit == "max_daily_loss" else "prospective_drawdown"
    assert not result.checks[check]
    assert service.get_account("demo").reserved_cash == 0
    repo.close()


@pytest.mark.parametrize("limit", ["max_daily_loss", "max_drawdown"])
def test_zero_account_loss_budget_blocks_reservations(account, limit):
    repo, service = account
    service.create_account("zero", profile(**{limit: D("0")}))
    result = service.submit("zero", "one", candidate(), action())
    assert result.status == "REJECTED"
    assert service.get_account("zero").reserved_cash == 0


def test_reserved_loss_capacity_is_aggregate(tmp_path):
    repo = Repository(tmp_path / "risk.db")
    service = PaperAccountService(repo)
    service.create_account("demo", profile(max_daily_loss=D("100")))
    assert service.submit("demo", "one", candidate(required_capital=D("40")), action("40", "60")).status == "RESERVED"
    second = service.submit("demo", "two", candidate("b", required_capital=D("40")), action("40"))
    assert second.checks["cash_available"]
    assert not second.checks["prospective_daily_loss"]
    assert service.get_account("demo").reserved_cash == D("40")
    repo.close()


def test_fees_and_principal_treatment_are_explicit(account):
    repo, service = account
    omitted_fee = service.submit("demo", "fee", candidate(expected_cost=D("5")), action())
    assert not omitted_fee.checks["cash_covers_known_exposure"]
    assert omitted_fee.status == "REJECTED"
    purchase = candidate("purchase", expected_cost=D("60"), expected_revenue=D("120"), estimated_probability=D("1"))
    result = service.submit("demo", "purchase", purchase, action(treatment="expensed"))
    assert result.status == "RESERVED"  # Principal is already in the 60 cost, not charged twice.
    assert service.get_account("demo").reserved_cash == D("60")


@pytest.mark.parametrize("changes,treatment", [({"failure_cost": D("100")}, "refundable"),
                                                ({"expected_cost": D("5")}, "expensed")])
def test_inconsistent_capital_contract_cannot_reserve(account, changes, treatment):
    repo, service = account
    result = service.submit("demo", "one", candidate(**changes), action(treatment=treatment))
    assert result.status == "REJECTED"
    assert not result.checks["capital_contract_valid"]
    assert service.get_account("demo").reserved_cash == 0


def test_wrong_currency_and_leverage_fail_closed(account):
    repo, service = account
    currency = service.submit("demo", "currency", candidate(), action(currency="USD"))
    assert currency.status == "REJECTED" and not currency.checks["currency_matches"]
    leverage = service.submit("demo", "leverage", candidate("leveraged", leverage=D("2")), action())
    assert leverage.status == "REJECTED" and not leverage.checks["leverage_limit"]
    assert service.get_account("demo").reserved_cash == 0


def test_observation_cannot_be_reinterpreted_in_another_currency(account):
    repo, service = account
    service.submit("demo", "one", candidate(), action())
    service.create_account("dollars", profile(), currency="USD")
    before = counts(repo)
    with pytest.raises(RequestConflictError, match="currency is immutable"):
        service.submit("dollars", "one", candidate(), action(currency="USD"))
    assert counts(repo) == before
    assert service.get_account("dollars").reserved_cash == 0


def test_halt_survives_restart_and_is_idempotent(account):
    repo, service = account
    service.submit("demo", "one", candidate(), action())
    halted = service.halt("demo", "operator stopped pilot")
    before = counts(repo)
    assert service.halt("demo", "repeated request") == halted
    assert counts(repo) == before
    assert halted.available_cash == 0 and halted.reserved_cash == D("60")
    path = repo.path
    repo.close()
    reopened = Repository(path)
    service = PaperAccountService(reopened)
    assert service.get_account("demo") == halted
    result = service.submit("demo", "two", candidate("b"), action("20"))
    assert result.status == "REJECTED" and not result.checks["account_active"]
    reopened.close()


def test_halt_audit_failure_preserves_active_account(account, monkeypatch):
    repo, service = account
    def fail(*args):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(repo, "audit", fail)
    with pytest.raises(RuntimeError):
        service.halt("demo", "operator request")
    state = service.get_account("demo")
    assert state.status == "ACTIVE" and state.version == 0


def test_genesis_is_immutable(account):
    repo, service = account
    before = counts(repo)
    with pytest.raises(RequestConflictError):
        service.create_account("demo", profile(total_capital=D("500")))
    assert service.get_account("demo").cash == D("200")
    assert counts(repo) == before


def test_foreign_keys_prevent_cross_account_reservation_links(account):
    repo, service = account
    service.create_account("other", profile())
    service.submit("demo", "one", candidate(), action())
    with pytest.raises(sqlite3.IntegrityError), repo.transaction():
        repo.conn.execute("UPDATE capital_reservations SET account_id='other'")
    assert service.get_account("demo").reserved_cash == D("60")
    assert service.get_account("other").reserved_cash == 0


def test_projection_discrepancy_halts_new_submissions(account):
    repo, service = account
    service.submit("demo", "one", candidate(), action())
    with repo.transaction():
        repo.conn.execute("UPDATE paper_accounts SET reserved_cash_minor=0 WHERE id='demo'")
    rejected = service.submit("demo", "two", candidate("b"), action())
    assert rejected.status == "REJECTED" and not rejected.checks["account_integrity"]
    assert service.get_account("demo").status == "HALTED"
    assert repo.conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1


@pytest.mark.parametrize("payload", ["not-json", "{}"])
def test_corrupt_genesis_audit_halts_account(account, payload):
    repo, service = account
    with repo.transaction():
        repo.conn.execute("UPDATE audit_events SET payload_json=? WHERE event_type='paper_account_created'", (payload,))
    assert service.get_account("demo").status == "HALTED"
    result = service.submit("demo", "one", candidate(), action())
    assert result.status == "REJECTED" and not result.checks["account_integrity"]


def test_owned_transaction_rejects_nesting_without_committing_caller(account):
    repo, service = account
    with pytest.raises(RuntimeError, match="outer rollback"), repo.transaction():
        repo.ledger("pending", "credit", D("1"))
        with pytest.raises(RuntimeError, match="already active"):
            service.submit("demo", "one", candidate(), action())
        with pytest.raises(RuntimeError, match="already active"):
            Phase1Engine(repo).process(candidate(), profile())
        assert repo.conn.in_transaction
        raise RuntimeError("outer rollback")
    assert repo.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0
    assert repo.conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 0


def test_busy_writer_returns_no_result_or_records_then_same_key_can_retry(account):
    repo, service = account
    other = Repository(repo.path, timeout=.01)
    with repo.transaction():
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            PaperAccountService(other).submit("demo", "one", candidate(), action())
    assert other.conn.execute("SELECT COUNT(*) FROM paper_requests").fetchone()[0] == 0
    assert PaperAccountService(other).submit("demo", "one", candidate(), action()).status == "RESERVED"
    other.close()


def test_existing_screened_observation_is_reused_without_overwriting(account):
    repo, service = account
    Phase1Engine(repo).process(candidate(), profile())
    assert service.submit("demo", "one", candidate(), action()).status == "RESERVED"
    assert repo.conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1
    assert repo.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2


@pytest.mark.parametrize("value", [D("0"), D("-1"), D("NaN"), D("1.001"), D("92233720368547758.08")])
def test_invalid_action_money_is_rejected(value):
    with pytest.raises(ValueError):
        PaperActionSpec("PHP", value, value, "refundable")


def test_currency_contract_and_live_mode_are_rejected(account):
    repo, service = account
    with pytest.raises(ValueError):
        PaperActionSpec("PHP", D("10"), D("10"), "unknown")
    with pytest.raises(ValueError):
        action(currency="EUR")
    with pytest.raises(ValueError):
        action("10", "9")
    with pytest.raises(ValueError):
        PaperAccountService(repo, mode="LIVE")
    with pytest.raises(ValueError):
        service.create_account("other", profile(max_leverage=D("2")))


def test_caller_context_and_fractional_reserve_cannot_change_cash(account):
    repo, service = account
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = context.traps[Rounded] = True
        result = service.submit("demo", "one", candidate(), action())
        assert result.status == "RESERVED"
        assert service.get_account("demo").available_cash == D("40")
    service.create_account("fractional", profile(total_capital=D("1"), reserve_ratio=D(".333")))
    assert service.get_account("fractional").available_cash == D(".66")


def test_audit_captures_effective_state_budget_and_result(account):
    repo, service = account
    result = service.submit("demo", "one", candidate(), action())
    row = repo.conn.execute("SELECT payload_json FROM audit_events WHERE event_type='paper_reservation_decision'").fetchone()
    audit = json.loads(row[0])
    assert audit["result"]["decision_id"] == result.decision_id
    assert audit["result"]["order_id"] == result.order_id
    assert audit["portfolio_state"]["allocated_today"] == "0"
    assert D(audit["account_after"]["reserved_cash"]) == D("60")
    assert audit["action"]["capital_treatment"] == "refundable"
    assert audit["reservation_policy"] == "paper-reservations-v1"


@pytest.mark.parametrize("after_commit", [False, True])
def test_process_exit_before_commit_or_response_is_recoverable(account, after_commit):
    repo, service = account
    path = repo.path
    repo.close()
    script = """
import os, sys
from decimal import Decimal as D
from src.persistence.sqlite import Repository
from src.core.models import Opportunity, OpportunityType
from src.core.paper_accounts import PaperAccountService, PaperActionSpec
repo=Repository(sys.argv[1])
opportunity=Opportunity('crash',OpportunityType.OTHER,'fixture',D('60'),D('120'),D('0'),D('.8'),confidence=D('1'))
if sys.argv[2]=='before':
    with repo.transaction():
        repo.save_opportunity(opportunity)
        repo.conn.execute("UPDATE paper_accounts SET reserved_cash_minor=6000 WHERE id='demo'")
        os._exit(0)
PaperAccountService(repo).submit('demo','crash',opportunity,PaperActionSpec('PHP',D('60'),D('60'),'refundable'))
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(path), "after" if after_commit else "before"],
                   cwd=Path(__file__).resolve().parents[1], check=True, timeout=10)
    reopened = Repository(path)
    service = PaperAccountService(reopened)
    assert service.get_account("demo").reserved_cash == (D("60") if after_commit else D("0"))
    opportunity = candidate("crash", title="fixture")
    result = service.submit("demo", "crash", opportunity, action())
    assert result.status == "RESERVED"
    assert reopened.conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
    assert reopened.conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    reopened.close()


def test_legacy_ledger_is_preserved_and_never_becomes_paper_cash(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript("""CREATE TABLE ledger (
        id INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, amount TEXT NOT NULL,
        currency TEXT NOT NULL, reference TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO ledger(event_id,kind,amount,currency) VALUES ('legacy','credit','999','PHP');""")
    connection.close()
    repo = Repository(path)
    assert repo.conn.execute("SELECT amount FROM ledger").fetchone()[0] == "999"
    assert repo.conn.execute("SELECT COUNT(*) FROM paper_accounts").fetchone()[0] == 0
    service = PaperAccountService(repo)
    service.create_account("demo", profile())
    assert service.get_account("demo").cash == D("200")
    repo.close()


def test_future_schema_and_unversioned_tables_fail_before_other_schema_writes(tmp_path):
    path = tmp_path / "future.db"
    repo = Repository(path)
    with repo.transaction():
        repo.conn.execute("UPDATE paper_schema SET version=999")
    repo.close()
    with pytest.raises(RuntimeError, match="unsupported"):
        Repository(path)
    path = tmp_path / "unversioned.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE paper_accounts(id TEXT)")
    connection.close()
    with pytest.raises(RuntimeError, match="unversioned"):
        Repository(path)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("paper_accounts",)]
    connection.close()


def test_missing_versioned_table_is_not_silently_recreated(tmp_path):
    path = tmp_path / "missing.db"
    repo = Repository(path)
    with repo.transaction():
        repo.conn.execute("DROP TABLE capital_reservations")
    repo.close()
    with pytest.raises(RuntimeError, match="incomplete"):
        Repository(path)


def test_backup_restore_preserves_account_and_reserved_capacity(account, tmp_path):
    repo, service = account
    expected = service.submit("demo", "one", candidate(), action())
    backup = sqlite3.connect(tmp_path / "backup.db")
    repo.conn.backup(backup)
    backup.close()
    restored = Repository(tmp_path / "backup.db")
    service = PaperAccountService(restored)
    assert service.get_account("demo").reserved_cash == D("60")
    assert service.submit("demo", "one", candidate(), action()) == expected
    restored.close()
