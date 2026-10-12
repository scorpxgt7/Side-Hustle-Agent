"""Additive paper-reservation schema; never infer accounts from the legacy ledger."""
from hashlib import sha256

SCHEMA_VERSION = 1
TABLES = {"paper_schema", "paper_accounts", "paper_observation_currencies", "paper_requests", "paper_orders", "capital_reservations"}
STATEMENTS = (
    """CREATE TABLE paper_schema (
        id INTEGER PRIMARY KEY CHECK(id = 1), version INTEGER NOT NULL,
        checksum TEXT NOT NULL)""",
    """CREATE TABLE paper_accounts (
        id TEXT PRIMARY KEY NOT NULL, currency TEXT NOT NULL CHECK(currency IN ('PHP','USD')),
        mode TEXT NOT NULL CHECK(mode = 'PAPER'), limits_json TEXT NOT NULL,
        genesis_event_id TEXT NOT NULL UNIQUE REFERENCES audit_events(event_id),
        cash_minor INTEGER NOT NULL CHECK(typeof(cash_minor) = 'integer' AND cash_minor >= 0),
        reserved_cash_minor INTEGER NOT NULL DEFAULT 0 CHECK(typeof(reserved_cash_minor) = 'integer' AND reserved_cash_minor >= 0),
        reserved_loss_minor INTEGER NOT NULL DEFAULT 0 CHECK(typeof(reserved_loss_minor) = 'integer' AND reserved_loss_minor >= 0),
        realized_daily_pnl_minor INTEGER NOT NULL DEFAULT 0 CHECK(typeof(realized_daily_pnl_minor) = 'integer'),
        peak_equity_minor INTEGER NOT NULL CHECK(typeof(peak_equity_minor) = 'integer' AND peak_equity_minor >= 0),
        current_equity_minor INTEGER NOT NULL CHECK(typeof(current_equity_minor) = 'integer' AND current_equity_minor >= 0),
        status TEXT NOT NULL CHECK(status IN ('ACTIVE','HALTED')), halt_reason TEXT,
        version INTEGER NOT NULL DEFAULT 0 CHECK(typeof(version) = 'integer' AND version >= 0),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK(reserved_cash_minor <= cash_minor), CHECK(current_equity_minor <= peak_equity_minor))""",
    """CREATE TABLE paper_observation_currencies (
        opportunity_id TEXT PRIMARY KEY NOT NULL REFERENCES opportunities(id),
        currency TEXT NOT NULL CHECK(currency IN ('PHP','USD')))""",
    """CREATE TABLE paper_requests (
        account_id TEXT NOT NULL REFERENCES paper_accounts(id), request_id TEXT NOT NULL,
        payload_digest TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT NOT NULL,
        decision_id INTEGER NOT NULL REFERENCES decisions(id),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(account_id, request_id))""",
    """CREATE TABLE paper_orders (
        id TEXT PRIMARY KEY NOT NULL, account_id TEXT NOT NULL REFERENCES paper_accounts(id),
        request_id TEXT NOT NULL, opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
        decision_id INTEGER NOT NULL REFERENCES decisions(id),
        status TEXT NOT NULL CHECK(status = 'RESERVED'),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(id, account_id), UNIQUE(account_id, request_id), UNIQUE(account_id, opportunity_id),
        FOREIGN KEY(account_id, request_id) REFERENCES paper_requests(account_id, request_id))""",
    """CREATE TABLE capital_reservations (
        order_id TEXT PRIMARY KEY NOT NULL, account_id TEXT NOT NULL REFERENCES paper_accounts(id),
        cash_minor INTEGER NOT NULL CHECK(typeof(cash_minor) = 'integer' AND cash_minor > 0),
        loss_minor INTEGER NOT NULL CHECK(typeof(loss_minor) = 'integer' AND loss_minor >= cash_minor),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(order_id, account_id) REFERENCES paper_orders(id, account_id))""",
)
SCHEMA_CHECKSUM = sha256("\n".join(STATEMENTS).encode()).hexdigest()


def validate_paper_schema(connection):
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "paper_schema" in names:
        rows = connection.execute("SELECT id,version,checksum FROM paper_schema").fetchall()
        if (len(rows) != 1 or tuple(rows[0]) != (1, SCHEMA_VERSION, SCHEMA_CHECKSUM)
                or not TABLES <= names):
            raise RuntimeError("unsupported or incomplete paper schema; writes disabled")
        return True
    if TABLES & names:
        raise RuntimeError("unversioned paper tables; migration requires investigation")
    return False


def initialize_paper_schema(repository):
    with repository.transaction():
        if validate_paper_schema(repository.conn):
            return
        for statement in STATEMENTS:
            repository.conn.execute(statement)
        repository.conn.execute("INSERT INTO paper_schema VALUES (1,?,?)", (SCHEMA_VERSION, SCHEMA_CHECKSUM))
