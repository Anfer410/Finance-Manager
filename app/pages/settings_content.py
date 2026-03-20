"""
pages/settings_content.py

Settings page:
  - All users: change their own display name + password
  - Admin only: user management (add, edit, deactivate users)
"""

import services.auth as auth
import json
import base64

from nicegui import ui
from datetime import datetime

from services.notifications import notify
from services.transaction_config import load_config, save_config
from services.raw_table_manager import default_manager
from services.config_repo import load_archive_enabled, save_archive_enabled
from data.bank_rules import load_rules, save_rules, BankRule
from data.category_rules import load_category_config, save_category_config, CategoryConfig, Category, CategoryRule


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _card(title: str, icon: str):
    """Returns a styled section card context."""
    return ui.card().classes('w-full rounded-2xl shadow-none border border-zinc-100 p-0 gap-0')


def _section_header(title: str, icon: str) -> None:
    with ui.row().classes('items-center gap-3 px-6 py-4 border-b border-zinc-100'):
        ui.icon(icon).classes('text-zinc-400 text-xl')
        ui.label(title).classes('text-base font-semibold text-zinc-700')


def _field(label: str, **props) -> ui.input:
    return ui.input(label=label).props('outlined dense').classes('w-full').props(**props) if props else \
           ui.input(label=label).props('outlined dense').classes('w-full')


# ── Profile section (all users) ───────────────────────────────────────────────

def _profile_section() -> None:
    user = auth.get_user_by_id(auth.current_user_id())
    if not user:
        return

    state = {"saved": False, "error": ""}

    with _card('My Profile', 'person'):
        _section_header('My Profile', 'person')
        with ui.column().classes('px-6 py-5 gap-4 w-full'):

            display_input = ui.input(label='Display name', value=user.display_name) \
                .props('outlined dense').classes('w-full max-w-sm')

            ui.label(f'Username: {user.username}').classes('text-sm text-zinc-400')
            ui.label(f'Role: {"Instance Admin" if user.is_instance_admin else ("Family Head" if user.family_role == "head" else "Member")}').classes('text-sm text-zinc-400')

            ui.separator().classes('my-1')
            ui.label('Change password').classes('text-sm font-medium text-zinc-600')

            with ui.row().classes('gap-3 w-full flex-wrap'):
                new_pw  = ui.input(label='New password',     password=True, password_toggle_button=True).props('outlined dense').classes('w-56')
                conf_pw = ui.input(label='Confirm password', password=True, password_toggle_button=True).props('outlined dense').classes('w-56')

            @ui.refreshable
            def profile_feedback():
                if state["saved"]:
                    ui.label('✓ Saved').classes('text-sm text-green-600')
                elif state["error"]:
                    ui.label(state["error"]).classes('text-sm text-red-500')

            profile_feedback()

            def save_profile():
                state["saved"] = False
                state["error"] = ""
                updates = {}
                dn = display_input.value.strip()
                if dn and dn != user.display_name:
                    updates["display_name"] = dn
                pw = new_pw.value
                if pw:
                    if pw != conf_pw.value:
                        state["error"] = "Passwords do not match."
                        profile_feedback.refresh()
                        return
                    if len(pw) < 6:
                        state["error"] = "Password must be at least 6 characters."
                        profile_feedback.refresh()
                        return
                    updates["password"] = pw
                if updates:
                    auth.update_user(user.id, **updates)
                    # Refresh display name in session
                    if "display_name" in updates:
                        auth.app.storage.user["auth_display_name"] = updates["display_name"]
                    new_pw.set_value("")
                    conf_pw.set_value("")
                state["saved"] = True
                profile_feedback.refresh()

            ui.button('Save changes', on_click=save_profile, icon='save') \
                .props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4')


# ── User management (admin only) ──────────────────────────────────────────────

def _user_row(u: auth.AuthUser, on_change, family_name: str | None = None) -> None:
    """One row in the user table — inline edit on click."""

    role_color   = 'bg-zinc-800 text-white' if u.is_instance_admin else ('bg-amber-100 text-amber-700' if u.family_role == 'head' else 'bg-blue-50 text-blue-700')
    active_color = 'text-green-600' if u.is_active else 'text-zinc-300'

    with ui.row().classes('items-center px-6 py-3 gap-4 border-b border-zinc-50 hover:bg-zinc-50 w-full'):
        # Status dot
        ui.icon('circle').classes(f'text-xs {active_color}')

        # Name + username
        with ui.column().classes('gap-0 min-w-32'):
            ui.label(u.display_name).classes('text-sm font-medium text-zinc-800')
            ui.label(f'@{u.username}').classes('text-xs text-zinc-400')

        # Family
        if family_name:
            ui.label(family_name).classes('text-xs text-zinc-500 min-w-24')
        else:
            ui.label('—').classes('text-xs text-zinc-300 min-w-24')

        # Role badge
        role_label = 'Admin' if u.is_instance_admin else ('Head' if u.family_role == 'head' else 'Member')
        ui.label(role_label).classes(f'text-xs px-2 py-0.5 rounded-full font-medium {role_color}')

        ui.space()

        # Edit button → opens dialog
        ui.button(icon='edit', on_click=lambda: _edit_user_dialog(u, on_change)) \
            .props('flat round dense').classes('text-zinc-400')


def _edit_user_dialog(u: auth.AuthUser, on_change) -> None:
    from services.family_service import get_all_families, add_user_to_family, remove_member, update_member_role
    state    = {"error": ""}
    families = get_all_families()
    fam_opts = {'': '— unassigned —', **{str(f.id): f.name for f in families}}

    with ui.dialog() as dlg, ui.card().classes('w-96 rounded-2xl p-6 gap-4'):
        with ui.row().classes('items-center justify-between w-full mb-2'):
            ui.label(f'Edit — {u.username}').classes('text-base font-semibold text-zinc-800')
            ui.button(icon='close', on_click=dlg.close).props('flat round dense').classes('text-zinc-400')

        display_input = ui.input(label='Display name', value=u.display_name) \
            .props('outlined dense').classes('w-full')
        active_toggle = ui.switch('Account active', value=u.is_active).classes('text-sm text-zinc-600')

        ui.separator()
        ui.label('Family').classes('text-xs font-semibold text-zinc-400 uppercase tracking-wide')
        family_select = ui.select(
            label='Family', options=fam_opts,
            value=str(u.family_id) if u.family_id else '',
        ).props('outlined dense').classes('w-full')
        role_select = ui.select(
            label='Family role',
            options={'member': 'Member', 'head': 'Family Head'},
            value=u.family_role or 'member',
        ).props('outlined dense').classes('w-full')

        ui.separator()
        ui.label('Reset password (optional)').classes('text-xs text-zinc-400')
        new_pw  = ui.input(label='New password',     password=True, password_toggle_button=True).props('outlined dense').classes('w-full')
        conf_pw = ui.input(label='Confirm password', password=True, password_toggle_button=True).props('outlined dense').classes('w-full')

        @ui.refreshable
        def dialog_feedback():
            if state["error"]:
                ui.label(state["error"]).classes('text-sm text-red-500')

        dialog_feedback()

        def save():
            state["error"] = ""
            updates = {}
            dn = display_input.value.strip()
            if dn:
                updates["display_name"] = dn
            updates["is_active"] = active_toggle.value
            pw = new_pw.value
            if pw:
                if pw != conf_pw.value:
                    state["error"] = "Passwords do not match."
                    dialog_feedback.refresh()
                    return
                if len(pw) < 6:
                    state["error"] = "Password must be at least 6 characters."
                    dialog_feedback.refresh()
                    return
                updates["password"] = pw
            auth.update_user(u.id, **updates)

            # Family assignment
            new_fid = int(family_select.value) if family_select.value else None
            if new_fid != u.family_id:
                if u.family_id:
                    remove_member(u.id, u.family_id)
                if new_fid:
                    add_user_to_family(u.id, new_fid, role_select.value)
            elif u.family_id and role_select.value != (u.family_role or 'member'):
                update_member_role(u.id, u.family_id, role_select.value)

            dlg.close()
            on_change()

        with ui.row().classes('gap-2 justify-end w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
            ui.button('Save', on_click=save, icon='save').props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4')

    dlg.open()


def _add_user_dialog(on_change) -> None:
    from services.family_service import get_all_families
    state    = {"error": ""}
    families = get_all_families()
    fam_opts = {'': '— no family —', **{str(f.id): f.name for f in families}}

    with ui.dialog() as dlg, ui.card().classes('w-96 rounded-2xl p-6 gap-4'):
        with ui.row().classes('items-center justify-between w-full mb-2'):
            ui.label('Add user').classes('text-base font-semibold text-zinc-800')
            ui.button(icon='close', on_click=dlg.close).props('flat round dense').classes('text-zinc-400')

        # ── Account info
        ui.label('Account info').classes('text-xs font-semibold text-zinc-400 uppercase tracking-wide mt-1')
        username_input = ui.input(label='Username', placeholder='e.g. jessica') \
            .props('outlined dense').classes('w-full')
        ui.label('Used to log in. Cannot be changed later.').classes('text-xs text-zinc-400 -mt-3 mb-1')

        display_input = ui.input(label='Display name', placeholder='e.g. Jessica') \
            .props('outlined dense').classes('w-full')
        ui.label('Shown in the header and user list.').classes('text-xs text-zinc-400 -mt-3 mb-1')

        # ── Family
        ui.label('Family').classes('text-xs font-semibold text-zinc-400 uppercase tracking-wide mt-2')
        family_select = ui.select(label='Family', options=fam_opts, value='') \
            .props('outlined dense').classes('w-full')
        role_select = ui.select(
            label='Family role',
            options={'member': 'Member', 'head': 'Family Head'},
            value='member',
        ).props('outlined dense').classes('w-full')
        ui.label('Family Head can manage settings and members.') \
            .classes('text-xs text-zinc-400 -mt-3 mb-1')

        # ── Password
        ui.label('Password').classes('text-xs font-semibold text-zinc-400 uppercase tracking-wide mt-2')
        pw_input   = ui.input(label='Password',         password=True, password_toggle_button=True) \
            .props('outlined dense').classes('w-full')
        conf_input = ui.input(label='Confirm password', password=True, password_toggle_button=True) \
            .props('outlined dense').classes('w-full')

        @ui.refreshable
        def dialog_feedback():
            if state["error"]:
                ui.label(state["error"]).classes('text-sm text-red-500')

        dialog_feedback()

        def create():
            state["error"] = ""
            username = username_input.value.strip()
            display  = display_input.value.strip()
            pw       = pw_input.value
            if not username or not display or not pw:
                state["error"] = "Username, display name and password are required."
                dialog_feedback.refresh()
                return
            if pw != conf_input.value:
                state["error"] = "Passwords do not match."
                dialog_feedback.refresh()
                return
            if len(pw) < 6:
                state["error"] = "Password must be at least 6 characters."
                dialog_feedback.refresh()
                return
            existing = auth.get_user_by_username(username)
            if existing:
                state["error"] = f"Username '{username}' is already taken."
                dialog_feedback.refresh()
                return
            fid = int(family_select.value) if family_select.value else None
            auth.create_user(
                username=username,
                password=pw,
                display_name=display,
                family_id=fid,
                family_role=role_select.value,
            )
            dlg.close()
            on_change()

        with ui.row().classes('gap-2 justify-end w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
            ui.button('Create', on_click=create, icon='person_add').props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4')

    dlg.open()


def _user_management_section() -> None:
    @ui.refreshable
    def user_table() -> None:
        from services.family_service import get_all_families
        users      = auth.get_all_users()
        fam_by_id  = {f.id: f.name for f in get_all_families()}
        with _card('User Management', 'group'):
            _section_header('User Management', 'group')

            # Table header
            with ui.row().classes('items-center px-6 py-2 gap-4 w-full'):
                ui.label('').classes('text-xs w-3')   # status dot column
                ui.label('User').classes('text-xs font-medium text-zinc-400 uppercase tracking-wide min-w-32')
                ui.label('Family').classes('text-xs font-medium text-zinc-400 uppercase tracking-wide min-w-24')
                ui.label('Role').classes('text-xs font-medium text-zinc-400 uppercase tracking-wide')
                ui.space()
                ui.button('Add user', icon='person_add',
                          on_click=lambda: _add_user_dialog(user_table.refresh)) \
                    .props('unelevated no-caps') \
                    .classes('bg-zinc-800 text-white rounded-lg px-3 text-sm')

            ui.separator().classes('mx-6')

            if not users:
                ui.label('No users yet.').classes('text-sm text-zinc-400 px-6 py-4')
            else:
                for u in users:
                    _user_row(u, on_change=user_table.refresh,
                              family_name=fam_by_id.get(u.family_id) if u.family_id else None)

            # Legend
            with ui.row().classes('px-6 py-3 gap-4'):
                ui.label('● Active').classes('text-xs text-green-600')
                ui.label('● Inactive').classes('text-xs text-zinc-300')

    user_table()


# ── Main content ──────────────────────────────────────────────────────────────


def _aliases_section() -> None:
    cfg = load_config(auth.current_family_id())

    with _card('Member name aliases', 'swap_horiz'):
        _section_header('Member name aliases', 'swap_horiz')
        with ui.column().classes('px-6 py-5 gap-4 w-full'):

            ui.label(
                'Maps member name substrings from bank CSVs to person identifiers. '
                'Used for banks like Citi that store cardholder name instead of a person tag '
                '(e.g. "JOHN" → "andy").'
            ).classes('text-xs text-zinc-400')

            @ui.refreshable
            def render_alias_chips() -> None:
                if not cfg.member_aliases:
                    ui.label('None configured.').classes('text-xs text-zinc-400')
                    return
                with ui.row().classes('flex-wrap gap-1'):
                    for name, alias in list(cfg.member_aliases.items()):
                        with ui.element('div').classes(
                            'inline-flex items-center gap-1 px-2 py-0.5 rounded-full '
                            'bg-blue-50 text-blue-700 border border-blue-200 text-xs font-mono'
                        ):
                            ui.label(f'{name} → {alias}')
                            ui.button(icon='close',
                                      on_click=lambda _, n=name: _remove_alias(n)) \
                                .props('flat round dense size=xs').classes('text-blue-400')

            def _remove_alias(name: str) -> None:
                cfg.member_aliases.pop(name, None)
                render_alias_chips.refresh()

            render_alias_chips()

            with ui.row().classes('items-center gap-2'):
                alias_name_in  = ui.input(label='Bank member name', placeholder='e.g. JOHN') \
                    .props('outlined dense').classes('flex-1')
                alias_value_in = ui.input(label='Person alias', placeholder='e.g. andy') \
                    .props('outlined dense').classes('w-36')

                def _add_alias() -> None:
                    name  = alias_name_in.value.strip().upper()
                    alias = alias_value_in.value.strip().lower()
                    if name and alias:
                        cfg.member_aliases[name] = alias
                        alias_name_in.set_value('')
                        alias_value_in.set_value('')
                        render_alias_chips.refresh()
                        save_config(cfg, auth.current_family_id())

                ui.button('Add', icon='add', on_click=_add_alias) \
                    .props('unelevated dense no-caps').classes('bg-zinc-800 text-white rounded-lg')


def _employers_section() -> None:
    """
    Employer patterns with ownership model:
    - Family Head / Admin: can add (head-owned, added_by=None) and remove any pattern.
    - Member: can add their own patterns (added_by=user_id) and remove only their own.
    Head-owned patterns show a lock icon and cannot be removed by members.
    """
    from services.transaction_config import EmployerPattern
    fid        = auth.current_family_id()
    uid        = auth.current_user_id()
    is_head    = auth.is_family_head()
    cfg        = load_config(fid)

    with _card('Employers', 'business'):
        _section_header('Employers', 'business')
        with ui.column().classes('px-6 py-5 gap-4 w-full'):

            ui.label(
                'Payroll description substrings used to identify your income transactions. '
                'Family Head patterns apply to everyone. Your personal patterns are only visible to you.'
            ).classes('text-xs text-zinc-400')

            @ui.refreshable
            def render_employer_chips() -> None:
                if not cfg.employer_patterns:
                    ui.label('No employers configured.').classes('text-xs text-zinc-400')
                    return
                with ui.column().classes('gap-1 w-full'):
                    for ep in list(cfg.employer_patterns):
                        is_mine    = ep.added_by == uid
                        is_head_ep = ep.added_by is None
                        can_remove = is_head or is_mine

                        # Members only see head-owned + their own patterns
                        if not is_head and not is_mine and not is_head_ep:
                            continue

                        chip_css = (
                            'bg-amber-50 text-amber-800 border-amber-200'
                            if is_head_ep else
                            'bg-zinc-100 text-zinc-700 border-zinc-200'
                        )
                        with ui.row().classes('items-center gap-1'):
                            with ui.element('div').classes(
                                f'inline-flex items-center gap-1 px-2 py-0.5 rounded-full '
                                f'border text-xs font-mono {chip_css}'
                            ):
                                if is_head_ep:
                                    ui.icon('lock').classes('text-amber-400').style('font-size:0.75rem')
                                ui.label(ep.pattern)
                                if can_remove:
                                    ui.button(
                                        icon='close',
                                        on_click=lambda _, e=ep: _remove_employer(e),
                                    ).props('flat round dense size=xs').classes('text-zinc-400')

            def _remove_employer(ep) -> None:
                cfg.employer_patterns = [e for e in cfg.employer_patterns if e is not ep]
                save_config(cfg, fid)
                render_employer_chips.refresh()

            render_employer_chips()

            with ui.row().classes('items-center gap-2'):
                pattern_in = ui.input(
                    label='Payroll description pattern',
                    placeholder='e.g. ACME CORP PAYROLL',
                ).props('outlined dense').classes('flex-1')

                def _add_employer() -> None:
                    val = pattern_in.value.strip().upper()
                    if not val:
                        return
                    if any(e.pattern == val for e in cfg.employer_patterns):
                        return
                    # Head adds as head-owned (None); members add as their own
                    added_by = None if is_head else uid
                    cfg.employer_patterns.append(EmployerPattern(pattern=val, added_by=added_by))
                    pattern_in.set_value('')
                    save_config(cfg, fid)
                    render_employer_chips.refresh()

                ui.button('Add', icon='add', on_click=_add_employer) \
                    .props('unelevated dense no-caps').classes('bg-zinc-800 text-white rounded-lg')


def _upload_manager_section() -> None:
    from services.upload_manager import get_upload_batches, reassign_persons, delete_batch
    from services.view_manager import ViewManager
    from data.db import get_engine, get_schema

    fid = auth.current_family_id()
    all_users = auth.get_all_users()
    user_opts = {str(u.id): u.display_name for u in all_users}

    def _reassign_dialog(b: dict, on_change) -> None:
        current_ids = [uid for uid, name in user_opts.items() if name in b['persons']]
        state = {"error": ""}

        with ui.dialog() as dlg, ui.card().classes('w-96 rounded-2xl p-6 gap-4'):
            with ui.row().classes('items-center justify-between w-full mb-2'):
                ui.label('Reassign person').classes('text-base font-semibold text-zinc-800')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense').classes('text-zinc-400')

            ui.label(b['source_file']).classes('text-xs font-mono text-zinc-400 truncate w-full')
            ui.label(f"{b['bank_name']} · {b['row_count']} rows").classes('text-xs text-zinc-400 -mt-2 mb-1')

            person_select = ui.select(
                label='Person(s)', options=user_opts, value=current_ids, multiple=True,
            ).props('outlined dense use-chips').classes('w-full')

            @ui.refreshable
            def _fb():
                if state["error"]:
                    ui.label(state["error"]).classes('text-sm text-red-500')
            _fb()

            def save():
                state["error"] = ""
                chosen = person_select.value
                if not chosen:
                    state["error"] = "Select at least one person."
                    _fb.refresh()
                    return
                ids = [int(v) for v in (chosen if isinstance(chosen, list) else [chosen])]
                try:
                    reassign_persons(b['source_file'], b['account_key'], b['table_type'], ids, fid)
                    ViewManager(get_engine(), schema=get_schema()).refresh()
                    notify('Person reassigned — views refreshed.', type='positive', position='top')
                    dlg.close()
                    on_change()
                except Exception as ex:
                    state["error"] = str(ex)
                    _fb.refresh()

            with ui.row().classes('gap-2 justify-end w-full mt-2'):
                ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
                ui.button('Save', icon='save', on_click=save).props('unelevated no-caps') \
                    .classes('bg-zinc-800 text-white rounded-lg px-4')

        dlg.open()

    def _confirm_delete(b: dict, on_change) -> None:
        with ui.dialog() as dlg, ui.card().classes('w-96 rounded-2xl p-6 gap-4'):
            with ui.row().classes('items-center justify-between w-full mb-2'):
                ui.label('Delete upload batch').classes('text-base font-semibold text-zinc-800')
                ui.button(icon='close', on_click=dlg.close).props('flat round dense').classes('text-zinc-400')

            ui.label(
                f"This will permanently delete {b['row_count']} transaction row(s) from "
                f"'{b['source_file']}' and the matching rows from the raw archive. "
                "This cannot be undone."
            ).classes('text-sm text-zinc-600')

            def do_delete():
                try:
                    n = delete_batch(b['source_file'], b['account_key'], b['table_type'], fid)
                    ViewManager(get_engine(), schema=get_schema()).refresh()
                    notify(f"Deleted {n} rows — views refreshed.", type='positive', position='top')
                    dlg.close()
                    on_change()
                except Exception as ex:
                    notify(f'Delete failed: {ex}', type='negative', position='top')

            with ui.row().classes('gap-2 justify-end w-full mt-2'):
                ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
                ui.button('Delete', icon='delete_outline', on_click=do_delete) \
                    .props('unelevated no-caps') \
                    .classes('bg-red-600 text-white rounded-lg px-4')

        dlg.open()

    def _batch_row(b: dict, on_change) -> None:
        date_str = (
            f"{b['date_from'].strftime('%b %d')} – {b['date_to'].strftime('%b %d, %Y')}"
            if b['date_from'] and b['date_to'] else '—'
        )
        persons_str = ', '.join(b['persons']) if b['persons'] else '—'
        type_color  = 'bg-blue-50 text-blue-700' if b['table_type'] == 'debit' else 'bg-purple-50 text-purple-700'

        with ui.row().classes('items-center gap-3 w-full px-2 py-2 border-b border-zinc-50 hover:bg-zinc-50'):
            with ui.column().classes('gap-0 flex-1 min-w-40 overflow-hidden'):
                ui.label(b['source_file']).classes('text-xs font-mono text-zinc-700 truncate')
                ui.label(b['uploaded_at'].strftime('%Y-%m-%d %H:%M') if b['uploaded_at'] else '') \
                    .classes('text-xs text-zinc-300')

            ui.label(b['bank_name']).classes('text-xs text-zinc-600 w-32 truncate')
            ui.label(b['table_type']).classes(f'text-xs px-1.5 py-0.5 rounded font-medium w-16 text-center {type_color}')
            ui.label(str(b['row_count'])).classes('text-xs text-zinc-600 w-12 text-right')
            ui.label(date_str).classes('text-xs text-zinc-500 w-36')
            ui.label(persons_str).classes('text-xs text-zinc-600 w-32 truncate')

            with ui.row().classes('gap-1 w-20 justify-end'):
                ui.button(icon='person', on_click=lambda _, batch=b: _reassign_dialog(batch, on_change)) \
                    .props('flat round dense').classes('text-zinc-400').tooltip('Reassign person')
                ui.button(icon='delete_outline', on_click=lambda _, batch=b: _confirm_delete(batch, on_change)) \
                    .props('flat round dense').classes('text-red-400').tooltip('Delete batch')

    @ui.refreshable
    def batch_table() -> None:
        batches = get_upload_batches(fid)

        with _card('Upload Manager', 'folder_open'):
            _section_header('Upload Manager', 'folder_open')
            with ui.column().classes('px-6 py-5 gap-3 w-full'):
                ui.label(
                    'View and correct uploaded transaction batches. '
                    'You can reassign people or delete an entire upload. '
                    'Both the transaction tables and the raw archive are updated.'
                ).classes('text-xs text-zinc-400')

                if not batches:
                    ui.label('No uploads found.').classes('text-sm text-zinc-400 py-2')
                    return

                with ui.row().classes('items-center gap-3 w-full px-2 py-1'):
                    ui.label('File').classes('text-xs font-medium text-zinc-400 uppercase tracking-wide flex-1 min-w-40')
                    ui.label('Account').classes('text-xs font-medium text-zinc-400 uppercase tracking-wide w-32')
                    ui.label('Type').classes('text-xs font-medium text-zinc-400 uppercase tracking-wide w-16')
                    ui.label('Rows').classes('text-xs font-medium text-zinc-400 uppercase tracking-wide w-12 text-right')
                    ui.label('Dates').classes('text-xs font-medium text-zinc-400 uppercase tracking-wide w-36')
                    ui.label('Person(s)').classes('text-xs font-medium text-zinc-400 uppercase tracking-wide w-32')
                    ui.label('').classes('w-20')

                ui.separator()

                for b in batches:
                    _batch_row(b, batch_table.refresh)

    batch_table()


def _refresh_views_section() -> None:
    with _card('Database views', 'storage'):
        _section_header('Database views', 'storage')
        with ui.column().classes('px-6 py-5 gap-3 w-full'):
            ui.label(
                'Rebuild the PostgreSQL views used by the dashboard. '
                'Run this after changing transfer patterns, bank accounts, or category rules.'
            ).classes('text-xs text-zinc-400')

            def _do_refresh():
                try:
                    from services.view_manager import ViewManager
                    from services.transfer_detection_service import run_detection
                    from data.db import get_engine, get_schema
                    engine = get_engine()
                    schema = get_schema()
                    fid = auth.current_family_id()
                    run_detection(fid, engine, schema)
                    ViewManager(engine, schema=schema).refresh()
                    notify('Views refreshed.', type='positive', position='top')
                except Exception as ex:
                    notify(f'Refresh failed: {ex}', type='negative', position='top')

            ui.button('Refresh views', icon='refresh', on_click=_do_refresh) \
                .props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4 self-start')


# ── Users tab: family members ─────────────────────────────────────────────────

def _family_members_section() -> None:
    from services.family_service import (
        get_family, get_family_members, update_member_role,
        remove_member, add_user_to_family, get_users_without_family,
    )
    fid = auth.current_family_id()
    if not fid:
        return

    @ui.refreshable
    def members_table() -> None:
        family  = get_family(fid)
        members = get_family_members(fid)
        uid     = auth.current_user_id()

        with _card(family.name if family else 'Family Members', 'group'):
            with ui.row().classes('items-center gap-3 px-6 py-4 border-b border-zinc-100'):
                ui.icon('group').classes('text-zinc-400 text-xl')
                ui.label(family.name if family else 'Family Members') \
                    .classes('text-base font-semibold text-zinc-700')
                ui.space()
                unassigned = get_users_without_family()
                if unassigned:
                    ui.button('Add user', icon='person_add',
                              on_click=lambda: _add_member_dialog(
                                  fid, unassigned, members_table.refresh)) \
                        .props('unelevated no-caps') \
                        .classes('bg-zinc-800 text-white rounded-lg px-4 text-sm')

            if not members:
                ui.label('No members.').classes('text-sm text-zinc-400 px-6 py-4')
            else:
                for m in members:
                    is_self = m.user_id == uid
                    dot_css = 'text-green-500' if m.is_active else 'text-zinc-300'
                    role_label, role_css = (
                        ('Instance Admin', 'bg-zinc-800 text-white') if m.is_instance_admin else
                        ('Family Head',    'bg-amber-100 text-amber-700') if m.family_role == 'head' else
                        ('Member',         'bg-blue-50 text-blue-700')
                    )
                    with ui.row().classes(
                        'items-center px-6 py-3 gap-4 border-b border-zinc-50 hover:bg-zinc-50 w-full'
                    ):
                        ui.icon('circle').classes(f'text-xs {dot_css}')
                        with ui.column().classes('gap-0 min-w-32'):
                            with ui.row().classes('items-center gap-2'):
                                ui.label(m.display_name).classes('text-sm font-medium text-zinc-800')
                                if is_self:
                                    ui.label('(you)').classes('text-xs text-zinc-400')
                            ui.label(f'@{m.username}').classes('text-xs text-zinc-400')
                        ui.label(role_label).classes(
                            f'text-xs px-2 py-0.5 rounded-full font-medium {role_css}'
                        )
                        ui.space()
                        if not is_self:
                            ui.button(icon='edit',
                                      on_click=lambda m=m: _edit_member_dialog(
                                          m, fid, members_table.refresh)) \
                                .props('flat round dense').classes('text-zinc-400')

    members_table()


def _edit_member_dialog(m, fid: int, on_change) -> None:
    from services.family_service import update_member_role, remove_member

    with ui.dialog() as dlg, ui.card().classes('w-80 rounded-2xl p-6 gap-4'):
        with ui.row().classes('items-center justify-between w-full mb-2'):
            ui.label(f'Edit — {m.display_name}').classes('text-base font-semibold text-zinc-800')
            ui.button(icon='close', on_click=dlg.close).props('flat round dense').classes('text-zinc-400')

        role_select = ui.select(
            label='Family role',
            options={'head': 'Family Head', 'member': 'Member'},
            value=m.family_role,
        ).props('outlined dense').classes('w-full')

        def save_role():
            update_member_role(m.user_id, fid, role_select.value)
            dlg.close()
            on_change()

        def confirm_remove():
            with ui.dialog() as cdlg, ui.card().classes('rounded-2xl p-6 gap-4 w-80'):
                ui.label(f'Remove {m.display_name} from this family?') \
                    .classes('text-sm text-zinc-700')
                with ui.row().classes('gap-2 justify-end w-full mt-2'):
                    ui.button('Cancel', on_click=cdlg.close).props('flat no-caps').classes('text-zinc-500')
                    ui.button('Remove', on_click=lambda: (_do_remove(cdlg))) \
                        .props('unelevated no-caps').classes('bg-red-600 text-white rounded-lg px-4')
            cdlg.open()

        def _do_remove(cdlg):
            remove_member(m.user_id, fid)
            cdlg.close()
            dlg.close()
            on_change()

        with ui.row().classes('gap-2 justify-between w-full mt-2'):
            ui.button('Remove', icon='person_remove', on_click=confirm_remove) \
                .props('flat no-caps').classes('text-red-500')
            with ui.row().classes('gap-2'):
                ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
                ui.button('Save', on_click=save_role, icon='save').props('unelevated no-caps') \
                    .classes('bg-zinc-800 text-white rounded-lg px-4')
    dlg.open()


def _add_member_dialog(fid: int, unassigned: list, on_change) -> None:
    from services.family_service import add_user_to_family
    options = {str(u['id']): f"{u['display_name']} (@{u['username']})" for u in unassigned}

    with ui.dialog() as dlg, ui.card().classes('w-96 rounded-2xl p-6 gap-4'):
        ui.label('Add Member').classes('text-base font-semibold text-zinc-800')
        user_select = ui.select(label='User', options=options).props('outlined dense').classes('w-full')
        role_select = ui.select(
            label='Role', options={'member': 'Member', 'head': 'Family Head'}, value='member',
        ).props('outlined dense').classes('w-full')

        def save():
            if user_select.value:
                add_user_to_family(int(user_select.value), fid, role_select.value)
                dlg.close()
                on_change()

        with ui.row().classes('gap-2 justify-end w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
            ui.button('Add', on_click=save, icon='person_add').props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4')
    dlg.open()


# ── Family tab: all families (admin only) ─────────────────────────────────────

def _all_families_section() -> None:
    from services.family_service import get_all_families, create_family, rename_family

    @ui.refreshable
    def families_list() -> None:
        families = get_all_families()
        with _card('All Families', 'corporate_fare'):
            with ui.row().classes('items-center gap-3 px-6 py-4 border-b border-zinc-100'):
                ui.icon('corporate_fare').classes('text-zinc-400 text-xl')
                ui.label('All Families').classes('text-base font-semibold text-zinc-700')
                ui.space()
                ui.button('New family', icon='add',
                          on_click=lambda: _create_family_dialog(families_list.refresh)) \
                    .props('unelevated no-caps') \
                    .classes('bg-zinc-800 text-white rounded-lg px-4 text-sm')

            if not families:
                ui.label('No families yet.').classes('text-sm text-zinc-400 px-6 py-4')
            else:
                for f in families:
                    with ui.row().classes(
                        'items-center px-6 py-3 gap-4 border-b border-zinc-50 hover:bg-zinc-50 w-full'
                    ):
                        ui.icon('group').classes('text-zinc-400')
                        with ui.column().classes('gap-0'):
                            ui.label(f.name).classes('text-sm font-medium text-zinc-800')
                            ui.label(f'#{f.id}').classes('text-xs text-zinc-400')
                        ui.space()
                        ui.label(
                            f'{f.member_count} member{"s" if f.member_count != 1 else ""}'
                        ).classes('text-xs text-zinc-500 bg-zinc-100 px-2 py-0.5 rounded-full')
                        ui.button(icon='edit',
                                  on_click=lambda f=f: _rename_family_dialog(
                                      f.id, f.name, families_list.refresh)) \
                            .props('flat round dense').classes('text-zinc-400')

    families_list()


def _rename_family_dialog(fid: int, current_name: str, on_change) -> None:
    from services.family_service import rename_family
    with ui.dialog() as dlg, ui.card().classes('w-80 rounded-2xl p-6 gap-4'):
        ui.label('Rename Family').classes('text-base font-semibold text-zinc-800')
        name_input = ui.input(label='Family name', value=current_name) \
            .props('outlined dense').classes('w-full')

        def save():
            n = name_input.value.strip()
            if n:
                rename_family(fid, n)
                dlg.close()
                on_change()

        with ui.row().classes('gap-2 justify-end w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
            ui.button('Save', on_click=save, icon='save').props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4')
    dlg.open()


def _create_family_dialog(on_change) -> None:
    from services.family_service import create_family
    with ui.dialog() as dlg, ui.card().classes('w-80 rounded-2xl p-6 gap-4'):
        ui.label('Create New Family').classes('text-base font-semibold text-zinc-800')
        ui.label('Config will be seeded from Default Family.').classes('text-xs text-zinc-400')
        name_input = ui.input(label='Family name', placeholder='e.g. The Smiths') \
            .props('outlined dense').classes('w-full')

        def save():
            n = name_input.value.strip()
            if n:
                create_family(n, auth.current_user_id())
                dlg.close()
                on_change()

        with ui.row().classes('gap-2 justify-end w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
            ui.button('Create', on_click=save, icon='add').props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4')
    dlg.open()


# ── Data export / import ───────────────────────────────────────────────────────

def _export_import_section() -> None:

    def _build_export() -> dict:
        """Assemble all config into a single portable dict."""
        fid        = auth.current_family_id()
        cat_cfg    = load_category_config(fid)
        bank_rules = load_rules(fid)
        txn_cfg    = load_config(fid)
        return {
            "_version": 2,
            "_exported_at": datetime.now().isoformat(timespec="seconds"),
            "categories": cat_cfg.to_dict(),
            "banks": [r.to_dict() for r in bank_rules],
            "transaction_config": txn_cfg.to_dict(),
        }

    def _do_export() -> None:
        data     = _build_export()
        payload  = json.dumps(data, indent=2)
        b64      = base64.b64encode(payload.encode()).decode()
        filename = f"finance_config_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        ui.run_javascript(f"""
            const a = document.createElement('a');
            a.href = 'data:application/json;base64,{b64}';
            a.download = '{filename}';
            a.click();
        """)
        notify(f'Exported {filename}', type='positive', position='top')

    with _card('Data migration', 'import_export'):
        _section_header('Data migration', 'import_export')
        with ui.column().classes('px-6 py-5 gap-5 w-full'):

            # ── Export ─────────────────────────────────────────────────────
            with ui.column().classes('gap-2 w-full'):
                ui.label('Export settings').classes('text-sm font-semibold text-zinc-700')
                ui.label(
                    'Downloads a JSON file containing all categories, category rules, '
                    'configured banks, and transaction config. '
                    'Use this to back up your config or migrate to another instance.'
                ).classes('text-xs text-zinc-400')
                ui.button('Export config', icon='download', on_click=_do_export) \
                    .props('unelevated no-caps') \
                    .classes('bg-zinc-800 text-white rounded-lg px-4 self-start')

            ui.separator()

            # ── Import ─────────────────────────────────────────────────────
            import_state = {'error': '', 'preview': None}

            with ui.column().classes('gap-2 w-full'):
                ui.label('Import settings').classes('text-sm font-semibold text-zinc-700')
                ui.label(
                    'Upload a previously exported config JSON. '
                    'You can choose which sections to import — existing data will be overwritten.'
                ).classes('text-xs text-zinc-400')

            @ui.refreshable
            def import_ui() -> None:
                if import_state['error']:
                    ui.label(import_state['error']).classes('text-sm text-red-500')
                    return

                if import_state['preview'] is None:
                    # Show file picker
                    async def handle_file(e):
                        import_state['error'] = ''
                        try:
                            raw = await e.file.read()
                            data = json.loads(raw)
                            if data.get('_version') not in (1, 2):
                                import_state['error'] = 'Unrecognised file format.'
                                import_ui.refresh()
                                return
                            import_state['preview'] = data
                            import_ui.refresh()
                        except Exception as ex:
                            import_state['error'] = f'Could not parse file: {ex}'
                            import_ui.refresh()

                    ui.upload(
                        label='Choose config JSON',
                        on_upload=handle_file,
                        auto_upload=True,
                        max_files=1,
                    ).props('accept=.json').classes('w-full')

                else:
                    # Show preview + section toggles
                    data = import_state['preview']
                    exported_at = data.get('_exported_at', 'unknown')

                    with ui.row().classes('items-center gap-2 mb-1'):
                        ui.icon('check_circle').classes('text-green-500 text-base')
                        ui.label(f'File loaded — exported {exported_at}') \
                            .classes('text-xs text-zinc-500')

                    sections = {}

                    def _toggle_row(key: str, label: str, description: str, present: bool) -> None:
                        with ui.row().classes(
                            'items-start gap-3 px-4 py-3 rounded-xl border w-full '
                            + ('border-zinc-200 bg-white' if present else 'border-zinc-100 bg-zinc-50')
                        ):
                            sw = ui.switch(value=present and True) \
                                .classes('mt-0.5') \
                                .props('' if present else 'disable')
                            sections[key] = sw
                            with ui.column().classes('gap-0'):
                                ui.label(label).classes(
                                    'text-sm font-medium '
                                    + ('text-zinc-800' if present else 'text-zinc-400')
                                )
                                ui.label(
                                    description if present else 'Not present in this file'
                                ).classes('text-xs text-zinc-400')

                    cat_data = data.get('categories')
                    _toggle_row(
                        'categories', 'Categories & rules',
                        f"{len(cat_data.get('categories', []))} categories, "
                        f"{len(cat_data.get('rules', []))} rules" if cat_data else '',
                        bool(cat_data),
                    )

                    # v2 uses 'banks', v1 used 'bank_rules' — support both
                    br_data = data.get('banks') or data.get('bank_rules')
                    _toggle_row(
                        'banks', 'Configured banks',
                        f"{len(br_data)} banks" if br_data else '',
                        bool(br_data),
                    )

                    txn_data = data.get('transaction_config')
                    _toggle_row(
                        'transaction_config', 'Transaction config',
                        'Transfer patterns, employer patterns, member aliases' if txn_data else '',
                        bool(txn_data),
                    )

                    @ui.refreshable
                    def import_feedback():
                        pass

                    import_feedback()

                    def do_import():
                        imported = []
                        fid = auth.current_family_id()
                        try:
                            if sections.get('categories') and sections['categories'].value and cat_data:
                                cfg = CategoryConfig(
                                    categories=[Category.from_dict(c) for c in cat_data.get('categories', [])],
                                    rules=[CategoryRule.from_dict(r) for r in cat_data.get('rules', [])],
                                )
                                save_category_config(cfg, fid)
                                imported.append('categories')

                            if sections.get('banks') and sections['banks'].value and br_data:
                                save_rules([BankRule.from_dict(r) for r in br_data], fid)
                                imported.append('banks')

                            if sections.get('transaction_config') and sections['transaction_config'].value and txn_data:
                                from services.transaction_config import TransactionConfig
                                save_config(TransactionConfig.from_dict(txn_data), fid)
                                imported.append('transaction config')

                            if imported:
                                from services.view_manager import ViewManager
                                from data.db import get_engine, get_schema
                                ViewManager(get_engine(), schema=get_schema()).refresh()
                                notify(f"Imported: {', '.join(imported)} — views refreshed.", type='positive', position='top')
                            else:
                                notify('Nothing selected to import.', type='warning', position='top')

                            import_state['preview'] = None
                            import_ui.refresh()
                        except Exception as ex:
                            notify(f'Import failed: {ex}', type='negative', position='top')

                    with ui.row().classes('gap-2 mt-1'):
                        ui.button('Cancel', on_click=lambda: (
                            import_state.update({'preview': None, 'error': ''}),
                            import_ui.refresh()
                        )).props('flat no-caps').classes('text-zinc-500')

                        ui.button('Import selected', icon='upload', on_click=do_import) \
                            .props('unelevated no-caps') \
                            .classes('bg-zinc-800 text-white rounded-lg px-4')

            import_ui()


# ── Finance data export ────────────────────────────────────────────────────────

def _finance_data_export_section() -> None:

    def _query_csv(sql: str, params: dict | None = None) -> str:
        import csv, io as _io
        from sqlalchemy import text
        from data.db import get_engine
        with get_engine().connect() as conn:
            result = conn.execute(text(sql), params or {})
            rows   = result.fetchall()
            cols   = list(result.keys())
        buf = _io.StringIO()
        w   = csv.writer(buf)
        w.writerow(cols)
        w.writerows([[str(v) if v is not None else "" for v in row] for row in rows])
        return buf.getvalue()

    with _card('Finance data export', 'table_chart'):
        _section_header('Finance data export', 'table_chart')
        with ui.column().classes('px-6 py-5 gap-3 w-full'):
            ui.label(
                'Export processed transaction data and loan records as CSV. '
                'Useful for backups or migrating to another instance.'
            ).classes('text-xs text-zinc-400')

            from data.db import get_schema
            schema = get_schema()

            try:
                all_users = auth.get_all_users()
            except Exception:
                all_users = []
            person_options = {0: 'All'} | {u.id: u.display_name for u in all_users if u.is_active}
            export_state   = {'person_id': 0}

            with ui.row().classes('items-center gap-2 w-full px-1 pb-1'):
                ui.label('Person:').classes('text-xs text-zinc-500 shrink-0')
                ui.select(
                    options=person_options,
                    value=0,
                    on_change=lambda e: export_state.update({'person_id': e.value}),
                ).classes('w-44').props('outlined dense')

            ROWS = [
                ('Debit transactions',  'account_balance',        'debit_transactions'),
                ('Credit transactions', 'credit_card',            'credit_transactions'),
                ('Loans',               'account_balance_wallet', 'loans'),
            ]

            for label, icon_name, fname_base in ROWS:
                def _download(fb=fname_base, lbl=label):
                    def _do():
                        try:
                            pid   = export_state['person_id'] or None  # 0 → None (All)
                            pname = (person_options.get(pid or 0, 'all')
                                     .lower().replace(' ', '_'))
                            if fb == 'debit_transactions':
                                if pid:
                                    sql    = (f"SELECT account_key, transaction_date, description, amount, "
                                              f"person, source_file, inserted_at "
                                              f"FROM {schema}.transactions_debit "
                                              f"WHERE :pid = ANY(person) ORDER BY transaction_date DESC")
                                    params = {'pid': pid}
                                else:
                                    sql    = (f"SELECT account_key, transaction_date, description, amount, "
                                              f"person, source_file, inserted_at "
                                              f"FROM {schema}.transactions_debit ORDER BY transaction_date DESC")
                                    params = {}
                            elif fb == 'credit_transactions':
                                if pid:
                                    sql    = (f"SELECT account_key, transaction_date, description, debit, credit, "
                                              f"person, source_file, inserted_at "
                                              f"FROM {schema}.transactions_credit "
                                              f"WHERE :pid = ANY(person) ORDER BY transaction_date DESC")
                                    params = {'pid': pid}
                                else:
                                    sql    = (f"SELECT account_key, transaction_date, description, debit, credit, "
                                              f"person, source_file, inserted_at "
                                              f"FROM {schema}.transactions_credit ORDER BY transaction_date DESC")
                                    params = {}
                            else:  # loans — no person filter
                                sql    = (f"SELECT id, name, loan_type, rate_type, interest_rate, "
                                          f"original_principal, term_months, start_date, monthly_payment, "
                                          f"current_balance, balance_as_of, lender, notes, is_active, "
                                          f"created_at, updated_at FROM {schema}.app_loans ORDER BY id")
                                params = {}
                                pname  = 'all'

                            csv_data = _query_csv(sql, params)
                            b64      = base64.b64encode(csv_data.encode()).decode()
                            fname    = f"{fb}_{pname}_{datetime.now().strftime('%Y%m%d')}.csv"
                            ui.run_javascript(f"""
                                const a = document.createElement('a');
                                a.href = 'data:text/csv;base64,{b64}';
                                a.download = '{fname}';
                                a.click();
                            """)
                        except Exception as ex:
                            notify(f'Export failed: {ex}', type='negative', position='top')
                    return _do

                with ui.row().classes('items-center gap-3 w-full px-1'):
                    ui.icon(icon_name).classes('text-zinc-300 text-base')
                    ui.label(label).classes('text-sm text-zinc-700 flex-1')
                    ui.button('Download CSV', icon='download', on_click=_download()) \
                        .props('flat dense no-caps') \
                        .classes('text-zinc-600')


# ── Finance data import ────────────────────────────────────────────────────────

def _finance_data_import_section() -> None:

    _TYPE_LABELS = {
        'debit':  'Debit transactions',
        'credit': 'Credit transactions',
        'loans':  'Loans',
    }

    import_state: dict = {'error': '', 'preview': None, 'file_type': None}

    with _card('Finance data import', 'upload_file'):
        _section_header('Finance data import', 'upload_file')
        with ui.column().classes('px-6 py-5 gap-3 w-full'):
            ui.label(
                'Import debit transactions, credit transactions, or loan records from a '
                'previously exported CSV. Duplicate transactions are skipped automatically.'
            ).classes('text-xs text-zinc-400')

            @ui.refreshable
            def import_ui() -> None:
                if import_state['error']:
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('error_outline').classes('text-red-400 text-base')
                        ui.label(import_state['error']).classes('text-sm text-red-500')
                    ui.button('Try again', on_click=lambda: (
                        import_state.update({'error': '', 'preview': None, 'file_type': None}),
                        import_ui.refresh(),
                    )).props('flat no-caps').classes('text-zinc-500 self-start mt-1')
                    return

                if import_state['preview'] is None:
                    async def handle_file(e):
                        import csv
                        import io as _io
                        import_state['error'] = ''
                        try:
                            raw  = (await e.file.read()).decode('utf-8-sig')
                            rows = list(csv.DictReader(_io.StringIO(raw)))
                            if not rows:
                                import_state['error'] = 'CSV file is empty.'
                                import_ui.refresh()
                                return
                            hdrs = set(rows[0].keys())
                            if {'account_key', 'transaction_date', 'description', 'amount'}.issubset(hdrs):
                                ftype = 'debit'
                            elif {'account_key', 'transaction_date', 'description', 'debit', 'credit'}.issubset(hdrs):
                                ftype = 'credit'
                            elif {'name', 'loan_type', 'interest_rate', 'start_date'}.issubset(hdrs):
                                ftype = 'loans'
                            else:
                                import_state['error'] = (
                                    'Unrecognised CSV format — could not detect type from headers.'
                                )
                                import_ui.refresh()
                                return
                            import_state['preview']   = rows
                            import_state['file_type'] = ftype
                            import_ui.refresh()
                        except Exception as ex:
                            import_state['error'] = f'Could not parse file: {ex}'
                            import_ui.refresh()

                    ui.upload(
                        label='Choose CSV file',
                        on_upload=handle_file,
                        auto_upload=True,
                        max_files=1,
                    ).props('accept=.csv').classes('w-full')

                else:
                    rows      = import_state['preview']
                    file_type = import_state['file_type']
                    type_lbl  = _TYPE_LABELS[file_type]

                    with ui.row().classes('items-center gap-2 mb-1'):
                        ui.icon('check_circle').classes('text-green-500 text-base')
                        ui.label(f'{type_lbl} — {len(rows)} rows ready to import') \
                            .classes('text-xs text-zinc-500')

                    def do_import():
                        from sqlalchemy import text as _text
                        from data.db import get_engine, get_schema
                        import re as _re
                        schema  = get_schema()
                        engine  = get_engine()
                        inserted = skipped = 0

                        def _parse_person(val) -> list:
                            """Convert any person serialization back to list[int] for INTEGER[]."""
                            if isinstance(val, list):
                                return [int(v) for v in val if str(v).strip().lstrip('-').isdigit()]
                            s = str(val).strip()
                            if not s:
                                return []
                            # Strip PostgreSQL array braces {1,2} or JSON array brackets [1,2]
                            s = s.strip('{}[]')
                            parts = [p.strip() for p in s.split(',') if p.strip()]
                            result = []
                            for p in parts:
                                try:
                                    result.append(int(p))
                                except ValueError:
                                    pass
                            return result

                        with engine.connect() as conn:
                            for row in rows:
                                try:
                                    with conn.begin_nested():
                                        if file_type == 'debit':
                                            conn.execute(_text(f"""
                                                INSERT INTO {schema}.transactions_debit
                                                    (account_key, transaction_date, description,
                                                     amount, person, source_file, inserted_at)
                                                VALUES (:ak, :td, :desc, :amt, :person, :src,
                                                        COALESCE(NULLIF(:ins,''), NOW()::TEXT)::TIMESTAMPTZ)
                                                ON CONFLICT DO NOTHING
                                            """), {
                                                'ak':     row['account_key'],
                                                'td':     row['transaction_date'],
                                                'desc':   row['description'],
                                                'amt':    float(row['amount'] or 0),
                                                'person': _parse_person(row.get('person', '')),
                                                'src':    row.get('source_file', ''),
                                                'ins':    row.get('inserted_at', ''),
                                            })
                                        elif file_type == 'credit':
                                            conn.execute(_text(f"""
                                                INSERT INTO {schema}.transactions_credit
                                                    (account_key, transaction_date, description,
                                                     debit, credit, person, source_file, inserted_at)
                                                VALUES (:ak, :td, :desc, :deb, :cred, :person, :src,
                                                        COALESCE(NULLIF(:ins,''), NOW()::TEXT)::TIMESTAMPTZ)
                                                ON CONFLICT DO NOTHING
                                            """), {
                                                'ak':     row['account_key'],
                                                'td':     row['transaction_date'],
                                                'desc':   row['description'],
                                                'deb':    float(row.get('debit')  or 0),
                                                'cred':   float(row.get('credit') or 0),
                                                'person': _parse_person(row.get('person', '')),
                                                'src':    row.get('source_file', ''),
                                                'ins':    row.get('inserted_at', ''),
                                            })
                                        else:  # loans
                                            is_act = row.get('is_active', 'True')
                                            conn.execute(_text(f"""
                                                INSERT INTO {schema}.app_loans
                                                    (name, loan_type, rate_type, interest_rate,
                                                     original_principal, term_months, start_date,
                                                     monthly_payment, current_balance, balance_as_of,
                                                     lender, notes, is_active)
                                                VALUES (:name, :ltype, :rtype, :rate,
                                                        :principal, :term, :start,
                                                        :payment, :balance, :bal_date,
                                                        :lender, :notes, :active)
                                            """), {
                                                'name':      row['name'],
                                                'ltype':     row.get('loan_type', 'other'),
                                                'rtype':     row.get('rate_type', 'fixed'),
                                                'rate':      float(row.get('interest_rate') or 0),
                                                'principal': float(row.get('original_principal') or 0),
                                                'term':      int(row.get('term_months') or 360),
                                                'start':     row['start_date'],
                                                'payment':   float(row.get('monthly_payment') or 0),
                                                'balance':   float(row.get('current_balance') or 0),
                                                'bal_date':  row['balance_as_of'],
                                                'lender':    row.get('lender', ''),
                                                'notes':     row.get('notes', ''),
                                                'active':    str(is_act).lower() in ('true', '1', 't'),
                                            })
                                    inserted += 1
                                except Exception:
                                    skipped += 1
                            conn.commit()

                        msg = f'Imported {inserted} {type_lbl.lower()}'
                        if skipped:
                            msg += f', {skipped} skipped (duplicates or errors)'
                        notify(msg, type='positive', position='top')
                        import_state.update({'preview': None, 'file_type': None, 'error': ''})
                        import_ui.refresh()

                    with ui.row().classes('gap-2 mt-1'):
                        ui.button('Cancel', on_click=lambda: (
                            import_state.update({'preview': None, 'file_type': None, 'error': ''}),
                            import_ui.refresh(),
                        )).props('flat no-caps').classes('text-zinc-500')
                        ui.button('Import', icon='upload', on_click=do_import) \
                            .props('unelevated no-caps') \
                            .classes('bg-zinc-800 text-white rounded-lg px-4')

            import_ui()


# ── Archive tab ────────────────────────────────────────────────────────────────

def _archive_toggle_section() -> None:
    fid     = auth.current_family_id()
    enabled = load_archive_enabled(fid)
    state   = {'enabled': enabled}

    with _card('Raw data archive', 'inventory_2'):
        _section_header('Raw data archive', 'inventory_2')
        with ui.column().classes('px-6 py-5 gap-4 w-full'):
            ui.label(
                'When enabled, every upload preserves the original parsed CSV rows in a '
                'dedicated raw_<account> table. These tables power the CSV export below and '
                'can serve as an audit trail. Disabling stops new uploads from writing to '
                'these tables — existing data is kept until you delete it.'
            ).classes('text-xs text-zinc-400 max-w-prose')

            @ui.refreshable
            def _toggle_row() -> None:
                current = load_archive_enabled(fid)
                state['enabled'] = current
                color = 'text-green-600' if current else 'text-zinc-400'
                label = 'Enabled' if current else 'Disabled'
                with ui.row().classes('items-center gap-4'):
                    sw = ui.switch('Enable raw data archive', value=current)
                    ui.label(label).classes(f'text-sm font-medium {color}')

                    def _on_change(e):
                        save_archive_enabled(fid, e.value)
                        _toggle_row.refresh()
                        notify(
                            'Archive enabled — uploads will now be stored in raw tables.' if e.value
                            else 'Archive disabled — new uploads will skip raw table storage.',
                            type='positive' if e.value else 'warning',
                            position='top',
                        )

                    sw.on_value_change(_on_change)

            _toggle_row()


# ── Raw data export ────────────────────────────────────────────────────────────

def _raw_export_section() -> None:
    with _card('Raw data export', 'table_view'):
        _section_header('Raw data export', 'table_view')
        with ui.column().classes('px-6 py-5 gap-4 w-full'):

            ui.label(
                'Download the original parsed transaction data for each bank account. '
                'These tables are populated on every upload and serve as the source archive.'
            ).classes('text-xs text-zinc-400')

            try:
                mgr      = default_manager()
                accounts = mgr.list_accounts()
            except Exception as ex:
                ui.label(f'Could not load tables: {ex}').classes('text-sm text-red-500')
                return

            if not accounts:
                ui.label('No raw data tables found yet — upload some transactions first.') \
                    .classes('text-sm text-zinc-400')
                return

            import re as _re
            rules_by_prefix = {r.prefix: r for r in (load_rules(auth.current_family_id()) or [])}

            try:
                if auth.is_instance_admin():
                    all_users = auth.get_all_users()
                else:
                    import services.family_service as fam
                    family_users = fam.get_family_members(auth.current_family_id())                    
                    all_users = [auth.get_user_by_id(u.user_id) for u in family_users]
            except Exception:
                all_users = []

            current_uid    = auth.current_user_id() or 0
            person_options = {u.id: u.display_name for u in all_users if u.is_active}

            # Build account options: account_key → human-readable label
            def _account_label(ak: str) -> str:
                rl = rules_by_prefix.get(ak)
                if not rl:
                    return ak.replace('_', ' ').title()
                bank_slug = _re.sub(r"[^a-z0-9]+", "_", rl.bank_name.strip().lower()).strip("_")
                if rl.prefix.startswith(bank_slug + "_"):
                    alias = rl.prefix[len(bank_slug) + 1:].replace("_", " ").title()
                    return f"{rl.bank_name} — {alias}"
                return rl.bank_name

            account_options  = {ak: _account_label(ak) for ak in accounts}
            raw_export_state = {
                'person_id':  current_uid if current_uid in person_options else next(iter(person_options), None),
                'account_key': accounts[0],
            }

            with ui.row().classes('items-center gap-2 w-full px-1 pb-2 flex-wrap'):
                ui.label('Person:').classes('text-xs text-zinc-500 shrink-0')
                ui.select(
                    options=person_options,
                    value=raw_export_state['person_id'],
                    on_change=lambda e: raw_export_state.update({'person_id': e.value}),
                ).classes('w-44').props('outlined dense')
                ui.label('Account:').classes('text-xs text-zinc-500 shrink-0 ml-3')
                ui.select(
                    options=account_options,
                    value=raw_export_state['account_key'],
                    on_change=lambda e: raw_export_state.update({'account_key': e.value}),
                ).classes('w-56').props('outlined dense')

            def _do_raw_download():
                try:
                    ak   = raw_export_state['account_key']
                    pid  = raw_export_state['person_id']
                    rl   = rules_by_prefix.get(ak)
                    pname = _re.sub(
                        r"[^a-z0-9]+", "_",
                        person_options.get(pid, str(pid)).lower()
                    ).strip("_")
                    if rl:
                        bank_slug  = _re.sub(r"[^a-z0-9]+", "_", rl.bank_name.strip().lower()).strip("_")
                        acct_part  = (rl.prefix[len(bank_slug) + 1:]
                                      if rl.prefix.startswith(bank_slug + "_") else rl.prefix)
                        full_alias = f"{bank_slug}_{acct_part}"
                    else:
                        full_alias = ak
                    fname    = f"raw_{full_alias}_{pname}.csv"
                    csv_data = default_manager().export_csv(ak, person_id=pid)
                    b64      = base64.b64encode(csv_data.encode()).decode()
                    ui.run_javascript(f"""
                        const a = document.createElement('a');
                        a.href = 'data:text/csv;base64,{b64}';
                        a.download = '{fname}';
                        a.click();
                    """)
                except Exception as ex:
                    notify(f'Export failed: {ex}', type='negative', position='top')

            ui.button('Download CSV', icon='download', on_click=_do_raw_download) \
                .props('unelevated dense no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-3 self-start')
            

# ── Entry point ────────────────────────────────────────────────────────────────

def content() -> None:
    is_head  = auth.is_family_head()
    is_admin = auth.is_instance_admin()

    with ui.column().classes('w-full max-w-4xl mx-auto px-4 py-6 gap-0'):
        with ui.row().classes('items-center gap-3 mb-4'):
            ui.icon('settings').classes('text-zinc-400 text-2xl')
            ui.label('Settings').classes('text-2xl font-bold text-zinc-800')

        # ── Tab bar ────────────────────────────────────────────────────────────
        with ui.tabs().classes('w-full border-b border-zinc-100') \
                .props('align=left indicator-color=zinc-800 active-color=zinc-800') as tabs:
            tab_personal = ui.tab('Personal', icon='person')
            if is_head:
                tab_users   = ui.tab('Users',    icon='group')
            if is_admin:
                tab_family = ui.tab('Family', icon='corporate_fare')
            if is_head:
                tab_uploads = ui.tab('Uploads',  icon='folder_open')
                tab_data    = ui.tab('Import/Export',     icon='move_down')
                tab_archive = ui.tab('Archive',  icon='inventory_2')

            tab_database    = ui.tab('Database', icon='storage')
            

        # ── Tab panels ─────────────────────────────────────────────────────────
        with ui.tab_panels(tabs, value=tab_personal).classes('w-full mt-6'):

            # ── Personal ──────────────────────────────────────────────────────
            with ui.tab_panel(tab_personal):
                with ui.column().classes('w-full gap-6'):
                    _profile_section()
                    _employers_section()
            
            if is_head:
                # ── Users (head+) ─────────────────────────────────────────────
                with ui.tab_panel(tab_users):
                    with ui.column().classes('w-full gap-6'):
                        _family_members_section()
                        if is_admin:
                            _user_management_section()
                
                
            if is_admin:
                # ── Family (admin only) ───────────────────────────────────────────
                with ui.tab_panel(tab_family):
                    with ui.column().classes('w-full gap-6'):
                        _all_families_section()
            
            if is_head:
                # ── Uploads (head+) ───────────────────────────────────────────────    
                with ui.tab_panel(tab_uploads):
                    _upload_manager_section()

                # ── Data (head+) ──────────────────────────────────────────────
                with ui.tab_panel(tab_data):
                    with ui.column().classes('w-full gap-6'):
                        _finance_data_export_section()
                        _finance_data_import_section()
                        _export_import_section()
                        

                # ── Archive (head+) ────────────────────────────────────────────
                with ui.tab_panel(tab_archive):
                    with ui.column().classes('w-full gap-6'):
                        _archive_toggle_section()
                        _raw_export_section()

            
            with ui.tab_panel(tab_database):
                with ui.column().classes('w-full gap-6'):
                    _refresh_views_section()

