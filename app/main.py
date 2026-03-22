"""Application entry point — page routing, shared layout decorator and run targets."""

import json
from functools import wraps
from pathlib import Path

from nicegui import app, ui

from db_migration import run_migrations

import header
import pages.finance_dashboard_content
import pages.upload_content
import pages.categories_content
import pages.settings_content
import pages.login_content
import pages.loans_content
import pages.loan_planning_content
import pages.charts_content
import pages.chart_builder_content
import pages.family_content
import pages.users_content
import services.auth as auth

from services.helpers import env

# ── Config ─────────────────────────────────────────────────────────────────────
with open('config.json') as f:
    config = json.load(f)

appName    = config["appName"]
appVersion = config["appVersion"]
appPort    = config["appPort"]
appEnv     = env("APP_ENV", "dev")

app.add_static_files('/assets', 'assets')


# ── Base layout decorator ──────────────────────────────────────────────────────
def with_base_layout(route_handler):
    @wraps(route_handler)
    def wrapper(*args, **kwargs):
        # Auth guard — redirect to login if not authenticated or session is stale
        if not auth.is_authenticated():
            ui.navigate.to("/login")
            return
        fresh_user = auth.get_user_by_id(auth.current_user_id())
        if not fresh_user:
            auth.logout()
            ui.navigate.to("/login")
            return
        # Refresh session if family data is missing (stale pre-family session)
        if auth.current_family_id() is None and fresh_user.family_id is not None:
            auth.login(fresh_user)

        ui.colors(primary='#18181b', secondary='#f4f4f5', positive='#4caf50',
                  negative='#ef4444', warning='#f59e0b', info='#3b82f6', accent='#e4e4e7')
        ui.add_head_html(
            "<style>" + open(Path(__file__).parent / "assets" / "css" / "global-css.css").read() + "</style>",
            shared=True
        )
        ui.add_head_html('<link rel="stylesheet" href="/assets/css/icons.css">', shared=True)
        ui.add_head_html('<link rel="preload" href="/assets/images/logo.png" as="image">')

        if 'sidebar-collapsed' not in app.storage.user:
            app.storage.user['sidebar-collapsed'] = True

        with header.frame(title=appName, version=appVersion, get_logo_func=None):
            return route_handler(*args, **kwargs)
    return wrapper


# ── Login page (no layout wrapper) ────────────────────────────────────────────
@ui.page('/login')
def login_page():
    ui.colors(primary='#18181b', secondary='#f4f4f5')
    ui.add_head_html(
        "<style>" + open(Path(__file__).parent / "assets" / "css" / "global-css.css").read() + "</style>",
        shared=True
    )
    pages.login_content.content()


# ── Main app page ──────────────────────────────────────────────────────────────
@ui.page('/')
@with_base_layout
def root():
    ui.sub_pages({
        '/':               index,
        '/upload':         upload,
        '/categories':     categories,
        '/settings':       settings,
        '/loans':          loans,
        '/loan-planning':  loan_planning,
        '/charts':         charts_gallery,
        '/chart-builder':  chart_builder,
        '/family':         family,
        '/users':          users,
    })


# ── Sub-page handlers ──────────────────────────────────────────────────────────
def index():
    pages.finance_dashboard_content.content()

def upload():
    pages.upload_content.content()

def categories():
    # Admin only
    if not auth.is_instance_admin():
        ui.navigate.to("/")
        return
    pages.categories_content.content()

def settings():
    pages.settings_content.content()

def loans():
    pages.loans_content.content()

def loan_planning():
    pages.loan_planning_content.content()

def charts_gallery():
    pages.charts_content.content()

def chart_builder():
    pages.chart_builder_content.content()

def family():
    if not (auth.is_family_head() or auth.is_instance_admin()):
        ui.navigate.to("/")
        return
    pages.family_content.content()

def users():
    if not auth.is_instance_admin():
        ui.navigate.to("/")
        return
    pages.users_content.content()


# ── Entry point ────────────────────────────────────────────────────────────────

fav_icon = "assets/images/logo.png"

storage_secret = env("STORAGE_SECRET", "dev-secret-change-me")

if appEnv == "prod":
    app.on_startup(run_migrations)
    ui.run(root, host='0.0.0.0', storage_secret=storage_secret, title=appName, port=appPort, favicon=fav_icon, reconnect_timeout=20, reload=False)
else:
    ui.run(root, storage_secret=storage_secret, title=appName, port=appPort, favicon=fav_icon, reconnect_timeout=20)