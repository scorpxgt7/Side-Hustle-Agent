from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

class OpportunityType(str, Enum):
    TRADING = "TRADING"
    ARBITRAGE = "ARBITRAGE"
    ONLINE_BUSINESS = "ONLINE_BUSINESS"
    AFFILIATE = "AFFILIATE"
    DIGITAL_PRODUCT = "DIGITAL_PRODUCT"
    COMPUTE = "COMPUTE"
    OTHER = "OTHER"

class Decision(str, Enum):
    REJECT = "REJECT"
    PAPER_TEST = "PAPER_TEST"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    EXECUTE = "EXECUTE"  # Reserved, never issued here.
    NO_ACTION = "NO_ACTION"

@dataclass(frozen=True)
class Opportunity:
    id: str
    type: OpportunityType
    title: str
    required_capital: Decimal
    expected_revenue: Decimal
    expected_cost: Decimal
    estimated_probability: Decimal
    liquidity_score: Decimal
    confidence: Decimal
    source: str
    failure_loss: Decimal = Decimal("0")

    @property
    def expected_net(self) -> Decimal:
        return self.expected_revenue - self.expected_cost

    @property
    def expected_value(self) -> Decimal:
        p = self.estimated_probability
        return p * self.expected_revenue - self.expected_cost - (Decimal("1") - p) * self.failure_loss

@dataclass(frozen=True)
class CapitalLimits:
    total_capital: Decimal
    reserve_ratio: Decimal
    max_single_allocation: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal
    max_leverage: Decimal

    @property
    def deployable_capital(self) -> Decimal:
        return self.total_capital * (Decimal("1") - self.reserve_ratio)

@dataclass(frozen=True)
class PortfolioState:
    allocated_today: Decimal
    realized_daily_pnl: Decimal
    peak_equity: Decimal
    current_equity: Decimal

    @property
    def drawdown(self) -> Decimal:
        return max(Decimal("0"), self.peak_equity - self.current_equity)

@dataclass(frozen=True)
class RiskDecision:
    decision: Decision
    reason: str
    opportunity_id: str
    checks: dict[str, bool] = field(default_factory=dict)
