# CHANGELOG


## v2.1.0 (2026-03-19)

### Bug Fixes

- Loans accessible to all users; fix demo bank config and view refresh
  ([`78926ae`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/78926aec3c7f2b3324007717924f8c8cdec467fd))

- loans_content.py / loan_planning_content.py: wrong guard was is_instance_admin() — changed to
  is_authenticated() so all family members can access loans and loan planning pages - db_demo: store
  BankConfig dicts (not bare strings) in app_config_banks so BankConfig.from_dict() succeeds for
  non-admin users - db_demo: combined view refresh across both demo families so Family 2
  account_keys appear in global views

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Scope app_loans and loan widgets to family_id
  ([`266da2e`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/266da2ed9dc5578b6e8af13ac8b882dedec529e2))

app_loans had no family_id column — all families saw all loans.

- Migration: add family_id to app_loans, backfill existing rows to family 1 - loan_service: add
  family_id param to load_loans, save_loan, delete_loan, match_payments, get_monthly_spend_income,
  get_baseline - loans_content / loan_planning_content: pass auth.current_family_id() through all
  service calls - RenderContext: add family_id field, resolved via auth.current_family_id() in
  build() so all widgets get it automatically - registry.py / settings_ui.py: update all 6 loan
  widget call sites to use ctx.family_id

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

### Features

- Add demo data
  ([`f09ffdb`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/f09ffdbe5a29d675e1b18394b12ee44564ecf18b))

- Add demo data provisioning script (db_demo.py)
  ([`47039cb`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/47039cba871e5327dc2e41597262755d1d227d44))

Two demo families with 3 years of realistic transaction history, pre-configured bank rules, loans,
  and per-family archive config. Includes 2026 Q1 sample CSVs for manual upload testing.

Run `python db_demo.py` to provision, `--destroy` to remove.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

- Gates to enable settings just for admins
  ([`615fe5b`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/615fe5ba58fef2d16b1e0443d442d819a4c2d9a8))

- Per-family raw archive toggle
  ([`97ffe8c`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/97ffe8cf3821a6692299eac6f5afe47d07e43e2f))

- New app_config_archive table (family_id PK, archive_enabled BOOLEAN) seeded TRUE for all existing
  families on migration - config_repo: load_archive_enabled() / save_archive_enabled() -
  upload_pipeline: gate step 5 (raw upsert) on load_archive_enabled(family_id); returns early with
  normal result if disabled — upload still succeeds - upload_manager: no changes needed, already
  guards all raw ops with _raw_table_exists() so stale tables from before disabling are still
  cleaned up on reassign/delete - settings: new Archive tab (head+) with enable/disable toggle and
  raw CSV export; raw export section moved here from Data tab - data/db.py: remove dead
  ArchiveConfig dataclass and get_archive_cfg() (the enabled flag was never actually checked by the
  pipeline)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

### Refactoring

- Viewmanager.refresh() loads all families automatically
  ([`37bd50a`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/37bd50a69d874cae2035e134c7ca1ecceed79fca))

Views now cover all families in one combined pass. Each family's account_key branches use that
  family's own category rules and transaction config, so per-family categorisation is correct. No
  family_id argument needed — callers just call refresh() and every family is included.

- view_manager: refresh() queries all families with bank rules, builds per-family _FamilyViewData,
  passes to each _build_*_view method - Each view builder iterates by family then by rule, using
  per-family cfg_cat/cfg for category expressions and exclusion patterns - All 8 call sites updated
  to drop the family_id argument - db_demo: removed _refresh_combined_views() helper (now redundant)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>


## v2.0.0 (2026-03-19)

### Documentation

- Add testing section to CLAUDE.md
  ([`76ba828`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/76ba8284e5e8e7258b29cc319f862fe952e16e14))

Documents the integration test setup, how to run tests, conftest fixtures, and the full test file
  inventory.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

### Features

- Multi-tenancy, family hierarchy, settings overhaul, upload manager
  ([`e529d3a`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/e529d3a8e32e6b5ba0ffde295a69b59d434bd6f9))

BREAKING CHANGE: Requires a clean database. All transaction queries are now scoped by family_id. The
  auth session, user model, and DB schema have changed significantly — existing sessions and data
  are incompatible.

Key changes: - Multi-tenancy: family_id stamped on all transactions; dashboard queries scoped via
  _family_filter() - Family hierarchy: families table, family_memberships, instance admin / family
  head / member roles; is_family_head() includes instance admins - Settings page: consolidated into
  tabbed layout (Personal / Uploads / Data / Users / Family); removed separate /family and /users
  nav items - Upload Manager: new Uploads tab to list batches, reassign person[], or delete an
  upload from both consolidated and raw archive tables - EmployerPattern ownership: head-owned
  patterns protected from members - User management: family assignment in create and edit dialogs;
  family column in user list - Dashboard defaults: hardcoded reference layout replaces auto-packed
  seed; non-KPI widgets default to row_span=2 - Config import now auto-refreshes views on success -
  Stale session guard: redirects to /login if DB user no longer exists - Chart builder: graceful
  empty-DB handling - New services: family_service, upload_manager, dashboard_grid_layout -
  Integration test suite with docker-compose postgres fixture

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>

### Breaking Changes

- Requires a clean database. All transaction queries are now scoped by family_id. The auth session,
  user model, and DB schema have changed significantly — existing sessions and data are
  incompatible.


## v1.7.1 (2026-03-18)

### Bug Fixes

- Db migrations
  ([`c4a297c`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/c4a297c2ea6d0c8989483b747b9ced8ae5b0683a))


## v1.7.0 (2026-03-18)

### Features

- Add cancel flow for widgets and dashboard
  ([`40a75e6`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/40a75e65b0c6f8fd8a472a9e8badc81a2d94ce01))

- Per bank settings
  ([`c13f3e6`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/c13f3e69ce3bff470654d2a7dacf82340833db93))

- Refactor finance dashboard to break into smaller components
  ([`3a86185`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/3a8618523775b7818461a7c1ece0119ae70f4b8d))

- Separate build in charts from custom
  ([`fef0ecb`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/fef0ecbd591ec125b29d6097e71c73a9303612e4))

- Setting moved from dashboard page
  ([`20693b3`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/20693b3582765bb2457970d4745091e0a3af7089))


## v1.6.0 (2026-03-18)

### Features

- Chart editor
  ([`c903a75`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/c903a75ad182ba89ae2461e2696999e10aec5b58))


## v1.5.1 (2026-03-17)

### Bug Fixes

- Person id on upload
  ([`9f1fe2b`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/9f1fe2bdafbd6db3bb4ef2446da36e9b869f9bad))


## v1.5.0 (2026-03-17)

### Bug Fixes

- Person id on upload
  ([`2241768`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/22417680f03b2d067436e798483c359c797163f7))

### Features

- Refactor widgets
  ([`15f04cf`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/15f04cf09470af88bb7e0915fb32e48769aff80e))


## v1.4.0 (2026-03-17)

### Bug Fixes

- Update calculations for matrix snaps
  ([`d25619b`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/d25619bf9508c526914f0200f3f4c8e0e0b44509))

### Features

- Drag and drop + resize
  ([`a22468c`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/a22468ca06c2b484cc5683735e867e7fda1d2cfe))

- Phase 1 editable dashboard
  ([`09df1cf`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/09df1cf3d1c1f6757752b2d3439a66a8801b7265))

- Phase 2 editable dashboard
  ([`f959f5a`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/f959f5ab50c18b13c8d963c850db369dc2847e5e))

- Unified font on dashboard labels
  ([`b4cb0b6`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/b4cb0b65618eb2eab6751e18eaac783009601fef))


## v1.3.0 (2026-03-16)

### Features

- Export per person
  ([`fc93c28`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/fc93c28f1bd4d242db3af406ef002efe4f68e3b7))

- Move wizard_component to components
  ([`926c86d`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/926c86dc2c79f91170102a60310d690ae8fe1624))

- Update settings icon
  ([`b6f5e60`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/b6f5e603a812aece5b01253f74c53028294a4f1f))

- Use consolidated tables instead of raw
  ([`49cb893`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/49cb8939e51b1df118a96a95dc1f62994f1580dd))


## v1.2.1 (2026-03-16)

### Bug Fixes

- Bug in bank rule matcher
  ([`55a6a54`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/55a6a54eecf08a380666ca3dbef5b35d2f03d1d6))


## v1.2.0 (2026-03-16)

### Features

- Refactor bank wizard
  ([`025bf95`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/025bf95a86f52625039350a601aaf3b7fd7383cb))


## v1.1.0 (2026-03-15)

### Bug Fixes

- Domain
  ([`7e9640f`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/7e9640f54830796b60b34d900cf4423f725acb28))

- Domain
  ([`8e677a7`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/8e677a7ea63b7088ca72da9d33048d1857ef1405))

- Gitlab ci
  ([`3c0a82a`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/3c0a82ad9b0f5654c3c53da0a8500241b7e4dc34))

- Semantic release
  ([`9fe4a3b`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/9fe4a3b300a0b4b85bf83a491998b541e1c93c87))

### Features

- Auto release on push to main
  ([`55aeba1`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/55aeba12803cd13ab2a85fc86a0437a974ce743f))

- Use notify from services
  ([`524bde2`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/524bde233ffa6d39ed97d6095ea062995651c3eb))


## v1.0.0 (2026-03-14)


## v0.2.0 (2026-03-13)

### Bug Fixes

- Gitlab ci
  ([`e557e1b`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/e557e1b59d22c6cca84de536c4a7a327a3620025))

- Gitlab ci
  ([`4d1c3c1`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/4d1c3c18fc763869eb02859984302c2662adbf6c))

- Handle missing views
  ([`8653275`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/86532756a12e061f91123ce0deecf931dd6e4658))

### Documentation

- Update changelog
  ([`972e758`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/972e7581da1514be0cf754e5c0c4e6c0c186b415))

### Features

- Alpha 0.1
  ([`eb30551`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/eb305518607c94a6f87e8c1b229e4bce001ae0ae))

- Alpha refactor
  ([`fa82f08`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/fa82f08339d2e9c6d789f4ee6fa87ac850ea8097))

- Refactor
  ([`1a41341`](https://gitlab.iveydomek.xyz/scripts/finances/finance-manager/-/commit/1a4134145b26c7bb7e30cf0702abdd71d65dab4c))
