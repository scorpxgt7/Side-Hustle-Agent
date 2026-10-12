"""Synthetic profit distributions and durable holds. No card data or live transfers."""
from dataclasses import asdict
from decimal import Decimal
import json
from uuid import uuid4

from .models import validate_identifier
from .paper_accounts import PaperAccountService, RequestConflictError, canonical_json, minor_units
from .paper_journal import journal_projection, post_journal

HELD_STATUSES = ('RESERVED','SENDING','PENDING','UNKNOWN')


def payout_integrity(connection, account_id):
    # Older schema migration checks run before the payout tables exist.
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_payouts'").fetchone():
        if connection.execute('SELECT version FROM paper_schema').fetchone()[0] not in (1,2):
            raise ValueError('missing payout history')
        return 0, 0, 0, 0
    payouts = connection.execute('SELECT * FROM paper_payouts WHERE account_id=?',(account_id,)).fetchall()
    events = connection.execute('SELECT * FROM paper_payout_events WHERE account_id=? ORDER BY rowid',(account_id,)).fetchall()
    expected = {}
    for event in events:
        payload, result = json.loads(event['payload_json']), json.loads(event['result_json'])
        audit = connection.execute('SELECT * FROM audit_events WHERE event_id=?',(event['audit_event_id'],)).fetchone()
        if audit is None or audit['event_type'] != 'paper_payout' or audit['entity_id'] != account_id:
            raise ValueError('payout audit missing')
        recorded = json.loads(audit['payload_json'])
        if recorded['command'] != payload or recorded['result'] != result:
            raise ValueError('payout audit differs')
        if result['status'] == 'REJECTED':
            continue
        if result['id'] != event['payout_id'] or result['account_id'] != account_id:
            raise ValueError('payout identity differs')
        previous = expected.get(result['id'])
        if event['kind'] == 'REQUEST':
            if previous is not None or result['status'] != 'RESERVED' or result['version'] != 0:
                raise ValueError('invalid payout genesis')
        elif previous is None or result['version'] != previous['version'] + 1:
            raise ValueError('invalid payout sequence')
        expected[result['id']] = result
    held_cash = held_loss = distribution = entries = 0
    for payout in payouts:
        if expected.get(payout['id']) != dict(payout):
            raise ValueError('payout projection differs')
        linked = connection.execute("SELECT * FROM paper_journal_entries WHERE account_id=? AND kind='PAYOUT' AND event_id IN (SELECT audit_event_id FROM paper_payout_events WHERE payout_id=?)",(account_id,payout['id'])).fetchall()
        if payout['status'] in HELD_STATUSES:
            held_cash += payout['amount_minor'] + payout['fee_cap_minor']
            held_loss += payout['fee_cap_minor']
        if payout['status'] == 'SUCCEEDED':
            fee = payout['actual_fee_minor']
            if type(fee) is not int or not 0 <= fee <= payout['fee_cap_minor'] or len(linked) != 1:
                raise ValueError('invalid payout settlement')
            postings = [['equity','debit',payout['amount_minor']],['expense','debit',fee],['cash','credit',payout['amount_minor']+fee]]
            if json.loads(linked[0]['postings_json']) != postings:
                raise ValueError('payout journal differs')
            distribution += payout['amount_minor']
            entries += 1
        elif linked or payout['actual_fee_minor'] is not None:
            raise ValueError('unsettled payout has money movement')
    if connection.execute("SELECT COUNT(*) FROM paper_journal_entries WHERE account_id=? AND kind='PAYOUT'",(account_id,)).fetchone()[0] != entries:
        raise ValueError('unlinked payout journal')
    return held_cash, held_loss, distribution, entries


class PaperPayoutService(PaperAccountService):
    def _prior(self, account_id, event_key, payload):
        row = self.repository.conn.execute('SELECT payload_json,result_json FROM paper_payout_events WHERE account_id=? AND event_key=?',(account_id,event_key)).fetchone()
        if row:
            if row[0] != payload:
                raise RequestConflictError('payout key has conflicting inputs')
            return json.loads(row[1])
        return None

    def _record(self, account_id,event_key,payout_id,kind,payload,result,event_id):
        self.repository.conn.execute('INSERT INTO paper_payout_events VALUES (?,?,?,?,?,?,?)',
            (account_id,event_key,payout_id,kind,payload,canonical_json(result),event_id))
        self.repository.audit(event_id,'paper_payout',account_id,
            {'synthetic':True,'command':json.loads(payload),'result':result,'policy_version':'paper-profit-payout-v1'})

    def get_payout(self, account_id,payout_id):
        row = self.repository.conn.execute('SELECT * FROM paper_payouts WHERE id=? AND account_id=?',(payout_id,account_id)).fetchone()
        if row is None:
            raise KeyError('unknown paper payout')
        return dict(row)

    def request_payout(self,account_id,event_key,route_ref,amount:Decimal,fee_cap:Decimal):
        for name,value in (('account_id',account_id),('event_key',event_key),('route_ref',route_ref)):
            validate_identifier(value,name)
        if not route_ref.startswith('route_'):
            raise ValueError('payout destination must be an opaque route_ reference, never card/account details')
        amount, fee = minor_units(amount), minor_units(fee_cap)
        if amount <= 0 or fee < 0:
            raise ValueError('payout must be positive with nonnegative fee allowance')
        payload = canonical_json({'kind':'REQUEST','route_ref':route_ref,'amount_minor':amount,'fee_cap_minor':fee})
        conn = self.repository.conn
        with self.repository.transaction():
            prior = self._prior(account_id,event_key,payload)
            if prior is not None:
                return prior
            row,limits = self._load(account_id)
            intact = self._integrity(row,limits)
            if not intact:
                self._halt(row,'payout reconciliation failed')
            outstanding = conn.execute("SELECT COUNT(*) FROM paper_orders WHERE account_id=? AND status IN ('RESERVED','FILLED')",(account_id,)).fetchone()[0]
            _,_,distributed,_ = payout_integrity(conn,account_id) if intact else (0,0,0,0)
            reason = None
            if not intact or row['status'] != 'ACTIVE':
                reason = 'Account is halted or inconsistent.'
            elif row['currency'] != 'PHP' or outstanding or row['reserved_cash_minor']:
                reason = 'PHP payout requires closed positions and no outstanding commitments.'
            elif amount+fee > row['realized_daily_pnl_minor']-distributed:
                reason = 'Payout exceeds realized profit after prior distributions and fees.'
            elif amount+fee > row['cash_minor']-self._protected(limits):
                reason = 'Payout would consume protected cash.'
            elif row['peak_equity_minor']-row['current_equity_minor']+fee >= minor_units(limits.max_drawdown) or row['realized_daily_pnl_minor']-fee <= -minor_units(limits.max_daily_loss):
                reason = 'Payout fee reaches a loss/drawdown boundary.'
            elif not self._payout_policy_ok(row,route_ref,fee):
                reason = 'Payout route or fee exceeds the pilot policy.'
            payout_id = None
            event_id = str(uuid4())
            if reason:
                result = {'status':'REJECTED','reason':reason}
            else:
                payout_id = str(uuid4())
                conn.execute("INSERT INTO paper_payouts(id,account_id,route_ref,amount_minor,fee_cap_minor,status) VALUES (?,?,?,?,?,'RESERVED')",(payout_id,account_id,route_ref,amount,fee))
                conn.execute('UPDATE paper_accounts SET reserved_cash_minor=reserved_cash_minor+?,reserved_loss_minor=reserved_loss_minor+?,version=version+1 WHERE id=?',(amount+fee,fee,account_id))
                result = self.get_payout(account_id,payout_id)
            self._record(account_id,event_key,payout_id,'REQUEST',payload,result,event_id)
            if not reason and not self._integrity(*self._load(account_id)):
                raise RuntimeError('payout reservation mismatch; rolled back')
        return result

    def _payout_policy_ok(self,row,route_ref,fee):
        return True

    def claim_payout(self,account_id,event_key,payout_id,*,provider_scope=None):
        return self._change(account_id,event_key,payout_id,'CLAIM','SENDING',None,None,provider_scope)

    def confirm_payout(self,account_id,event_key,payout_id,status,*,provider_id=None,actual_fee=Decimal('0')):
        if status not in ('PENDING','UNKNOWN','SUCCEEDED','FAILED'):
            raise ValueError('unsupported payout outcome')
        fee = minor_units(actual_fee)
        if fee < 0:
            raise ValueError('negative payout fee')
        if provider_id is not None:
            validate_identifier(provider_id,'provider_id')
        return self._change(account_id,event_key,payout_id,'CONFIRM',status,provider_id,fee)

    def _change(self,account_id,event_key,payout_id,kind,status,provider_id,fee,provider_scope=None):
        for name,value in (('account_id',account_id),('event_key',event_key),('payout_id',payout_id)):
            validate_identifier(value,name)
        payload = canonical_json({'kind':kind,'payout_id':payout_id,'status':status,'provider_id':provider_id,'fee_minor':fee,'provider_scope':provider_scope})
        conn = self.repository.conn
        with self.repository.transaction():
            prior = self._prior(account_id,event_key,payload)
            if prior is not None:
                return prior
            row,limits = self._load(account_id)
            payout = self.get_payout(account_id,payout_id)
            intact = self._integrity(row,limits)
            if not intact:
                self._halt(row,'payout reconciliation failed')
            allowed = ((kind=='CLAIM' and payout['status']=='RESERVED' and row['status']=='ACTIVE')
                       or (kind=='CONFIRM' and payout['status'] in ('SENDING','PENDING','UNKNOWN')))
            if not intact or not allowed:
                result = {'status':'REJECTED','reason':'Payout is inconsistent, halted, already claimed or terminal.'}
                self._record(account_id,event_key,payout_id,kind,payload,result,str(uuid4()))
                return result
            if provider_scope is not None:
                if not isinstance(provider_scope,str) or len(provider_scope)!=64 or any(c not in '0123456789abcdef' for c in provider_scope):
                    raise ValueError('invalid provider scope fingerprint')
                if payout['provider_scope'] is not None and provider_scope!=payout['provider_scope']:
                    raise RequestConflictError('payout provider scope changed')
            if provider_id and payout['provider_id'] and provider_id != payout['provider_id']:
                raise RequestConflictError('payout provider identity changed')
            if status in ('SUCCEEDED','FAILED') and (not provider_id or fee > payout['fee_cap_minor'] or (status=='FAILED' and fee)):
                status = 'UNKNOWN'  # Retain all holds; unsupported provider outcomes cannot free capacity.
            event_id = str(uuid4())
            if status in ('SUCCEEDED','FAILED'):
                conn.execute('UPDATE paper_accounts SET reserved_cash_minor=reserved_cash_minor-?,reserved_loss_minor=reserved_loss_minor-? WHERE id=?',(payout['amount_minor']+payout['fee_cap_minor'],payout['fee_cap_minor'],account_id))
            if status == 'SUCCEEDED':
                post_journal(self.repository,account_id,event_id,'PAYOUT',None,
                    [('equity','debit',payout['amount_minor']),('expense','debit',fee),('cash','credit',payout['amount_minor']+fee)])
            if status == 'UNKNOWN':
                self._halt(row,'payout outcome unknown; holds retained until reconciliation')
            conn.execute('UPDATE paper_payouts SET status=?,provider_id=COALESCE(?,provider_id),provider_scope=COALESCE(?,provider_scope),actual_fee_minor=?,version=version+1 WHERE id=?',
                         (status,provider_id,provider_scope,fee if status=='SUCCEEDED' else None,payout_id))
            balances,peak,_ = journal_projection(conn,account_id)
            capital = minor_units(limits.total_capital)
            conn.execute('UPDATE paper_accounts SET cash_minor=?,current_equity_minor=?,peak_equity_minor=?,realized_daily_pnl_minor=?,version=version+1 WHERE id=?',
                (balances['cash'],balances['cash']+balances['asset']+balances['equity']+capital,peak,-balances['income']-balances['expense'],account_id))
            result = self.get_payout(account_id,payout_id)
            self._record(account_id,event_key,payout_id,kind,payload,result,event_id)
            if not self._integrity(*self._load(account_id)):
                raise RuntimeError('payout projection mismatch; rolled back')
        return result
