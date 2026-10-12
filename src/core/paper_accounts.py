"""Synthetic account genesis and atomic cash/loss reservations. No live path."""
from dataclasses import asdict, dataclass
from decimal import Decimal
from datetime import datetime, timezone
from fractions import Fraction
from hashlib import sha256
import json
from math import ceil
from uuid import uuid4

from .arithmetic import ARITHMETIC_POLICY_VERSION, exact_decimal
from .economics import EconomicEvaluator
from .models import CapitalLimits, Decision, Opportunity, OpportunityType, PortfolioState, RiskDecision, finite_decimal, validate_identifier
from .risk import RISK_RULE_VERSION, RiskGovernor
from .paper_journal import open_exposure, post_journal, projection_matches

RESERVATION_POLICY_VERSION = "paper-reservations-v1"
MAX_MINOR_UNITS = 2**63 - 1
MONETARY_FIELDS = ("required_capital", "expected_revenue", "expected_cost", "failure_revenue", "failure_cost")


class RequestConflictError(ValueError):
    """A stable request or observation identity was reused with conflicting inputs."""


def minor_units(value: Decimal) -> int:
    finite_decimal(value, "money")
    scaled = Fraction(value) * 100
    if scaled.denominator != 1 or abs(scaled.numerator) > MAX_MINOR_UNITS:
        raise ValueError("paper money must be exact PHP/USD cents within signed 64-bit storage")
    return scaled.numerator


def money(value: int) -> Decimal:
    return exact_decimal(Fraction(value, 100))


def canonical_json(value) -> str:
    def encode(item):
        if isinstance(item, Decimal):
            return format(exact_decimal(Fraction(item)), "f")
        raise TypeError(f"unsupported payload type: {type(item).__name__}")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=encode)


def validate_currency(currency):
    if currency not in ("PHP", "USD"):
        raise ValueError("paper currency must be PHP or USD; no FX conversion is supported")


@dataclass(frozen=True)
class PaperActionSpec:
    currency: str
    cash_required: Decimal
    worst_case_loss: Decimal
    capital_treatment: str

    def __post_init__(self):
        validate_currency(self.currency)
        if self.capital_treatment not in ("refundable", "expensed"):
            raise ValueError("capital treatment must be explicitly refundable or expensed")
        cash = minor_units(self.cash_required)
        loss = minor_units(self.worst_case_loss)
        if cash <= 0 or loss < cash:
            raise ValueError("cash must be positive; initial paper actions reserve at least full cash loss")


@dataclass(frozen=True)
class PaperAccountState:
    account_id: str
    currency: str
    cash: Decimal
    reserved_cash: Decimal
    reserved_loss: Decimal
    available_cash: Decimal
    status: str
    version: int
    halt_reason: str | None
    deployed_cash: Decimal
    open_loss: Decimal
    realized_pnl: Decimal
    current_equity: Decimal
    peak_equity: Decimal


@dataclass(frozen=True)
class ReservationResult:
    account_id: str
    request_id: str
    status: str
    order_id: str | None
    decision_id: int
    reason: str
    cash_reserved: Decimal
    loss_reserved: Decimal
    checks: dict


class PaperAccountService:
    def __init__(self, repository, *, mode="PAPER", clock=None):
        if mode != "PAPER":
            raise ValueError("only synthetic PAPER accounts are supported")
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.evaluator = EconomicEvaluator()
        self.risk = RiskGovernor()

    def _load(self, account_id):
        row = self.repository.conn.execute("SELECT * FROM paper_accounts WHERE id=?", (account_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown paper account: {account_id}")
        limits = CapitalLimits(**{key: Decimal(value) for key, value in json.loads(row["limits_json"]).items()})
        return row, limits

    @staticmethod
    def _protected(limits):
        return ceil(minor_units(limits.total_capital) * Fraction(limits.reserve_ratio))

    def _state(self, row, limits):
        available = (max(0, row["cash_minor"] - self._protected(limits) - row["reserved_cash_minor"])
                     if row["status"] == "ACTIVE" else 0)
        return PaperAccountState(row["id"], row["currency"], money(row["cash_minor"]),
                                 money(row["reserved_cash_minor"]), money(row["reserved_loss_minor"]),
                                 money(available), row["status"], row["version"], row["halt_reason"],
                                 *(money(v) for v in open_exposure(self.repository.conn, row["id"])),
                                 money(row["realized_daily_pnl_minor"]), money(row["current_equity_minor"]),
                                 money(row["peak_equity_minor"]))

    def _integrity(self, row, limits):
        return projection_matches(self.repository.conn, row)

    def _halt(self, row, reason):
        if row["status"] == "HALTED":
            return
        self.repository.conn.execute("UPDATE paper_accounts SET status='HALTED',halt_reason=?,version=version+1 WHERE id=?",
                                     (reason, row["id"]))
        self.repository.audit(str(uuid4()), "paper_account_halted", row["id"],
                              {"reason": reason, "previous_version": row["version"]})

    def create_account(self, account_id: str, limits: CapitalLimits, *, currency="PHP") -> PaperAccountState:
        validate_identifier(account_id, "account_id")
        validate_currency(currency)
        if not isinstance(limits, CapitalLimits) or limits.max_leverage != 1:
            raise ValueError("paper accounts require CapitalLimits with leverage 1")
        for field in ("total_capital", "max_single_allocation", "max_daily_loss", "max_drawdown"):
            minor_units(getattr(limits, field))
        capital = minor_units(limits.total_capital)
        policy = canonical_json(asdict(limits))
        with self.repository.transaction():
            if self.repository.conn.execute("SELECT 1 FROM paper_accounts WHERE id=?", (account_id,)).fetchone():
                raise RequestConflictError("paper account already exists; genesis is immutable")
            event_id = str(uuid4())
            self.repository.audit(event_id, "paper_account_created", account_id,
                                  {"synthetic": True, "currency": currency, "cash_minor": capital,
                                   "limits": json.loads(policy), "policy_version": RESERVATION_POLICY_VERSION})
            self.repository.conn.execute("""INSERT INTO paper_accounts
                (id,currency,mode,limits_json,genesis_event_id,cash_minor,peak_equity_minor,current_equity_minor,status)
                VALUES (?,?,'PAPER',?,?,?,?,?,'ACTIVE')""", (account_id, currency, policy, event_id, capital, capital, capital))
            post_journal(self.repository, account_id, event_id, "GENESIS", None,
                         [("cash", "debit", capital), ("equity", "credit", capital)])
            row, effective = self._load(account_id)
            result = self._state(row, effective)
        return result

    def get_account(self, account_id: str) -> PaperAccountState:
        validate_identifier(account_id, "account_id")
        with self.repository.transaction():
            row, limits = self._load(account_id)
            if not self._integrity(row, limits):
                self._halt(row, "paper projection does not match journal/order records")
                row, limits = self._load(account_id)
            result = self._state(row, limits)
        return result

    def halt(self, account_id: str, reason: str) -> PaperAccountState:
        validate_identifier(account_id, "account_id")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("halt reason is required")
        with self.repository.transaction():
            row, limits = self._load(account_id)
            self._halt(row, reason)
            row, limits = self._load(account_id)
            result = self._state(row, limits)
        return result

    def _ensure_observation(self, opportunity, currency):
        binding = self.repository.conn.execute("SELECT currency FROM paper_observation_currencies WHERE opportunity_id=?",
                                               (opportunity.id,)).fetchone()
        if binding is not None and binding[0] != currency:
            raise RequestConflictError("observation currency is immutable; use a new observation ID")
        row = self.repository.conn.execute("""SELECT o.*,x.failure_revenue,x.failure_cost,x.leverage
            FROM opportunities o LEFT JOIN opportunity_outcomes x ON x.opportunity_id=o.id
            WHERE o.id=?""", (opportunity.id,)).fetchone()
        if row is None:
            self.repository.save_opportunity(opportunity)
        else:
            values = {field: row[field] for field in ("id", "title", "source")}
            values["type"] = OpportunityType(row["type"])
            for field in (*MONETARY_FIELDS, "confidence", "liquidity_score", "leverage", "estimated_probability"):
                column = "probability" if field == "estimated_probability" else field
                if row[column] is None:
                    raise RequestConflictError("historical observation has incomplete outcome assumptions")
                values[field] = Decimal(row[column])
            if canonical_json(asdict(Opportunity(**values))) != canonical_json(asdict(opportunity)):
                raise RequestConflictError("opportunity ID already belongs to a different immutable observation")
        if binding is None:
            self.repository.conn.execute("INSERT INTO paper_observation_currencies VALUES (?,?)", (opportunity.id, currency))

    def submit(self, account_id: str, request_id: str, opportunity: Opportunity,
               action: PaperActionSpec, *, expires_at=None) -> ReservationResult:
        validate_identifier(account_id, "account_id")
        validate_identifier(request_id, "request_id")
        if not isinstance(opportunity, Opportunity) or not isinstance(action, PaperActionSpec):
            raise ValueError("submission requires Opportunity and PaperActionSpec")
        for field in MONETARY_FIELDS:
            minor_units(getattr(opportunity, field))
        cash, loss = minor_units(action.cash_required), minor_units(action.worst_case_loss)
        from .paper_lifecycle import utc_timestamp
        deadline = utc_timestamp(expires_at) if expires_at is not None else None
        request_payload = {"opportunity": asdict(opportunity), "action": asdict(action),
                                  "policy_version": RESERVATION_POLICY_VERSION,
                                  "arithmetic_policy": ARITHMETIC_POLICY_VERSION}
        if deadline is not None:
            request_payload["expires_at"] = deadline
        payload = canonical_json(request_payload)
        digest = sha256(payload.encode()).hexdigest()
        with self.repository.transaction():
            prior = self.repository.conn.execute("SELECT * FROM paper_requests WHERE account_id=? AND request_id=?",
                                                 (account_id, request_id)).fetchone()
            if prior is not None:
                if prior["payload_digest"] != digest or prior["payload_json"] != payload:
                    raise RequestConflictError("request ID was reused with conflicting inputs")
                restored = json.loads(prior["result_json"])
                for field in ("cash_reserved", "loss_reserved"):
                    restored[field] = Decimal(restored[field])
                return ReservationResult(**restored)
            row, limits = self._load(account_id)
            integrity = self._integrity(row, limits)
            if not integrity:
                self._halt(row, "paper projection does not match journal/order records")
                row, limits = self._load(account_id)
            self._ensure_observation(opportunity, action.currency)
            deployed, open_loss = open_exposure(self.repository.conn, account_id)
            state = PortfolioState(money(row["reserved_cash_minor"] + deployed), money(row["realized_daily_pnl_minor"]),
                                   money(row["peak_equity_minor"]), money(row["current_equity_minor"]))
            evaluation = self.evaluator.evaluate(opportunity)
            screened = self.risk.evaluate(opportunity, limits, state, evaluation)
            total_loss = row["reserved_loss_minor"] + open_loss + loss
            principal = minor_units(opportunity.required_capital)
            common_cost = minor_units(opportunity.expected_cost)
            failure_cost = minor_units(opportunity.failure_cost)
            if action.capital_treatment == "refundable":
                # In this contract failure_cost is principal write-off; all fees belong in common_cost.
                known_costs = principal + common_cost
                contract_valid = failure_cost <= principal
            else:
                # Purchase principal is already expensed; do not charge it a second time.
                known_costs = common_cost + failure_cost
                contract_valid = common_cost >= principal
            duplicate = self.repository.conn.execute("SELECT 1 FROM paper_orders WHERE account_id=? AND opportunity_id=?",
                                                     (account_id, opportunity.id)).fetchone() is not None
            checks = {**screened.checks,
                      "account_integrity": integrity, "account_active": row["status"] == "ACTIVE",
                      "currency_matches": action.currency == row["currency"], "observation_not_reserved": not duplicate,
                      "capital_contract_valid": contract_valid,
                      "deadline_in_future": deadline is None or deadline > utc_timestamp(self.clock()),
                      "cash_covers_known_exposure": cash >= max(principal, known_costs),
                      "cash_within_single_allocation": cash <= minor_units(limits.max_single_allocation),
                      "cash_available": cash <= row["cash_minor"] - self._protected(limits) - row["reserved_cash_minor"],
                      "cash_within_deployable_allocation": row["reserved_cash_minor"] + deployed + cash <= minor_units(limits.total_capital) - self._protected(limits),
                      "prospective_daily_loss": row["realized_daily_pnl_minor"] - total_loss > -minor_units(limits.max_daily_loss),
                      "prospective_drawdown": row["peak_equity_minor"] - row["current_equity_minor"] + total_loss < minor_units(limits.max_drawdown)}
            approved = all(checks.values())
            reason = ("Cash and conservative loss capacity reserved in PAPER mode." if approved else
                      "Failed hard checks: " + ", ".join(key for key, ok in checks.items() if not ok))
            decision = RiskDecision(Decision.PAPER_TEST if approved else Decision.REJECT, reason, opportunity.id, checks)
            decision_id = self.repository.save_decision(opportunity.id, decision)
            order_id = str(uuid4()) if approved else None
            result = ReservationResult(account_id, request_id, "RESERVED" if approved else "REJECTED",
                                       order_id, decision_id, reason, money(cash if approved else 0),
                                       money(loss if approved else 0), checks)
            self.repository.conn.execute("INSERT INTO paper_requests(account_id,request_id,payload_digest,payload_json,result_json,decision_id) VALUES (?,?,?,?,?,?)",
                                         (account_id, request_id, digest, payload, canonical_json(asdict(result)), decision_id))
            if approved:
                self.repository.conn.execute("INSERT INTO paper_orders(id,account_id,request_id,opportunity_id,decision_id,status,expires_at) VALUES (?,?,?,?,?,'RESERVED',?)",
                                             (order_id, account_id, request_id, opportunity.id, decision_id, deadline))
                self.repository.conn.execute("INSERT INTO capital_reservations(order_id,account_id,cash_minor,loss_minor) VALUES (?,?,?,?)",
                                             (order_id, account_id, cash, loss))
                changed = self.repository.conn.execute("UPDATE paper_accounts SET reserved_cash_minor=?,reserved_loss_minor=?,version=version+1 WHERE id=? AND version=?",
                                                       (row["reserved_cash_minor"] + cash, row["reserved_loss_minor"] + loss, account_id, row["version"]))
                if changed.rowcount != 1:
                    raise RuntimeError("paper account version changed; retry the same request")
            self.repository.audit(str(uuid4()), "paper_reservation_decision", opportunity.id,
                                  {"schema_version": 1, "rule_version": RISK_RULE_VERSION,
                                   "arithmetic_policy": ARITHMETIC_POLICY_VERSION,
                                   "reservation_policy": RESERVATION_POLICY_VERSION,
                                   "account_id": account_id, "account_version_before": row["version"],
                                   "opportunity": asdict(opportunity), "action": asdict(action),
                                   "limits": asdict(limits), "portfolio_state": asdict(state),
                                   "evaluation": asdict(evaluation), "decision": decision.decision.value,
                                   "result": asdict(result), "account_after": asdict(self._state(*self._load(account_id)))})
        return result
