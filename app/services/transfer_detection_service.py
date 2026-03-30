"""
services/transfer_detection_service.py

Automated detection of transactions that represent money movement rather than
real spend or income.  Results are written to the transaction_flags table.
Views read from that table to exclude flagged rows.

Phase 1 — internal_transfer:
    Same-family transfers between different debit accounts (e.g. checking → savings).
    Both sides of the transfer are flagged so they are excluded from v_debit_spend
    and v_income.

Phase 2 — credit_payment:
    Checking debits that match a credit card payment received (transactions_credit
    where credit > 0).  Only the checking/debit side is flagged — the credit-card
    payment row is already excluded from v_credit_spend by the debit > 0 filter.

Phase 3 — potential_transfer:
    Checking outflows whose description matches a transfer-like pattern (from the
    family's transfer_patterns config) but were NOT paired as an internal_transfer
    or credit_payment.  These are one-sided transfers — the destination account
    has no statement uploaded.  Flagged as potential_transfer with user_kept=FALSE
    (excluded from spend by default).  Surfaced in the UI for user review:
      - "Keep as spend" → user_kept=TRUE, re-appears in spend
      - Can name the destination account → saves a NamedTransferExclusion pattern

user_kept = TRUE means the user has reviewed and wants to keep the transaction
in spend/income.  Flagged rows with user_kept=TRUE are never re-flagged.
"""

from __future__ import annotations

from sqlalchemy import Engine, text


def detect_internal_transfers(family_id: int, engine: Engine, schema: str) -> int:
    """
    Detect transfers between debit accounts within the same family.

    Matches pairs of rows in transactions_debit where:
      - Same family_id, different account_key
      - One amount < 0 (outflow), one amount > 0 (inflow)
      - ABS(amounts) are equal
      - Dates within 7 days of each other

    Both sides of each pair are inserted into transaction_flags.
    Rows already flagged with user_kept=TRUE are never re-flagged.

    Returns the number of new flag rows inserted.
    """
    sql = text(f"""
        WITH pairs AS (
            SELECT DISTINCT ON (LEAST(a.id, b.id), GREATEST(a.id, b.id))
                a.id          AS out_id,
                b.id          AS in_id,
                ABS(a.amount) AS amount,
                a.family_id   AS family_id
            FROM {schema}.transactions_debit a
            JOIN {schema}.transactions_debit b
                ON  a.family_id    = b.family_id
                AND a.account_key != b.account_key
                AND ABS(a.amount)  = ABS(b.amount)
                AND a.amount < 0
                AND b.amount > 0
                AND ABS(b.transaction_date - a.transaction_date) <= 7
            WHERE a.family_id = :family_id
              AND NOT EXISTS (
                  SELECT 1 FROM {schema}.transaction_flags f
                  WHERE f.tx_table   = 'debit'
                    AND f.tx_id      = a.id
                    AND f.flag_type  = 'internal_transfer'
                    AND f.user_kept  = TRUE
              )
              AND NOT EXISTS (
                  SELECT 1 FROM {schema}.transaction_flags f
                  WHERE f.tx_table   = 'debit'
                    AND f.tx_id      = b.id
                    AND f.flag_type  = 'internal_transfer'
                    AND f.user_kept  = TRUE
              )
        ),
        flag_outflows AS (
            INSERT INTO {schema}.transaction_flags
                (family_id, flag_type, tx_table, tx_id, matched_table, matched_id, amount)
            SELECT family_id, 'internal_transfer', 'debit', out_id, 'debit', in_id, amount
            FROM pairs
            ON CONFLICT (tx_table, tx_id, flag_type) DO NOTHING
            RETURNING 1
        ),
        flag_inflows AS (
            INSERT INTO {schema}.transaction_flags
                (family_id, flag_type, tx_table, tx_id, matched_table, matched_id, amount)
            SELECT family_id, 'internal_transfer', 'debit', in_id, 'debit', out_id, amount
            FROM pairs
            ON CONFLICT (tx_table, tx_id, flag_type) DO NOTHING
            RETURNING 1
        )
        SELECT
            (SELECT COUNT(*) FROM flag_outflows) +
            (SELECT COUNT(*) FROM flag_inflows)  AS total
    """)

    with engine.begin() as conn:
        row = conn.execute(sql, {"family_id": family_id}).fetchone()
        count = int(row[0]) if row else 0

    if count:
        print(f"[TransferDetection] family {family_id}: {count} internal transfer flag(s) inserted")
    return count


def detect_credit_payments(family_id: int, engine: Engine, schema: str) -> int:
    """
    Detect credit card payments in checking accounts.

    Matches rows in transactions_debit (amount < 0) against rows in
    transactions_credit (credit > 0) where:
      - Same family_id
      - ABS(debit.amount) == credit.credit  (exact amount match)
      - Dates within 7 days of each other

    Only the checking/debit side is flagged — credit card payment rows
    (credit > 0) are already naturally excluded from v_credit_spend
    because that view only selects rows where debit > 0.

    Each debit row is matched to the closest credit payment in time
    (DISTINCT ON debit id, ordered by date proximity).
    Rows already flagged with user_kept=TRUE are never re-flagged.

    Returns the number of new flag rows inserted.
    """
    sql = text(f"""
        WITH matches AS (
            SELECT DISTINCT ON (d.id)
                d.id          AS debit_id,
                c.id          AS credit_id,
                ABS(d.amount) AS amount,
                d.family_id   AS family_id
            FROM {schema}.transactions_debit d
            JOIN {schema}.transactions_credit c
                ON  d.family_id = c.family_id
                AND ABS(d.amount) = c.credit
                AND d.amount  < 0
                AND c.credit  > 0
                AND ABS(c.transaction_date - d.transaction_date) <= 7
            WHERE d.family_id = :family_id
              AND NOT EXISTS (
                  SELECT 1 FROM {schema}.transaction_flags f
                  WHERE f.tx_table  = 'debit'
                    AND f.tx_id     = d.id
                    AND f.flag_type = 'credit_payment'
                    AND f.user_kept = TRUE
              )
            ORDER BY d.id, ABS(c.transaction_date - d.transaction_date)
        ),
        inserted AS (
            INSERT INTO {schema}.transaction_flags
                (family_id, flag_type, tx_table, tx_id, matched_table, matched_id, amount)
            SELECT family_id, 'credit_payment', 'debit', debit_id, 'credit', credit_id, amount
            FROM matches
            ON CONFLICT (tx_table, tx_id, flag_type) DO NOTHING
            RETURNING 1
        )
        SELECT COUNT(*) FROM inserted
    """)

    with engine.begin() as conn:
        row = conn.execute(sql, {"family_id": family_id}).fetchone()
        count = int(row[0]) if row else 0

    if count:
        print(f"[TransferDetection] family {family_id}: {count} credit payment flag(s) inserted")
    return count


def detect_potential_transfers(family_id: int, engine: Engine, schema: str) -> int:
    """
    Detect one-sided transfer outflows: debit transactions that match a
    transfer-like description pattern but were NOT paired as an
    internal_transfer or credit_payment (i.e. the destination account has
    no statement uploaded).

    Detection patterns come from:
      1. The family's transfer_patterns config list  (broad safety-net patterns)
      2. The family's named_transfer_exclusions list (user-confirmed account patterns)

    Inserts potential_transfer flags with user_kept=FALSE (excluded from spend
    by default).  Skips rows that already have any flag for this tx_id+flag_type,
    or that have user_kept=TRUE on an existing potential_transfer flag (user said
    "keep as spend" — don't re-flag).

    Returns the number of new flag rows inserted.
    """
    from services.transaction_config import load_config

    def _esc(s: str) -> str:
        return s.replace("'", "''")

    cfg = load_config(family_id)

    all_patterns = list(cfg.transfer_patterns) + cfg.named_exclusion_patterns
    if not all_patterns:
        return 0

    # Build inline ILIKE conditions — same approach as view_manager, avoids
    # SQLAlchemy conflicts between :named_params and ::CAST notation.
    ilike_expr = " OR ".join(
        f"d.description ILIKE '%{_esc(p)}%'" for p in all_patterns
    )

    sql = text(f"""
        WITH candidates AS (
            SELECT d.id, ABS(d.amount) AS amount, d.family_id
            FROM {schema}.transactions_debit d
            WHERE d.family_id = :family_id
              AND d.amount < 0
              AND ({ilike_expr})
              AND NOT EXISTS (
                  SELECT 1 FROM {schema}.transaction_flags f
                  WHERE f.tx_table  = 'debit'
                    AND f.tx_id     = d.id
                    AND f.flag_type IN ('internal_transfer', 'credit_payment')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM {schema}.transaction_flags f
                  WHERE f.tx_table  = 'debit'
                    AND f.tx_id     = d.id
                    AND f.flag_type = 'potential_transfer'
                    AND f.user_kept = TRUE
              )
        ),
        inserted AS (
            INSERT INTO {schema}.transaction_flags
                (family_id, flag_type, tx_table, tx_id, amount)
            SELECT DISTINCT family_id, 'potential_transfer', 'debit', id, amount
            FROM candidates
            ON CONFLICT (tx_table, tx_id, flag_type) DO NOTHING
            RETURNING 1
        )
        SELECT COUNT(*) FROM inserted
    """)

    with engine.begin() as conn:
        row = conn.execute(sql, {"family_id": family_id}).fetchone()
        count = int(row[0]) if row else 0

    if count:
        print(f"[TransferDetection] family {family_id}: {count} potential transfer flag(s) inserted")
    return count


def cleanup_stale_potential_transfers(family_id: int, engine: Engine, schema: str) -> int:
    """
    Delete potential_transfer flags (user_kept=FALSE) whose transactions no
    longer match any current transfer pattern.  Must run before re-detection so
    that removed patterns clean up their stale flags immediately.

    If no patterns are configured at all, all pending potential_transfer flags
    are removed (none can be valid).
    """
    from services.transaction_config import load_config

    def _esc(s: str) -> str:
        return s.replace("'", "''")

    cfg          = load_config(family_id)
    all_patterns = list(cfg.transfer_patterns) + cfg.named_exclusion_patterns

    if all_patterns:
        still_matches  = " OR ".join(
            f"d.description ILIKE '%{_esc(p)}%'" for p in all_patterns
        )
        no_match_clause = f"AND NOT ({still_matches})"
    else:
        no_match_clause = ""   # no patterns → remove everything

    sql = text(f"""
        DELETE FROM {schema}.transaction_flags f
        USING {schema}.transactions_debit d
        WHERE f.tx_id     = d.id
          AND f.tx_table  = 'debit'
          AND f.flag_type = 'potential_transfer'
          AND f.user_kept = FALSE
          AND f.family_id = :family_id
          {no_match_clause}
    """)

    with engine.begin() as conn:
        count = conn.execute(sql, {"family_id": family_id}).rowcount

    if count:
        print(f"[TransferDetection] family {family_id}: {count} stale potential_transfer flag(s) removed")
    return count


def run_detection(family_id: int, engine: Engine, schema: str) -> None:
    """
    Run all detection algorithms for a family.
    Called after every upload and after manual view refresh.
    Order matters: cleanup first so removed patterns clear stale flags,
    then internal_transfer and credit_payment, then potential_transfer
    so it skips already-paired rows.
    """
    cleanup_stale_potential_transfers(family_id, engine, schema)
    detect_internal_transfers(family_id, engine, schema)
    detect_credit_payments(family_id, engine, schema)
    detect_potential_transfers(family_id, engine, schema)


# ─────────────────────────────────────────────────────────────────────────────
# Transfer flag queries  (used by the upload UI review panel)
# ─────────────────────────────────────────────────────────────────────────────

_TRANSFER_CANDIDATE_KEYWORDS = [
    "ZELLE", "VENMO", "CASHAPP", "CASH APP", "PAYPAL",
    "WIRE", "ACH", "XFER", "BILLPAY", "BILL PAY",
    "P2P", "SEND MONEY", "SQUARE CASH",
]


def get_pending_transfers(
    family_id: int, user_id: int, is_head: bool, engine: Engine, schema: str
) -> list[dict]:
    """Return all unreviewed potential_transfer flags with transaction detail."""
    person_filter = "" if is_head else "AND :uid = ANY(d.person)"
    sql = text(f"""
        SELECT f.id          AS flag_id,
               f.amount,
               f.detected_at,
               d.description,
               d.transaction_date,
               d.account_key,
               d.person
        FROM   {schema}.transaction_flags f
        JOIN   {schema}.transactions_debit d ON d.id = f.tx_id
        WHERE  f.family_id  = :fid
          AND  f.flag_type  = 'potential_transfer'
          AND  NOT f.user_kept
          {person_filter}
        ORDER BY d.transaction_date DESC
    """)
    params: dict = {"fid": family_id}
    if not is_head:
        params["uid"] = user_id
    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [dict(r) for r in rows]


def count_pending_transfers(
    family_id: int, user_id: int, is_head: bool, engine: Engine, schema: str
) -> int:
    """Return the count of unreviewed potential_transfer flags."""
    person_filter = "" if is_head else "AND :uid = ANY(d.person)"
    sql = text(f"""
        SELECT COUNT(*)
        FROM   {schema}.transaction_flags f
        JOIN   {schema}.transactions_debit d ON d.id = f.tx_id
        WHERE  f.family_id  = :fid
          AND  f.flag_type  = 'potential_transfer'
          AND  NOT f.user_kept
          {person_filter}
    """)
    params: dict = {"fid": family_id}
    if not is_head:
        params["uid"] = user_id
    with engine.connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


def set_flag_user_kept(flag_id: int, user_kept: bool, engine: Engine, schema: str) -> None:
    """Set user_kept on a transaction flag (accept or reject a potential transfer)."""
    with engine.begin() as conn:
        conn.execute(
            text(f"UPDATE {schema}.transaction_flags SET user_kept = :k WHERE id = :id"),
            {"k": user_kept, "id": flag_id},
        )


def get_reviewed_transfers(
    family_id: int, user_id: int, is_head: bool, engine: Engine, schema: str
) -> list[dict]:
    """Return all user_kept=TRUE potential_transfer flags with transaction detail."""
    person_filter = "" if is_head else "AND :uid = ANY(d.person)"
    sql = text(f"""
        SELECT f.id          AS flag_id,
               f.amount,
               d.description,
               d.transaction_date,
               d.account_key
        FROM   {schema}.transaction_flags f
        JOIN   {schema}.transactions_debit d ON d.id = f.tx_id
        WHERE  f.family_id  = :fid
          AND  f.flag_type  = 'potential_transfer'
          AND  f.user_kept
          {person_filter}
        ORDER BY d.transaction_date DESC
    """)
    params: dict = {"fid": family_id}
    if not is_head:
        params["uid"] = user_id
    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [dict(r) for r in rows]


def get_pattern_impact(
    patterns: list[str], family_id: int, engine: Engine, schema: str
) -> dict[str, tuple[int, int]]:
    """
    Return {pattern: (inflow_count, outflow_count)} for checking accounts.
    Counts transactions_debit rows whose description ILIKE '%pattern%'.
    """
    if not patterns:
        return {}
    from data.bank_rules import load_rules
    rules = load_rules(family_id)
    checking_keys = [r.prefix for r in rules if r.account_type == "checking"]
    if not checking_keys:
        return {p: (0, 0) for p in patterns}

    values_clause = ", ".join(f"(:p{i})" for i in range(len(patterns)))
    key_clause    = ", ".join(f":ck{j}" for j in range(len(checking_keys)))
    params: dict  = {"fid": family_id}
    for i, p in enumerate(patterns):
        params[f"p{i}"] = p
    for j, k in enumerate(checking_keys):
        params[f"ck{j}"] = k

    sql = text(f"""
        SELECT
            unnested.pat,
            COUNT(*) FILTER (WHERE d.amount > 0) AS inflows,
            COUNT(*) FILTER (WHERE d.amount < 0) AS outflows
        FROM {schema}.transactions_debit d
        CROSS JOIN (VALUES {values_clause}) AS unnested(pat)
        WHERE d.family_id = :fid
          AND d.account_key IN ({key_clause})
          AND d.description ILIKE '%' || unnested.pat || '%'
        GROUP BY unnested.pat
    """)

    result = {p: (0, 0) for p in patterns}
    with engine.connect() as conn:
        for row in conn.execute(sql, params).mappings():
            result[row["pat"]] = (row["inflows"] or 0, row["outflows"] or 0)
    return result


def get_pattern_suggestions(
    family_id: int, existing: list[str], engine: Engine, schema: str
) -> list[str]:
    """
    Return keywords from _TRANSFER_CANDIDATE_KEYWORDS that appear in unflagged
    checking outflows but are not already in the configured transfer_patterns.
    """
    from data.bank_rules import load_rules
    rules = load_rules(family_id)
    checking_keys = [r.prefix for r in rules if r.account_type == "checking"]
    if not checking_keys:
        return []

    existing_upper = {p.upper() for p in existing}
    candidates = [c for c in _TRANSFER_CANDIDATE_KEYWORDS if c.upper() not in existing_upper]
    if not candidates:
        return []

    key_clause    = ", ".join(f":ck{j}" for j in range(len(checking_keys)))
    values_clause = ", ".join(f"(:c{i})" for i in range(len(candidates)))
    params: dict  = {"fid": family_id}
    for j, k in enumerate(checking_keys):
        params[f"ck{j}"] = k
    for i, c in enumerate(candidates):
        params[f"c{i}"] = c

    sql = text(f"""
        SELECT cand.kw
        FROM (VALUES {values_clause}) AS cand(kw)
        WHERE EXISTS (
            SELECT 1
            FROM {schema}.transactions_debit d
            WHERE d.family_id = :fid
              AND d.account_key IN ({key_clause})
              AND d.amount < 0
              AND d.description ILIKE '%' || cand.kw || '%'
              AND d.id NOT IN (
                  SELECT tx_id
                  FROM {schema}.transaction_flags
                  WHERE family_id = :fid
                    AND tx_table  = 'debit'
                    AND flag_type IN ('internal_transfer', 'credit_payment', 'potential_transfer')
                    AND NOT user_kept
              )
        )
        ORDER BY cand.kw
    """)

    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [r["kw"] for r in rows]


def get_pattern_matches(
    pattern: str, family_id: int, engine: Engine, schema: str
) -> list[dict]:
    """Return debit outflows whose description contains *pattern*."""
    sql = text(f"""
        SELECT transaction_date, description, ABS(amount) AS amount, account_key
        FROM   {schema}.transactions_debit
        WHERE  family_id = :fid
          AND  amount < 0
          AND  description ILIKE :pat
        ORDER BY transaction_date DESC
        LIMIT 200
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"fid": family_id, "pat": f"%{pattern}%"}).mappings().all()
    return [dict(r) for r in rows]
