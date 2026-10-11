from dataclasses import dataclass
from decimal import Decimal
from src.core.models import finite_decimal


@dataclass
class PaperLedger:
    balance: Decimal = Decimal("0")

    def __post_init__(self):
        finite_decimal(self.balance, "balance")
        if self.balance < 0:
            raise ValueError("balance must be non-negative")

    def credit(self, amount: Decimal) -> None:
        finite_decimal(amount, "amount")
        if amount < 0:
            raise ValueError("credit amount must be non-negative")
        self.balance += amount

    def debit(self, amount: Decimal) -> None:
        finite_decimal(amount, "amount")
        if amount < 0:
            raise ValueError("debit amount must be non-negative")
        if amount > self.balance:
            raise ValueError("insufficient paper balance")
        self.balance -= amount
