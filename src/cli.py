"""Read-only reports, offline autonomy demo and explicitly selected test-provider operations."""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone,timedelta
from decimal import Decimal
import json
import os

from src.adapters.fixtures import OfflineProvider
from src.adapters.paymongo import PayMongoSandboxClient,ProviderUnknown
from src.core.models import CapitalLimits,Opportunity,OpportunityType
from src.core.paper_accounts import PaperActionSpec,canonical_json,minor_units
from src.core.paper_lifecycle import PaperFillSpec
from src.core.pilot import Evidence,PilotPaperService,PilotPolicy
from src.core.reconciliation import reconcile_path
from src.core.sandbox_gateway import SandboxGateway
from src.persistence.sqlite import Repository

FIXTURE_TIME=datetime(2026,10,12,tzinfo=timezone.utc)


def demo(path,*,stop_after_fill=False):
    repo=Repository(path)
    try:
        service=PilotPaperService(repo,clock=lambda:FIXTURE_TIME)
        if not repo.conn.execute("SELECT 1 FROM paper_accounts WHERE id='demo'").fetchone():
            service.create_account('demo',CapitalLimits(Decimal('500'),Decimal('.5'),Decimal('50'),Decimal('500'),Decimal('500')))
        service.create_profile('demo',PilotPolicy(('vendor_fixture',),'route_gotyme_test'))
        opportunity=Opportunity('fixture-observation',OpportunityType.OTHER,'Synthetic fixture; no actual customer or profit evidence',
            Decimal('45'),Decimal('100'),Decimal('50'),Decimal('1'),confidence=Decimal('1'))
        evidence=Evidence('https://example.com/fixture-evidence',FIXTURE_TIME,FIXTURE_TIME+timedelta(minutes=5),'vendor_fixture')
        reservation=service.submit_pilot('demo','fixture-reserve',opportunity,PaperActionSpec('PHP',Decimal('50'),Decimal('50'),'expensed'),evidence)
        if reservation.status!='RESERVED':
            return {'mode':'OFFLINE_FIXTURE','status':'REJECTED','reason':reservation.reason}
        fill=service.fill('demo','fixture-fill',reservation.order_id,PaperFillSpec('PHP',Decimal('45'),Decimal('5')),expected_version=0)
        if fill.status!='FILLED':
            return {'mode':'OFFLINE_FIXTURE','status':'REJECTED','reason':fill.reason}
        if not stop_after_fill:
            provider=OfflineProvider()
            gateway=SandboxGateway(service,provider)
            checkout=gateway.prepare_checkout('demo','fixture-checkout',reservation.order_id,10000)
            checkout=gateway.dispatch_checkout('demo',checkout['id'])
            if checkout['status']!='CLEARED_FIXTURE':
                checkout=gateway.poll_checkout('demo',checkout['id'])
                receipt=provider.get_checkout(checkout['provider_id'],checkout['gross_minor'],checkout['id'])
                gateway.settle_fixture_income('demo',checkout['id'],receipt)
            payout=service.request_payout('demo','fixture-payout','route_gotyme_test',Decimal('35'),Decimal('10'))
            if payout['status']=='REJECTED':
                return {'mode':'OFFLINE_FIXTURE','status':'REJECTED','reason':payout['reason']}
            gateway.dispatch_payout('demo',payout['id'])
        return {'mode':'OFFLINE_FIXTURE','real_money_moved':False,'account':asdict(service.get_account('demo')),
                'order_id':reservation.order_id,'reconciliation':reconcile_path(path,'demo').as_dict()}
    finally:
        repo.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('readiness',help='capabilities and activation blockers; never prints credentials')
    d=commands.add_parser('demo',help='offline simulated expense/income/payout, with no external requests')
    d.add_argument('--db',required=True)
    d.add_argument('--stop-after-fill',action='store_true')
    r=commands.add_parser('reconcile',help='read-only consistent snapshot; no migration or repair')
    r.add_argument('--db',required=True);r.add_argument('--account',required=True)
    checkout=commands.add_parser('sandbox-checkout',help='requires a test key; customer checkout only')
    checkout.add_argument('--db',required=True);checkout.add_argument('--account',required=True)
    checkout.add_argument('--order',required=True);checkout.add_argument('--request-key',required=True)
    checkout.add_argument('--amount',required=True,help='PHP gross customer price, exact Decimal cents')
    payout=commands.add_parser('sandbox-payout',help='only provider simulator destination numbers')
    payout.add_argument('--db',required=True);payout.add_argument('--account',required=True);payout.add_argument('--payout-id',required=True)
    pp=commands.add_parser('sandbox-poll-payout',help='read by stable reference; never resend an uncertain transfer')
    pp.add_argument('--db',required=True);pp.add_argument('--account',required=True);pp.add_argument('--payout-id',required=True)
    poll=commands.add_parser('sandbox-poll-checkout',help='paid is not cleared income')
    poll.add_argument('--db',required=True);poll.add_argument('--account',required=True);poll.add_argument('--operation-id',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='readiness':
            key=os.environ.get('PAYMONGO_TEST_SECRET_KEY','')
            result={'mode':'PAPER','live_enabled':False,'card_access_enabled':False,'withdrawals_enabled':False,
                    'intended_bank':'GoTyme Bank','intended_card':'Visa virtual debit',
                    'sandbox_test_key_present':bool(key),'sandbox_key_has_test_prefix':key.startswith('sk_test_'),
                    'sandbox_wallet_source_present':bool(os.environ.get('PAYMONGO_TEST_WALLET_NUMBER')),
                    'verified_scope':['read-only paper reconciliation','bounded offline expense/income/payout cycle','PayMongo test-key-only v2 adapters'],
                    'live_blockers':['provider KYC and enabled wallet capabilities','actual authenticated sandbox smoke tests',
                        'verified one-off expense vendor integration','authoritative cleared-wallet reconciliation',
                        'verified own-bank payout destination','hosting/authentication/monitoring','tax/refund/reserve policy',
                        'evidence of a profitable strategy','separately reviewed live release']}
        elif args.command=='demo':
            result=demo(args.db,stop_after_fill=args.stop_after_fill)
        elif args.command=='reconcile':
            result=reconcile_path(args.db,args.account).as_dict()
        else:
            provider=PayMongoSandboxClient.from_environment()  # Reject missing/live keys before database writes.
            repo=Repository(args.db)
            try:
                gateway=SandboxGateway(PilotPaperService(repo),provider)
                if args.command=='sandbox-checkout':
                    op=gateway.prepare_checkout(args.account,args.request_key,args.order,minor_units(Decimal(args.amount)))
                    result=gateway.dispatch_checkout(args.account,op['id'])
                elif args.command=='sandbox-payout':
                    result=gateway.dispatch_payout(args.account,args.payout_id)
                elif args.command=='sandbox-poll-payout':
                    result=gateway.poll_payout(args.account,args.payout_id)
                else:
                    result=gateway.poll_checkout(args.account,args.operation_id)
            finally:
                repo.close()
        print(canonical_json(result))
        return 2 if result.get('ok') is False or result.get('status') in ('REJECTED','UNKNOWN') else 0
    except (ValueError,KeyError,RuntimeError,ProviderUnknown):
        # Never echo provider bodies, bank numbers, API keys or caller credentials.
        print(json.dumps({'error':'Operation blocked or needs reconciliation; inspect configuration and opaque IDs.'}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
