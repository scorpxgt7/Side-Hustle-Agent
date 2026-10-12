"""Independent read-only reconstruction. Never repairs a balance or changes a halt."""
from dataclasses import asdict, dataclass
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
import json
from pathlib import Path
from math import ceil
import sqlite3

from .models import validate_identifier


def cents(value):
    value = Decimal(value)
    if not value.is_finite():
        raise ValueError('nonfinite history')
    scaled = Fraction(value)*100
    if scaled.denominator != 1:
        raise ValueError('fractional cent in history')
    return scaled.numerator


@dataclass(frozen=True)
class ReconciliationReport:
    account_id: str
    ok: bool
    currency: str
    expected: dict
    actual: dict
    discrepancies: tuple[str,...]
    journal_entries: int
    open_orders: int
    distributed_minor: int
    open_cash_minor: int = 0
    open_loss_minor: int = 0
    protected_cash_minor: int = 0
    spendable_cash_minor: int = 0
    scope: str = 'INTERNAL_PAPER_ONLY'

    def as_dict(self):
        return asdict(self)


def reconcile_path(path,account_id):
    """Opening this function cannot create, migrate or write to a database."""
    validate_identifier(account_id,'account_id')
    connection = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        from src.persistence.paper_schema import validate_paper_schema
        if validate_paper_schema(connection) != 3:
            raise RuntimeError('reconciliation requires an already migrated v3 database')
        return reconcile_connection(connection,account_id)
    finally:
        connection.close()


def reconcile_connection(conn,account_id):
    """Call inside an existing read/write snapshot; does not own/commit the transaction."""
    validate_identifier(account_id,'account_id')
    if not conn.in_transaction:
        raise RuntimeError('reconciliation requires a consistent transaction snapshot')
    row = conn.execute('SELECT * FROM paper_accounts WHERE id=?',(account_id,)).fetchone()
    if row is None:
        raise KeyError('unknown paper account')
    issues = []
    def fail(code,ref=''):
        issues.append(code+(':'+str(ref) if ref else ''))
    actual = {field:row[field] for field in ('cash_minor','reserved_cash_minor','reserved_loss_minor',
              'realized_daily_pnl_minor','current_equity_minor','peak_equity_minor')}
    balances = {'cash':0,'asset':0,'equity':0,'income':0,'expense':0}
    capital = peak = reserved_cash = reserved_loss = distribution = 0
    expected_entries = {}
    open_orders = open_cash = open_loss = protected = 0
    entry_count = conn.execute('SELECT COUNT(*) FROM paper_journal_entries WHERE account_id=?',(account_id,)).fetchone()[0]
    try:
        genesis = conn.execute('SELECT * FROM audit_events WHERE event_id=?',(row['genesis_event_id'],)).fetchone()
        audit = json.loads(genesis['payload_json'])
        policy = json.loads(row['limits_json'])
        capital = cents(policy['total_capital'])
        protected = ceil(capital * Fraction(Decimal(policy['reserve_ratio'])))
        if (genesis['event_type']!='paper_account_created' or genesis['entity_id']!=account_id or audit['synthetic'] is not True
                or audit['currency']!=row['currency'] or audit['cash_minor']!=capital
                or audit['limits']!=json.loads(row['limits_json'])):
            fail('genesis_mismatch')
        expected_entries[row['genesis_event_id']] = ('GENESIS',None,[['cash','debit',capital],['equity','credit',capital]])
        profile=conn.execute('SELECT * FROM pilot_profiles WHERE account_id=?',(account_id,)).fetchone()
        if profile is not None:
            profile_audit=conn.execute('SELECT * FROM audit_events WHERE event_id=?',(profile['audit_event_id'],)).fetchone()
            if profile_audit is None or profile_audit['event_type']!='paper_pilot_profile' or profile_audit['entity_id']!=account_id or json.loads(profile_audit['payload_json'])!={'synthetic':True,'policy':json.loads(profile['policy_json'])}:
                fail('pilot_policy_audit')
        orders = conn.execute('SELECT * FROM paper_orders WHERE account_id=?',(account_id,)).fetchall()
        reservations = {r['order_id']:r for r in conn.execute('SELECT * FROM capital_reservations WHERE account_id=?',(account_id,))}
        positions = {p['order_id']:p for p in conn.execute('SELECT * FROM paper_positions WHERE account_id=?',(account_id,))}
        if not set(reservations) <= {o['id'] for o in orders} or not set(positions) <= {o['id'] for o in orders}:
            fail('orphan_commitment')
        for order in orders:
            ref = order['id']
            request = conn.execute('SELECT * FROM paper_requests WHERE account_id=? AND request_id=?',(account_id,order['request_id'])).fetchone()
            original = json.loads(request['payload_json'])
            receipt = json.loads(request['result_json'])
            if sha256(request['payload_json'].encode()).hexdigest()!=request['payload_digest']:
                fail('request_digest',ref)
            if receipt['order_id']!=ref or receipt['status']!='RESERVED' or original['opportunity']['id']!=order['opportunity_id'] or original['action']['currency']!=row['currency'] or original.get('expires_at')!=order['expires_at']:
                fail('order_request_identity',ref)
            decision = conn.execute('SELECT * FROM decisions WHERE id=?',(order['decision_id'],)).fetchone()
            binding = conn.execute('SELECT currency FROM paper_observation_currencies WHERE opportunity_id=?',(order['opportunity_id'],)).fetchone()
            if (decision is None or decision['opportunity_id']!=order['opportunity_id'] or decision['decision']!='paper_test'
                    or binding is None or binding[0]!=row['currency']):
                fail('decision_currency_link',ref)
            effects = conn.execute('SELECT * FROM paper_lifecycle_events WHERE account_id=? AND order_id=? ORDER BY rowid',(account_id,ref)).fetchall()
            current_status,version = 'RESERVED',0
            principal = entry_fee = entry_spent = 0
            treatment = original['action']['capital_treatment']
            for effect in effects:
                command,result = json.loads(effect['payload_json']),json.loads(effect['result_json'])
                evidence = conn.execute('SELECT * FROM audit_events WHERE event_id=?',(effect['audit_event_id'],)).fetchone()
                recorded = json.loads(evidence['payload_json'])
                if (sha256(effect['payload_json'].encode()).hexdigest()!=effect['payload_digest']
                        or evidence['event_type']!='paper_lifecycle' or evidence['entity_id']!=ref
                        or recorded['command']!=command or recorded['result']!=result
                        or command['kind']!=effect['kind'] or command['order_id']!=ref
                        or result['account_id']!=account_id or result['order_id']!=ref or result['event_key']!=effect['event_key']):
                    fail('lifecycle_audit',effect['event_key'])
                if result['status']=='REJECTED':
                    continue
                kind = command['kind']
                if command['expected_version']!=version:
                    fail('transition_version',effect['event_key'])
                version += 1
                if kind=='FILL' and current_status=='RESERVED':
                    spec = command['spec']
                    principal,entry_fee = cents(spec['capital']),cents(spec['fees'])
                    entry_spent = principal+entry_fee
                    if principal!=cents(original['opportunity']['required_capital']) or entry_spent>cents(original['action']['cash_required']) or spec['currency']!=row['currency']:
                        fail('fill_contract',ref)
                    expected_entries[effect['audit_event_id']] = ('FILL',ref,
                        [['asset' if treatment=='refundable' else 'expense','debit',principal],['expense','debit',entry_fee],['cash','credit',entry_spent]])
                    current_status = 'FILLED'
                elif kind=='SETTLE' and current_status=='FILLED':
                    spec = command['spec']
                    returned,revenue,fee = (cents(spec[f]) for f in ('capital_return','revenue','fees'))
                    gross = returned+revenue
                    if spec['currency']!=row['currency'] or fee>gross or returned>principal or (treatment=='expensed' and returned):
                        fail('settlement_contract',ref)
                    lines = [['cash','debit',gross]]
                    if treatment=='refundable':
                        lines.append(['asset','credit',principal])
                        net = gross-principal
                        lines.append(['income','credit',net] if net>=0 else ['expense','debit',-net])
                    else:
                        lines.append(['income','credit',gross])
                    lines += [['expense','debit',fee],['cash','credit',fee]]
                    expected_entries[effect['audit_event_id']] = ('SETTLEMENT',ref,lines)
                    p = positions.get(ref)
                    if p is None or (p['returned_minor'],p['revenue_minor'],p['exit_fees_minor'],p['pnl_minor'])!=(returned,revenue,fee,gross-fee-entry_spent):
                        fail('closed_position',ref)
                    current_status = 'CLOSED'
                elif kind in ('CANCEL','EXPIRE') and current_status=='RESERVED':
                    current_status = 'CANCELLED' if kind=='CANCEL' else 'EXPIRED'
                    if kind=='EXPIRE' and (not order['expires_at'] or recorded['observed_at']<order['expires_at']):
                        fail('early_expiry',ref)
                else:
                    fail('invalid_transition',effect['event_key'])
                if result['status']!=current_status or result['order_version']!=version:
                    fail('transition_receipt',effect['event_key'])
            if order['status']!=current_status or order['version']!=version:
                fail('order_projection',ref)
            reservation,position = reservations.get(ref),positions.get(ref)
            if current_status=='RESERVED':
                open_orders += 1
                cash,loss = cents(original['action']['cash_required']),cents(original['action']['worst_case_loss'])
                reserved_cash += cash
                reserved_loss += loss
                if reservation is None or (reservation['cash_minor'],reservation['loss_minor'])!=(cash,loss) or position is not None:
                    fail('reservation_projection',ref)
            else:
                if reservation is not None:
                    fail('closed_reservation',ref)
                if current_status in ('FILLED','CLOSED'):
                    expense = entry_fee+(principal if treatment=='expensed' else 0)
                    if position is None or (position['capital_minor'],position['fees_minor'],position['cash_spent_minor'],position['entry_expense_minor'],position['loss_minor'],position['treatment'],position['status'])!=(principal,entry_fee,entry_spent,expense,cents(original['action']['worst_case_loss'])-expense,treatment,'OPEN' if current_status=='FILLED' else 'CLOSED'):
                        fail('position_projection',ref)
                    if current_status=='FILLED':
                        open_orders += 1
                        open_cash += entry_spent
                        open_loss += cents(original['action']['worst_case_loss'])-expense
                elif position is not None:
                    fail('unexpected_position',ref)
        payouts = {r['id']:dict(r) for r in conn.execute('SELECT * FROM paper_payouts WHERE account_id=?',(account_id,))}
        reconstructed = {}
        for event in conn.execute('SELECT * FROM paper_payout_events WHERE account_id=? ORDER BY rowid',(account_id,)):
            command,result = json.loads(event['payload_json']),json.loads(event['result_json'])
            evidence = conn.execute('SELECT * FROM audit_events WHERE event_id=?',(event['audit_event_id'],)).fetchone()
            recorded = json.loads(evidence['payload_json'])
            if evidence['event_type']!='paper_payout' or evidence['entity_id']!=account_id or recorded['command']!=command or recorded['result']!=result:
                fail('payout_audit',event['event_key'])
            if result['status']=='REJECTED':
                continue
            old = reconstructed.get(result['id'])
            if event['kind']=='REQUEST':
                if old or result['status']!='RESERVED' or result['version']!=0 or (result['amount_minor'],result['fee_cap_minor'],result['route_ref'])!=(command['amount_minor'],command['fee_cap_minor'],command['route_ref']):
                    fail('payout_request',event['event_key'])
            elif old is None or result['version']!=old['version']+1:
                fail('payout_version',event['event_key'])
            reconstructed[result['id']] = result
            if result['status']=='SUCCEEDED':
                amount,fee = result['amount_minor'],result['actual_fee_minor']
                if type(fee) is not int or not 0<=fee<=result['fee_cap_minor']:
                    fail('payout_fee',result['id'])
                expected_entries[event['audit_event_id']] = ('PAYOUT',None,[['equity','debit',amount],['expense','debit',fee],['cash','credit',amount+fee]])
                distribution += amount
        if payouts!=reconstructed:
            fail('payout_projection')
        for p in reconstructed.values():
            if p['status'] in ('RESERVED','SENDING','PENDING','UNKNOWN'):
                reserved_cash += p['amount_minor']+p['fee_cap_minor']
                reserved_loss += p['fee_cap_minor']
        checkout_rows = {r['id']:dict(r) for r in conn.execute('SELECT * FROM sandbox_checkouts WHERE account_id=?',(account_id,))}
        checkout_snapshots = {}
        for event in conn.execute('SELECT * FROM sandbox_provider_events WHERE account_id=? ORDER BY rowid',(account_id,)):
            evidence=conn.execute('SELECT * FROM audit_events WHERE event_id=?',(event['audit_event_id'],)).fetchone()
            record=json.loads(evidence['payload_json'])
            result=json.loads(event['result_json'])
            if evidence['event_type']!='sandbox_provider' or evidence['entity_id']!=account_id or record['operation_id']!=event['operation_id'] or record['kind']!=event['kind'] or record['result']!=result or record['mode'] not in ('SANDBOX','OFFLINE_FIXTURE'):
                fail('sandbox_provider_audit',event['event_key'])
            if event['kind'].startswith('CHECKOUT_') or event['kind']=='FIXTURE_CLEARED':
                checkout_snapshots[event['operation_id']]=result
        if checkout_rows!=checkout_snapshots:
            fail('sandbox_checkout_projection')
        for checkout in checkout_rows.values():
            if checkout['status']=='CLEARED_FIXTURE' and conn.execute('SELECT status FROM paper_orders WHERE id=? AND account_id=?',(checkout['order_id'],account_id)).fetchone()[0]!='CLOSED':
                fail('fixture_order_not_closed',checkout['id'])
        observed_entries = set()
        for entry in conn.execute('SELECT * FROM paper_journal_entries WHERE account_id=? ORDER BY id',(account_id,)):
            ref = entry['event_id']
            observed_entries.add(ref)
            lines = conn.execute('SELECT line,bucket,side,amount_minor FROM paper_journal_postings WHERE entry_id=? ORDER BY line',(entry['id'],)).fetchall()
            postings = [[line['bucket'],line['side'],line['amount_minor']] for line in lines]
            if entry['currency']!=row['currency'] or [r['line'] for r in lines]!=list(range(len(lines))) or postings!=json.loads(entry['postings_json']):
                fail('journal_manifest',entry['id'])
            if expected_entries.get(ref)!=(entry['kind'],entry['order_id'],postings):
                fail('journal_evidence',entry['id'])
            if sum(a if side=='debit' else -a for _,side,a in postings)!=0:
                fail('unbalanced_journal',entry['id'])
            for bucket,side,amount in postings:
                if bucket not in balances or side not in ('debit','credit') or type(amount) is not int or amount<0:
                    raise ValueError('invalid posting')
                balances[bucket] += amount if side=='debit' else -amount
            performance = balances['cash']+balances['asset']+balances['equity']+capital
            peak = max(peak,performance)
        if observed_entries!=set(expected_entries):
            fail('missing_journal_evidence')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            fail('foreign_keys')
    except (ValueError,TypeError,KeyError,IndexError) as error:
        fail('malformed_history',type(error).__name__)
    expected = {'cash_minor':balances['cash'],'reserved_cash_minor':reserved_cash,'reserved_loss_minor':reserved_loss,
                'realized_daily_pnl_minor':-balances['income']-balances['expense'],
                'current_equity_minor':balances['cash']+balances['asset']+distribution,'peak_equity_minor':peak}
    for field,value in expected.items():
        if actual[field]!=value:
            fail('projection_'+field)
    return ReconciliationReport(account_id,not issues,row['currency'],expected,actual,tuple(issues),entry_count,open_orders,distribution,open_cash,open_loss,protected,
                                max(0,balances['cash']-protected-reserved_cash) if not issues and row['status']=='ACTIVE' else 0)
