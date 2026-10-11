"""Small SQLite persistence layer for opportunities, decisions, ledger and audit events."""
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any


class Repository:
    def __init__(self, path: str | Path = "data/side_hustle.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        self.conn.commit()

    def _init_schema(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS opportunities (
            id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL,
            required_capital TEXT NOT NULL, expected_revenue TEXT NOT NULL,
            expected_cost TEXT NOT NULL, probability TEXT NOT NULL,
            liquidity_score TEXT NOT NULL, confidence TEXT NOT NULL,
            source TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id TEXT NOT NULL,
            decision TEXT NOT NULL, reason TEXT NOT NULL, checks_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL,
            reference TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL, entity_id TEXT, payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS opportunity_outcomes (
            opportunity_id TEXT PRIMARY KEY, failure_revenue TEXT NOT NULL,
            failure_cost TEXT NOT NULL, leverage TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_opportunity ON decisions(opportunity_id);
        CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_id);
        """)

    @staticmethod
    def _decimal(value: Decimal) -> str:
        return format(value, "f")

    def save_opportunity(self, o: Any) -> None:
        self.conn.execute("""INSERT INTO opportunities
        (id,type,title,required_capital,expected_revenue,expected_cost,probability,liquidity_score,confidence,source)
        VALUES (?,?,?,?,?,?,?,?,?,?)""", (
            o.id, o.type.value, o.title, self._decimal(o.required_capital),
            self._decimal(o.expected_revenue), self._decimal(o.expected_cost),
            self._decimal(o.estimated_probability), self._decimal(o.liquidity_score),
            self._decimal(o.confidence), o.source))

        self.conn.execute("INSERT INTO opportunity_outcomes VALUES (?,?,?,?)",
                          (o.id, self._decimal(o.failure_revenue), self._decimal(o.failure_cost), self._decimal(o.leverage)))

    def save_decision(self, opportunity_id: str, decision: Any) -> None:
        self.conn.execute("INSERT INTO decisions(opportunity_id,decision,reason,checks_json) VALUES (?,?,?,?)",
                          (opportunity_id, decision.decision.value, decision.reason, json.dumps(decision.checks, sort_keys=True)))

    def audit(self, event_id: str, event_type: str, entity_id: str | None, payload: dict) -> None:
        self.conn.execute("INSERT INTO audit_events(event_id,event_type,entity_id,payload_json) VALUES (?,?,?,?)",
                          (event_id, event_type, entity_id, json.dumps(payload, sort_keys=True, default=str)))

    def ledger(self, event_id: str, kind: str, amount: Decimal, currency: str = "USD", reference: str | None = None) -> None:
        from src.core.models import finite_decimal
        finite_decimal(amount, "amount")
        self.conn.execute("INSERT INTO ledger(event_id,kind,amount,currency,reference) VALUES (?,?,?,?,?)",
                          (event_id, kind, self._decimal(amount), currency, reference))

    def close(self) -> None:
        self.conn.close()
