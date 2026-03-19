"""
pages/family_content.py

Family Management page.

Accessible to: Family Head + Instance Admin.

Family Head sees:
  - Current family name + member list
  - Can change any member's role (member ↔ head)
  - Can remove any member (except themselves)

Instance Admin additionally sees:
  - All families overview
  - Create new family button
  - Can jump between families to manage them
"""
from __future__ import annotations

import services.auth as auth
from services.family_service import (
    get_family, get_family_members, get_all_families,
    create_family, rename_family,
    update_member_role, remove_member, add_user_to_family,
    get_users_without_family, FamilyMember,
)
from services.notifications import notify
from nicegui import ui


# ── Helpers ────────────────────────────────────────────────────────────────────

def _card(title: str, icon: str):
    return ui.card().classes('w-full rounded-2xl shadow-none border border-zinc-100 p-0 gap-0')


def _section_header(title: str, icon: str) -> None:
    with ui.row().classes('items-center gap-3 px-6 py-4 border-b border-zinc-100'):
        ui.icon(icon).classes('text-zinc-400 text-xl')
        ui.label(title).classes('text-base font-semibold text-zinc-700')


def _role_badge(role: str, is_admin: bool) -> None:
    if is_admin:
        label, css = 'Instance Admin', 'bg-zinc-800 text-white'
    elif role == 'head':
        label, css = 'Family Head', 'bg-amber-100 text-amber-700'
    else:
        label, css = 'Member', 'bg-blue-50 text-blue-700'
    ui.label(label).classes(f'text-xs px-2 py-0.5 rounded-full font-medium {css}')


# ── Member row ─────────────────────────────────────────────────────────────────

def _member_row(m: FamilyMember, family_id: int, current_user_id: int,
                is_head_or_admin: bool, on_change) -> None:
    is_self = m.user_id == current_user_id
    active_dot = 'text-green-500' if m.is_active else 'text-zinc-300'

    with ui.row().classes('items-center px-6 py-3 gap-4 border-b border-zinc-50 hover:bg-zinc-50 w-full'):
        ui.icon('circle').classes(f'text-xs {active_dot}')

        with ui.column().classes('gap-0 min-w-32'):
            with ui.row().classes('items-center gap-2'):
                ui.label(m.display_name).classes('text-sm font-medium text-zinc-800')
                if is_self:
                    ui.label('(you)').classes('text-xs text-zinc-400')
            ui.label(f'@{m.username}').classes('text-xs text-zinc-400')

        ui.label(m.person_name).classes(
            'text-xs bg-zinc-100 text-zinc-600 px-2 py-0.5 rounded-full font-mono'
        )

        _role_badge(m.family_role, m.is_instance_admin)

        if m.joined_at:
            joined = m.joined_at.strftime('%b %Y') if hasattr(m.joined_at, 'strftime') else str(m.joined_at)[:7]
            ui.label(f'Joined {joined}').classes('text-xs text-zinc-400')

        ui.space()

        if is_head_or_admin and not is_self:
            ui.button(icon='edit',
                      on_click=lambda m=m: _edit_member_dialog(m, family_id, on_change)) \
                .props('flat round dense').classes('text-zinc-400')


def _edit_member_dialog(m: FamilyMember, family_id: int, on_change) -> None:
    state = {"error": ""}

    with ui.dialog() as dlg, ui.card().classes('w-96 rounded-2xl p-6 gap-4'):
        with ui.row().classes('items-center justify-between w-full mb-2'):
            ui.label(f'Edit — {m.display_name}').classes('text-base font-semibold text-zinc-800')
            ui.button(icon='close', on_click=dlg.close).props('flat round dense').classes('text-zinc-400')

        role_select = ui.select(
            label='Family role',
            options={'head': 'Family Head', 'member': 'Member'},
            value=m.family_role,
        ).props('outlined dense').classes('w-full')

        @ui.refreshable
        def feedback():
            if state["error"]:
                ui.label(state["error"]).classes('text-sm text-red-500')

        feedback()

        def save_role():
            update_member_role(m.user_id, family_id, role_select.value)
            dlg.close()
            on_change()

        def confirm_remove():
            with ui.dialog() as confirm_dlg, ui.card().classes('rounded-2xl p-6 gap-4 w-80'):
                ui.label(f'Remove {m.display_name} from this family?') \
                    .classes('text-sm text-zinc-700')
                ui.label('Their past transaction history stays attributed to this family.') \
                    .classes('text-xs text-zinc-400')
                with ui.row().classes('gap-2 justify-end w-full mt-2'):
                    ui.button('Cancel', on_click=confirm_dlg.close) \
                        .props('flat no-caps').classes('text-zinc-500')
                    ui.button('Remove', on_click=lambda: _do_remove(confirm_dlg)) \
                        .props('unelevated no-caps') \
                        .classes('bg-red-600 text-white rounded-lg px-4')
            confirm_dlg.open()

        def _do_remove(confirm_dlg):
            remove_member(m.user_id, family_id)
            confirm_dlg.close()
            dlg.close()
            on_change()

        with ui.row().classes('gap-2 justify-between w-full mt-2'):
            ui.button('Remove from family', icon='person_remove',
                      on_click=confirm_remove) \
                .props('flat no-caps').classes('text-red-500')
            with ui.row().classes('gap-2'):
                ui.button('Cancel', on_click=dlg.close) \
                    .props('flat no-caps').classes('text-zinc-500')
                ui.button('Save role', on_click=save_role, icon='save') \
                    .props('unelevated no-caps') \
                    .classes('bg-zinc-800 text-white rounded-lg px-4')

    dlg.open()


# ── My family section ──────────────────────────────────────────────────────────

@ui.refreshable
def _my_family_section(family_id: int) -> None:
    family = get_family(family_id)
    members = get_family_members(family_id)
    current_uid = auth.current_user_id()
    is_head_or_admin = auth.is_family_head() or auth.is_instance_admin()

    with _card('My Family', 'group'):
        with ui.row().classes('items-center gap-3 px-6 py-4 border-b border-zinc-100'):
            ui.icon('group').classes('text-zinc-400 text-xl')
            if family:
                ui.label(family.name).classes('text-base font-semibold text-zinc-700')
            ui.space()
            if auth.is_instance_admin() and family:
                ui.button(icon='edit',
                          on_click=lambda: _rename_family_dialog(family_id, family.name,
                                                                  _my_family_section.refresh)) \
                    .props('flat round dense').classes('text-zinc-400') \
                    .tooltip('Rename family')

        if not members:
            with ui.row().classes('px-6 py-8 justify-center w-full'):
                ui.label('No members found.').classes('text-sm text-zinc-400')
        else:
            for m in members:
                _member_row(m, family_id, current_uid, is_head_or_admin,
                            _my_family_section.refresh)

        # Add unassigned user (Instance Admin only)
        if auth.is_instance_admin():
            with ui.row().classes('px-6 py-4 border-t border-zinc-100'):
                ui.button('Add unassigned user', icon='person_add',
                          on_click=lambda: _add_user_dialog(family_id, _my_family_section.refresh)) \
                    .props('flat no-caps').classes('text-zinc-600 text-sm')


def _rename_family_dialog(family_id: int, current_name: str, on_change) -> None:
    with ui.dialog() as dlg, ui.card().classes('w-80 rounded-2xl p-6 gap-4'):
        ui.label('Rename Family').classes('text-base font-semibold text-zinc-800')
        name_input = ui.input(label='Family name', value=current_name) \
            .props('outlined dense').classes('w-full')

        def save():
            n = name_input.value.strip()
            if not n:
                return
            rename_family(family_id, n)
            dlg.close()
            on_change()

        with ui.row().classes('gap-2 justify-end w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
            ui.button('Save', on_click=save, icon='save').props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4')
    dlg.open()


def _add_user_dialog(family_id: int, on_change) -> None:
    users = get_users_without_family()
    if not users:
        notify('No unassigned users found.', type='info', position='top')
        return

    options = {str(u['id']): f"{u['display_name']} (@{u['username']})" for u in users}

    with ui.dialog() as dlg, ui.card().classes('w-96 rounded-2xl p-6 gap-4'):
        ui.label('Add User to Family').classes('text-base font-semibold text-zinc-800')

        user_select = ui.select(label='User', options=options) \
            .props('outlined dense').classes('w-full')
        role_select = ui.select(
            label='Role',
            options={'member': 'Member', 'head': 'Family Head'},
            value='member',
        ).props('outlined dense').classes('w-full')

        def save():
            if not user_select.value:
                return
            add_user_to_family(int(user_select.value), family_id, role_select.value)
            dlg.close()
            on_change()

        with ui.row().classes('gap-2 justify-end w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
            ui.button('Add', on_click=save, icon='person_add').props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4')
    dlg.open()


# ── All families section (Instance Admin only) ─────────────────────────────────

@ui.refreshable
def _all_families_section() -> None:
    families = get_all_families()

    with _card('All Families', 'corporate_fare'):
        with ui.row().classes('items-center gap-3 px-6 py-4 border-b border-zinc-100'):
            ui.icon('corporate_fare').classes('text-zinc-400 text-xl')
            ui.label('All Families').classes('text-base font-semibold text-zinc-700')
            ui.space()
            ui.button('New family', icon='add',
                      on_click=lambda: _create_family_dialog(_all_families_section.refresh)) \
                .props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4 text-sm')

        if not families:
            with ui.row().classes('px-6 py-8 justify-center w-full'):
                ui.label('No families yet.').classes('text-sm text-zinc-400')
        else:
            for f in families:
                _family_row(f)


def _family_row(f) -> None:
    with ui.row().classes(
        'items-center px-6 py-3 gap-4 border-b border-zinc-50 hover:bg-zinc-50 w-full'
    ):
        ui.icon('group').classes('text-zinc-400 text-lg')
        with ui.column().classes('gap-0'):
            ui.label(f.name).classes('text-sm font-medium text-zinc-800')
            ui.label(f'Family #{f.id}').classes('text-xs text-zinc-400')
        ui.space()
        ui.label(f'{f.member_count} member{"s" if f.member_count != 1 else ""}') \
            .classes('text-xs text-zinc-500 bg-zinc-100 px-2 py-0.5 rounded-full')


def _create_family_dialog(on_change) -> None:
    with ui.dialog() as dlg, ui.card().classes('w-80 rounded-2xl p-6 gap-4'):
        ui.label('Create New Family').classes('text-base font-semibold text-zinc-800')
        ui.label('Config will be seeded from the Default Family.') \
            .classes('text-xs text-zinc-400')
        name_input = ui.input(label='Family name', placeholder='e.g. The Smiths') \
            .props('outlined dense').classes('w-full')

        def save():
            n = name_input.value.strip()
            if not n:
                return
            create_family(n, auth.current_user_id())
            dlg.close()
            on_change()

        with ui.row().classes('gap-2 justify-end w-full'):
            ui.button('Cancel', on_click=dlg.close).props('flat no-caps').classes('text-zinc-500')
            ui.button('Create', on_click=save, icon='add').props('unelevated no-caps') \
                .classes('bg-zinc-800 text-white rounded-lg px-4')
    dlg.open()


# ── Entry point ────────────────────────────────────────────────────────────────

def content() -> None:
    family_id = auth.current_family_id()
    is_admin  = auth.is_instance_admin()

    with ui.column().classes('w-full max-w-4xl mx-auto px-4 py-6 gap-6'):
        ui.label('Family Management').classes('text-2xl font-bold text-zinc-800')

        if family_id:
            _my_family_section(family_id)
        else:
            with ui.card().classes('w-full rounded-2xl border border-zinc-100 px-6 py-8'):
                ui.label('You are not currently a member of any family.') \
                    .classes('text-sm text-zinc-500')

        if is_admin:
            _all_families_section()
