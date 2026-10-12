"""Durable test-provider outbox. Network requests never run inside SQLite transactions."""
from dataclasses import asdict
from hashlib import sha256
import json
from uuid import uuid4

from .models import validate_identifier
from .paper_accounts import RequestConflictError,canonical_json,money
from .paper_lifecycle import PaperSettlementSpec
from .reconciliation import reconcile_connection
from src.adapters.paymongo import CheckoutReceipt,ProviderUnknown


class SandboxGateway:
    def __init__(self,service,provider):
        if provider.mode not in ('SANDBOX','OFFLINE_FIXTURE'):
            raise ValueError('gateway accepts test providers only')
        self.service=service
        self.repository=service.repository
        self.provider=provider

    def _event(self,account_id,key,operation_id,kind,result):
        payload=canonical_json(result)
        prior=self.repository.conn.execute('SELECT result_json FROM sandbox_provider_events WHERE account_id=? AND event_key=?',(account_id,key)).fetchone()
        if prior:
            if prior[0]!=payload:
                raise RequestConflictError('provider event key conflicts')
            return
        event_id=str(uuid4())
        self.repository.conn.execute('INSERT INTO sandbox_provider_events VALUES (?,?,?,?,?,?)',(account_id,key,operation_id,kind,payload,event_id))
        self.repository.audit(event_id,'sandbox_provider',account_id,{'operation_id':operation_id,'kind':kind,'result':result,'mode':self.provider.mode})

    def get_checkout(self,account_id,operation_id):
        row=self.repository.conn.execute('SELECT * FROM sandbox_checkouts WHERE id=? AND account_id=?',(operation_id,account_id)).fetchone()
        if row is None:
            raise KeyError('unknown checkout operation')
        return dict(row)

    def prepare_checkout(self,account_id,request_key,order_id,gross_minor):
        for name,value in (('account_id',account_id),('request_key',request_key),('order_id',order_id)):
            validate_identifier(value,name)
        if type(gross_minor) is not int or not 0<gross_minor<=2**63-1:
            raise ValueError('checkout gross must be positive integer cents')
        conn=self.repository.conn
        with self.repository.transaction():
            previous=conn.execute('SELECT * FROM sandbox_checkouts WHERE account_id=? AND request_key=?',(account_id,request_key)).fetchone()
            if previous:
                if (previous['order_id'],previous['gross_minor'],previous['provider_scope'])!=(order_id,gross_minor,self.provider.scope_fingerprint):
                    raise RequestConflictError('checkout request key conflicts')
                return dict(previous)
            account,limits=self.service._load(account_id)
            order,_=self.service._order(account_id,order_id)
            if account['status']!='ACTIVE' or order['status']!='FILLED' or not reconcile_connection(conn,account_id).ok:
                raise ValueError('checkout needs an intact active account and filled experiment')
            operation_id=str(uuid4())
            conn.execute("INSERT INTO sandbox_checkouts(id,account_id,request_key,order_id,gross_minor,provider_scope,status) VALUES (?,?,?,?,?,?,'QUEUED')",
                         (operation_id,account_id,request_key,order_id,gross_minor,self.provider.scope_fingerprint))
            result=self.get_checkout(account_id,operation_id)
            self._event(account_id,'checkout-created-'+operation_id,operation_id,'CHECKOUT_CREATED',result)
        return result

    def dispatch_checkout(self,account_id,operation_id):
        conn=self.repository.conn
        with self.repository.transaction():
            operation=self.get_checkout(account_id,operation_id)
            self._scope(operation['provider_scope'])
            if operation['status']!='QUEUED':
                return operation  # Never resubmit an already attempted POST, even after restart.
            account,_=self.service._load(account_id)
            if account['status']!='ACTIVE' or not reconcile_connection(conn,account_id).ok:
                raise ValueError('account is halted or unreconciled')
            conn.execute("UPDATE sandbox_checkouts SET status='SENDING' WHERE id=?",(operation_id,))
            self._event(account_id,'checkout-claim-'+operation_id,operation_id,'CHECKOUT_CLAIMED',self.get_checkout(account_id,operation_id))
        try:
            receipt=self.provider.create_checkout(operation_id,operation['gross_minor'])
            return self._record_checkout(account_id,operation_id,receipt)
        except ProviderUnknown:
            return self._unknown_checkout(account_id,operation_id)

    def _scope(self,scope):
        if scope!=self.provider.scope_fingerprint:
            raise RequestConflictError('provider credentials/source/destination scope changed')

    def _record_checkout(self,account_id,operation_id,receipt):
        if not isinstance(receipt,CheckoutReceipt) or receipt.status not in ('READY','PAID_UNCLEARED','EXPIRED'):
            raise ProviderUnknown('unsupported checkout receipt')
        with self.repository.transaction():
            operation=self.get_checkout(account_id,operation_id)
            self._scope(operation['provider_scope'])
            if receipt.gross_minor!=operation['gross_minor'] or (operation['provider_id'] and receipt.provider_id!=operation['provider_id']):
                raise ProviderUnknown('checkout correlation changed')
            if operation['status']=='CLEARED_FIXTURE':
                return operation
            encoded=canonical_json(asdict(receipt))
            if operation['result_json']==encoded:
                return operation
            self.repository.conn.execute('UPDATE sandbox_checkouts SET provider_id=?,payment_id=?,status=?,result_json=? WHERE id=?',
                (receipt.provider_id,receipt.payment_id,receipt.status,encoded,operation_id))
            result=self.get_checkout(account_id,operation_id)
            key='checkout-response-'+sha256((operation_id+encoded).encode()).hexdigest()
            self._event(account_id,key,operation_id,'CHECKOUT_OBSERVED',result)
        return result

    def _unknown_checkout(self,account_id,operation_id):
        with self.repository.transaction():
            operation=self.get_checkout(account_id,operation_id)
            if operation['status'] not in ('SENDING','UNKNOWN'):
                return operation
            self.repository.conn.execute("UPDATE sandbox_checkouts SET status='UNKNOWN' WHERE id=?",(operation_id,))
            row,_=self.service._load(account_id)
            self.service._halt(row,'sandbox checkout outcome unknown; creation will not be repeated')
            result=self.get_checkout(account_id,operation_id)
            self._event(account_id,'checkout-unknown-'+operation_id,operation_id,'CHECKOUT_UNKNOWN',result)
        return result

    def poll_checkout(self,account_id,operation_id):
        operation=self.get_checkout(account_id,operation_id)
        self._scope(operation['provider_scope'])
        if not operation['provider_id']:
            return operation  # Missing ID needs provider/manual lookup, not another POST.
        return self._record_checkout(account_id,operation_id,
            self.provider.get_checkout(operation['provider_id'],operation['gross_minor'],operation_id))

    def settle_fixture_income(self,account_id,operation_id,receipt):
        """Explicit offline simulation. Network 'paid' events never invoke this method."""
        if self.provider.mode!='OFFLINE_FIXTURE' or receipt.status!='PAID_UNCLEARED' or receipt.net_minor is None or receipt.net_minor<0 or receipt.deductions_minor!=receipt.gross_minor-receipt.net_minor:
            raise ValueError('income clearance is restricted to offline synthetic fixtures')
        operation=self.get_checkout(account_id,operation_id)
        self._scope(operation['provider_scope'])
        if operation['status']=='CLEARED_FIXTURE':
            return operation
        if receipt.provider_id!=operation['provider_id'] or receipt.payment_id!=operation['payment_id'] or receipt.gross_minor!=operation['gross_minor']:
            raise ValueError('fixture income correlation mismatch')
        outcome=self.service.settle(account_id,'fixture-income-'+operation_id,operation['order_id'],
                                   PaperSettlementSpec('PHP',money(0),money(receipt.gross_minor),money(receipt.deductions_minor)),expected_version=1)
        if outcome.status!='CLOSED':
            return {'status':'REJECTED','reason':outcome.reason}
        with self.repository.transaction():
            self.repository.conn.execute("UPDATE sandbox_checkouts SET status='CLEARED_FIXTURE' WHERE id=?",(operation_id,))
            result=self.get_checkout(account_id,operation_id)
            self._event(account_id,'checkout-cleared-'+operation_id,operation_id,'FIXTURE_CLEARED',result)
        return result

    def dispatch_payout(self,account_id,payout_id):
        payout=self.service.get_payout(account_id,payout_id)
        if payout['route_ref']!=self.provider.route_ref:
            raise RequestConflictError('payout destination is outside the provider binding')
        if payout['status']!='RESERVED':
            return self.poll_payout(account_id,payout_id) if payout['status'] in ('PENDING','UNKNOWN') else payout
        claimed=self.service.claim_payout(account_id,'provider-claim-'+payout_id,payout_id,provider_scope=self.provider.scope_fingerprint)
        if claimed['status']!='SENDING':
            return claimed
        with self.repository.transaction():
            latest=self.service.get_payout(account_id,payout_id)
            self._scope(latest['provider_scope'])
            marker='provider-post-'+payout_id
            exists=self.repository.conn.execute('SELECT 1 FROM sandbox_provider_events WHERE account_id=? AND event_key=?',(account_id,marker)).fetchone()
            account,_=self.service._load(account_id)
            if exists or latest['status']!='SENDING' or account['status']!='ACTIVE' or not reconcile_connection(self.repository.conn,account_id).ok:
                dispatch=False
            else:
                self._event(account_id,marker,payout_id,'PAYOUT_DISPATCH',latest)
                dispatch=True
        if not dispatch:
            return self.service.get_payout(account_id,payout_id)
        try:
            receipt=self.provider.create_payout(payout_id,payout['amount_minor'],payout['fee_cap_minor'])
            return self._payout_receipt(account_id,payout,receipt)
        except (ProviderUnknown,ValueError):
            return self.service.confirm_payout(account_id,'provider-unknown-'+payout_id,payout_id,'UNKNOWN')

    def _payout_receipt(self,account_id,payout,receipt):
        self._scope(self.service.get_payout(account_id,payout['id'])['provider_scope'])
        if payout['provider_id'] and receipt.provider_id!=payout['provider_id']:
            raise ProviderUnknown('payout provider identity changed')
        if receipt.reference_number!=payout['id'] or receipt.amount_minor!=payout['amount_minor']:
            raise ProviderUnknown('payout receipt correlation mismatch')
        key='provider-response-'+sha256(canonical_json(asdict(receipt)).encode()).hexdigest()
        return self.service.confirm_payout(account_id,key,payout['id'],receipt.status,
            provider_id=receipt.provider_id,actual_fee=money(receipt.fee_minor))

    def poll_payout(self,account_id,payout_id):
        payout=self.service.get_payout(account_id,payout_id)
        self._scope(payout['provider_scope'])
        try:
            return self._payout_receipt(account_id,payout,
                self.provider.lookup_payout(payout_id,payout['amount_minor'],payout['fee_cap_minor']))
        except ProviderUnknown:
            return self.service.confirm_payout(account_id,'provider-unknown-'+payout_id,payout_id,'UNKNOWN')
