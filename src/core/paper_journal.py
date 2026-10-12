"""Balanced synthetic postings and internal projection checks; no external valuation."""
import json

MAX_MINOR = 2**63 - 1
BUCKETS = {'cash', 'asset', 'equity', 'income', 'expense'}


def post_journal(repository, account_id, event_id, kind, order_id, postings):
    if not repository.conn.in_transaction:
        raise RuntimeError('journal writes require an owned transaction')
    for bucket, side, amount in postings:
        if bucket not in BUCKETS or side not in ('debit', 'credit') or type(amount) is not int or not 0 <= amount <= MAX_MINOR:
            raise ValueError('invalid synthetic posting')
    if sum(a if s == 'debit' else -a for _, s, a in postings) != 0:
        raise ValueError('journal entry must balance')
    currency = repository.conn.execute('SELECT currency FROM paper_accounts WHERE id=?', (account_id,)).fetchone()[0]
    cursor = repository.conn.execute('''INSERT INTO paper_journal_entries
        (account_id,order_id,event_id,kind,currency,postings_json) VALUES (?,?,?,?,?,?)''',
        (account_id, order_id, event_id, kind, currency, json.dumps(postings, separators=(',', ':'))))
    repository.conn.executemany('INSERT INTO paper_journal_postings VALUES (?,?,?,?,?)',
        [(cursor.lastrowid, n, bucket, side, amount) for n, (bucket, side, amount) in enumerate(postings)])
    return cursor.lastrowid


def fill_postings(treatment, capital, fees):
    return [('asset' if treatment == 'refundable' else 'expense', 'debit', capital),
            ('expense', 'debit', fees), ('cash', 'credit', capital + fees)]


def settlement_postings(treatment, capital, returned, revenue, fees):
    gross = returned + revenue
    postings = [('cash', 'debit', gross)]
    if treatment == 'refundable':
        postings.append(('asset', 'credit', capital))
        net = gross - capital
        postings.append(('income', 'credit', net) if net >= 0 else ('expense', 'debit', -net))
    else:
        postings.append(('income', 'credit', gross))
    return [*postings, ('expense', 'debit', fees), ('cash', 'credit', fees)]


def genesis_matches(connection, row):
    from .models import CapitalLimits
    from .paper_accounts import minor_units
    from decimal import Decimal
    genesis = connection.execute('SELECT * FROM audit_events WHERE event_id=?', (row['genesis_event_id'],)).fetchone()
    try:
        original = json.loads(genesis['payload_json'])
        policy = json.loads(row['limits_json'])
        limits = CapitalLimits(**{key: Decimal(value) for key, value in policy.items()})
        return (genesis['event_type'] == 'paper_account_created' and genesis['entity_id'] == row['id']
                and original['synthetic'] is True and original['limits'] == policy
                and original['currency'] == row['currency'] and row['mode'] == 'PAPER'
                and limits.max_leverage == 1
                and original['cash_minor'] == minor_units(limits.total_capital))
    except (ValueError, TypeError, KeyError):
        return False


def open_exposure(connection, account_id):
    positions = connection.execute("SELECT * FROM paper_positions WHERE account_id=? AND status='OPEN'", (account_id,)).fetchall()
    return (sum(p['cash_spent_minor'] for p in positions), sum(p['loss_minor'] for p in positions))


def journal_projection(connection, account_id):
    """Recompute cash, cost-valued assets, P&L and peak from sealed balanced entries."""
    balances = {bucket: 0 for bucket in BUCKETS}
    peak = 0
    capital = 0
    entries = connection.execute('SELECT * FROM paper_journal_entries WHERE account_id=? ORDER BY id', (account_id,)).fetchall()
    for entry in entries:
        lines = connection.execute('SELECT line,bucket,side,amount_minor FROM paper_journal_postings WHERE entry_id=? ORDER BY line', (entry['id'],)).fetchall()
        postings = [(r['bucket'], r['side'], r['amount_minor']) for r in lines]
        if (not postings or [r['line'] for r in lines] != list(range(len(lines)))
                or json.loads(entry['postings_json']) != [list(p) for p in postings]
                or sum(a if s == 'debit' else -a for _, s, a in postings) != 0):
            raise ValueError('unbalanced or altered paper journal')
        if entry['kind'] == 'GENESIS':
            capital = sum(a for b, s, a in postings if b == 'cash' and s == 'debit')
        for bucket, side, amount in postings:
            balances[bucket] += amount if side == 'debit' else -amount
        equity = balances['cash'] + balances['asset'] + balances['equity'] + capital
        if balances['cash'] < 0 or balances['asset'] < 0 or equity > MAX_MINOR:
            raise ValueError('invalid journal projection')
        peak = max(peak, equity)
    return balances, peak, entries


def projection_matches(connection, row):
    """Check internal records before movements. Market/external reconciliation is separate."""
    from .paper_accounts import minor_units, canonical_json
    from hashlib import sha256
    from decimal import Decimal
    try:
        if not genesis_matches(connection, row):
            return False
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pilot_profiles'").fetchone():
            profile=connection.execute('SELECT * FROM pilot_profiles WHERE account_id=?',(row['id'],)).fetchone()
            if profile is not None:
                from .pilot import PilotPolicy
                values=json.loads(profile['policy_json'])
                audit=connection.execute('SELECT * FROM audit_events WHERE event_id=?',(profile['audit_event_id'],)).fetchone()
                if audit is None or audit['event_type']!='paper_pilot_profile' or audit['entity_id']!=row['id'] or json.loads(audit['payload_json'])!={'synthetic':True,'policy':values}:
                    return False
                values['allowed_vendors']=tuple(values['allowed_vendors'])
                PilotPolicy(**values)
        balances, peak, entries = journal_projection(connection, row['id'])
        original = json.loads(connection.execute('SELECT payload_json FROM audit_events WHERE event_id=?',
                              (row['genesis_event_id'],)).fetchone()[0])
        genesis = [e for e in entries if e['kind'] == 'GENESIS']
        if (len(genesis) != 1 or not entries or entries[0]['id'] != genesis[0]['id']
                or genesis[0]['event_id'] != row['genesis_event_id']
                or json.loads(genesis[0]['postings_json']) != [['cash','debit',original['cash_minor']], ['equity','credit',original['cash_minor']]]):
            return False
        orders = connection.execute('SELECT * FROM paper_orders WHERE account_id=?', (row['id'],)).fetchall()
        reservations = {r['order_id']: r for r in connection.execute('SELECT * FROM capital_reservations WHERE account_id=?', (row['id'],))}
        positions = {p['order_id']: p for p in connection.execute('SELECT * FROM paper_positions WHERE account_id=?', (row['id'],))}
        order_ids = {o['id'] for o in orders}
        if not set(reservations) <= order_ids or not set(positions) <= order_ids:
            return False
        asset = 0
        for order in orders:
            request = connection.execute('SELECT * FROM paper_requests WHERE account_id=? AND request_id=?', (row['id'], order['request_id'])).fetchone()
            binding = connection.execute('SELECT currency FROM paper_observation_currencies WHERE opportunity_id=?', (order['opportunity_id'],)).fetchone()
            decision = connection.execute('SELECT opportunity_id,decision FROM decisions WHERE id=?', (order['decision_id'],)).fetchone()
            if (binding is None or binding[0] != row['currency'] or decision is None
                    or decision[0] != order['opportunity_id'] or decision[1] != 'paper_test'):
                return False
            submitted = json.loads(request['payload_json'])
            receipt = json.loads(request['result_json'])
            if (sha256(request['payload_json'].encode()).hexdigest() != request['payload_digest']
                    or submitted['opportunity']['id'] != order['opportunity_id']
                    or submitted['action']['currency'] != row['currency']
                    or order['expires_at'] != submitted.get('expires_at')
                    or request['decision_id'] != order['decision_id']
                    or receipt['status'] != 'RESERVED' or receipt['order_id'] != order['id']):
                return False
            reserved, position = reservations.get(order['id']), positions.get(order['id'])
            effects = connection.execute('SELECT * FROM paper_lifecycle_events WHERE account_id=? AND order_id=?', (row['id'], order['id'])).fetchall()
            for effect in effects:
                command, result = json.loads(effect['payload_json']), json.loads(effect['result_json'])
                audit = connection.execute('SELECT * FROM audit_events WHERE event_id=?', (effect['audit_event_id'],)).fetchone()
                if audit is None:
                    return False
                audited = json.loads(audit['payload_json'])
                if result['status'] != 'REJECTED':
                    expected_status = {'FILL':'FILLED','CANCEL':'CANCELLED','EXPIRE':'EXPIRED','SETTLE':'CLOSED'}[effect['kind']]
                    if (result['status'] != expected_status or result['order_status'] != expected_status
                            or result['order_version'] != command['expected_version'] + 1
                            or command['expected_version'] != (1 if effect['kind']=='SETTLE' else 0)):
                        return False
                if (sha256(effect['payload_json'].encode()).hexdigest() != effect['payload_digest']
                        or command['kind'] != effect['kind'] or command['order_id'] != order['id']
                        or result['account_id'] != row['id'] or result['order_id'] != order['id']
                        or result['event_key'] != effect['event_key']
                        or audit['event_type'] != 'paper_lifecycle' or audit['entity_id'] != order['id']
                        or canonical_json(audited['command']) != canonical_json(command)
                        or canonical_json(audited['result']) != canonical_json(result)):
                    return False
            successful = {e['kind']: e for e in effects if json.loads(e['result_json'])['status'] != 'REJECTED'}
            if len(successful) != sum(json.loads(e['result_json'])['status'] != 'REJECTED' for e in effects):
                return False
            status = order['status']
            if status == 'RESERVED':
                if position is not None or reserved is None or successful or order['version'] != 0:
                    return False
                if (reserved['cash_minor'] != minor_units(Decimal(submitted['action']['cash_required']))
                        or reserved['loss_minor'] != minor_units(Decimal(submitted['action']['worst_case_loss']))):
                    return False
            elif status in ('CANCELLED', 'EXPIRED'):
                kind = 'CANCEL' if status == 'CANCELLED' else 'EXPIRE'
                if reserved is not None or position is not None or set(successful) != {kind} or order['version'] != 1:
                    return False
            elif status in ('FILLED', 'CLOSED'):
                if reserved is not None or position is None or position['treatment'] != submitted['action']['capital_treatment']:
                    return False
                expected_kinds = {'FILL'} if status == 'FILLED' else {'FILL','SETTLE'}
                if (set(successful) != expected_kinds or order['version'] != len(expected_kinds)
                        or position['status'] != ('OPEN' if status == 'FILLED' else 'CLOSED')):
                    return False
                fill = json.loads(successful['FILL']['payload_json'])['spec']
                entry_expense = position['fees_minor'] + (position['capital_minor'] if position['treatment'] == 'expensed' else 0)
                if (position['capital_minor'] != minor_units(Decimal(fill['capital']))
                        or position['capital_minor'] != minor_units(Decimal(submitted['opportunity']['required_capital']))
                        or position['fees_minor'] != minor_units(Decimal(fill['fees']))
                        or fill['currency'] != row['currency']
                        or position['cash_spent_minor'] != position['capital_minor'] + position['fees_minor']
                        or position['entry_expense_minor'] != entry_expense
                        or position['loss_minor'] != minor_units(Decimal(submitted['action']['worst_case_loss'])) - entry_expense):
                    return False
                if status == 'FILLED' and position['treatment'] == 'refundable':
                    asset += position['capital_minor']
                if status == 'CLOSED':
                    settled = json.loads(successful['SETTLE']['payload_json'])['spec']
                    if any(position[column] != minor_units(Decimal(settled[field])) for column, field in
                           (('returned_minor','capital_return'), ('revenue_minor','revenue'), ('exit_fees_minor','fees'))):
                        return False
                    if (settled['currency'] != row['currency'] or position['pnl_minor'] !=
                            position['returned_minor'] + position['revenue_minor'] - position['exit_fees_minor'] - position['cash_spent_minor']):
                        return False
            else:
                return False
        from .paper_payouts import payout_integrity
        payout_cash, payout_loss, distribution, payout_entries = payout_integrity(connection, row['id'])
        for entry in entries:
            if entry['currency'] != row['currency']:
                return False
            if entry['kind'] in ('GENESIS','PAYOUT'):
                continue
            position = positions.get(entry['order_id'])
            if position is None:
                return False
            event = connection.execute('SELECT * FROM paper_lifecycle_events WHERE audit_event_id=?', (entry['event_id'],)).fetchone()
            audit = connection.execute('SELECT * FROM audit_events WHERE event_id=?', (entry['event_id'],)).fetchone()
            kind = 'FILL' if entry['kind'] == 'FILL' else 'SETTLE'
            if (event is None or audit is None or event['kind'] != kind or event['order_id'] != entry['order_id']
                    or event['account_id'] != row['id'] or audit['event_type'] != 'paper_lifecycle'
                    or audit['entity_id'] != entry['order_id']
                    or json.loads(event['result_json'])['status'] != ('FILLED' if kind == 'FILL' else 'CLOSED')
                    or json.loads(event['result_json'])['journal_id'] != entry['id']):
                return False
            if kind == 'FILL':
                expected = fill_postings(position['treatment'], position['capital_minor'], position['fees_minor'])
            else:
                if position['status'] != 'CLOSED':
                    return False
                expected = settlement_postings(position['treatment'], position['capital_minor'], position['returned_minor'], position['revenue_minor'], position['exit_fees_minor'])
            if json.loads(entry['postings_json']) != [list(p) for p in expected]:
                return False
        if len(entries) != 1 + payout_entries + len(positions) + sum(p['status'] == 'CLOSED' for p in positions.values()):
            return False
        return (row['cash_minor'] == balances['cash'] and balances['asset'] == asset
                and row['current_equity_minor'] == balances['cash'] + balances['asset'] + distribution
                and row['peak_equity_minor'] == peak
                and row['realized_daily_pnl_minor'] == -balances['income'] - balances['expense']
                and row['reserved_cash_minor'] == payout_cash + sum(r['cash_minor'] for r in reservations.values())
                and row['reserved_loss_minor'] == payout_loss + sum(r['loss_minor'] for r in reservations.values()))
    except (ValueError, TypeError, KeyError, IndexError):
        return False
