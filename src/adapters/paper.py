from dataclasses import dataclass
from decimal import Decimal
from src.core.models import finite_decimal
from src.core.arithmetic import exact_add, exact_subtract


@dataclass
class PaperLedger:
    balance: Decimal = Decimal("0")

    def __post_init__(self):
        finite_decimal(self.balance, "balance")
        if self.balance < 0:
            raise ValueError("balance must be non-negative")

    def credit(self, amount: Decimal) -> None:
        self.__post_init__()
        finite_decimal(amount, "amount")
        if amount < 0:
            raise ValueError("credit amount must be non-negative")
        balance = exact_add(self.balance, amount)
        finite_decimal(balance, "balance")
        self.balance = balance

    def debit(self, amount: Decimal) -> None:
        self.__post_init__()
        finite_decimal(amount, "amount")
        if amount < 0:
            raise ValueError("debit amount must be non-negative")
        if amount > self.balance:
            raise ValueError("insufficient paper balance")
        balance = exact_subtract(self.balance, amount)
        finite_decimal(balance, "balance")
        self.balance = balance
