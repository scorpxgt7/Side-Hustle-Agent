"""Independent reconstruction, cumulative budgets and synthetic payout safety."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from decimal import Decimal as D
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest

from src.persistence.sqlite import Repository
from src.persistence.paper_schema import V2_STATEMENTS,V2_CHECKSUM,SCHEMA_VERSION
from src.core.models import CapitalLimits,Opportunity,OpportunityType
from src.core.pilot import PilotPaperService,PilotPolicy,Evidence
from src.core.paper_accounts import PaperAccountService,PaperActionSpec,RequestConflictError
from src.core.paper_lifecycle import PaperFillSpec,PaperSettlementSpec
from src.core.reconciliation import reconcile_path,reconcile_connection

NOW=datetime(2026,10,12,tzinfo=timezone.utc)


def limits():
    return CapitalLimits(D('500'),D('.5'),D('50'),D('500'),D('500'))


def candidate(identifier='a'):
    return Opportunity(identifier,OpportunityType.OTHER,'synthetic fixture',D('45'),D('100'),D('50'),D('1'),confidence=D('1'))


def evidence(vendor='vendor_fixture',age=0):
    return Evidence('https://example.com/evidence',NOW-timedelta(seconds=age),NOW+timedelta(minutes=5),vendor)


@pytest.fixture
def pilot(tmp_path):
    repo=Repository(tmp_path/'pilot.db')
    service=PilotPaperService(repo,clock=lambda:NOW)
    service.create_account('demo',limits())
    service.create_profile('demo',PilotPolicy(('vendor_fixture',),'route_gotyme_test'))
    yield repo,service
    repo.close()


def experiment(service,key='one',settle=True):
    receipt=service.submit_pilot('demo',key,candidate(key),PaperActionSpec('PHP',D('50'),D('50'),'expensed'),evidence())
    assert receipt.status=='RESERVED'
    assert service.fill('demo','fill-'+key,receipt.order_id,PaperFillSpec('PHP',D('45'),D('5')),expected_version=0).status=='FILLED'
    if settle:
        assert service.settle('demo','settle-'+key,receipt.order_id,PaperSettlementSpec('PHP',D('0'),D('100'),D('5')),expected_version=1).status=='CLOSED'
    return receipt.order_id


def test_independent_report_and_profit_only_payout(pilot):
    repo,service=pilot
    experiment(service)
    before=reconcile_path(repo.path,'demo')
    assert before.ok and before.expected['cash_minor']==54500 and before.expected['realized_daily_pnl_minor']==4500
    payout=service.request_payout('demo','payout','route_gotyme_test',D('35'),D('10'))
    assert payout['status']=='RESERVED'
    assert service.get_account('demo').reserved_cash==D('45')
    assert service.get_account('demo').reserved_loss==D('10')
    assert service.claim_payout('demo','claim',payout['id'])['status']=='SENDING'
    assert service.confirm_payout('demo','pending',payout['id'],'PENDING',provider_id='tr_test')['status']=='PENDING'
    assert service.confirm_payout('demo','done',payout['id'],'SUCCEEDED',provider_id='tr_test',actual_fee=D('10'))['status']=='SUCCEEDED'
    report=reconcile_path(repo.path,'demo')
    assert report.ok and report.distributed_minor==3500
    assert report.expected=={'cash_minor':50000,'reserved_cash_minor':0,'reserved_loss_minor':0,
        'realized_daily_pnl_minor':3500,'current_equity_minor':53500,'peak_equity_minor':54500}
    # Owner distribution is not a trading loss: only the 10 fee causes drawdown.
    state=service.get_account('demo')
    assert state.peak_equity-state.current_equity==D('10')
    assert service.request_payout('demo','too-much','route_gotyme_test',D('1'),D('0'))['status']=='REJECTED'
    experiment(service,'second')
    assert service.get_account('demo').cash==D('545')
    assert reconcile_path(repo.path,'demo').ok


def test_report_does_not_call_writer_projection_code(pilot,monkeypatch):
    repo,service=pilot
    experiment(service)
    from src.core import paper_journal
    monkeypatch.setattr(paper_journal,'journal_projection',lambda *_:(_ for _ in ()).throw(AssertionError('writer called')))
    monkeypatch.setattr(paper_journal,'projection_matches',lambda *_:False)
    assert reconcile_path(repo.path,'demo').ok


def test_read_only_report_cannot_create_or_modify_database(pilot,tmp_path):
    repo,service=pilot
    experiment(service,settle=False)
    with repo.transaction():
        repo.conn.execute("UPDATE paper_accounts SET cash_minor=44000 WHERE id='demo'")
    before=[tuple(r) for r in repo.conn.execute('SELECT * FROM paper_accounts')]
    report=reconcile_path(repo.path,'demo')
    assert not report.ok and 'projection_cash_minor' in report.discrepancies
    assert [tuple(r) for r in repo.conn.execute('SELECT * FROM paper_accounts')]==before
    assert service.get_account('demo').status=='HALTED'
    missing=tmp_path/'does-not-exist.db'
    with pytest.raises(sqlite3.OperationalError):
        reconcile_path(missing,'demo')
    assert not missing.exists()
    with pytest.raises(RuntimeError,match='snapshot'):
        reconcile_connection(repo.conn,'demo')


@pytest.mark.parametrize('change,check',[
    ({'vendor':'vendor_other'},'pilot_vendor_approved'),({'age':901},'pilot_evidence_fresh'),
    ({'age':-1},'pilot_evidence_fresh')])
def test_unapproved_or_stale_evidence_is_not_automated(pilot,change,check):
    repo,service=pilot
    result=service.submit_pilot('demo','bad',candidate(),PaperActionSpec('PHP',D('50'),D('50'),'expensed'),evidence(**change))
    assert result.status=='REJECTED' and not result.checks[check]
    assert service.get_account('demo').cash==D('500')


def test_expired_evidence_blocks_fill_and_retry_is_historical(pilot):
    repo,service=pilot
    result=service.submit_pilot('demo','one',candidate(),PaperActionSpec('PHP',D('50'),D('50'),'expensed'),evidence())
    service.clock=lambda:NOW+timedelta(minutes=5)
    assert service.fill('demo','late',result.order_id,PaperFillSpec('PHP',D('45'),D('5')),expected_version=0).status=='REJECTED'
    assert service.get_account('demo').reserved_cash==D('50')
    assert service.submit_pilot('demo','one',candidate(),PaperActionSpec('PHP',D('50'),D('50'),'expensed'),evidence())==result


def test_lifetime_spending_does_not_reset_after_gains(pilot):
    repo,service=pilot
    for n in range(9):
        experiment(service,str(n))  # 9 * (50 entry + 5 exit) = 495 spent.
    result=service.submit_pilot('demo','over',candidate('over'),PaperActionSpec('PHP',D('50'),D('50'),'expensed'),evidence())
    assert result.status=='REJECTED' and not result.checks['pilot_lifetime_spend']
    assert service.get_account('demo').cash==D('905')
    assert reconcile_path(repo.path,'demo').ok


def test_pilot_profile_is_immutable_and_cannot_hold_payment_details(pilot):
    repo,service=pilot
    with pytest.raises(RequestConflictError):
        service.create_profile('demo',PilotPolicy(('vendor_other',),'route_gotyme_test'))
    with pytest.raises(ValueError):
        PilotPolicy(('4111111111111111',),'route_gotyme_test')
    with pytest.raises(ValueError):
        PilotPolicy(('vendor_fixture',),'4111111111111111')
    with pytest.raises(ValueError):
        PilotPolicy(('vendor_fixture',),'route_gotyme_test',lifetime_spend_minor=50001)
    with pytest.raises(ValueError):
        Evidence('https://example.com/evidence?secret=1',NOW,NOW+timedelta(minutes=5),'vendor_fixture')
    with repo.transaction(),pytest.raises(sqlite3.IntegrityError,match='immutable'):
        repo.conn.execute("UPDATE pilot_profiles SET policy_json='{}'")


@pytest.mark.parametrize('route,amount,fee',[('route_other','35','10'),('route_gotyme_test','36','10'),('route_gotyme_test','1','11')])
def test_payout_route_profit_and_fee_limits(pilot,route,amount,fee):
    repo,service=pilot
    experiment(service)
    assert service.request_payout('demo','bad',route,D(amount),D(fee))['status']=='REJECTED'
    assert service.get_account('demo').reserved_cash==0


def test_open_experiment_or_halt_blocks_payout(pilot):
    repo,service=pilot
    experiment(service)
    experiment(service,'open',settle=False)
    assert service.request_payout('demo','open','route_gotyme_test',D('1'),D('0'))['status']=='REJECTED'
    service.halt('demo','operator')
    assert service.request_payout('demo','halted','route_gotyme_test',D('1'),D('0'))['status']=='REJECTED'


def test_unknown_payout_retains_holds_and_can_only_be_reconciled(pilot):
    repo,service=pilot
    experiment(service)
    p=service.request_payout('demo','payout','route_gotyme_test',D('35'),D('10'))
    first=service.claim_payout('demo','claim',p['id'])
    assert service.claim_payout('demo','claim',p['id'])==first
    assert service.claim_payout('demo','another-claim',p['id'])['status']=='REJECTED'
    unknown=service.confirm_payout('demo','unknown',p['id'],'UNKNOWN')
    assert unknown['status']=='UNKNOWN' and service.get_account('demo').status=='HALTED'
    assert service.get_account('demo').reserved_cash==D('45')
    assert reconcile_path(repo.path,'demo').ok
    with pytest.raises(RequestConflictError):
        service.confirm_payout('demo','unknown',p['id'],'SUCCEEDED',provider_id='tr_test')
    assert service.confirm_payout('demo','read-confirmed',p['id'],'SUCCEEDED',provider_id='tr_test',actual_fee=D('10'))['status']=='SUCCEEDED'
    assert service.get_account('demo').status=='HALTED'
    assert service.get_account('demo').reserved_cash==0
    assert reconcile_path(repo.path,'demo').ok


@pytest.mark.parametrize('outcome,provider,fee',[('SUCCEEDED',None,'10'),('SUCCEEDED','tr_test','11'),('FAILED','tr_test','1')])
def test_unverified_or_excess_fee_outcomes_cannot_release_holds(pilot,outcome,provider,fee):
    repo,service=pilot
    experiment(service)
    p=service.request_payout('demo','payout','route_gotyme_test',D('35'),D('10'))
    service.claim_payout('demo','claim',p['id'])
    result=service.confirm_payout('demo','response',p['id'],outcome,provider_id=provider,actual_fee=D(fee))
    assert result['status']=='UNKNOWN'
    assert service.get_account('demo').reserved_cash==D('45')
    assert service.get_account('demo').cash==D('545')


def test_confirmed_failure_releases_cash_without_fabricating_income(pilot):
    repo,service=pilot
    experiment(service)
    p=service.request_payout('demo','payout','route_gotyme_test',D('35'),D('10'))
    service.claim_payout('demo','claim',p['id'])
    assert service.confirm_payout('demo','failure',p['id'],'FAILED',provider_id='tr_test')['status']=='FAILED'
    assert service.get_account('demo').reserved_cash==0
    assert service.get_account('demo').cash==D('545')
    assert reconcile_path(repo.path,'demo').ok


@pytest.mark.parametrize('kind',['request','claim','confirm'])
def test_payout_audit_failure_is_atomic(pilot,kind,monkeypatch):
    repo,service=pilot
    experiment(service)
    p=None
    if kind!='request':
        p=service.request_payout('demo','payout','route_gotyme_test',D('35'),D('10'))
    if kind=='confirm':
        service.claim_payout('demo','claim',p['id'])
    tables=('paper_accounts','paper_payouts','paper_payout_events','paper_journal_entries','paper_journal_postings','audit_events')
    before={t:[tuple(r) for r in repo.conn.execute('SELECT * FROM '+t)] for t in tables}
    monkeypatch.setattr(repo,'audit',lambda *_:(_ for _ in ()).throw(RuntimeError('injected')))
    with pytest.raises(RuntimeError,match='injected'):
        if kind=='request':
            service.request_payout('demo','payout','route_gotyme_test',D('35'),D('10'))
        elif kind=='claim':
            service.claim_payout('demo','claim',p['id'])
        else:
            service.confirm_payout('demo','success',p['id'],'SUCCEEDED',provider_id='tr_test',actual_fee=D('10'))
    assert {t:[tuple(r) for r in repo.conn.execute('SELECT * FROM '+t)] for t in tables}==before


def test_independent_connections_cannot_double_claim_profit(pilot):
    repo,service=pilot
    experiment(service)
    barrier=Barrier(2)
    def worker(n):
        other=Repository(repo.path,timeout=3)
        try:
            barrier.wait(timeout=5)
            return PilotPaperService(other).request_payout('demo',str(n),'route_gotyme_test',D('35'),D('10'))
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(worker,(1,2)))
    assert sorted(r['status'] for r in results)==['REJECTED','RESERVED']
    assert service.get_account('demo').reserved_cash==D('45')
    assert reconcile_path(repo.path,'demo').ok


def create_v2(path):
    repo=Repository.__new__(Repository)
    repo.path=path
    repo.conn=sqlite3.connect(path,isolation_level='DEFERRED')
    repo.conn.row_factory=sqlite3.Row
    repo.conn.execute('PRAGMA foreign_keys=ON')
    repo._init_schema()
    for statement in V2_STATEMENTS:
        repo.conn.execute(statement)
    repo.conn.execute('INSERT INTO paper_schema VALUES (1,2,?)',(V2_CHECKSUM,))
    repo.conn.commit()
    PaperAccountService(repo).create_account('legacy',limits())
    repo.close()


def test_v2_migration_preserves_journals_and_guards(tmp_path):
    path=tmp_path/'v2.db'
    create_v2(path)
    old=sqlite3.connect(path)
    before=old.execute('SELECT * FROM paper_journal_entries').fetchall()
    old.close()
    repo=Repository(path)
    assert repo.conn.execute('SELECT version FROM paper_schema').fetchone()[0]==SCHEMA_VERSION
    assert [tuple(r) for r in repo.conn.execute('SELECT * FROM paper_journal_entries')]==before
    assert reconcile_path(path,'legacy').ok
    assert repo.conn.execute('PRAGMA foreign_key_check').fetchall()==[]
    repo.close()


def test_bad_v2_migration_leaves_all_original_history_intact(tmp_path):
    path=tmp_path/'v2.db'
    create_v2(path)
    conn=sqlite3.connect(path)
    conn.execute("UPDATE paper_accounts SET cash_minor=49000 WHERE id='legacy'")
    conn.commit()
    before=conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
    conn.close()
    with pytest.raises(RuntimeError,match='v2 history'):
        Repository(path)
    conn=sqlite3.connect(path)
    assert conn.execute('SELECT version,checksum FROM paper_schema').fetchone()==(2,V2_CHECKSUM)
    assert conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()==before
    conn.close()


def test_legacy_submission_api_cannot_bypass_persisted_pilot_budget(pilot):
    from src.core.paper_accounts import canonical_json
    repo,service=pilot
    for n in range(9):
        experiment(service,str(n))
    original=candidate('bypass')
    ev=evidence()
    from src.core.paper_lifecycle import utc_timestamp
    source=canonical_json({'source_url':ev.source_url,'observed_at':utc_timestamp(ev.observed_at),
                           'expires_at':utc_timestamp(ev.expires_at),'vendor_ref':ev.vendor_ref})
    legacy=PaperAccountService(repo,clock=lambda:NOW)
    result=legacy.submit('demo','legacy',replace(original,source=source),PaperActionSpec('PHP',D('50'),D('50'),'expensed'),expires_at=ev.expires_at)
    assert result.status=='REJECTED' and not result.checks['pilot_lifetime_spend']


def test_legacy_payout_api_cannot_change_a_pilot_route(pilot):
    from src.core.paper_payouts import PaperPayoutService
    repo,service=pilot
    experiment(service)
    assert PaperPayoutService(repo).request_payout('demo','bypass','route_other',D('35'),D('10'))['status']=='REJECTED'
    assert service.get_account('demo').reserved_cash==0


def test_legacy_lifecycle_api_cannot_exceed_pilot_exit_fee_budget(tmp_path):
    from src.core.paper_lifecycle import PaperLifecycleService
    repo=Repository(tmp_path/'limited.db')
    service=PilotPaperService(repo,clock=lambda:NOW)
    service.create_account('demo',limits())
    service.create_profile('demo',PilotPolicy(('vendor_fixture',),'route_gotyme_test',lifetime_spend_minor=5000))
    order=experiment(service,settle=False)
    result=PaperLifecycleService(repo,clock=lambda:NOW).settle('demo','bypass',order,PaperSettlementSpec('PHP',D('0'),D('100'),D('1')),expected_version=1)
    assert result.status=='REJECTED' and 'lifetime' in result.reason
    assert service.get_account('demo').cash==D('450')
    repo.close()


def test_corrupt_pilot_audit_halts_even_through_older_api(pilot):
    repo,service=pilot
    with repo.transaction():
        repo.conn.execute("UPDATE audit_events SET payload_json='{}' WHERE event_type='paper_pilot_profile'")
    report=reconcile_path(repo.path,'demo')
    assert not report.ok and 'pilot_policy_audit' in report.discrepancies
    assert PaperAccountService(repo).get_account('demo').status=='HALTED'
