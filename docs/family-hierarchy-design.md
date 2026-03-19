# Family Hierarchy — Design Decisions

> Living document. Update as decisions are made or revised.
> Companion to: `app/services/auth.py`, `app/db_migration.py`, `app/data/bank_rules.py`

---

## Roles

Three roles replacing the current `admin | user` binary:

| Role | Scope | Capabilities |
|------|-------|-------------|
| **Member** | Family | View family dashboards; upload only to permitted accounts; cannot edit anything |
| **Family Head** | Family | Full edit within family (views, widgets, banks, charts); add/remove members; manage roles within family |
| **Instance Admin** | Instance | All Family Head capabilities + create families; create/manage dashboard templates; assign users to families |

- Instance Admin is stored as a flag on `app_users` (instance-level)
- Family role (`member | head`) lives in `family_memberships`
- A user belongs to **one active family at a time** — this applies to Instance Admin too; they are a regular family member within their family and subject to the same single-family constraint

---

## Data Model

### New tables

```sql
families (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    created_by  INTEGER REFERENCES app_users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)

family_memberships (
    id          SERIAL PRIMARY KEY,
    family_id   INTEGER NOT NULL REFERENCES families(id),
    user_id     INTEGER NOT NULL REFERENCES app_users(id),
    family_role TEXT NOT NULL CHECK (family_role IN ('member', 'head')),
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    left_at     TIMESTAMPTZ           -- NULL = currently active member
)
```

### Transaction stamping

Add two columns to `transactions_debit` and `transactions_credit`:

```sql
family_id    INTEGER   -- family context at time of upload (immutable after insert)
uploaded_by  INTEGER   -- user_id of the uploader (immutable after insert)
```

Existing `person INTEGER[]` is retained — it records **who the transaction is attributed to**
(already supports joint accounts via `BankRule.person_override`).

| Column | Meaning | Set by |
|--------|---------|--------|
| `person[]` | Who the transaction belongs to (attribution) | Bank rule / uploader |
| `uploaded_by` | Who pressed upload | Auth session |
| `family_id` | Which family context | Auth session (active family) |

### Config scoping

All `app_config_*` tables (bank_rules, banks, categories, transaction) become **per-family**.
The singleton `id = 1` constraint is replaced by `family_id` as the primary key.

At family creation: configs are seeded from instance defaults (simple copy — no inheritance chain).

---

## Query Patterns

No date-range membership lookups at query time. `family_id` on the row is the source of truth.

```sql
-- Family dashboard
WHERE family_id = :fid

-- Member's personal view across all history (incl. old families)
WHERE :uid = ANY(person)

-- Old family retains access to departed member's historical data
WHERE family_id = :old_fid   -- rows are immutable, still tagged with old family_id
```

---

## Family Split ("kid moves out")

When a user leaves a family:

1. Set `family_memberships.left_at = NOW()` — membership becomes historical record
2. **Automated:** scan old family's bank rules and remove departed user's ID from any `person_override` lists; save rules
3. **Automated:** remove departed user's rows from `user_bank_permissions` for that family
4. New family is created (or user joins an existing one); new `family_memberships` row inserted
4. New family's bank rules are configured fresh (seeded from instance defaults)
5. All future uploads by that user stamp `family_id = new_family_id`
6. Past transactions are untouched — frozen with `family_id = old_family_id`

**Access after split:**
- Old family: sees all historical data including the departed member's past transactions (correct — they were part of that family)
- New family: sees only data uploaded under the new family_id
- Departed user's personal view: `WHERE :uid = ANY(person)` — sees full history across both families

No data migration needed at split time. No complex queries needed at read time.

---

## Joint / Shared Accounts

`BankRule.person_override` already handles shared accounts — it stamps a fixed list of user IDs onto `person[]` regardless of who uploads. This mechanism is preserved.

**At split time for a joint account** (e.g. a parent+child joint checking):

- Old family: the departed user's ID is **automatically removed** from `person_override` on any bank rules where they appear. This happens as part of the split flow — Family Head should not need to remember to do this manually.
- New family: If the departed user wants to continue tracking the same account, they add it to their new family's bank rules. Each upload gets their new `family_id` stamped.
- Both families can independently track the same real-world account — each sees only their own uploads (their own `family_id`). This is correct: they are now tracking separate finances.

There is no "shared account" special case in the data model. Sharing is achieved by bank rule configuration, not by cross-family data references.

**Split flow (automated steps):**
1. Set `family_memberships.left_at = NOW()`
2. Scan old family's bank rules — remove departed user's ID from every `person_override` list where it appears; save updated rules
3. Remove departed user's rows from `user_bank_permissions` for that family
4. Insert new `family_memberships` row in destination family (or create new family)

---

## Upload Permissions

Within a family, not all members should be able to upload to all accounts.
A new permissions table controls this:

```sql
user_bank_permissions (
    user_id     INTEGER NOT NULL REFERENCES app_users(id),
    family_id   INTEGER NOT NULL REFERENCES families(id),
    account_key TEXT NOT NULL,      -- matches BankRule.prefix
    can_upload  BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (user_id, family_id, account_key)
)
```

- Family Heads can upload to all accounts in their family by default
- Members can only upload to accounts explicitly granted here
- At split time: departed user's permissions in old family become irrelevant (they can no longer upload to old family at all)

---

## Migration of Existing Data

1. Create one `families` row: `(id=1, name='Default Family')`
2. Insert all current users into `family_memberships` with `family_role = 'head'` for admins, `'member'` for users; `joined_at = created_at`
3. `UPDATE transactions_debit SET family_id = 1, uploaded_by = person[1]`
4. `UPDATE transactions_credit SET family_id = 1, uploaded_by = person[1]`
5. Copy existing `app_config_*` rows to family-scoped versions (family_id = 1)
6. Rename current `admin` role → Instance Admin flag; `user` role → `member`

---

## Data Isolation

**Data is strictly per-family. No data is shared between families at any level.**

- All transaction queries are always scoped to a single `family_id`
- Instance Admin has no special cross-family data access
- The only thing that crosses family boundaries is **configuration templates** (layout/structure only, zero data)

---

## Dashboard Templates

Instead of "shared dashboards", the Instance Admin manages a library of **dashboard templates** — reusable layout and widget configurations with no attached data.

**How it works:**
- A template is a dashboard layout: widget types, positions, spans, and default configs — no data, no chart results
- Instance Admin can create templates from scratch or promote any existing family dashboard to a template
- Family Heads can import a template to their family, which creates a new dashboard pre-populated with that layout; they then customise it freely
- Family Heads can also export their own dashboard as a template (available instance-wide or kept private)
- Templates are a starting point only — after import they are fully independent copies

**New table:**

```sql
dashboard_templates (
    id           SERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    created_by   INTEGER REFERENCES app_users(id),
    is_published BOOLEAN NOT NULL DEFAULT FALSE,  -- FALSE = draft/private to creator
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
)

dashboard_template_widgets (
    id           SERIAL PRIMARY KEY,
    template_id  INTEGER NOT NULL REFERENCES dashboard_templates(id) ON DELETE CASCADE,
    chart_id     TEXT NOT NULL,
    col_start    SMALLINT NOT NULL,
    row_start    SMALLINT NOT NULL,
    col_span     SMALLINT NOT NULL,
    row_span     SMALLINT NOT NULL,
    config       JSONB NOT NULL DEFAULT '{}'
)
```

**Import/export flow:**
- Export: serialise `app_dashboards` + its `app_dashboard_widgets` rows into a template record (or a downloadable JSON file)
- Import: create a new `app_dashboards` row for the family from the template, copy widget rows — family_id and user_id set to the importer
- JSON file export/import also allows sharing templates outside the instance (future)

**Who can do what:**

| Action | Member | Family Head | Instance Admin |
|--------|--------|-------------|----------------|
| Browse published templates | — | Yes | Yes |
| Import template to family | — | Yes | Yes |
| Export family dashboard as template | — | Yes (private) | Yes (can publish) |
| Publish / unpublish templates | — | — | Yes |
| Delete any template | — | — | Yes |

---

## Account Management & Auth

### New fields on `app_users`

```sql
email               TEXT UNIQUE,          -- required for email-based flows, nullable for now
must_change_password BOOLEAN NOT NULL DEFAULT FALSE  -- forces reset on next login
```

### Password reset flows

Three independent flows, any of which can be used:

**1. Self-service email reset**
- User enters their email on the login page → system sends a time-limited reset link
- Requires a reset tokens table and SMTP config in `app_settings`
- User clicks link → sets new password → token marked used

```sql
password_reset_tokens (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,  -- store hash, not raw token
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ            -- NULL = not yet used
)
```

**2. Family Head sets a temp password**
- Head sets a temporary password for any member in their family
- Sets `must_change_password = TRUE` on that user
- User logs in with temp password and is forced to set their own immediately
- No email required — suitable for families where not everyone has email configured

**3. Instance Admin full provisioning**
- Can create users, set/reset any password, assign to families, manage family_role
- Extends the current CLI `create_admin` capability into the UI

### Invite flow (Family Head)

Family Head invites a new member by entering their email. The system:
1. Creates an invitation record (not a user yet)
2. Sends an email with a one-time signup link
3. User clicks link → sets their display name + password → account created and added to the family

```sql
invitations (
    id          SERIAL PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,
    invited_by  INTEGER NOT NULL REFERENCES app_users(id),
    family_id   INTEGER NOT NULL REFERENCES families(id),
    family_role TEXT NOT NULL DEFAULT 'member',
    email       TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ            -- NULL = pending
)
```

This is the primary onboarding flow for non-technical family members — they never need the CLI.

### Email infrastructure

Follows the same priority pattern as `get_archive_cfg()`: **DB settings override env vars, env vars override defaults.**

```
Priority: app_settings (UI) → environment variables → disabled
```

Environment variables (set in `docker-compose.yml` or `.env`):

```
SMTP_HOST         smtp.gmail.com
SMTP_PORT         587
SMTP_USERNAME     you@gmail.com
SMTP_PASSWORD     your-app-password   # Gmail: use an App Password, not account password
SMTP_FROM         you@gmail.com
SMTP_USE_TLS      true
```

The same keys are mirrored in `app_settings` under `"smtp"` so the Instance Admin can override them via the Settings UI without touching env vars — useful if the relay changes without a redeploy.

If neither env vars nor UI settings provide a host: email-dependent flows (self-service reset, invite) are silently disabled. Temp password and Instance Admin provisioning flows still work without SMTP.

### OAuth / OIDC (future, optional)

Authentik and similar self-hosted identity providers are a natural fit for users who want centralised auth. Design as an additive layer — OAuth does not replace password auth, both coexist.

```sql
oauth_accounts (
    id               SERIAL PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    provider         TEXT NOT NULL,          -- e.g. 'authentik', 'google'
    provider_user_id TEXT NOT NULL,
    UNIQUE (provider, provider_user_id)
)
```

- Family membership, roles, and all app data remain in this DB regardless of auth method
- OAuth login: match `provider_user_id` → look up `user_id` → normal session flow from there
- A user can have both a password and one or more OAuth accounts linked
- Deferred — implement after the family model is stable

---

## Open Questions

_(none currently)_
