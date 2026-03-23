"""
pages/users_content.py

User Management page — Instance Admin only.

Capabilities:
  - View all users with family membership info
  - Create new user (auto-assigned to a family)
  - Edit user: display name, person name, active toggle, is_instance_admin toggle
  - Set temporary password (sets must_change_password=true)
  - Move user to a different family
"""
from __future__ import annotations

import services.auth as auth
from services.family_service import (
    get_all_families, add_user_to_family, remove_member,
)
from services.notifications import notify
from nicegui import ui
from services.ui_inputs import labeled_input, labeled_select


# ── Helpers ────────────────────────────────────────────────────────────────────

def _section_header(title: str, icon: str) -> None:
    with ui.row().classes('items-center gap-3 px-6 py-4 border-b border-zinc-100'):
        ui.icon(icon).classes('text-zinc-400 text-xl')
        ui.label(title).classes('text-base font-semibold text-zinc-700')


def _role_badge(u: auth.AuthUser) -> None:
    if u.is_instance_admin:
        label, css = 'Instance Admin', 'bg-zinc-800 text-white'
    elif u.family_role == 'head':
        label, css = 'Family Head', 'bg-amber-100 text-amber-700'
    else:
        label, css = 'Member', 'bg-blue-50 text-blue-700'
    ui.label(label).classes(f'text-xs px-2 py-0.5 rounded-full font-medium {css}')


def _family_name(family_id: int | None, families: list) -> str:
    if family_id is None:
        return '—'
    for f in families:
        if f.id == family_id:
            return f.name
    return f'#{family_id}'


# ── User row ───────────────────────────────────────────────────────────────────

def _user_row(u: auth.AuthUser, families: list, on_change) -> None:
    active_dot = 'text-green-500' if u.is_active else 'text-zinc-300'
    is_self = u.id == auth.current_user_id()

    with ui.row().classes('items-center px-6 py-3 gap-4 border-b border-zinc-50 hover:bg-zinc-50 w-full'):
        ui.icon('circle').classes(f'text-xs {active_dot}')

        with ui.column().classes('gap-0 min-w-32'):
            with ui.row().classes('items-center gap-2'):
                ui.label(u.display_name).classes('text-sm font-medium text-zinc-800')
                if is_self:
                    ui.label('(you)').classes('text-xs text-zinc-400')
            ui.label(f'@{u.username}').classes('text-xs text-zinc-400')

        ui.label(u.person_name).classes(
            'text-xs bg-zinc-100 text-zinc-600 px-2 py-0.5 rounded-full font-mono'
        )

        _role_badge(u)

        ui.label(_family_name(u.family_id, families)) \
            .classes('text-xs text-zinc-500')

        ui.space()

        ui.button(icon='edit',
                  on_click=lambda u=u: _edit_user_dialog(u, families, on_change)) \
            .props('flat round dense').classes('text-zinc-400')


# ── Edit user dialog ───────────────────────────────────────────────────────────

def _edit_user_dialog(u: auth.AuthUser, families: list, on_change) -> None:
    state = {"error": ""}
    family_options = {str(f.id): f.name for f in families}

    with ui.dialog() as dlg, ui.card().classes('w-96 rounded-2xl p-6 gap-4'):
        with ui.row().classes('items-center justify-between w-full mb-2'):
            ui.label(f'Edit — {u.username}').classes('text-base font-semibold text-zinc-800')
            ui.button(icon='close', on_click=dlg.close) \
                .props('flat round dense').classes('text-zinc-400')

        display_input = labeled_input('Display name', value=u.display_name)
        person_input = labeled_input('Person name (lowercase)', value=u.person_name)
        ui.label('Identifier used to tag transactions.') \
            .classes('text-xs text-zinc-400 -mt-3 mb-1')

        with ui.row().classes('gap-4 w-full'):
            active_toggle = ui.switch('Active', value=u.is_active)
            admin_toggle  = ui.switch('Instance Admin', value=u.is_instance_admin)

        ui.separator()
        ui.label('Family assignment').classes('text-sm font-medium text-zinc-600')

        family_select = labeled_select(
            'Family',
            {**{'': '— unassigned —'}, **family_options},
            value=str(u.family_id) if u.family_id else '',
        )

        if u.family_id:
            role_select = labeled_select(
                'Family role',
                {'member': 'Member', 'head': 'Family Head'},
                value=u.family_role or 'member',
            )
        else:
            role_select = labeled_select(
                'Family role',
                {'member': 'Member', 'head': 'Family Head'},
                value='member',
            )

        ui.separator()
        ui.label('Set temporary password (optional)').classes('text-xs text-zinc-400')
        ui.label('User will be forced to change it on next login.') \
            .classes('text-xs text-zinc-400')
        new_pw  = labeled_input('Temp password',     password=True, password_toggle_button=True)
        conf_pw = labeled_input('Confirm password',  password=True, password_toggle_button=True)

        @ui.refreshable
        def feedback():
            if state["error"]:
                ui.label(state["error"]).classes('text-sm text-red-500')

        feedback()

        def save():
            state["error"] = ""
            updates = {}

            dn = display_input.value.strip()
            if dn:
                updates["display_name"] = dn
            pn = person_input.value.strip().lower()
            if pn:
                updates["person_name"] = pn

            updates["is_active"]         = active_toggle.value
            updates["is_instance_admin"] = admin_toggle.value

            pw = new_pw.value
            if pw:
                if pw != conf_pw.value:
                    state["error"] = "Passwords do not match."
                    feedback.refresh()
                    return
                if len(pw) < 6:
                    state["error"] = "Password must be at least 6 characters."
                    feedback.refresh()
                    return
                updates["password"]             = pw
                updates["must_change_password"] = True

            auth.update_user(u.id, **updates)

            # Handle family change
            new_fid_str = family_select.value
            new_fid = int(new_fid_str) if new_fid_str else None
            if new_fid != u.family_id:
                # Remove from current family if any
                if u.family_id:
                    remove_member(u.id, u.family_id)
                # Add to new family if one selected
                if new_fid:
                    add_user_to_family(u.id, new_fid, role_select.value)
            elif u.family_id and role_select.value != u.family_role:
                # Same family, role changed
                from services.family_service import update_member_role
                update_member_role(u.id, u.family_id, role_select.value)

            dlg.close()
            on_change()

        with ui.row().classes('gap-2 justify-end w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
            ui.button('Save', on_click=save, icon='save').props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4')

    dlg.open()


# ── Create user dialog ─────────────────────────────────────────────────────────

def _create_user_dialog(families: list, on_change) -> None:
    state = {"error": ""}
    family_options = {str(f.id): f.name for f in families}

    with ui.dialog() as dlg, ui.card().classes('w-96 rounded-2xl p-6 gap-4'):
        ui.label('Create New User').classes('text-base font-semibold text-zinc-800')

        username_input = labeled_input('Username')
        display_input  = labeled_input('Display name')
        person_input   = labeled_input('Person name (lowercase)')
        pw_input  = labeled_input('Password', password=True, password_toggle_button=True)

        ui.separator()
        ui.label('Family assignment').classes('text-sm font-medium text-zinc-600')

        family_select = labeled_select(
            'Family',
            {**{'': '— unassigned —'}, **family_options},
            value=str(families[0].id) if families else '',
        )
        role_select = labeled_select(
            'Role',
            {'member': 'Member', 'head': 'Family Head'},
            value='member',
        )

        @ui.refreshable
        def feedback():
            if state["error"]:
                ui.label(state["error"]).classes('text-sm text-red-500')

        feedback()

        def save():
            state["error"] = ""
            u = username_input.value.strip()
            d = display_input.value.strip()
            p = person_input.value.strip().lower()
            pw = pw_input.value

            if not u or not d or not p or not pw:
                state["error"] = "All fields are required."
                feedback.refresh()
                return
            if len(pw) < 6:
                state["error"] = "Password must be at least 6 characters."
                feedback.refresh()
                return

            fid_str = family_select.value
            fid = int(fid_str) if fid_str else 1

            try:
                auth.create_user(
                    username=u, password=pw,
                    display_name=d, person_name=p,
                    family_id=fid, family_role=role_select.value,
                )
            except Exception as ex:
                state["error"] = f"Error: {ex}"
                feedback.refresh()
                return

            dlg.close()
            on_change()

        with ui.row().classes('gap-2 justify-end w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
            ui.button('Create', on_click=save, icon='person_add').props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4')

    dlg.open()


# ── Main user list ─────────────────────────────────────────────────────────────

@ui.refreshable
def _users_section() -> None:
    all_users = auth.get_all_users()
    families  = get_all_families()

    with ui.card().classes('w-full rounded-2xl shadow-none border border-zinc-100 p-0 gap-0'):
        with ui.row().classes('items-center gap-3 px-6 py-4 border-b border-zinc-100'):
            ui.icon('people').classes('text-zinc-400 text-xl')
            ui.label(f'All Users ({len(all_users)})').classes('text-base font-semibold text-zinc-700')
            ui.space()
            ui.button('New user', icon='person_add',
                      on_click=lambda: _create_user_dialog(families, _users_section.refresh)) \
                .props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4 text-sm')

        if not all_users:
            with ui.row().classes('px-6 py-8 justify-center w-full'):
                ui.label('No users found.').classes('text-sm text-zinc-400')
        else:
            for u in all_users:
                _user_row(u, families, _users_section.refresh)


# ── Entry point ────────────────────────────────────────────────────────────────

def content() -> None:
    with ui.column().classes('w-full max-w-4xl mx-auto px-4 py-6 gap-6'):
        ui.label('User Management').classes('text-2xl font-bold text-zinc-800')
        _users_section()
