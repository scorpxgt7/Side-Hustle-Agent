"""Core domain models. Provider and LLM independent."""
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from fractions import Fraction
from typing import Optional
from .arithmetic import exact_decimal, exact_subtract, validate_decimal


def finite_decimal(value, name):
    validate_decimal(value, name)


def validate_identifier(value, name="id"):
    if (not isinstance(value, str) or not value
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError(f"{name} must be a non-empty string without whitespace/control characters")


def validate_fields(instance, nonnegative=(), ratios=(), positive=()):
    for name in (*nonnegative, *ratios, *positive):
        value = getattr(instance, name)
        finite_decimal(value, name)
        if name in ratios and not 0 <= value <= 1:
            raise ValueError(f"{name} must be between 0 and 1")
        if name in nonnegative and value < 0:
            raise ValueError(f"{name} must be non-negative")
        if name in positive and value <= 0:
            raise ValueError(f"{name} must be positive")


class OpportunityType(str, Enum):
    TRADING = "trading"
    ARBITRAGE = "arbitrage"
    ONLINE_BUSINESS = "online_business"
    AFFILIATE = "affiliate"
    DIGITAL_PRODUCT = "digital_product"
    COMPUTE = "compute"
    OTHER = "other"


class Decision(str, Enum):
    REJECT = "reject"
    PAPER_TEST = "paper_test"
    APPROVAL_REQUIRED = "approval_required"
    EXECUTE = "execute"
    NO_ACTION = "no_action"


@dataclass(frozen=True)
class Opportunity:
    id: str
    type: OpportunityType
    title: str
    required_capital: Decimal
    expected_revenue: Decimal
    expected_cost: Decimal
    estimated_probability: Decimal
    liquidity_score: Decimal = Decimal("1")
    confidence: Decimal = Decimal("0")
    source: Optional[str] = None

    failure_revenue: Decimal = Decimal("0")
    failure_cost: Decimal = Decimal("0")
    leverage: Decimal = Decimal("1")

    def __post_init__(self):
        validate_identifier(self.id)
        validate_fields(self,
            nonnegative=("expected_revenue", "expected_cost", "failure_revenue", "failure_cost"),
            ratios=("estimated_probability", "confidence", "liquidity_score"),
            positive=("required_capital", "leverage"))
        if self.leverage < 1:
            raise ValueError("leverage must be at least 1")

    @property
    def expected_net(self) -> Decimal:
        return exact_subtract(self.expected_revenue, self.expected_cost)

    @property
    def expected_value(self) -> Decimal:
        p = Fraction(self.estimated_probability)
        success = Fraction(self.expected_revenue) - Fraction(self.expected_cost)
        failure = (Fraction(self.failure_revenue) - Fraction(self.expected_cost)
                   - Fraction(self.failure_cost))
        return exact_decimal(p * success + (1 - p) * failure)


@dataclass(frozen=True)
class CapitalLimits:
    total_capital: Decimal
    reserve_ratio: Decimal
    max_single_allocation: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal
    max_leverage: Decimal = Decimal("1")

    def __post_init__(self):
        validate_fields(self,
            nonnegative=("total_capital", "max_single_allocation", "max_daily_loss", "max_drawdown"),
            ratios=("reserve_ratio",), positive=("max_leverage",))
        if self.max_leverage < 1:
            raise ValueError("max_leverage must be at least 1")

    @property
    def deployable_capital(self) -> Decimal:
        return exact_decimal(Fraction(self.total_capital) * (1 - Fraction(self.reserve_ratio)))


@dataclass(frozen=True)
class PortfolioState:
    allocated_today: Decimal = Decimal("0")
    realized_daily_pnl: Decimal = Decimal("0")
    peak_equity: Decimal = Decimal("0")
    current_equity: Decimal = Decimal("0")

    def __post_init__(self):
        validate_fields(self, nonnegative=("allocated_today", "peak_equity", "current_equity"))
        finite_decimal(self.realized_daily_pnl, "realized_daily_pnl")
        if self.current_equity > self.peak_equity:
            raise ValueError("peak_equity cannot be below current_equity")

    @property
    def drawdown(self) -> Decimal:
        if self.peak_equity <= 0:
            return Decimal("0")
        return max(Decimal("0"), exact_subtract(self.peak_equity, self.current_equity))


@dataclass
class RiskDecision:
    decision: Decision
    reason: str
    opportunity_id: str
    checks: dict = field(default_factory=dict)
