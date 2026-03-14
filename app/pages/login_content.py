"""
pages/login_content.py

Standalone login page — no sidebar, no header.
Registered in main.py as @ui.page('/login').
"""

from nicegui import ui
import services.auth as auth


def content() -> None:
    # Redirect if already logged in
    if auth.is_authenticated():
        ui.navigate.to("/")
        return

    ui.add_head_html("""
        <style>
            body { background: #f8fafd; }
            .login-card {
                width: 100%;
                max-width: 400px;
                background: #fff;
                border-radius: 1rem;
                box-shadow: 0 4px 24px rgba(0,0,0,0.08);
                padding: 2.5rem 2rem;
            }
        </style>
    """)

    error_state = {"msg": ""}

    with ui.column().classes("items-center justify-center w-full min-h-screen gap-0"):

        # Logo + app name
        with ui.column().classes("items-center mb-6"):
            ui.html('<div style="width:5rem;height:5rem;background-image:url(\'/assets/images/logo.png\');background-size:contain;background-repeat:no-repeat;background-position:center;"></div>')
            ui.label("Finance Manager").classes("text-2xl font-bold text-zinc-800 mt-2")
            ui.label("Sign in to your account").classes("text-sm text-zinc-400 mt-1")

        with ui.element("div").classes("login-card"):

            ui.label("Username")
            username_input = ui.input(
                label="Username",
                placeholder="Enter your username",
            ).props("outlined dense").classes("w-full mb-3")
            ui.label("Password")
            password_input = ui.input(
                label="Password",
                placeholder="Enter your password",
                password=True,
                password_toggle_button=True,
            ).props("outlined dense").classes("w-full mb-4")

            @ui.refreshable
            def error_label() -> None:
                if error_state["msg"]:
                    ui.label(error_state["msg"]).classes(
                        "text-sm text-red-500 mb-3 text-center w-full"
                    )

            error_label()

            def do_login() -> None:
                username = username_input.value or ""
                password = password_input.value or ""
                if not username or not password:
                    error_state["msg"] = "Please enter username and password."
                    error_label.refresh()
                    return
                ok, msg = auth.attempt_login(username, password)
                if ok:
                    ui.navigate.to("/")
                else:
                    error_state["msg"] = msg
                    password_input.set_value("")
                    error_label.refresh()

            ui.button("Sign In", on_click=do_login) \
                .props("unelevated no-caps").classes(
                    "w-full bg-zinc-800 text-white font-semibold py-2 rounded-lg"
                )

            # Allow Enter key on password field to submit
            password_input.on("keydown.enter", do_login)
            username_input.on("keydown.enter", lambda: password_input.run_method("focus"))