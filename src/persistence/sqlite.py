"""Transactional opportunity, decision, and audit persistence; not a trading ledger."""
import json
import sqlite3
from pathlib import Path
from ..core.models import Opportunity, RiskDecision
from ..core.economics import EconomicEvaluation

class Repository:
    def __init__(self, path: str = "data/side_hustle.db"):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS opportunities (
          id TEXT PRIMARY KEY, category TEXT NOT NULL, title TEXT NOT NULL,
          source TEXT NOT NULL, required_capital TEXT NOT NULL, expected_value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS decisions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id TEXT NOT NULL,
          decision TEXT NOT NULL, reason TEXT NOT NULL,
          recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(opportunity_id) REFERENCES opportunities(id));
        CREATE TABLE IF NOT EXISTS audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL,
          event_type TEXT NOT NULL, payload TEXT NOT NULL,
          recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        """)
        self.conn.commit()

    def record(self, o: Opportunity, evaluation: EconomicEvaluation, decision: RiskDecision) -> None:
        with self.conn:
            self.conn.execute("""INSERT INTO opportunities
              (id,category,title,source,required_capital,expected_value)
              VALUES (?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET category=excluded.category,
                 title=excluded.title,source=excluded.source,
                 required_capital=excluded.required_capital,expected_value=excluded.expected_value""",
                (o.id, o.type.value, o.title, o.source,
                 str(o.required_capital), str(evaluation.expected_value)))
            self.conn.execute("INSERT INTO decisions (opportunity_id,decision,reason) VALUES (?,?,?)",
                              (o.id, decision.decision.value, decision.reason))
            payload = json.dumps({"decision":decision.decision.value, "reason":decision.reason,
                                  "expected_value":str(evaluation.expected_value),
                                  "risk_adjusted_score":str(evaluation.risk_adjusted_score),
                                  "checks":decision.checks},sort_keys=True)
            self.conn.execute("INSERT INTO audit_events (entity_id,event_type,payload) VALUES (?,?,?)",
                              (o.id, "opportunity_evaluated", payload))

    def close(self) -> None:
        self.conn.close()
