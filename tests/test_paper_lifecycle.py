"""Paper lifecycle invariants with independent expected cash, P&L and race outcomes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, Inexact, Rounded, localcontext
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier

import pytest

from src.core.models import CapitalLimits, Opportunity, OpportunityType
from src.core.paper_accounts import PaperActionSpec, RequestConflictError, canonical_json
from src.core.paper_lifecycle import PaperLifecycleService, PaperFillSpec, PaperSettlementSpec
from src.persistence.paper_schema import V1_STATEMENTS, V1_CHECKSUM
from src.persistence.sqlite import Repository

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def limits(**changes):
    return replace(CapitalLimits(D('200'),D('.5'),D('100'),D('500'),D('500')), **changes)


def opportunity(identifier='a', **changes):
    return replace(Opportunity(identifier,OpportunityType.OTHER,'fixture',D('60'),D('120'),D('0'),D('1'),confidence=D('1')), **changes)


@pytest.fixture
def paper(tmp_path):
    repo = Repository(tmp_path / 'lifecycle.db')
    service = PaperLifecycleService(repo, clock=lambda: NOW)
    service.create_account('demo', limits())
    yield repo, service
    repo.close()


def reserve(service, key='request', treatment='refundable', cash='65', loss=None, expiry=None):
    candidate = opportunity(key, expected_cost=D('5') if treatment == 'refundable' else D('65'))
    result = service.submit('demo',key,candidate,PaperActionSpec('PHP',D(cash),D(loss or cash),treatment),expires_at=expiry)
    assert result.status == 'RESERVED'
    return result.order_id


def snapshot(repo):
    return {table:[tuple(row) for row in repo.conn.execute(f'SELECT * FROM {table} ORDER BY 1')]
            for table in ('paper_accounts','paper_orders','capital_reservations','paper_positions',
                          'paper_lifecycle_events','paper_journal_entries','paper_journal_postings','audit_events')}


def test_refundable_profit_balances_and_fees_once(paper):
    repo, service = paper
    order = reserve(service)
    fill = service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
    assert (fill.status,fill.cash_change,fill.pnl_change) == ('FILLED',D('-65'),D('-5'))
    state = service.get_account('demo')
    assert (state.cash,state.current_equity,state.realized_pnl,state.reserved_cash,state.open_loss,state.deployed_cash) == (D('135'),D('195'),D('-5'),D('0'),D('60'),D('65'))
    assert state.available_cash == D('35')
    settled = service.settle('demo','settle',order,PaperSettlementSpec('PHP',D('60'),D('20'),D('2')),expected_version=1)
    assert (settled.status,settled.cash_change,settled.pnl_change) == ('CLOSED',D('78'),D('18'))
    state = service.get_account('demo')
    assert (state.cash,state.current_equity,state.peak_equity,state.realized_pnl,state.open_loss,state.deployed_cash) == (D('213'),D('213'),D('213'),D('13'),D('0'),D('0'))
    assert service.get_order('demo',order).realized_pnl == D('13')
    entries = repo.conn.execute('''SELECT e.id,SUM(CASE p.side WHEN 'debit' THEN p.amount_minor ELSE -p.amount_minor END)
        FROM paper_journal_entries e JOIN paper_journal_postings p ON p.entry_id=e.id GROUP BY e.id''').fetchall()
    assert len(entries) == 3 and all(row[1] == 0 for row in entries)
    assert repo.conn.execute('SELECT COUNT(*) FROM ledger').fetchone()[0] == 0


@pytest.mark.parametrize('returned,revenue,expected_cash,expected_pnl', [('0','0','135','-65'),('30','0','165','-35'),('60','0','195','-5')])
def test_refundable_writeoff_salvage_and_breakeven(paper,returned,revenue,expected_cash,expected_pnl):
    repo, service = paper
    order = reserve(service)
    service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
    result = service.settle('demo','settle',order,PaperSettlementSpec('PHP',D(returned),D(revenue)),expected_version=1)
    assert result.status == 'CLOSED'
    state = service.get_account('demo')
    assert state.cash == state.current_equity == D(expected_cash)
    assert state.realized_pnl == service.get_order('demo',order).realized_pnl == D(expected_pnl)
    assert state.peak_equity == D('200')


def test_expensed_purchase_is_charged_once(paper):
    repo, service = paper
    order = reserve(service,treatment='expensed')
    filled = service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
    assert filled.pnl_change == D('-65')
    state = service.get_account('demo')
    assert state.cash == state.current_equity == D('135') and state.open_loss == 0
    assert state.realized_pnl == D('-65')
    bad = service.settle('demo','bad-return',order,PaperSettlementSpec('PHP',D('60'),D('20')),expected_version=1)
    assert bad.status == 'REJECTED'
    settled = service.settle('demo','settle',order,PaperSettlementSpec('PHP',D('0'),D('80'),D('2')),expected_version=1)
    assert settled.status == 'CLOSED' and settled.pnl_change == D('78')
    state = service.get_account('demo')
    assert state.cash == D('213') and state.realized_pnl == D('13')
    assert service.get_order('demo',order).realized_pnl == D('13')


def test_fill_releases_unused_cash_but_keeps_conservative_loss(paper):
    repo, service = paper
    order = reserve(service,cash='80',loss='90')
    service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('2')),expected_version=0)
    state = service.get_account('demo')
    assert (state.cash,state.available_cash,state.open_loss,state.deployed_cash) == (D('138'),D('38'),D('88'),D('62'))
    assert state.reserved_cash == state.reserved_loss == 0


@pytest.mark.parametrize('boundary',['max_daily_loss','max_drawdown'])
def test_open_position_still_uses_loss_capacity(paper,boundary):
    repo, service = paper
    service.create_account('risk', limits(**{boundary:D('100')}))
    first = service.submit('risk','a',opportunity(),PaperActionSpec('PHP',D('60'),D('60'),'refundable'))
    assert first.status == 'RESERVED'
    assert service.fill('risk','fill',first.order_id,PaperFillSpec('PHP',D('60')),expected_version=0).status == 'FILLED'
    second = service.submit('risk','b',opportunity('b',required_capital=D('40')),PaperActionSpec('PHP',D('40'),D('40'),'refundable'))
    assert second.status == 'REJECTED'
    assert not second.checks['prospective_daily_loss' if boundary=='max_daily_loss' else 'prospective_drawdown']
    assert service.get_account('risk').open_loss == D('60')


@pytest.mark.parametrize('kind',['cancel','expire'])
def test_release_once_and_historical_reservation_retry(paper,kind):
    repo, service = paper
    deadline = NOW + timedelta(seconds=1)
    order = reserve(service,expiry=deadline)
    if kind == 'expire':
        service.clock = lambda: deadline
    result = getattr(service,kind)('demo','release',order,expected_version=0)
    assert result.status == ('CANCELLED' if kind=='cancel' else 'EXPIRED')
    before = snapshot(repo)
    assert getattr(service,kind)('demo','release',order,expected_version=0) == result
    assert snapshot(repo) == before
    duplicate = getattr(service,kind)('demo','again',order,expected_version=1)
    assert duplicate.status == 'REJECTED'
    state = service.get_account('demo')
    assert (state.cash,state.current_equity,state.available_cash,state.open_loss) == (D('200'),D('200'),D('100'),D('0'))
    assert repo.conn.execute('SELECT COUNT(*) FROM paper_journal_entries').fetchone()[0] == 1
    original = service.submit('demo','request',opportunity('request',expected_cost=D('5')),PaperActionSpec('PHP',D('65'),D('65'),'refundable'),expires_at=deadline)
    assert original.status == 'RESERVED' and original.order_id == order
    assert service.get_order('demo',order).status in ('CANCELLED','EXPIRED')


def test_deadlines_have_no_implicit_release_and_block_late_fill(paper):
    repo, service = paper
    deadline = NOW + timedelta(seconds=1)
    order = reserve(service,expiry=deadline)
    assert service.expire('demo','early',order,expected_version=0).status == 'REJECTED'
    service.clock = lambda: deadline
    assert service.expire('demo','early',order,expected_version=0).status == 'REJECTED'  # Cached rejection.
    assert service.fill('demo','late',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0).status == 'REJECTED'
    assert service.get_account('demo').reserved_cash == D('65')
    assert service.expire('demo','reached',order,expected_version=0).status == 'EXPIRED'
    indefinite = reserve(service,key='indefinite')
    service.clock = lambda: NOW + timedelta(days=100)
    assert service.expire('demo','no-deadline',indefinite,expected_version=0).status == 'REJECTED'
    past = service.submit('demo','past',opportunity('past'),PaperActionSpec('PHP',D('60'),D('60'),'refundable'),expires_at=NOW)
    assert past.status == 'REJECTED' and not past.checks['deadline_in_future']
    with pytest.raises(ValueError,match='aware'):
        reserve(service,key='naive',expiry=datetime(2026,10,12))


def test_lifecycle_retries_and_conflicts_survive_reopen(paper):
    repo, service = paper
    order = reserve(service)
    spec = PaperFillSpec('PHP',D('60'),D('5'))
    first = service.fill('demo','fill',order,spec,expected_version=0)
    before = snapshot(repo)
    assert service.fill('demo','fill',order,PaperFillSpec('PHP',D('60.00'),D('5.0')),expected_version=0) == first
    with pytest.raises(RequestConflictError):
        service.cancel('demo','fill',order,expected_version=0)
    with pytest.raises(RequestConflictError):
        service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('4')),expected_version=0)
    assert snapshot(repo) == before
    closed = service.settle('demo','settle',order,PaperSettlementSpec('PHP',D('60'),D('20')),expected_version=1)
    path = repo.path
    repo.close()
    reopened = Repository(path)
    other = PaperLifecycleService(reopened)
    assert other.fill('demo','fill',order,spec,expected_version=0) == first
    assert other.settle('demo','settle',order,PaperSettlementSpec('PHP',D('60.0'),D('20.00')),expected_version=1) == closed
    assert other.get_account('demo').cash == D('215')
    reopened.close()


@pytest.mark.parametrize('operation',['cancel','settle'])
def test_halted_account_allows_reduction_but_no_fill(paper,operation):
    repo, service = paper
    order = reserve(service)
    if operation == 'settle':
        service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
    service.halt('demo','operator stop')
    if operation == 'cancel':
        assert service.fill('demo','blocked',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0).status == 'REJECTED'
        assert service.cancel('demo','cancel',order,expected_version=0).status == 'CANCELLED'
    else:
        assert service.settle('demo','settle',order,PaperSettlementSpec('PHP',D('60'),D('0')),expected_version=1).status == 'CLOSED'
    assert service.get_account('demo').status == 'HALTED' and service.get_account('demo').available_cash == 0


def test_corrupt_projection_halts_without_releasing_commitment(paper):
    repo, service = paper
    order = reserve(service)
    with repo.transaction():
        repo.conn.execute("UPDATE paper_accounts SET current_equity_minor=19900 WHERE id='demo'")
    result = service.cancel('demo','cancel',order,expected_version=0)
    assert result.status == 'REJECTED'
    assert service.get_account('demo').status == 'HALTED'
    assert service.get_account('demo').reserved_cash == D('65')


@pytest.mark.parametrize('changes',[{'capital':D('59')},{'fees':D('6')},{'currency':'USD'}])
def test_unsupported_fill_rejects_without_spending(paper,changes):
    repo, service = paper
    order = reserve(service)
    spec = replace(PaperFillSpec('PHP',D('60'),D('5')),**changes)
    assert service.fill('demo','invalid',order,spec,expected_version=0).status == 'REJECTED'
    assert service.get_account('demo').cash == D('200')
    assert service.get_account('demo').reserved_cash == D('65')
    assert service.get_order('demo',order).version == 0


@pytest.mark.parametrize('spec',[lambda:PaperFillSpec('PHP',D('NaN')),lambda:PaperFillSpec('PHP',D('0')),
    lambda:PaperFillSpec('PHP',D('60'),D('-1')),lambda:PaperFillSpec('PHP',D('60.001')),
    lambda:PaperSettlementSpec('PHP',D('0'),D('0'),D('1')),lambda:PaperSettlementSpec('PHP',D('0'),D('-1')),
    lambda:PaperSettlementSpec('EUR',D('0'),D('1'))])
def test_invalid_money_contracts(spec):
    with pytest.raises(ValueError):
        spec()


def test_stale_version_and_unknown_account_order_are_not_authorized(paper):
    repo, service = paper
    order = reserve(service)
    assert service.fill('demo','stale',order,PaperFillSpec('PHP',D('60')),expected_version=1).status == 'REJECTED'
    service.create_account('other',limits())
    before = snapshot(repo)
    with pytest.raises(KeyError):
        service.cancel('other','foreign',order,expected_version=0)
    with pytest.raises(ValueError):
        service.cancel('demo','invalid',order,expected_version=True)
    assert snapshot(repo) == before


@pytest.mark.parametrize('table,operation',[('paper_positions','INSERT'),('paper_journal_entries','INSERT'),
    ('paper_journal_postings','INSERT'),('paper_lifecycle_events','INSERT'),('paper_orders','UPDATE'),
    ('paper_accounts','UPDATE'),('capital_reservations','DELETE')])
def test_fill_write_failure_rolls_back_money_and_all_records(paper,table,operation):
    repo, service = paper
    order = reserve(service)
    before = snapshot(repo)
    with repo.transaction():
        repo.conn.execute(f"CREATE TRIGGER injected BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError,match='injected'):
        service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
    assert snapshot(repo) == before and not repo.conn.in_transaction
    with repo.transaction():
        repo.conn.execute('DROP TRIGGER injected')
    assert service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0).status == 'FILLED'


@pytest.mark.parametrize('operation',['fill','cancel','expire','settle'])
def test_audit_failure_rolls_back_entire_transition(paper,operation,monkeypatch):
    repo, service = paper
    order = reserve(service,expiry=NOW+timedelta(seconds=1))
    if operation == 'settle':
        service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
    if operation == 'expire':
        service.clock = lambda: NOW+timedelta(seconds=1)
    before = snapshot(repo)
    def unavailable(*args):
        raise RuntimeError('audit unavailable')
    monkeypatch.setattr(repo,'audit',unavailable)
    args = []
    if operation == 'fill':
        args = [PaperFillSpec('PHP',D('60'),D('5'))]
    elif operation == 'settle':
        args = [PaperSettlementSpec('PHP',D('60'),D('20'),D('2'))]
    with pytest.raises(RuntimeError,match='audit unavailable'):
        getattr(service,operation)('demo','transition',order,*args,expected_version=1 if operation=='settle' else 0)
    assert snapshot(repo) == before


@pytest.mark.parametrize('table',['paper_positions','paper_orders','paper_accounts','paper_lifecycle_events','paper_journal_postings'])
def test_settlement_write_failure_retains_open_position(paper,table):
    repo, service = paper
    order = reserve(service)
    service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
    before = snapshot(repo)
    operation = 'UPDATE' if table in ('paper_positions','paper_orders','paper_accounts') else 'INSERT'
    with repo.transaction():
        repo.conn.execute(f"CREATE TRIGGER injected BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError,match='injected'):
        service.settle('demo','settle',order,PaperSettlementSpec('PHP',D('60'),D('20')),expected_version=1)
    assert snapshot(repo) == before
    assert service.get_account('demo').open_loss == D('60')


@pytest.mark.parametrize('same_fill',[False,True])
def test_two_connections_serialize_fill_cancel_and_identical_fills(paper,same_fill):
    repo, service = paper
    order = reserve(service)
    barrier = Barrier(2)
    def worker(index):
        other = Repository(repo.path,timeout=2)
        try:
            engine = PaperLifecycleService(other,clock=lambda:NOW)
            barrier.wait(timeout=5)
            if index == 0 or same_fill:
                return engine.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
            return engine.cancel('demo','cancel',order,expected_version=0)
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(worker,(0,1)))
    state, latest = service.get_account('demo'), service.get_order('demo',order)
    assert state.status == 'ACTIVE' and latest.version == 1
    if same_fill:
        assert results[0] == results[1] and latest.status == 'FILLED'
    else:
        assert sum(r.status == 'REJECTED' for r in results) == 1
        assert latest.status in ('FILLED','CANCELLED')
    assert state.cash == (D('135') if latest.status == 'FILLED' else D('200'))
    assert state.reserved_cash == 0
    assert repo.conn.execute('SELECT COUNT(*) FROM paper_positions').fetchone()[0] == (latest.status=='FILLED')


@pytest.mark.parametrize('same_key',[False,True])
def test_two_connections_settle_only_once(paper,same_key):
    repo, service = paper
    order = reserve(service)
    service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
    barrier = Barrier(2)
    def worker(index):
        other = Repository(repo.path,timeout=2)
        try:
            barrier.wait(timeout=5)
            return PaperLifecycleService(other).settle('demo','same' if same_key else str(index),order,
                PaperSettlementSpec('PHP',D('60'),D('20'),D('2')),expected_version=1)
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(worker,(0,1)))
    if same_key:
        assert results[0] == results[1]
    else:
        assert sorted(r.status for r in results) == ['CLOSED','REJECTED']
    assert service.get_account('demo').cash == D('213')
    assert service.get_account('demo').realized_pnl == D('13')
    assert repo.conn.execute('SELECT COUNT(*) FROM paper_journal_entries').fetchone()[0] == 3


@pytest.mark.parametrize('after_commit',[False,True])
def test_process_exit_before_fill_commit_or_response(paper,after_commit):
    repo, service = paper
    order = reserve(service)
    path = repo.path
    repo.close()
    script = '''
import os,sys
from decimal import Decimal as D
from src.persistence.sqlite import Repository
from src.core.paper_lifecycle import PaperLifecycleService,PaperFillSpec
repo=Repository(sys.argv[1])
if sys.argv[3]=='before':
    original=repo.audit
    def crash(*args):
        if args[1]=='paper_lifecycle':
            os._exit(0)
        original(*args)
    repo.audit=crash
PaperLifecycleService(repo).fill('demo','fill',sys.argv[2],PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
os._exit(0)
'''
    subprocess.run([sys.executable,'-c',script,str(path),order,'after' if after_commit else 'before'],
                   cwd=Path(__file__).resolve().parents[1],check=True,timeout=10)
    reopened = Repository(path)
    other = PaperLifecycleService(reopened)
    assert other.get_account('demo').cash == (D('135') if after_commit else D('200'))
    assert other.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0).status == 'FILLED'
    assert other.get_account('demo').cash == D('135')
    assert reopened.conn.execute('SELECT COUNT(*) FROM paper_journal_entries').fetchone()[0] == 2
    assert reopened.conn.execute('SELECT COUNT(*) FROM paper_positions').fetchone()[0] == 1
    reopened.close()


def test_lifecycle_is_independent_of_caller_decimal_context(paper):
    repo, service = paper
    with localcontext() as context:
        context.prec = 1
        context.traps[Inexact] = context.traps[Rounded] = True
        order = reserve(service)
        service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
        service.settle('demo','settle',order,PaperSettlementSpec('PHP',D('60'),D('20'),D('2')),expected_version=1)
        assert service.get_account('demo').cash == D('213')
        assert service.get_account('demo').realized_pnl == D('13')
        assert not context.flags[Inexact] and not context.flags[Rounded]


@pytest.mark.parametrize('table',['paper_journal_entries','paper_journal_postings','paper_lifecycle_events'])
@pytest.mark.parametrize('operation',['UPDATE','DELETE'])
def test_committed_accounting_history_cannot_be_rewritten(paper,table,operation):
    repo, service = paper
    order = reserve(service)
    service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
    before = snapshot(repo)
    with pytest.raises(sqlite3.IntegrityError,match='immutable'), repo.transaction():
        column = 'postings_json' if table=='paper_journal_entries' else 'side' if table=='paper_journal_postings' else 'payload_json'
        repo.conn.execute(f'UPDATE {table} SET {column}={column}' if operation=='UPDATE' else f'DELETE FROM {table}')
    assert snapshot(repo) == before


@pytest.mark.parametrize('corruption',['extra_posting','position','audit'])
def test_changed_internal_evidence_halts_before_more_movement(paper,corruption):
    repo, service = paper
    order = reserve(service)
    filled = service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
    with repo.transaction():
        if corruption == 'extra_posting':
            repo.conn.execute("INSERT INTO paper_journal_postings VALUES (?,99,'expense','debit',1)",(filled.journal_id,))
        elif corruption == 'position':
            repo.conn.execute('UPDATE paper_positions SET loss_minor=0 WHERE order_id=?',(order,))
        else:
            repo.conn.execute("UPDATE audit_events SET payload_json='{}' WHERE event_type='paper_lifecycle'")
    assert service.settle('demo','settle',order,PaperSettlementSpec('PHP',D('60'),D('20')),expected_version=1).status == 'REJECTED'
    assert service.get_account('demo').status == 'HALTED'
    assert service.get_account('demo').cash == D('135')
    assert service.get_order('demo',order).status == 'FILLED'


def test_settlement_storage_overflow_is_rejected_without_effect(paper):
    repo, service = paper
    order = reserve(service)
    service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
    result = service.settle('demo','overflow',order,PaperSettlementSpec('PHP',D('0'),D('92233720368547758.07')),expected_version=1)
    assert result.status == 'REJECTED'
    assert service.get_account('demo').cash == D('135')
    assert service.get_order('demo',order).status == 'FILLED'


def create_v1(path):
    """An actual version-one schema, with explicit audited synthetic genesis/reservation."""
    from hashlib import sha256
    assert V1_CHECKSUM == 'aba86b2c697c9a6df895192442548e1d0f842cba6573443a1dc71e62e19dc2bf'
    from src.core.models import Decision, RiskDecision
    from src.core.paper_accounts import ReservationResult
    repo = Repository.__new__(Repository)
    repo.conn = sqlite3.connect(path,isolation_level='DEFERRED')
    repo.conn.row_factory = sqlite3.Row
    repo.conn.execute('PRAGMA foreign_keys=ON')
    repo._init_schema()
    for statement in V1_STATEMENTS:
        repo.conn.execute(statement)
    repo.conn.execute('INSERT INTO paper_schema VALUES (1,1,?)',(V1_CHECKSUM,))
    policy = canonical_json(asdict(limits()))
    repo.audit('genesis','paper_account_created','demo',{'synthetic':True,'currency':'PHP','cash_minor':20000,
               'limits':json.loads(policy),'policy_version':'paper-reservations-v1'})
    repo.conn.execute('''INSERT INTO paper_accounts(id,currency,mode,limits_json,genesis_event_id,cash_minor,
        reserved_cash_minor,reserved_loss_minor,peak_equity_minor,current_equity_minor,status,version)
        VALUES ('demo','PHP','PAPER',?,'genesis',20000,6000,6000,20000,20000,'ACTIVE',1)''',(policy,))
    candidate = opportunity('old')
    spec = PaperActionSpec('PHP',D('60'),D('60'),'refundable')
    repo.save_opportunity(candidate)
    repo.conn.execute("INSERT INTO paper_observation_currencies VALUES ('old','PHP')")
    decision_id = repo.save_decision('old',RiskDecision(Decision.PAPER_TEST,'fixture','old',{'fixture':True}))
    result = ReservationResult('demo','old','RESERVED','old-order',decision_id,'fixture',D('60'),D('60'),{'fixture':True})
    payload = canonical_json({'opportunity':asdict(candidate),'action':asdict(spec),
                             'policy_version':'paper-reservations-v1','arithmetic_policy':'exact-decimal-v1'})
    repo.conn.execute('INSERT INTO paper_requests(account_id,request_id,payload_digest,payload_json,result_json,decision_id) VALUES (?,?,?,?,?,?)',
        ('demo','old',sha256(payload.encode()).hexdigest(),payload,canonical_json(asdict(result)),decision_id))
    repo.conn.execute("INSERT INTO paper_orders(id,account_id,request_id,opportunity_id,decision_id,status) VALUES ('old-order','demo','old','old',?,'RESERVED')",(decision_id,))
    repo.conn.execute("INSERT INTO capital_reservations(order_id,account_id,cash_minor,loss_minor) VALUES ('old-order','demo',6000,6000)")
    repo.conn.commit()
    repo.conn.close()
    return result


def test_v1_migration_preserves_retry_history_and_enables_fill(tmp_path):
    path = tmp_path/'v1.db'
    result = create_v1(path)
    repo = Repository(path)
    service = PaperLifecycleService(repo)
    assert repo.conn.execute('SELECT version FROM paper_schema').fetchone()[0] == 2
    assert service.get_account('demo').cash == D('200')
    assert service.get_account('demo').reserved_cash == D('60')
    assert service.submit('demo','old',opportunity('old'),PaperActionSpec('PHP',D('60.0'),D('60.00'),'refundable')) == result
    assert repo.conn.execute('SELECT COUNT(*) FROM paper_journal_entries').fetchone()[0] == 1
    assert repo.conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert service.fill('demo','fill','old-order',PaperFillSpec('PHP',D('60')),expected_version=0).status == 'FILLED'
    assert service.settle('demo','settle','old-order',PaperSettlementSpec('PHP',D('60'),D('10')),expected_version=1).status == 'CLOSED'
    assert service.get_account('demo').cash == D('210')
    repo.close()
    reopened = Repository(path)
    assert reopened.conn.execute('SELECT COUNT(*) FROM paper_journal_entries').fetchone()[0] == 3
    reopened.close()


@pytest.mark.parametrize('corruption',['cash','reservation','request','foreign_key'])
def test_invalid_v1_migration_rolls_back_all_ddl_and_backfill(tmp_path,corruption):
    path = tmp_path/'v1.db'
    create_v1(path)
    conn = sqlite3.connect(path)
    if corruption == 'cash':
        conn.execute('UPDATE paper_accounts SET cash_minor=19000,peak_equity_minor=19000,current_equity_minor=19000')
    elif corruption == 'reservation':
        conn.execute('UPDATE paper_accounts SET reserved_loss_minor=0')
    elif corruption == 'request':
        conn.execute("UPDATE paper_requests SET payload_json='{}'")
    else:
        conn.execute("UPDATE paper_orders SET opportunity_id='missing'")
    conn.commit()
    before = conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    conn.close()
    with pytest.raises(RuntimeError,match='migration|foreign key'):
        Repository(path)
    conn = sqlite3.connect(path)
    assert conn.execute('SELECT version,checksum FROM paper_schema').fetchone() == (1,V1_CHECKSUM)
    assert conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() == before
    assert conn.execute('SELECT COUNT(*) FROM paper_orders').fetchone()[0] == 1
    conn.close()


def test_missing_history_guard_fails_closed_on_reopen(paper):
    repo, service = paper
    path = repo.path
    with repo.transaction():
        repo.conn.execute('DROP TRIGGER paper_journal_postings_update_blocked')
    repo.close()
    with pytest.raises(RuntimeError,match='incomplete'):
        Repository(path)


def test_backup_retains_filled_position_and_settlement_retries(paper,tmp_path):
    repo, service = paper
    order = reserve(service)
    filled = service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
    backup = sqlite3.connect(tmp_path/'backup.db')
    repo.conn.backup(backup)
    backup.close()
    restored = Repository(tmp_path/'backup.db')
    other = PaperLifecycleService(restored)
    assert other.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0) == filled
    assert other.get_account('demo').open_loss == D('60')
    assert other.settle('demo','settle',order,PaperSettlementSpec('PHP',D('60'),D('20')),expected_version=1).status == 'CLOSED'
    assert other.get_account('demo').cash == D('215')
    restored.close()


def test_gains_do_not_expand_fixed_all_in_deployment_limit(paper):
    repo, service = paper
    seed = service.submit('demo','seed',opportunity('seed',required_capital=D('10')),PaperActionSpec('PHP',D('10'),D('10'),'refundable'))
    service.fill('demo','seed-fill',seed.order_id,PaperFillSpec('PHP',D('10')),expected_version=0)
    service.settle('demo','seed-close',seed.order_id,PaperSettlementSpec('PHP',D('10'),D('30')),expected_version=1)
    order = reserve(service)
    service.fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
    result = service.submit('demo','over-cap',opportunity('over-cap',required_capital=D('30'),expected_cost=D('15')),
                            PaperActionSpec('PHP',D('45'),D('45'),'refundable'))
    assert result.checks['cash_available'] and result.checks['within_deployable_capital']
    assert not result.checks['cash_within_deployable_allocation'] and result.status == 'REJECTED'
    assert service.get_account('demo').deployed_cash == D('65')


def test_expensed_writeoff_and_new_action_account_for_spent_loss(paper):
    repo, service = paper
    service.create_account('risk',limits(max_daily_loss=D('100')))
    first = service.submit('risk','purchase',opportunity('purchase',expected_cost=D('60')),
                           PaperActionSpec('PHP',D('60'),D('60'),'expensed'))
    service.fill('risk','fill',first.order_id,PaperFillSpec('PHP',D('60')),expected_version=0)
    next_action = service.submit('risk','next',opportunity('next',required_capital=D('40')),
                                 PaperActionSpec('PHP',D('40'),D('40'),'refundable'))
    assert next_action.status == 'REJECTED' and not next_action.checks['prospective_daily_loss']
    assert service.settle('risk','writeoff',first.order_id,PaperSettlementSpec('PHP',D('0'),D('0')),expected_version=1).status == 'CLOSED'
    assert service.get_account('risk').cash == D('140')
    assert service.get_account('risk').realized_pnl == D('-60')
    assert service.get_account('risk').open_loss == 0


def test_lifecycle_busy_writer_and_nested_transaction_do_not_commit_caller(paper):
    repo, service = paper
    order = reserve(service)
    other = Repository(repo.path,timeout=.01)
    before = snapshot(repo)
    with pytest.raises(RuntimeError,match='outer rollback'), repo.transaction():
        repo.ledger('caller-pending','credit',D('1'))
        with pytest.raises(RuntimeError,match='already active'):
            service.cancel('demo','cancel',order,expected_version=0)
        with pytest.raises(sqlite3.OperationalError,match='locked'):
            PaperLifecycleService(other).fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0)
        raise RuntimeError('outer rollback')
    assert snapshot(repo) == before
    assert repo.conn.execute('SELECT COUNT(*) FROM ledger').fetchone()[0] == 0
    assert PaperLifecycleService(other).fill('demo','fill',order,PaperFillSpec('PHP',D('60'),D('5')),expected_version=0).status == 'FILLED'
    other.close()


def test_corrupt_deadline_cannot_authorize_early_expiry(paper):
    repo, service = paper
    order = reserve(service,expiry=NOW+timedelta(days=1))
    with repo.transaction():
        repo.conn.execute("UPDATE paper_orders SET expires_at='2000-01-01T00:00:00.000000+00:00' WHERE id=?",(order,))
    assert service.expire('demo','expire',order,expected_version=0).status == 'REJECTED'
    assert service.get_account('demo').status == 'HALTED'
    assert service.get_account('demo').reserved_cash == D('65')
