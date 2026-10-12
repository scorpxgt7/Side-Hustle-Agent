"""PayMongo test-key-only HTTP adapter. No card collection and no live switch."""
from base64 import b64encode
from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import os
import re
import time
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode,urlsplit
from urllib.request import HTTPRedirectHandler,Request,build_opener

MAX_BODY=1024*1024
TEST_DESTINATIONS={f'99999999000{n}' for n in range(1,7)}
GOTYME_BIC='GOTYPHM2XXX'


class ProviderUnknown(RuntimeError):
    """No trustworthy final outcome; commitments must remain held."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise ProviderUnknown('Provider redirects are disabled')


def integer(value,*,positive=False):
    if type(value) is str and value.isascii() and value.isdigit():
        value=int(value)
    if type(value) is not int or not (1 if positive else 0)<=value<=2**63-1:
        raise ProviderUnknown('Invalid provider monetary integer')
    return value


def reference(value,prefix=None):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value) or (prefix and not value.startswith(prefix)):
        raise ProviderUnknown('Invalid provider reference')
    return value


def reject_live(value):
    if isinstance(value,dict):
        if 'livemode' in value and value['livemode'] is not False:
            raise ProviderUnknown('Non-test provider resource rejected')
        for child in value.values():
            reject_live(child)
    elif isinstance(value,list):
        for child in value:
            reject_live(child)


@dataclass(frozen=True)
class CheckoutReceipt:
    provider_id:str
    checkout_url:str
    status:str
    gross_minor:int
    net_minor:int|None=None
    deductions_minor:int|None=None
    payment_id:str|None=None


@dataclass(frozen=True)
class TransferReceipt:
    provider_id:str
    status:str
    amount_minor:int
    fee_minor:int
    reference_number:str
    batch_id:str|None=None


class PayMongoSandboxClient:
    mode='SANDBOX'

    def __init__(self,secret_key,*,source_wallet_number=None,route_ref='route_gotyme_test',
                 test_destination='999999990001',transport=None,timeout=10):
        if not isinstance(secret_key,str) or not re.fullmatch(r'sk_test_[A-Za-z0-9]{8,}',secret_key):
            raise ValueError('A PayMongo sk_test_ key is required; live keys are rejected')
        if test_destination not in TEST_DESTINATIONS or not route_ref.startswith('route_'):
            raise ValueError('Only documented simulator destination numbers are allowed')
        if source_wallet_number is not None and (not isinstance(source_wallet_number,str) or not re.fullmatch(r'[0-9]{6,32}',source_wallet_number)):
            raise ValueError('Provide the test wallet source number from the provider dashboard')
        if type(timeout) not in (int,float) or not 0<timeout<=30:
            raise ValueError('Provider timeout must be bounded')
        self._secret_key=secret_key
        self._source_wallet=source_wallet_number
        self.route_ref=route_ref
        self._destination=test_destination
        self._transport=transport or self._http
        self.timeout=timeout
        self.scope_fingerprint=sha256((secret_key+'|'+str(source_wallet_number)+'|'+route_ref+'|'+test_destination).encode()).hexdigest()

    @classmethod
    def from_environment(cls):
        return cls(os.environ.get('PAYMONGO_TEST_SECRET_KEY',''),source_wallet_number=os.environ.get('PAYMONGO_TEST_WALLET_NUMBER'),
                   test_destination=os.environ.get('PAYMONGO_SIMULATOR_DESTINATION','999999990001'))

    def _http(self,method,path,body,headers):
        if not re.fullmatch(r'sk_test_[A-Za-z0-9]{8,}',self._secret_key) or set(headers)-{'Idempotency-Key'}:
            raise ProviderUnknown('Test credentials and fixed provider headers are required')
        auth=b64encode((self._secret_key+':').encode()).decode()
        request=Request('https://api.paymongo.com'+path,data=json.dumps(body,separators=(',',':')).encode() if body is not None else None,
                        headers={'Authorization':'Basic '+auth,'Content-Type':'application/json','Accept':'application/json',**headers},method=method)
        try:
            with build_opener(_NoRedirect()).open(request,timeout=self.timeout) as response:
                raw=response.read(MAX_BODY+1)
            if len(raw)>MAX_BODY:
                raise ProviderUnknown('Provider response exceeds size limit')
            return json.loads(raw)
        except (HTTPError,URLError,TimeoutError,OSError,ValueError):
            raise ProviderUnknown('Provider request has no verified outcome; do not repeat creation') from None

    def _call(self,method,path,body=None,headers=None):
        if not path.startswith(('/v1/checkout_sessions/','/v2/checkout_sessions','/v2/batch_transfers','/v2/transfers?','/v2/wallets')) or '..' in path:
            raise ValueError('Unsupported provider endpoint')
        try:
            response=self._transport(method,path,body,headers or {})
            if not isinstance(response,dict):
                raise ProviderUnknown('Invalid provider response')
            reject_live(response)
            return response
        except ProviderUnknown:
            raise
        except Exception:
            raise ProviderUnknown('Provider transport failed; outcome unknown') from None

    def create_checkout(self,reference_number,gross_minor,*,success_url='https://example.com/sandbox/success',cancel_url='https://example.com/sandbox/cancel'):
        reference(reference_number)
        integer(gross_minor,positive=True)
        for url in (success_url,cancel_url):
            parsed=urlsplit(url)
            if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Redirect URLs must be public HTTPS URLs without credentials/query data')
        response=self._call('POST','/v2/checkout_sessions',{'data':{'attributes':{
            'line_items':[{'name':'Synthetic test order','amount':gross_minor,'currency':'PHP','quantity':1}],
            'payment_method_types':['card','qrph'],'success_url':success_url,'cancel_url':cancel_url,
            'reference_number':reference_number,'pass_on_fees':False}}})
        return self._checkout(response,gross_minor,reference_number,created=True)

    def get_checkout(self,provider_id,gross_minor,reference_number):
        reference(provider_id,'cs_')
        return self._checkout(self._call('GET','/v1/checkout_sessions/'+provider_id),gross_minor,reference_number,provider_id=provider_id)

    def _checkout(self,response,gross_minor,reference_number,*,created=False,provider_id=None):
        try:
            data=response['data'];attrs=data['attributes'];identifier=reference(data['id'],'cs_')
            if data.get('type')!='checkout_session' or attrs['livemode'] is not False or (provider_id and identifier!=provider_id):
                raise ProviderUnknown('Checkout mode/identity mismatch')
            if (not created and attrs['reference_number']!=reference_number) or ('reference_number' in attrs and attrs['reference_number']!=reference_number):
                raise ProviderUnknown('Checkout correlation mismatch')
            url=attrs['checkout_url'];parsed=urlsplit(url)
            if parsed.scheme!='https' or parsed.hostname!='checkout.paymongo.com' or parsed.username or parsed.password or parsed.port not in (None,443):
                raise ProviderUnknown('Unexpected checkout destination')
            payments=[p for p in attrs.get('payments',[]) if p.get('attributes',{}).get('status')=='paid']
            if len(payments)>1:
                raise ProviderUnknown('Multiple paid attempts require reconciliation')
            if payments:
                paid=payments[0]['attributes'];pid=reference(payments[0]['id'],'pay_')
                amount=integer(paid['amount'],positive=True);net=integer(paid['net_amount'])
                if paid['livemode'] is not False or paid['currency']!='PHP' or amount!=gross_minor or net>amount or paid.get('disputed') is not False or paid.get('refunds')!=[]:
                    raise ProviderUnknown('Paid checkout amount/mode/refund mismatch')
                # A paid event, available_at or scheduled credited_at is not cleared-wallet proof.
                return CheckoutReceipt(identifier,url,'PAID_UNCLEARED',amount,net,amount-net,pid)
            return CheckoutReceipt(identifier,url,'EXPIRED' if attrs.get('status')=='expired' else 'READY',gross_minor)
        except (KeyError,TypeError,ValueError):
            raise ProviderUnknown('Incomplete checkout response') from None

    def create_payout(self,payout_id,amount_minor,fee_cap_minor):
        reference(payout_id);integer(amount_minor,positive=True);integer(fee_cap_minor)
        if self._source_wallet is None:
            raise ValueError('Test wallet source number is not configured')
        request={'transfers':[{'provider':'pesonet','amount':amount_minor,'currency':'PHP','reference_number':payout_id,
            'source_account':{'number':self._source_wallet,'name':'Sandbox source','bic':'PAEYPHM2XXX','bank_name':'PayMongo'},
            'destination_account':{'number':self._destination,'name':'Sandbox destination','bic':GOTYME_BIC,'bank_name':'GoTyme Bank Corporation'}}]}
        response=self._call('POST','/v2/batch_transfers',request,{'Idempotency-Key':payout_id})
        return self._transfer(response,payout_id,amount_minor,fee_cap_minor)

    def lookup_payout(self,payout_id,amount_minor,fee_cap_minor):
        reference(payout_id)
        return self._transfer(self._call('GET','/v2/transfers?'+urlencode({'reference_number':payout_id})),payout_id,amount_minor,fee_cap_minor)

    def _transfer(self,response,payout_id,amount_minor,fee_cap_minor):
        try:
            data=response['data']
            batch_id=data.get('id') if isinstance(data,dict) else None
            records=data['transfers'] if isinstance(data,dict) else data
            if not isinstance(records,list) or len(records)!=1:
                raise ProviderUnknown('Transfer lookup is ambiguous or empty')
            item=records[0]
            if item['livemode'] is not False or item['reference_number']!=payout_id or item['currency']!='PHP' or item['provider']!='pesonet':
                raise ProviderUnknown('Transfer mode/correlation mismatch')
            target=item['destination_account']
            if target['number']!=self._destination or target['bic']!=GOTYME_BIC:
                raise ProviderUnknown('Transfer destination mismatch')
            amount,fee=integer(item['amount'],positive=True),integer(item['fee'])
            if amount!=amount_minor or fee>fee_cap_minor or item['status'] not in ('pending','succeeded','failed'):
                raise ProviderUnknown('Transfer amount/fee/status mismatch')
            return TransferReceipt(reference(item['id'],'tr_'),item['status'].upper(),amount,fee,payout_id,batch_id)
        except (KeyError,TypeError,ValueError):
            raise ProviderUnknown('Incomplete transfer response') from None


def verify_test_webhook(body:bytes,signature:str,secret:str,*,now=None,tolerance=300):
    """Authenticate the exact body; notifications alone never credit cash or authorize payout."""
    if not isinstance(body,bytes) or len(body)>MAX_BODY or not isinstance(signature,str) or len(signature)>2048 or not isinstance(secret,str) or not secret.startswith('whsk_'):
        raise ValueError('Invalid test webhook inputs')
    try:
        fields={}
        for pair in signature.split(','):
            key,value=pair.strip().split('=',1)
            if key in fields:
                raise ValueError('Duplicate signature field')
            fields[key]=value
        if set(fields)!={'t','te','li'} or fields['li'] or not re.fullmatch(r'[0-9a-f]{64}',fields['te']):
            raise ValueError('Test signature required')
        timestamp=int(fields['t'])
        if abs((time.time() if now is None else now)-timestamp)>tolerance:
            raise ValueError('Webhook timestamp is stale/future')
        digest=hmac.new(secret.encode(),fields['t'].encode()+b'.'+body,sha256).hexdigest()
        if not hmac.compare_digest(digest,fields['te']):
            raise ValueError('Invalid webhook signature')
        event=json.loads(body)['data']
        reference(event['id'],'evt_')
        if event['attributes']['livemode'] is not False:
            raise ValueError('Live webhook rejected')
        reject_live(event)
        return event
    except (KeyError,TypeError,ProviderUnknown):
        raise ValueError('Invalid test webhook') from None
