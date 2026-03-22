"""Application shell — top header bar, collapsible sidebar and active-link tracking."""

from contextlib import contextmanager
from nicegui import ui, app
import services.auth as auth


@contextmanager
def frame(title: str, version: str, get_logo_func=None):

    # ── Sidebar toggle ─────────────────────────────────────────────────────────
    async def toggle_sidebar():
        app.storage.user['sidebar-collapsed'] = not app.storage.user['sidebar-collapsed']
        if app.storage.user['sidebar-collapsed']:
            left_drawer.props("width=300")
            corps.text = "Collapse"
            corps.icon = "chevron_left"
            await ui.run_javascript('new Promise(resolve => setTimeout(resolve, 50))')
            for label in sidebar_labels:
                label.classes(remove='collapsed', add='expanded')
        else:
            for label in sidebar_labels:
                label.classes(remove='expanded', add='collapsed')
            await ui.run_javascript('new Promise(resolve => setTimeout(resolve, 50))')
            left_drawer.props("width=100")
            corps.text = ""
            corps.icon = "chevron_right"

    def do_logout():
        auth.logout()
        ui.run_javascript('window.location.href = "/login"')

    display_name = auth.current_display_name() or "User"
    is_admin     = auth.is_instance_admin()

    currencies: list[str] = []
    try:
        from data.finance_dashboard_data import get_currencies
        currencies = get_currencies()
    except Exception:
        pass

    # ── Header toolbar ─────────────────────────────────────────────────────────
    with ui.header().classes(replace='row items-center h-20 justify-start') as header_el:
        ui.label("").classes('pr-4')
        ui.html('<div style="width:4rem;height:4rem;background-image:url(\'/assets/images/logo.png\');background-size:contain;background-repeat:no-repeat;background-position:center;"></div>',
                sanitize=False)
        ui.label("").classes("pr-2")
        ui.label(title).classes('app-name')
        ui.space()
        # Ensure a valid currency is always selected
        _cur_sel = auth.current_selected_currency()
        if not _cur_sel or _cur_sel not in currencies:
            _cur_sel = currencies[0]
            auth.set_selected_currency(_cur_sel)
        ui.select(
            options={c: c for c in currencies},
            value=_cur_sel,
            label='Currency',
            on_change=lambda e: (
                auth.set_selected_currency(e.value),
                ui.run_javascript('window.location.reload()'),
            ),
        ).props('outlined dense').classes('w-36 mr-2')
        with ui.dropdown_button('', icon='account_circle') \
                .classes('mr-4 header-account-btn') \
                .props('flat push no-icon-animation auto-close unelevated'):
            with ui.element('div').classes('account-dropdown'):
                ui.label(display_name).classes('account-name')
                ui.label('Admin' if is_admin else 'Member').classes(
                    'text-xs px-2 py-0.5 rounded-full font-medium '
                    + ('bg-zinc-800 text-white' if is_admin else 'bg-blue-100 text-blue-700')
                ).style('width:fit-content; margin: 0 auto 8px;')
                ui.element('div').classes('account-separator')
                with ui.row().classes('account-menu-item').style('min-height:48px') \
                        .on('click', lambda: ui.navigate.to('/settings')):
                    ui.icon('settings').classes('account-icon')
                    ui.label('Settings')
                ui.element('div').classes('account-separator')
                with ui.row().classes('account-menu-item logout').style('min-height:48px') \
                        .on('click', do_logout):
                    ui.icon('logout').classes('account-icon')
                    ui.label('Logout')

    header_el.style('background-color: #F8FAFD;')

    # ── Sidebar nav ────────────────────────────────────────────────────────────
    with ui.left_drawer() \
            .classes('text-black relative') \
            .style('background-color: #F8FAFD; transition: width 0.3s ease-in-out;') \
            .props('breakpoint=400') as left_drawer:

        sidebar_labels = []
        nav_links = []

        with ui.link('', '/').classes('w-full no-underline text-black') \
                .style('border-radius: 2rem;') as dashboard_link:
            with ui.row().classes('items-center mb-2 mt-2 cursor-pointer w-full no-wrap'):
                dashboard_icon  = ui.image('/assets/images/dashboard.png').classes('ml-5 w-10 h-10 flex-shrink-0')
                dashboard_label = ui.label('Finance Dashboard').classes('text-lg sidebar-label ml-3 flex-shrink-0')
                sidebar_labels.append(dashboard_label)
        nav_links.append({'link': dashboard_link, 'icon': dashboard_icon,
                          'patterns': ['/'], 'exact': True})

        with ui.link('', '/upload').classes('w-full no-underline text-black') \
                .style('border-radius: 2rem;') as upload_link:
            with ui.row().classes('items-center mb-2 mt-2 cursor-pointer w-full no-wrap'):
                upload_icon  = ui.image('/assets/images/upload.png').classes('ml-5 w-10 h-10 flex-shrink-0')
                upload_label = ui.label('Upload data').classes('text-lg sidebar-label ml-3 flex-shrink-0')
                sidebar_labels.append(upload_label)
        nav_links.append({'link': upload_link, 'icon': upload_icon,
                          'patterns': ['/upload'], 'exact': True})



        ui.separator()
        with ui.link('', '/loans').classes('w-full no-underline text-black') \
                    .style('border-radius: 2rem;') as loans_link:
            with ui.row().classes('items-center mb-2 mt-2 cursor-pointer w-full no-wrap'):
                loans_icon  = ui.image('/assets/images/loan.png').classes('ml-5 w-10 h-10 flex-shrink-0')
                loans_label = ui.label('Loans').classes('text-lg sidebar-label ml-3 flex-shrink-0')
                sidebar_labels.append(loans_label)
            nav_links.append({'link': loans_link, 'icon': loans_icon,
                              'patterns': ['/loans'], 'exact': True})

        with ui.link('', '/loan-planning').classes('w-full no-underline text-black') \
                    .style('border-radius: 2rem;') as loan_planning_link:
            with ui.row().classes('items-center mb-2 mt-2 cursor-pointer w-full no-wrap'):
                loan_planning_icon  = ui.image('/assets/images/loan_planning.png').classes('ml-5 w-10 h-10 flex-shrink-0')
                loan_planning_label = ui.label('Loan Planning').classes('text-lg sidebar-label ml-3 flex-shrink-0')
                sidebar_labels.append(loan_planning_label)
            nav_links.append({'link': loan_planning_link, 'icon': loan_planning_icon,
                              'patterns': ['/loan-planning'], 'exact': True})

        ui.separator()
        # Admin-only nav items
        if is_admin:
            with ui.link('', '/categories').classes('w-full no-underline text-black') \
                    .style('border-radius: 2rem;') as categories_link:
                with ui.row().classes('items-center mb-2 mt-2 cursor-pointer w-full no-wrap'):
                    categories_icon  = ui.image('/assets/images/categories.png').classes('ml-5 w-10 h-10 flex-shrink-0')
                    categories_label = ui.label('Categories').classes('text-lg sidebar-label ml-3 flex-shrink-0')
                    sidebar_labels.append(categories_label)
            nav_links.append({'link': categories_link, 'icon': categories_icon,
                              'patterns': ['/categories'], 'exact': True})
        with ui.link('', '/charts').classes('w-full no-underline text-black') \
                .style('border-radius: 2rem;') as charts_link:
            with ui.row().classes('items-center mb-2 mt-2 cursor-pointer w-full no-wrap'):
                charts_icon  = ui.icon('bar_chart').classes('ml-5 w-10 h-10 flex-shrink-0').style('font-size:2.5rem;color:#18181b')
                charts_label = ui.label('Charts').classes('text-lg sidebar-label ml-3 flex-shrink-0')
                sidebar_labels.append(charts_label)
            nav_links.append({'link': charts_link, 'icon': charts_icon,
                          'patterns': ['/charts', '/chart-builder'], 'exact': False})
            
            
        ui.separator()


        with ui.link('', '/settings').classes('w-full no-underline text-black') \
                .style('border-radius: 2rem;') as settings_link:
            with ui.row().classes('items-center mb-2 mt-2 cursor-pointer w-full no-wrap'):
                settings_icon  = ui.image('/assets/images/settings.png').classes('ml-5 w-10 h-10 flex-shrink-0')
                settings_label = ui.label('Settings').classes('text-lg sidebar-label ml-3 flex-shrink-0')
                sidebar_labels.append(settings_label)
        nav_links.append({'link': settings_link, 'icon': settings_icon,
                          'patterns': ['/settings'], 'exact': True})


        corps = ui.button("Collapse", icon='chevron_left') \
            .classes('absolute bottom-4 right-4 transition-all duration-300') \
            .props('flat').on('click', lambda: toggle_sidebar())

        def apply_highlight(active_item) -> None:
            for item in nav_links:
                item['link'].classes(remove='nav-link-active')
                item['icon'].classes(remove='nav-icon-active')
            active_item['link'].classes(add='nav-link-active')
            active_item['icon'].classes(add='nav-icon-active')

        for nav_item in nav_links:
            nav_item['link'].on('click', lambda _, i=nav_item: apply_highlight(i))

        async def init_highlight() -> None:
            path = await ui.run_javascript('window.location.pathname')
            for item in nav_links:
                for pattern in item['patterns']:
                    match = (path == pattern) if item['exact'] else (path == pattern or path.startswith(pattern + '/'))
                    if match:
                        apply_highlight(item)
                        return

        ui.timer(0, init_highlight, once=True)

    # ── Sync drawer to persisted state ────────────────────────────────────────
    if app.storage.user.get('sidebar-collapsed', True):
        left_drawer.props("width=300")
        corps.text = "Collapse"
        corps.icon = "chevron_left"
        for label in sidebar_labels:
            label.classes(add='expanded')
    else:
        left_drawer.props("width=100")
        corps.text = ""
        corps.icon = "chevron_right"
        for label in sidebar_labels:
            label.classes(add='collapsed')

    with ui.column().classes('w-full items-stretch'):
        yield