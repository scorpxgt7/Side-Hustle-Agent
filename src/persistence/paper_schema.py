"""Versioned synthetic accounting schema. Known v1 upgrades are all-or-nothing."""
from hashlib import sha256
import json

V1_STATEMENTS = (
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
V1_CHECKSUM = sha256("\n".join(V1_STATEMENTS).encode()).hexdigest()
V1_TABLES = {"paper_schema", "paper_accounts", "paper_observation_currencies",
             "paper_requests", "paper_orders", "capital_reservations"}
ORDER_DDL = V1_STATEMENTS[4].replace("CHECK(status = 'RESERVED')",
    "CHECK(status IN ('RESERVED','FILLED','CANCELLED','EXPIRED','CLOSED'))").replace(
    "created_at TEXT", "version INTEGER NOT NULL DEFAULT 0 CHECK(typeof(version)='integer' AND version>=0), expires_at TEXT, created_at TEXT")
EXTRA_STATEMENTS = (
    """CREATE TABLE paper_positions (
        order_id TEXT PRIMARY KEY NOT NULL, account_id TEXT NOT NULL,
        treatment TEXT NOT NULL CHECK(treatment IN ('refundable','expensed')),
        capital_minor INTEGER NOT NULL CHECK(typeof(capital_minor)='integer' AND capital_minor>0),
        fees_minor INTEGER NOT NULL CHECK(typeof(fees_minor)='integer' AND fees_minor>=0),
        cash_spent_minor INTEGER NOT NULL CHECK(typeof(cash_spent_minor)='integer' AND cash_spent_minor>0),
        entry_expense_minor INTEGER NOT NULL CHECK(typeof(entry_expense_minor)='integer' AND entry_expense_minor>=0),
        loss_minor INTEGER NOT NULL CHECK(typeof(loss_minor)='integer' AND loss_minor>=0),
        status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED')),
        returned_minor INTEGER, revenue_minor INTEGER, exit_fees_minor INTEGER, pnl_minor INTEGER,
        FOREIGN KEY(order_id,account_id) REFERENCES paper_orders(id,account_id),
        CHECK(cash_spent_minor=capital_minor+fees_minor),
        CHECK((status='OPEN' AND returned_minor IS NULL AND revenue_minor IS NULL AND exit_fees_minor IS NULL AND pnl_minor IS NULL)
           OR (status='CLOSED' AND typeof(returned_minor)='integer' AND returned_minor>=0
               AND typeof(revenue_minor)='integer' AND revenue_minor>=0
               AND typeof(exit_fees_minor)='integer' AND exit_fees_minor>=0 AND typeof(pnl_minor)='integer')))""",
    """CREATE TABLE paper_lifecycle_events (
        account_id TEXT NOT NULL, event_key TEXT NOT NULL, order_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('FILL','CANCEL','EXPIRE','SETTLE')),
        payload_digest TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT NOT NULL,
        audit_event_id TEXT NOT NULL UNIQUE REFERENCES audit_events(event_id) DEFERRABLE INITIALLY DEFERRED,
        PRIMARY KEY(account_id,event_key),
        FOREIGN KEY(order_id,account_id) REFERENCES paper_orders(id,account_id))""",
    """CREATE TABLE paper_journal_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL REFERENCES paper_accounts(id),
        order_id TEXT, event_id TEXT NOT NULL UNIQUE REFERENCES audit_events(event_id) DEFERRABLE INITIALLY DEFERRED,
        kind TEXT NOT NULL CHECK(kind IN ('GENESIS','FILL','SETTLEMENT')),
        currency TEXT NOT NULL CHECK(currency IN ('PHP','USD')), postings_json TEXT NOT NULL,
        UNIQUE(order_id,kind), FOREIGN KEY(order_id,account_id) REFERENCES paper_orders(id,account_id),
        CHECK((kind='GENESIS' AND order_id IS NULL) OR (kind!='GENESIS' AND order_id IS NOT NULL)))""",
    """CREATE TABLE paper_journal_postings (
        entry_id INTEGER NOT NULL REFERENCES paper_journal_entries(id), line INTEGER NOT NULL,
        bucket TEXT NOT NULL CHECK(bucket IN ('cash','asset','equity','income','expense')),
        side TEXT NOT NULL CHECK(side IN ('debit','credit')),
        amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer' AND amount_minor>=0),
        PRIMARY KEY(entry_id,line))""",
)
TRIGGER_STATEMENTS = tuple(
    f"CREATE TRIGGER {table}_{operation.lower()}_blocked BEFORE {operation} ON {table} "
    "BEGIN SELECT RAISE(ABORT,'paper accounting history is immutable'); END"
    for table in ("paper_journal_entries", "paper_journal_postings", "paper_lifecycle_events")
    for operation in ("UPDATE", "DELETE"))
STATEMENTS = (*V1_STATEMENTS[:4], ORDER_DDL, V1_STATEMENTS[5], *EXTRA_STATEMENTS, *TRIGGER_STATEMENTS)
V2_STATEMENTS = STATEMENTS
V2_CHECKSUM = sha256("\n".join(V2_STATEMENTS).encode()).hexdigest()
V2_TABLES = V1_TABLES | {"paper_positions", "paper_lifecycle_events", "paper_journal_entries", "paper_journal_postings"}
SCHEMA_VERSION = 3
PAYOUT_STATEMENTS = (
    """CREATE TABLE pilot_profiles (
        account_id TEXT PRIMARY KEY REFERENCES paper_accounts(id), policy_json TEXT NOT NULL,
        audit_event_id TEXT NOT NULL UNIQUE REFERENCES audit_events(event_id))""",
    """CREATE TABLE paper_payouts (
        id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES paper_accounts(id),
        route_ref TEXT NOT NULL, amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer' AND amount_minor>0),
        fee_cap_minor INTEGER NOT NULL CHECK(typeof(fee_cap_minor)='integer' AND fee_cap_minor>=0),
        status TEXT NOT NULL CHECK(status IN ('RESERVED','SENDING','PENDING','UNKNOWN','SUCCEEDED','FAILED')),
        provider_id TEXT UNIQUE, provider_scope TEXT, actual_fee_minor INTEGER,
        version INTEGER NOT NULL DEFAULT 0, UNIQUE(id,account_id))""",
    """CREATE TABLE paper_payout_events (
        account_id TEXT NOT NULL REFERENCES paper_accounts(id), event_key TEXT NOT NULL,
        payout_id TEXT, kind TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT NOT NULL,
        audit_event_id TEXT NOT NULL UNIQUE REFERENCES audit_events(event_id) DEFERRABLE INITIALLY DEFERRED,
        PRIMARY KEY(account_id,event_key), FOREIGN KEY(payout_id,account_id) REFERENCES paper_payouts(id,account_id))""",
)
V3_JOURNAL_DDL = EXTRA_STATEMENTS[2].replace("('GENESIS','FILL','SETTLEMENT')", "('GENESIS','FILL','SETTLEMENT','PAYOUT')").replace(
    "(kind='GENESIS' AND order_id IS NULL) OR (kind!='GENESIS' AND order_id IS NOT NULL)",
    "(kind IN ('GENESIS','PAYOUT') AND order_id IS NULL) OR (kind IN ('FILL','SETTLEMENT') AND order_id IS NOT NULL)")
PAYOUT_TRIGGERS = tuple(f"CREATE TRIGGER {table}_{operation.lower()}_blocked BEFORE {operation} ON {table} "
    "BEGIN SELECT RAISE(ABORT,'paper accounting history is immutable'); END"
    for table in ('pilot_profiles','paper_payout_events') for operation in ('UPDATE','DELETE'))
PROVIDER_STATEMENTS = (
    """CREATE TABLE sandbox_checkouts (
        id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES paper_accounts(id), request_key TEXT NOT NULL,
        order_id TEXT NOT NULL, gross_minor INTEGER NOT NULL CHECK(typeof(gross_minor)='integer' AND gross_minor>0),
        provider_scope TEXT NOT NULL, provider_id TEXT UNIQUE, payment_id TEXT UNIQUE,
        status TEXT NOT NULL CHECK(status IN ('QUEUED','SENDING','UNKNOWN','READY','PAID_UNCLEARED','EXPIRED','CLEARED_FIXTURE')),
        result_json TEXT, UNIQUE(account_id,request_key), UNIQUE(order_id,account_id),
        FOREIGN KEY(order_id,account_id) REFERENCES paper_orders(id,account_id))""",
    """CREATE TABLE sandbox_provider_events (
        account_id TEXT NOT NULL REFERENCES paper_accounts(id), event_key TEXT NOT NULL, operation_id TEXT NOT NULL,
        kind TEXT NOT NULL, result_json TEXT NOT NULL,
        audit_event_id TEXT NOT NULL UNIQUE REFERENCES audit_events(event_id) DEFERRABLE INITIALLY DEFERRED,
        PRIMARY KEY(account_id,event_key))""",
)
PROVIDER_TRIGGERS = tuple(f"CREATE TRIGGER sandbox_provider_events_{op.lower()}_blocked BEFORE {op} ON sandbox_provider_events "
    "BEGIN SELECT RAISE(ABORT,'paper accounting history is immutable'); END" for op in ('UPDATE','DELETE'))
STATEMENTS = tuple(V3_JOURNAL_DDL if statement == EXTRA_STATEMENTS[2] else statement for statement in V2_STATEMENTS) + PAYOUT_STATEMENTS + PAYOUT_TRIGGERS + PROVIDER_STATEMENTS + PROVIDER_TRIGGERS
SCHEMA_CHECKSUM = sha256("\n".join(STATEMENTS).encode()).hexdigest()
TABLES = V2_TABLES | {"pilot_profiles","paper_payouts","paper_payout_events","sandbox_checkouts","sandbox_provider_events"}
TRIGGERS = {f"{table}_{operation}_blocked" for table in
            ("paper_journal_entries", "paper_journal_postings", "paper_lifecycle_events")
            for operation in ("update", "delete")}
V2_TRIGGERS = set(TRIGGERS)
TRIGGERS |= {f"{table}_{operation}_blocked" for table in ("pilot_profiles","paper_payout_events","sandbox_provider_events") for operation in ("update","delete")}


def validate_paper_schema(connection):
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "paper_schema" in names:
        rows = connection.execute("SELECT id,version,checksum FROM paper_schema").fetchall()
        if len(rows) != 1:
            raise RuntimeError("unsupported or incomplete paper schema; writes disabled")
        metadata = tuple(rows[0])
        if metadata == (1, 1, V1_CHECKSUM) and V1_TABLES <= names and not (TABLES - V1_TABLES) & names:
            return 1
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        if metadata == (1, 2, V2_CHECKSUM) and V2_TABLES <= names and V2_TRIGGERS <= triggers and not (TABLES - V2_TABLES) & names:
            return 2
        if metadata == (1, SCHEMA_VERSION, SCHEMA_CHECKSUM) and TABLES <= names and TRIGGERS <= triggers:
            return 3
        raise RuntimeError("unsupported or incomplete paper schema; writes disabled")
    if TABLES & names:
        raise RuntimeError("unversioned paper tables; migration requires investigation")
    return 0


def initialize_paper_schema(repository):
    from src.core.paper_journal import genesis_matches, post_journal, projection_matches
    with repository.transaction():
        version = validate_paper_schema(repository.conn)
        if version == 3:
            return
        if version == 0:
            for statement in STATEMENTS:
                repository.conn.execute(statement)
            repository.conn.execute("INSERT INTO paper_schema VALUES (1,?,?)", (SCHEMA_VERSION, SCHEMA_CHECKSUM))
            return
        if version == 1:
            _upgrade_v1(repository)
        _upgrade_v2(repository)


def _upgrade_v1(repository):
    from src.core.paper_journal import genesis_matches, post_journal, projection_matches
    if repository.conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("paper migration foreign key discrepancy")
    accounts = repository.conn.execute("SELECT * FROM paper_accounts").fetchall()
    # v1 has never spent cash: only an intact genesis and reservation projection can be upgraded.
    for row in accounts:
        reservations = repository.conn.execute("SELECT * FROM capital_reservations WHERE account_id=?", (row['id'],)).fetchall()
        orders = repository.conn.execute("SELECT id,status FROM paper_orders WHERE account_id=?", (row['id'],)).fetchall()
        if (not genesis_matches(repository.conn, row) or
                not row['cash_minor'] == row['peak_equity_minor'] == row['current_equity_minor'] or
                row['cash_minor'] != json.loads(repository.conn.execute(
                    'SELECT payload_json FROM audit_events WHERE event_id=?', (row['genesis_event_id'],)).fetchone()[0])['cash_minor'] or
                row['realized_daily_pnl_minor'] != 0 or
                {r['order_id'] for r in reservations} != {o['id'] for o in orders} or
                any(o['status'] != 'RESERVED' for o in orders) or
                row['reserved_cash_minor'] != sum(r['cash_minor'] for r in reservations) or
                row['reserved_loss_minor'] != sum(r['loss_minor'] for r in reservations)):
            raise RuntimeError("v1 account discrepancy; migration aborted without changes")
    # Rebuild the two dependent tables together while keeping foreign keys enabled.
    reservations = repository.conn.execute("SELECT * FROM capital_reservations").fetchall()
    repository.conn.execute(ORDER_DDL.replace("CREATE TABLE paper_orders", "CREATE TABLE paper_orders_v2", 1))
    repository.conn.execute("""INSERT INTO paper_orders_v2(id,account_id,request_id,opportunity_id,decision_id,status,created_at)
        SELECT id,account_id,request_id,opportunity_id,decision_id,status,created_at FROM paper_orders""")
    repository.conn.execute("DROP TABLE capital_reservations")
    repository.conn.execute("DROP TABLE paper_orders")
    repository.conn.execute("ALTER TABLE paper_orders_v2 RENAME TO paper_orders")
    repository.conn.execute(V1_STATEMENTS[5])
    repository.conn.executemany("INSERT INTO capital_reservations(order_id,account_id,cash_minor,loss_minor,created_at) VALUES (?,?,?,?,?)",
                               [tuple(r) for r in reservations])
    for statement in (*EXTRA_STATEMENTS, *TRIGGER_STATEMENTS):
        repository.conn.execute(statement)
    for row in accounts:
        post_journal(repository, row['id'], row['genesis_event_id'], 'GENESIS', None,
                     [('cash','debit',row['cash_minor']), ('equity','credit',row['cash_minor'])])
    if any(not projection_matches(repository.conn, row) for row in accounts):
        raise RuntimeError("v1 history discrepancy; migration aborted without changes")
    if repository.conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("paper migration foreign key discrepancy")
    repository.conn.execute("UPDATE paper_schema SET version=2,checksum=? WHERE id=1", (V2_CHECKSUM,))


def _upgrade_v2(repository):
    from src.core.paper_journal import projection_matches
    conn = repository.conn
    if conn.execute('PRAGMA foreign_key_check').fetchall() or any(
            not projection_matches(conn,row) for row in conn.execute('SELECT * FROM paper_accounts')):
        raise RuntimeError('v2 history discrepancy; migration aborted without changes')
    entries = [tuple(r) for r in conn.execute('SELECT * FROM paper_journal_entries')]
    postings = [tuple(r) for r in conn.execute('SELECT * FROM paper_journal_postings')]
    # No monetary values or existing entry IDs are changed by this upgrade.
    conn.execute('DROP TABLE paper_journal_postings')
    conn.execute('DROP TABLE paper_journal_entries')
    conn.execute(V3_JOURNAL_DDL)
    conn.execute(EXTRA_STATEMENTS[3])
    conn.executemany('INSERT INTO paper_journal_entries VALUES (?,?,?,?,?,?,?)',entries)
    conn.executemany('INSERT INTO paper_journal_postings VALUES (?,?,?,?,?)',postings)
    for statement in TRIGGER_STATEMENTS[:4] + PAYOUT_STATEMENTS + PAYOUT_TRIGGERS + PROVIDER_STATEMENTS + PROVIDER_TRIGGERS:
        conn.execute(statement)
    if conn.execute('PRAGMA foreign_key_check').fetchall():
        raise RuntimeError('v3 migration foreign key discrepancy')
    conn.execute('UPDATE paper_schema SET version=?,checksum=? WHERE id=1',(SCHEMA_VERSION,SCHEMA_CHECKSUM))
