"""Bounded PAPER autonomy for approved one-off expenses. No funding/card credentials."""
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import json
from urllib.parse import urlsplit
from uuid import uuid4

from .models import validate_identifier
from .paper_accounts import RequestConflictError, canonical_json, minor_units
from .paper_lifecycle import PaperLifecycleService, utc_timestamp
from .paper_payouts import PaperPayoutService
from .reconciliation import reconcile_connection


@dataclass(frozen=True)
class PilotPolicy:
    allowed_vendors: tuple[str,...]
    payout_route: str
    per_action_minor: int = 5000
    lifetime_spend_minor: int = 50000
    cumulative_loss_minor: int = 50000
    max_payout_fee_minor: int = 1000
    max_evidence_age_seconds: int = 900

    def __post_init__(self):
        if not self.allowed_vendors or len(set(self.allowed_vendors))!=len(self.allowed_vendors):
            raise ValueError('distinct approved vendors required')
        for ref in (*self.allowed_vendors,self.payout_route):
            validate_identifier(ref,'opaque_reference')
        if any(not v.startswith('vendor_') for v in self.allowed_vendors) or not self.payout_route.startswith('route_'):
            raise ValueError('use vendor_/route_ references, never payment details')
        fields = (self.per_action_minor,self.lifetime_spend_minor,self.cumulative_loss_minor,self.max_payout_fee_minor,self.max_evidence_age_seconds)
        if any(type(v) is not int or v<0 for v in fields) or not 0<self.per_action_minor<=self.lifetime_spend_minor<=50000 or not 0<self.cumulative_loss_minor<=50000 or self.max_evidence_age_seconds<=0:
            raise ValueError('pilot policy exceeds PHP 500 or has invalid integer bounds')


@dataclass(frozen=True)
class Evidence:
    source_url: str
    observed_at: datetime
    expires_at: datetime
    vendor_ref: str

    def __post_init__(self):
        parsed = urlsplit(self.source_url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('evidence requires a public HTTPS URL without credentials/query data')
        validate_identifier(self.vendor_ref,'vendor_ref')
        if not self.vendor_ref.startswith('vendor_') or utc_timestamp(self.expires_at)<=utc_timestamp(self.observed_at):
            raise ValueError('invalid evidence vendor/deadline')


class PilotPaperService(PaperLifecycleService,PaperPayoutService):
    def create_profile(self,account_id,policy:PilotPolicy):
        validate_identifier(account_id,'account_id')
        if not isinstance(policy,PilotPolicy):
            raise ValueError('explicit PilotPolicy required')
        payload = canonical_json(asdict(policy))
        with self.repository.transaction():
            row,_ = self._load(account_id)
            if row['currency']!='PHP':
                raise ValueError('pilot is PHP only')
            previous = self.repository.conn.execute('SELECT policy_json FROM pilot_profiles WHERE account_id=?',(account_id,)).fetchone()
            if previous:
                if previous[0]!=payload:
                    raise RequestConflictError('pilot policy is immutable')
                return
            if self.repository.conn.execute('SELECT 1 FROM paper_orders WHERE account_id=?',(account_id,)).fetchone():
                raise ValueError('bind the profile before any experiments')
            event_id = str(uuid4())
            self.repository.audit(event_id,'paper_pilot_profile',account_id,{'synthetic':True,'policy':json.loads(payload)})
            self.repository.conn.execute('INSERT INTO pilot_profiles VALUES (?,?,?)',(account_id,payload,event_id))

    def _policy(self,account_id):
        row = self.repository.conn.execute('SELECT * FROM pilot_profiles WHERE account_id=?',(account_id,)).fetchone()
        if row is None:
            raise ValueError('no immutable pilot profile')
        values = json.loads(row['policy_json'])
        event = self.repository.conn.execute('SELECT * FROM audit_events WHERE event_id=?',(row['audit_event_id'],)).fetchone()
        if event is None or event['event_type']!='paper_pilot_profile' or event['entity_id']!=account_id or json.loads(event['payload_json'])!={'synthetic':True,'policy':values}:
            raise ValueError('pilot policy audit mismatch')
        values['allowed_vendors'] = tuple(values['allowed_vendors'])
        return PilotPolicy(**values)

    def _budget(self,account_id):
        conn = self.repository.conn
        spent = conn.execute("""SELECT COALESCE(SUM(CASE WHEN e.kind IN ('FILL','SETTLEMENT') AND p.bucket='cash' AND p.side='credit' THEN p.amount_minor WHEN e.kind='PAYOUT' AND p.bucket='expense' AND p.side='debit' THEN p.amount_minor ELSE 0 END),0)
            FROM paper_journal_entries e JOIN paper_journal_postings p ON p.entry_id=e.id WHERE e.account_id=?""",(account_id,)).fetchone()[0]
        reserved = sum(r[0] for r in conn.execute('SELECT cash_minor FROM capital_reservations WHERE account_id=?',(account_id,)))
        positions = conn.execute('SELECT * FROM paper_positions WHERE account_id=?',(account_id,)).fetchall()
        remaining = sum(p['loss_minor'] for p in positions if p['status']=='OPEN')
        loss = sum(max(0,-p['pnl_minor']) if p['status']=='CLOSED' else p['entry_expense_minor']+p['loss_minor'] for p in positions)
        loss += sum(r[0] for r in conn.execute('SELECT loss_minor FROM capital_reservations WHERE account_id=?',(account_id,)))
        payout_fees = sum(r[0] for r in conn.execute("SELECT fee_cap_minor FROM paper_payouts WHERE account_id=? AND status IN ('RESERVED','SENDING','PENDING','UNKNOWN')",(account_id,)))
        realized_payout_fees = conn.execute("SELECT COALESCE(SUM(actual_fee_minor),0) FROM paper_payouts WHERE account_id=? AND status='SUCCEEDED'",(account_id,)).fetchone()[0]
        return spent,reserved+remaining+payout_fees,loss+payout_fees+realized_payout_fees

    def submit_pilot(self,account_id,request_id,opportunity,action,evidence:Evidence):
        if not isinstance(evidence,Evidence):
            raise ValueError('typed evidence required')
        source = canonical_json({'source_url':evidence.source_url,'observed_at':utc_timestamp(evidence.observed_at),
                                 'expires_at':utc_timestamp(evidence.expires_at),'vendor_ref':evidence.vendor_ref})
        return self.submit(account_id,request_id,replace(opportunity,source=source),action,expires_at=evidence.expires_at)

    def _submission_checks(self,row,limits,opportunity,action):
        try:
            policy = self._policy(row['id'])
            evidence = json.loads(opportunity.source)
            typed = Evidence(evidence['source_url'],datetime.fromisoformat(evidence['observed_at']),datetime.fromisoformat(evidence['expires_at']),evidence['vendor_ref'])
            now = self.clock()
            age = (now.astimezone(timezone.utc)-typed.observed_at.astimezone(timezone.utc)).total_seconds()
            spent,held,loss = self._budget(row['id'])
            report = reconcile_connection(self.repository.conn,row['id'])
            return {'pilot_reconciled':report.ok,'pilot_vendor_approved':typed.vendor_ref in policy.allowed_vendors,
                    'pilot_expensed_oneoff':action.capital_treatment=='expensed',
                    'pilot_evidence_fresh':0<=age<=policy.max_evidence_age_seconds and utc_timestamp(now)<evidence['expires_at'],
                    'pilot_single_spend':minor_units(action.cash_required)<=policy.per_action_minor,
                    'pilot_lifetime_spend':spent+held+minor_units(action.cash_required)<=policy.lifetime_spend_minor,
                    'pilot_cumulative_loss':loss+minor_units(action.worst_case_loss)<=policy.cumulative_loss_minor}
        except (ValueError,TypeError,KeyError):
            return {'pilot_policy_and_evidence':False}

    def _extra_reject_reason(self,row,order,position,kind,spec):
        if kind not in ('FILL','SETTLE'):
            return None
        try:
            policy = self._policy(row['id'])
            if not reconcile_connection(self.repository.conn,row['id']).ok:
                return 'Independent reconciliation failed.'
            spent,held,_ = self._budget(row['id'])
            if kind=='SETTLE' and spent+held+minor_units(spec.fees)>policy.lifetime_spend_minor:
                return 'Exit fees exceed lifetime spending allowance.'
            if kind=='FILL':
                request = self.repository.conn.execute('SELECT payload_json FROM paper_requests WHERE account_id=? AND request_id=?',(row['id'],order['request_id'])).fetchone()
                original = json.loads(request[0])
                evidence = json.loads(original['opportunity']['source'])
                if evidence['vendor_ref'] not in policy.allowed_vendors or utc_timestamp(self.clock())>=evidence['expires_at']:
                    return 'Vendor/evidence authorization is no longer valid.'
        except (ValueError,TypeError,KeyError):
            return 'Pilot profile/evidence is invalid.'
        return None

    def _payout_policy_ok(self,row,route_ref,fee):
        try:
            policy = self._policy(row['id'])
            spent,held,loss = self._budget(row['id'])
            return (reconcile_connection(self.repository.conn,row['id']).ok and route_ref==policy.payout_route
                    and fee<=policy.max_payout_fee_minor and spent+held+fee<=policy.lifetime_spend_minor
                    and loss+fee<=policy.cumulative_loss_minor)
        except (ValueError,TypeError,KeyError):
            return False
