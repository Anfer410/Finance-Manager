"""
pages/settings_content.py

Settings page:
  - All users: change their own display name + password
  - Admin only: user management (add, edit, deactivate users)
"""

from nicegui import ui
import services.auth as auth
import json
import base64
from datetime import datetime
from services.transaction_config import load_config, save_config
from services.bank_rules import load_rules, save_rules, BankRule
from services.category_rules import load_category_config, save_category_config, CategoryConfig, Category, CategoryRule


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
            ui.label(f'Role: {"Admin" if user.role == "admin" else "Member"}').classes('text-sm text-zinc-400')

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
                    if len(pw) < 4:
                        state["error"] = "Password must be at least 4 characters."
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

def _user_row(u: auth.AuthUser, on_change) -> None:
    """One row in the user table — inline edit on click."""

    role_color  = 'bg-zinc-800 text-white' if u.role == 'admin' else 'bg-blue-50 text-blue-700'
    active_color = 'text-green-600' if u.is_active else 'text-zinc-300'

    with ui.row().classes('items-center px-6 py-3 gap-4 border-b border-zinc-50 hover:bg-zinc-50 w-full'):
        # Status dot
        ui.icon('circle').classes(f'text-xs {active_color}')

        # Name + username
        with ui.column().classes('gap-0 min-w-32'):
            ui.label(u.display_name).classes('text-sm font-medium text-zinc-800')
            ui.label(f'@{u.username}').classes('text-xs text-zinc-400')

        # Person tag
        ui.label(u.person_name).classes('text-xs bg-zinc-100 text-zinc-600 px-2 py-0.5 rounded-full font-mono')

        # Role badge
        ui.label(u.role.capitalize()).classes(f'text-xs px-2 py-0.5 rounded-full font-medium {role_color}')

        ui.space()

        # Edit button → opens dialog
        ui.button(icon='edit', on_click=lambda: _edit_user_dialog(u, on_change)) \
            .props('flat round dense').classes('text-zinc-400')


def _edit_user_dialog(u: auth.AuthUser, on_change) -> None:
    state = {"error": ""}

    with ui.dialog() as dlg, ui.card().classes('w-96 rounded-2xl p-6 gap-4'):
        with ui.row().classes('items-center justify-between w-full mb-2'):
            ui.label(f'Edit — {u.username}').classes('text-base font-semibold text-zinc-800')
            ui.button(icon='close', on_click=dlg.close).props('flat round dense').classes('text-zinc-400')

        display_input  = ui.input(label='Display name', value=u.display_name).props('outlined dense').classes('w-full')
        person_input = ui.input(label='Person name', value=u.person_name) \
            .props('outlined dense').classes('w-full')
        ui.label('Lowercase identifier used to tag their transactions.') \
            .classes('text-xs text-zinc-400 -mt-3 mb-1')
        role_select    = ui.select(
            label='Role',
            options={'admin': 'Admin', 'user': 'Member'},
            value=u.role,
        ).props('outlined dense').classes('w-full')
        active_toggle  = ui.switch('Account active', value=u.is_active).classes('text-sm text-zinc-600')

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
            updates["person_name"] = person_input.value.strip().lower() or u.person_name
            updates["role"]        = role_select.value
            updates["is_active"]   = active_toggle.value
            pw = new_pw.value
            if pw:
                if pw != conf_pw.value:
                    state["error"] = "Passwords do not match."
                    dialog_feedback.refresh()
                    return
                if len(pw) < 4:
                    state["error"] = "Password must be at least 4 characters."
                    dialog_feedback.refresh()
                    return
                updates["password"] = pw
            auth.update_user(u.id, **updates)
            dlg.close()
            on_change()

        with ui.row().classes('gap-2 justify-end w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
            ui.button('Save', on_click=save, icon='save').props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4')

    dlg.open()


def _add_user_dialog(on_change) -> None:
    state = {"error": ""}

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

        # ── Data access
        ui.label('Data access').classes('text-xs font-semibold text-zinc-400 uppercase tracking-wide mt-2')
        person_input = ui.input(label='Person name', placeholder='e.g. jessica') \
            .props('outlined dense').classes('w-full')
        ui.label('Lowercase identifier used to tag their transactions. Set once, keep it consistent.') \
            .classes('text-xs text-zinc-400 -mt-3 mb-1')

        role_select = ui.select(
            label='Role',
            options={'admin': 'Admin', 'user': 'Member'},
            value='user',
        ).props('outlined dense').classes('w-full')
        ui.label('Admin sees all data and manages settings. Member sees only their own.') \
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
            if len(pw) < 4:
                state["error"] = "Password must be at least 4 characters."
                dialog_feedback.refresh()
                return
            existing = auth.get_user_by_username(username)
            if existing:
                state["error"] = f"Username '{username}' is already taken."
                dialog_feedback.refresh()
                return
            person = person_input.value.strip().lower()
            if not person:
                state["error"] = "Person name is required."
                dialog_feedback.refresh()
                return
            auth.create_user(
                username=username,
                password=pw,
                display_name=display,
                person_name=person,
                role=role_select.value,
            )
            dlg.close()
            on_change()

        with ui.row().classes('gap-2 justify-end w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
            ui.button('Create', on_click=create, icon='person_add').props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4')

    dlg.open()


def _user_management_section() -> None:
    # Pull persons from transaction data for the person selector
    try:
        from services.finance_dashboard_data import get_persons
        all_persons = get_persons()
    except Exception:
        all_persons = []

    @ui.refreshable
    def user_table() -> None:
        users = auth.get_all_users()
        with _card('User Management', 'group'):
            _section_header('User Management', 'group')

            # Table header
            with ui.row().classes('items-center px-6 py-2 gap-4 w-full'):
                ui.label('').classes('text-xs w-3')   # status dot column
                ui.label('User').classes('text-xs font-medium text-zinc-400 uppercase tracking-wide min-w-32')
                ui.label('Person').classes('text-xs font-medium text-zinc-400 uppercase tracking-wide')
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
                    _user_row(u, on_change=user_table.refresh)

            # Legend
            with ui.row().classes('px-6 py-3 gap-4'):
                ui.label('● Active').classes('text-xs text-green-600')
                ui.label('● Inactive').classes('text-xs text-zinc-300')

    user_table()


# ── Main content ──────────────────────────────────────────────────────────────


def _aliases_section() -> None:
    cfg = load_config()

    with _card('Member name aliases', 'swap_horiz'):
        _section_header('Member name aliases', 'swap_horiz')
        with ui.column().classes('px-6 py-5 gap-4 w-full'):

            ui.label(
                'Maps member name substrings from bank CSVs to person identifiers. '
                'Used for banks like Citi that store cardholder name instead of a person tag '
                '(e.g. "ANDRZEJ" → "andy").'
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
                alias_name_in  = ui.input(label='Bank member name', placeholder='e.g. ANDRZEJ') \
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
                        save_config(cfg)

                ui.button('Add', icon='add', on_click=_add_alias) \
                    .props('unelevated dense no-caps').classes('bg-zinc-800 text-white rounded-lg')


def content() -> None:
    with ui.column().classes('w-full max-w-3xl mx-auto px-4 py-6 gap-6'):

        # Page title
        with ui.row().classes('items-center gap-3 mb-2'):
            ui.icon('settings').classes('text-zinc-400 text-2xl')
            ui.label('Settings').classes('text-2xl font-bold text-zinc-800')

        # Profile — visible to everyone
        _profile_section()

        # User management — admin only
        if auth.is_admin():
            _user_management_section()
            _aliases_section()
            _export_import_section()


# ── Data export / import ───────────────────────────────────────────────────────

def _export_import_section() -> None:

    def _build_export() -> dict:
        """Assemble all config into a single portable dict."""
        cat_cfg   = load_category_config()
        bank_rules = load_rules()
        txn_cfg   = load_config()
        return {
            "_version": 1,
            "_exported_at": datetime.now().isoformat(timespec="seconds"),
            "categories": cat_cfg.to_dict(),
            "bank_rules": [r.to_dict() for r in bank_rules],
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
        ui.notify(f'Exported {filename}', type='positive', position='top')

    with _card('Data migration', 'import_export'):
        _section_header('Data migration', 'import_export')
        with ui.column().classes('px-6 py-5 gap-5 w-full'):

            # ── Export ─────────────────────────────────────────────────────
            with ui.column().classes('gap-2 w-full'):
                ui.label('Export settings').classes('text-sm font-semibold text-zinc-700')
                ui.label(
                    'Downloads a JSON file containing all categories, category rules, '
                    'bank detection rules, and transaction config. '
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
                            if data.get('_version') != 1:
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

                    br_data = data.get('bank_rules')
                    _toggle_row(
                        'bank_rules', 'Bank detection rules',
                        f"{len(br_data)} rules" if br_data else '',
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
                        try:
                            if sections.get('categories') and sections['categories'].value and cat_data:
                                cfg = CategoryConfig(
                                    categories=[Category.from_dict(c) for c in cat_data.get('categories', [])],
                                    rules=[CategoryRule.from_dict(r) for r in cat_data.get('rules', [])],
                                )
                                save_category_config(cfg)
                                imported.append('categories')

                            if sections.get('bank_rules') and sections['bank_rules'].value and br_data:
                                save_rules([BankRule.from_dict(r) for r in br_data])
                                imported.append('bank rules')

                            if sections.get('transaction_config') and sections['transaction_config'].value and txn_data:
                                from services.transaction_config import TransactionConfig
                                save_config(TransactionConfig.from_dict(txn_data))
                                imported.append('transaction config')

                            if imported:
                                ui.notify(f"Imported: {', '.join(imported)}", type='positive', position='top')
                            else:
                                ui.notify('Nothing selected to import.', type='warning', position='top')

                            import_state['preview'] = None
                            import_ui.refresh()
                        except Exception as ex:
                            ui.notify(f'Import failed: {ex}', type='negative', position='top')

                    with ui.row().classes('gap-2 mt-1'):
                        ui.button('Cancel', on_click=lambda: (
                            import_state.update({'preview': None, 'error': ''}),
                            import_ui.refresh()
                        )).props('flat no-caps').classes('text-zinc-500')

                        ui.button('Import selected', icon='upload', on_click=do_import) \
                            .props('unelevated no-caps') \
                            .classes('bg-zinc-800 text-white rounded-lg px-4')

            import_ui()