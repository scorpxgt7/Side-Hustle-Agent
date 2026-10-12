"""Full synthetic fills and settlement. Stable keys recover committed responses."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from uuid import uuid4

from .paper_accounts import (MAX_MINOR_UNITS, PaperAccountService, RequestConflictError,
                             canonical_json, minor_units, money, validate_currency)
from .models import validate_identifier
from .paper_journal import fill_postings, journal_projection, post_journal, settlement_postings

LIFECYCLE_POLICY_VERSION = 'paper-lifecycle-v1'


def utc_timestamp(value=None):
    value = datetime.now(timezone.utc) if value is None else value
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('paper deadlines/clocks require an aware datetime')
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


def nonnegative(value):
    amount = minor_units(value)
    if amount < 0:
        raise ValueError('paper cash flows must be nonnegative')
    return amount


@dataclass(frozen=True)
class PaperFillSpec:
    currency: str
    capital: Decimal
    fees: Decimal = Decimal('0')

    def __post_init__(self):
        validate_currency(self.currency)
        capital, fees = nonnegative(self.capital), nonnegative(self.fees)
        if capital <= 0 or capital + fees > MAX_MINOR_UNITS:
            raise ValueError('fill capital must be positive and full cash flow must fit storage')


@dataclass(frozen=True)
class PaperSettlementSpec:
    currency: str
    capital_return: Decimal
    revenue: Decimal
    fees: Decimal = Decimal('0')

    def __post_init__(self):
        validate_currency(self.currency)
        returned, revenue, fees = (nonnegative(v) for v in (self.capital_return, self.revenue, self.fees))
        if returned + revenue > MAX_MINOR_UNITS or fees > returned + revenue:
            raise ValueError('settlement must fit storage; exit fees must be deducted from gross proceeds')


@dataclass(frozen=True)
class PaperOrderState:
    account_id: str
    order_id: str
    status: str
    version: int
    expires_at: str | None
    position_status: str | None
    cash_spent: Decimal
    open_loss: Decimal
    realized_pnl: Decimal | None


@dataclass(frozen=True)
class LifecycleResult:
    account_id: str
    event_key: str
    order_id: str
    status: str
    reason: str
    order_status: str
    order_version: int
    account_version: int
    cash_change: Decimal
    pnl_change: Decimal
    journal_id: int | None


class PaperLifecycleService(PaperAccountService):
    def __init__(self, repository, *, mode='PAPER', clock=None):
        super().__init__(repository, mode=mode, clock=clock)

    def _order(self, account_id, order_id):
        row = self.repository.conn.execute('SELECT * FROM paper_orders WHERE id=? AND account_id=?', (order_id, account_id)).fetchone()
        if row is None:
            raise KeyError('unknown paper order in this account')
        position = self.repository.conn.execute('SELECT * FROM paper_positions WHERE order_id=? AND account_id=?', (order_id, account_id)).fetchone()
        return row, position

    def get_order(self, account_id, order_id):
        validate_identifier(account_id, 'account_id')
        validate_identifier(order_id, 'order_id')
        with self.repository.transaction():
            row, limits = self._load(account_id)
            if not self._integrity(row, limits):
                self._halt(row, 'paper projection does not match journal/order records')
            order, position = self._order(account_id, order_id)
            return PaperOrderState(account_id, order_id, order['status'], order['version'], order['expires_at'],
                position['status'] if position else None, money(position['cash_spent_minor'] if position else 0),
                money(position['loss_minor'] if position and position['status'] == 'OPEN' else 0),
                money(position['pnl_minor']) if position and position['status'] == 'CLOSED' else None)

    def fill(self, account_id, event_key, order_id, spec: PaperFillSpec, *, expected_version: int):
        if not isinstance(spec, PaperFillSpec):
            raise ValueError('full fill requires PaperFillSpec')
        return self._command(account_id, event_key, order_id, 'FILL', expected_version, spec)

    def cancel(self, account_id, event_key, order_id, *, expected_version: int):
        return self._command(account_id, event_key, order_id, 'CANCEL', expected_version)

    def expire(self, account_id, event_key, order_id, *, expected_version: int):
        return self._command(account_id, event_key, order_id, 'EXPIRE', expected_version)

    def settle(self, account_id, event_key, order_id, spec: PaperSettlementSpec, *, expected_version: int):
        if not isinstance(spec, PaperSettlementSpec):
            raise ValueError('full settlement requires PaperSettlementSpec')
        return self._command(account_id, event_key, order_id, 'SETTLE', expected_version, spec)

    def _command(self, account_id, event_key, order_id, kind, expected_version, spec=None):
        for name, value in (('account_id',account_id), ('event_key',event_key), ('order_id',order_id)):
            validate_identifier(value, name)
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError('expected_version must be a nonnegative integer')
        payload = canonical_json({'order_id':order_id, 'kind':kind, 'expected_version':expected_version,
                                  'spec':asdict(spec) if spec else None, 'policy_version':LIFECYCLE_POLICY_VERSION})
        digest = sha256(payload.encode()).hexdigest()
        conn = self.repository.conn
        with self.repository.transaction():
            prior = conn.execute('SELECT * FROM paper_lifecycle_events WHERE account_id=? AND event_key=?', (account_id, event_key)).fetchone()
            if prior:
                if prior['payload_digest'] != digest or prior['payload_json'] != payload:
                    raise RequestConflictError('lifecycle key was reused with conflicting inputs')
                restored = json.loads(prior['result_json'])
                for field in ('cash_change','pnl_change'):
                    restored[field] = Decimal(restored[field])
                return LifecycleResult(**restored)
            row, limits = self._load(account_id)
            order, position = self._order(account_id, order_id)
            integrity = self._integrity(row, limits)
            if not integrity:
                self._halt(row, 'paper projection does not match journal/order records')
                row, limits = self._load(account_id)
            now = utc_timestamp(self.clock())
            reason = self._reject_reason(row, order, position, integrity, kind, expected_version, spec, now)
            event_id = str(uuid4())
            journal_id = None
            cash_change = pnl_change = 0
            if reason is None:
                if kind in ('CANCEL','EXPIRE','FILL'):
                    reserved = conn.execute('SELECT * FROM capital_reservations WHERE order_id=?', (order_id,)).fetchone()
                    conn.execute('DELETE FROM capital_reservations WHERE order_id=?', (order_id,))
                    conn.execute('''UPDATE paper_accounts SET reserved_cash_minor=reserved_cash_minor-?,
                        reserved_loss_minor=reserved_loss_minor-? WHERE id=?''',
                        (reserved['cash_minor'], reserved['loss_minor'], account_id))
                if kind == 'FILL':
                    original = json.loads(conn.execute('SELECT payload_json FROM paper_requests WHERE account_id=? AND request_id=?', (account_id, order['request_id'])).fetchone()[0])
                    treatment = original['action']['capital_treatment']
                    capital, fees = minor_units(spec.capital), minor_units(spec.fees)
                    entry_expense = fees + (capital if treatment == 'expensed' else 0)
                    conn.execute('''INSERT INTO paper_positions(order_id,account_id,treatment,capital_minor,fees_minor,
                        cash_spent_minor,entry_expense_minor,loss_minor,status) VALUES (?,?,?,?,?,?,?,?,'OPEN')''',
                        (order_id,account_id,treatment,capital,fees,capital+fees,entry_expense,reserved['loss_minor']-entry_expense))
                    postings = fill_postings(treatment, capital, fees)
                    journal_id = post_journal(self.repository, account_id, event_id, 'FILL', order_id, postings)
                    cash_change, pnl_change = -capital-fees, -entry_expense
                elif kind == 'SETTLE':
                    returned, revenue, fees = (minor_units(v) for v in (spec.capital_return,spec.revenue,spec.fees))
                    postings = settlement_postings(position['treatment'],position['capital_minor'],returned,revenue,fees)
                    journal_id = post_journal(self.repository, account_id, event_id, 'SETTLEMENT', order_id, postings)
                    cash_change = returned + revenue - fees
                    pnl_change = cash_change - (position['capital_minor'] if position['treatment']=='refundable' else 0)
                    conn.execute('''UPDATE paper_positions SET status='CLOSED',returned_minor=?,revenue_minor=?,
                        exit_fees_minor=?,pnl_minor=? WHERE order_id=? AND status='OPEN' ''',
                        (returned,revenue,fees,pnl_change-position['entry_expense_minor'],order_id))
                status = {'FILL':'FILLED','CANCEL':'CANCELLED','EXPIRE':'EXPIRED','SETTLE':'CLOSED'}[kind]
                changed = conn.execute('UPDATE paper_orders SET status=?,version=version+1 WHERE id=? AND version=?', (status,order_id,expected_version))
                if changed.rowcount != 1:
                    raise RuntimeError('paper order changed inside owned transaction')
                balances, peak, _ = journal_projection(conn, account_id)
                changed = conn.execute('''UPDATE paper_accounts SET cash_minor=?,current_equity_minor=?,peak_equity_minor=?,
                    realized_daily_pnl_minor=?,version=version+1 WHERE id=? AND version=?''',
                    (balances['cash'],balances['cash']+balances['asset']+balances['equity']+minor_units(limits.total_capital),peak,-balances['income']-balances['expense'],account_id,row['version']))
                if changed.rowcount != 1:
                    raise RuntimeError('paper account changed inside owned transaction')
            else:
                status = 'REJECTED'
            after, effective = self._load(account_id)
            latest, _ = self._order(account_id, order_id)
            result = LifecycleResult(account_id,event_key,order_id,status,reason or 'Synthetic transition committed.',
                latest['status'],latest['version'],after['version'],money(cash_change),money(pnl_change),journal_id)
            conn.execute('''INSERT INTO paper_lifecycle_events(account_id,event_key,order_id,kind,payload_digest,
                payload_json,result_json,audit_event_id) VALUES (?,?,?,?,?,?,?,?)''',
                (account_id,event_key,order_id,kind,digest,payload,canonical_json(asdict(result)),event_id))
            self.repository.audit(event_id, 'paper_lifecycle', order_id,
                json.loads(canonical_json({'policy_version':LIFECYCLE_POLICY_VERSION,'synthetic':True,'command':json.loads(payload),
                 'account_version_before':row['version'],'order_version_before':order['version'],
                 'observed_at':now,'result':asdict(result),'account_after':asdict(self._state(after,effective))})))
            if status != 'REJECTED' and not self._integrity(after, effective):
                raise RuntimeError('post-transition paper projection mismatch; transaction rolled back')
        return result

    def _reject_reason(self, row, order, position, integrity, kind, expected_version, spec, now):
        if not integrity:
            return 'Account integrity failed; halted with commitments retained.'
        if order['version'] != expected_version:
            return 'Stale order version; reload before issuing a new command.'
        expected_status = 'FILLED' if kind == 'SETTLE' else 'RESERVED'
        if order['status'] != expected_status:
            return f'{kind} requires {expected_status}.'
        if spec and spec.currency != row['currency']:
            return 'Currency differs from the immutable account denomination.'
        if kind == 'FILL':
            if row['status'] != 'ACTIVE':
                return 'Account is halted; new deployment is disabled.'
            if order['expires_at'] is not None and now >= order['expires_at']:
                return 'Order deadline has elapsed; release requires explicit expiry/cancellation.'
            request = self.repository.conn.execute('SELECT payload_json FROM paper_requests WHERE account_id=? AND request_id=?', (row['id'],order['request_id'])).fetchone()
            original = json.loads(request[0])
            reserved = self.repository.conn.execute('SELECT cash_minor FROM capital_reservations WHERE order_id=?', (order['id'],)).fetchone()[0]
            if minor_units(spec.capital) != minor_units(Decimal(original['opportunity']['required_capital'])):
                return 'Only full fills at the recorded principal are supported.'
            if minor_units(spec.capital) + minor_units(spec.fees) > reserved:
                return 'Actual cash spend exceeds the reservation.'
        elif kind == 'EXPIRE':
            if order['expires_at'] is None or now < order['expires_at']:
                return 'Expiry requires a persisted deadline reached by the service clock.'
        elif kind == 'SETTLE':
            returned, revenue, fees = (minor_units(v) for v in (spec.capital_return,spec.revenue,spec.fees))
            if ((position['treatment']=='expensed' and returned != 0)
                    or returned > position['capital_minor']):
                return 'Capital return is unsupported by the recorded cash-flow contract.'
            net = returned + revenue - fees
            incremental = net - (position['capital_minor'] if position['treatment']=='refundable' else 0)
            if incremental < -position['loss_minor']:
                return 'Settlement exceeds the retained loss allowance.'
            if row['cash_minor'] + net > MAX_MINOR_UNITS or row['current_equity_minor'] + incremental > MAX_MINOR_UNITS:
                return 'Settlement would exceed supported account storage.'
        return self._extra_reject_reason(row, order, position, kind, spec)

    def _extra_reject_reason(self, row, order, position, kind, spec):
        return None
