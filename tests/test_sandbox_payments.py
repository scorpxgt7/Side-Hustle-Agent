"""Verified endpoint contracts, no live keys, durable claims and unknown-outcome safety."""
from dataclasses import asdict,replace
from decimal import Decimal as D
from hashlib import sha256
import hmac
import json

import pytest

from src.adapters.paymongo import (PayMongoSandboxClient,ProviderUnknown,CheckoutReceipt,TransferReceipt,verify_test_webhook)
from src.adapters.fixtures import OfflineProvider
from src.core.sandbox_gateway import SandboxGateway
from src.core.reconciliation import reconcile_path
from src.cli import demo,main
from test_reconciliation_pilot import pilot,experiment,evidence,candidate
from src.core.paper_accounts import PaperActionSpec,RequestConflictError
from src.core.paper_lifecycle import PaperFillSpec

TEST_KEY='sk_test_fixture12345678'


def checkout(provider_id='cs_test',ref='request',gross=10000,paid=False,**changes):
    attrs={'livemode':False,'reference_number':ref,'checkout_url':'https://checkout.paymongo.com/'+provider_id,
           'status':'active','payments':[]}
    if paid:
        attrs['payments']=[{'id':'pay_test','attributes':{'amount':gross,'net_amount':gross-500,'livemode':False,
            'currency':'PHP','status':'paid','disputed':False,'refunds':[],
            'credited_at':1,'available_at':1,'billing':{'email':'private@example.com'}}}]
    attrs.update(changes)
    return {'data':{'id':provider_id,'type':'checkout_session','attributes':attrs}}


def transfer(ref='request',status='succeeded',fee=1000,**changes):
    item={'id':'tr_test','livemode':False,'reference_number':ref,'amount':3500,'fee':fee,
          'currency':'PHP','provider':'pesonet','status':status,
          'destination_account':{'number':'999999990001','bic':'GOTYPHM2XXX'}}
    item.update(changes)
    return {'data':{'id':'batch_tr_test','transfers':[item]}}


def client(transport):
    return PayMongoSandboxClient(TEST_KEY,source_wallet_number='123456789012',transport=transport)


@pytest.mark.parametrize('key',['','sk_live_actual123456','pk_test_publickey','sk_test_short','sk_test_12345678\n'])
def test_live_missing_or_invalid_keys_are_rejected(key):
    with pytest.raises(ValueError):
        PayMongoSandboxClient(key)


def test_card_or_real_account_numbers_cannot_be_a_sandbox_destination():
    with pytest.raises(ValueError):
        PayMongoSandboxClient(TEST_KEY,test_destination='4111111111111111')
    with pytest.raises(ValueError):
        PayMongoSandboxClient(TEST_KEY,test_destination='123456789012')


def test_checkout_v2_contract_and_paid_is_not_cleared():
    calls=[]
    def transport(method,path,body,headers):
        calls.append((method,path,body,headers))
        return checkout(ref='request',paid=method=='GET')
    adapter=client(transport)
    created=adapter.create_checkout('request',10000)
    assert created.status=='READY'
    assert calls[0][1]=='/v2/checkout_sessions'
    payload=calls[0][2]['data']['attributes']
    assert payload['line_items']==[{'name':'Synthetic test order','amount':10000,'currency':'PHP','quantity':1}]
    assert 'card_number' not in json.dumps(payload)
    paid=adapter.get_checkout('cs_test',10000,'request')
    assert calls[1][1]=='/v1/checkout_sessions/cs_test'
    assert paid.status=='PAID_UNCLEARED' and paid.net_minor==9500 and paid.deductions_minor==500
    assert 'private@example.com' not in json.dumps(asdict(paid))


@pytest.mark.parametrize('changes',[{'livemode':True},{'livemode':'false'},
    {'checkout_url':'https://evil.example/checkout'},{'reference_number':'other'}])
def test_checkout_response_identity_and_environment_must_match(changes):
    adapter=client(lambda *_:checkout(**changes))
    with pytest.raises(ProviderUnknown):
        adapter.create_checkout('request',10000)


@pytest.mark.parametrize('field,value',[('amount',9999),('amount',100.0),('currency','USD'),('net_amount',10001),('disputed',True),('livemode',True),('refunds',[{'id':'re_test'}])])
def test_paid_checkout_cannot_silently_change_money_or_mode(field,value):
    body=checkout(paid=True)
    body['data']['attributes']['payments'][0]['attributes'][field]=value
    with pytest.raises(ProviderUnknown):
        client(lambda *_:body).get_checkout('cs_test',10000,'request')


def test_transfer_uses_v2_simulator_pesonet_and_stable_idempotency():
    calls=[]
    def transport(method,path,body,headers):
        calls.append((method,path,body,headers))
        return transfer()
    adapter=client(transport)
    result=adapter.create_payout('request',3500,1000)
    assert result.status=='SUCCEEDED'
    assert calls[0][1]=='/v2/batch_transfers'
    assert calls[0][3]=={'Idempotency-Key':'request'}
    payload=calls[0][2]['transfers'][0]
    assert payload['provider']=='pesonet' and payload['amount']==3500
    assert payload['destination_account']['number']=='999999990001'
    assert payload['destination_account']['bic']=='GOTYPHM2XXX'
    adapter.lookup_payout('request',3500,1000)
    assert calls[1][0]=='GET' and calls[1][1]=='/v2/transfers?reference_number=request'


@pytest.mark.parametrize('changes',[{'livemode':True},{'reference_number':'other'}, {'amount':3501},
    {'fee':1001},{'provider':'instapay'}, {'status':'mystery'},
    {'destination_account':{'number':'123456789012','bic':'GOTYPHM2XXX'}}])
def test_bad_transfer_cannot_be_booked_or_release_budget(changes):
    with pytest.raises(ProviderUnknown):
        client(lambda *_:transfer(**changes)).create_payout('request',3500,1000)


def test_transport_errors_do_not_expose_secret_or_bank_data():
    def fail(*args):
        raise RuntimeError(TEST_KEY+' 123456789012 card sensitive data')
    with pytest.raises(ProviderUnknown) as error:
        client(fail).create_checkout('request',10000)
    assert TEST_KEY not in str(error.value) and '123456789012' not in str(error.value)


def signed(body,secret='whsk_fixture_only',timestamp=1000):
    signature=hmac.new(secret.encode(),str(timestamp).encode()+b'.'+body,sha256).hexdigest()
    return f't={timestamp},te={signature},li='


def test_webhook_signature_mode_timestamp_and_raw_body_are_verified():
    body=json.dumps({'data':{'id':'evt_test','type':'event','attributes':{'type':'checkout_session.payment.paid','livemode':False}}}).encode()
    assert verify_test_webhook(body,signed(body),'whsk_fixture_only',now=1000)['id']=='evt_test'
    for raw,signature,now in [(body+b' ',signed(body),1000),(body,signed(body),1301),
                              (body,signed(body).replace('li=','li=abcd'),1000),
                              (body,signed(body)+',t=1000',1000)]:
        with pytest.raises(ValueError):
            verify_test_webhook(raw,signature,'whsk_fixture_only',now=now)
    live=body.replace(b'false',b'true')
    with pytest.raises(ValueError):
        verify_test_webhook(live,signed(live),'whsk_fixture_only',now=1000)


def checkout_order(service):
    reservation=service.submit_pilot('demo','net',candidate('net'),PaperActionSpec('PHP',D('50'),D('50'),'expensed'),evidence())
    service.fill('demo','fill-net',reservation.order_id,PaperFillSpec('PHP',D('45'),D('5')),expected_version=0)
    return reservation.order_id


def test_checkout_claim_and_uncertain_network_outcome_are_durable(pilot):
    repo,service=pilot
    order=checkout_order(service)
    calls=[]
    def fail(*args):
        calls.append(args)
        raise ProviderUnknown('timeout')
    gateway=SandboxGateway(service,client(fail))
    operation=gateway.prepare_checkout('demo','checkout',order,10000)
    unknown=gateway.dispatch_checkout('demo',operation['id'])
    assert unknown['status']=='UNKNOWN' and len(calls)==1
    assert gateway.dispatch_checkout('demo',operation['id'])==unknown and len(calls)==1
    assert service.get_account('demo').status=='HALTED'
    assert service.get_account('demo').cash==D('450')
    assert service.get_order('demo',order).status=='FILLED'
    assert reconcile_path(repo.path,'demo').ok
    with pytest.raises(RequestConflictError):
        gateway.prepare_checkout('demo','checkout',order,10001)


def test_network_paid_notification_never_creates_paper_income(pilot):
    repo,service=pilot
    order=checkout_order(service)
    def transport(method,path,body,headers):
        ref=body['data']['attributes']['reference_number'] if body else reference[0]
        reference[0]=ref
        return checkout(ref=ref,paid=method=='GET')
    reference=['']
    gateway=SandboxGateway(service,client(transport))
    operation=gateway.prepare_checkout('demo','checkout',order,10000)
    gateway.dispatch_checkout('demo',operation['id'])
    paid=gateway.poll_checkout('demo',operation['id'])
    assert paid['status']=='PAID_UNCLEARED'
    assert service.get_account('demo').cash==D('450')
    receipt=CheckoutReceipt(**json.loads(paid['result_json']))
    with pytest.raises(ValueError,match='offline'):
        gateway.settle_fixture_income('demo',operation['id'],receipt)
    assert reconcile_path(repo.path,'demo').ok


def test_a_crashed_claim_is_never_posted_again(pilot):
    repo,service=pilot
    order=checkout_order(service)
    calls=[]
    gateway=SandboxGateway(service,client(lambda *args:calls.append(args)))
    operation=gateway.prepare_checkout('demo','checkout',order,10000)
    # Emulate the durable pre-network claim exactly; there is no provider response yet.
    with repo.transaction():
        repo.conn.execute("UPDATE sandbox_checkouts SET status='SENDING' WHERE id=?",(operation['id'],))
        gateway._event('demo','crash-claim',operation['id'],'CHECKOUT_CLAIMED',gateway.get_checkout('demo',operation['id']))
    assert gateway.dispatch_checkout('demo',operation['id'])['status']=='SENDING' and calls==[]
    assert reconcile_path(repo.path,'demo').ok


def test_unknown_payout_is_recovered_by_read_not_repeated_post(pilot):
    repo,service=pilot
    experiment(service)
    p=service.request_payout('demo','pay','route_gotyme_test',D('35'),D('10'))
    calls=[]
    def transport(method,path,body,headers):
        calls.append(method)
        if method=='POST':
            raise ProviderUnknown('lost response')
        return transfer(ref=p['id'])
    gateway=SandboxGateway(service,client(transport))
    assert gateway.dispatch_payout('demo',p['id'])['status']=='UNKNOWN'
    assert service.get_account('demo').reserved_cash==D('45')
    assert gateway.dispatch_payout('demo',p['id'])['status']=='SUCCEEDED'
    assert calls==['POST','GET']
    assert service.get_account('demo').cash==D('500')
    assert service.get_account('demo').status=='HALTED'
    assert reconcile_path(repo.path,'demo').ok


def test_changed_provider_scope_cannot_dispatch_or_query_existing_job(pilot):
    repo,service=pilot
    experiment(service)
    p=service.request_payout('demo','pay','route_gotyme_test',D('35'),D('10'))
    first=client(lambda *_:transfer(ref=p['id'],status='pending'))
    assert SandboxGateway(service,first).dispatch_payout('demo',p['id'])['status']=='PENDING'
    calls=[]
    changed=PayMongoSandboxClient('sk_test_different12345678',source_wallet_number='123456789012',transport=lambda *args:calls.append(args))
    with pytest.raises(RequestConflictError):
        SandboxGateway(service,changed).dispatch_payout('demo',p['id'])
    assert calls==[]
    assert service.get_account('demo').reserved_cash==D('45')


def test_demo_runs_whole_flow_and_repeating_it_has_one_effect(tmp_path):
    path=tmp_path/'demo.db'
    first=demo(path)
    assert first['mode']=='OFFLINE_FIXTURE' and first['real_money_moved'] is False
    assert first['account']['cash']==D('500')
    assert first['reconciliation']['distributed_minor']==3500 and first['reconciliation']['ok']
    assert demo(path)==first


def test_cli_rejects_live_keys_without_creating_database(tmp_path,monkeypatch,capsys):
    path=tmp_path/'never.db'
    monkeypatch.setenv('PAYMONGO_TEST_SECRET_KEY','sk_live_reallysecret')
    assert main(['sandbox-checkout','--db',str(path),'--account','demo','--order','unused','--request-key','one','--amount','100'])==2
    assert not path.exists()
    assert 'sk_live_reallysecret' not in capsys.readouterr().out


def test_two_dispatchers_use_one_durable_payout_post(pilot):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier,Event,Lock
    from src.persistence.sqlite import Repository
    from src.core.pilot import PilotPaperService
    repo,service=pilot
    experiment(service)
    payout=service.request_payout('demo','pay','route_gotyme_test',D('35'),D('10'))
    barrier=Barrier(2)
    lock=Lock()
    calls=[]
    def transport(method,path,body,headers):
        with lock:
            calls.append(method)
        return transfer(ref=payout['id'])
    def worker(index):
        other=Repository(repo.path,timeout=3)
        try:
            gateway=SandboxGateway(PilotPaperService(other),client(transport))
            barrier.wait(timeout=5)
            return gateway.dispatch_payout('demo',payout['id'])
        finally:
            other.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(worker,(1,2)))
    assert calls==['POST']
    assert service.get_account('demo').cash==D('500')
    assert reconcile_path(repo.path,'demo').ok


def test_provider_post_marker_survives_crash_before_response(pilot):
    repo,service=pilot
    experiment(service)
    p=service.request_payout('demo','pay','route_gotyme_test',D('35'),D('10'))
    calls=[]
    adapter=client(lambda method,*args:calls.append(method) or transfer(ref=p['id']))
    gateway=SandboxGateway(service,adapter)
    service.claim_payout('demo','provider-claim-'+p['id'],p['id'],provider_scope=adapter.scope_fingerprint)
    with repo.transaction():
        gateway._event('demo','provider-post-'+p['id'],p['id'],'PAYOUT_DISPATCH',service.get_payout('demo',p['id']))
    assert gateway.dispatch_payout('demo',p['id'])['status']=='SENDING'
    assert calls==[]
    assert gateway.poll_payout('demo',p['id'])['status']=='SUCCEEDED'
    assert calls==['GET']
    assert reconcile_path(repo.path,'demo').ok


def test_provider_claim_audit_failure_never_calls_network(pilot,monkeypatch):
    repo,service=pilot
    experiment(service)
    p=service.request_payout('demo','pay','route_gotyme_test',D('35'),D('10'))
    calls=[]
    gateway=SandboxGateway(service,client(lambda *args:calls.append(args)))
    monkeypatch.setattr(repo,'audit',lambda *_:(_ for _ in ()).throw(RuntimeError('injected audit')))
    with pytest.raises(RuntimeError,match='injected audit'):
        gateway.dispatch_payout('demo',p['id'])
    assert calls==[] and service.get_payout('demo',p['id'])['status']=='RESERVED'
    assert service.get_account('demo').reserved_cash==D('45')


def test_readiness_never_prints_configured_credentials(monkeypatch,capsys):
    monkeypatch.setenv('PAYMONGO_TEST_SECRET_KEY',TEST_KEY)
    monkeypatch.setenv('PAYMONGO_TEST_WALLET_NUMBER','123456789012')
    assert main(['readiness'])==0
    output=capsys.readouterr().out
    assert TEST_KEY not in output and '123456789012' not in output
    assert json.loads(output)['live_enabled'] is False


def test_halt_between_claim_and_dispatch_blocks_provider_post(pilot,monkeypatch):
    repo,service=pilot
    experiment(service)
    p=service.request_payout('demo','pay','route_gotyme_test',D('35'),D('10'))
    calls=[]
    gateway=SandboxGateway(service,client(lambda *args:calls.append(args)))
    original=service.claim_payout
    def claim_then_halt(*args,**kwargs):
        result=original(*args,**kwargs)
        service.halt('demo','operator before dispatch')
        return result
    monkeypatch.setattr(service,'claim_payout',claim_then_halt)
    assert gateway.dispatch_payout('demo',p['id'])['status']=='SENDING'
    assert calls==[]
    assert service.get_account('demo').reserved_cash==D('45')
    assert reconcile_path(repo.path,'demo').ok
