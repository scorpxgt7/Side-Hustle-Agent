"""Explicit offline integration fixtures, never customer income or executable card access."""
from hashlib import sha256
from .paymongo import CheckoutReceipt,TransferReceipt


class OfflineProvider:
    mode='OFFLINE_FIXTURE'
    route_ref='route_gotyme_test'
    scope_fingerprint=sha256(b'offline-synthetic-fixture-v1').hexdigest()

    def create_checkout(self,reference_number,gross_minor):
        identifier='cs_fixture_'+reference_number
        return CheckoutReceipt(identifier,'https://checkout.paymongo.com/'+identifier,'READY',gross_minor)

    def get_checkout(self,provider_id,gross_minor,reference_number):
        return CheckoutReceipt(provider_id,'https://checkout.paymongo.com/'+provider_id,'PAID_UNCLEARED',gross_minor,
                               gross_minor-500,500,'pay_fixture_'+reference_number)

    def create_payout(self,payout_id,amount_minor,fee_cap_minor):
        return TransferReceipt('tr_fixture_'+payout_id,'SUCCEEDED',amount_minor,fee_cap_minor,payout_id)

    def lookup_payout(self,payout_id,amount_minor,fee_cap_minor):
        return self.create_payout(payout_id,amount_minor,fee_cap_minor)
