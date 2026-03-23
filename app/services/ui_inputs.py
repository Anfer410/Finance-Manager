"""
Labeled input/select helpers — visible labels baked in above the field.

Usage:
    from services.ui_inputs import labeled_input, labeled_select

    # Form style (default) — medium label, gap-1
    name_in = labeled_input('Bank name', placeholder='e.g. Citi')
    name_in = labeled_input('Password', password=True, password_toggle_button=True)
    name_in = labeled_input('Filename value',
                            hint='Matched against the uploaded filename.',
                            value=rule.match_value,
                            placeholder='e.g. transaction_download')

    # Compact style — small muted label, gap-0.5 (search bars, filter rows)
    search_in = labeled_input('Search', placeholder='any column...', compact=True)

    # Select — label above (default)
    type_sel = labeled_select('Chart Type', _CHART_TYPE_OPTIONS, value='bar', on_change=...)
    type_sel = labeled_select('Match type', MATCH_TYPE_OPTIONS, compact=True, classes='w-48')

    # Select — label to the left (inline)
    year_sel = labeled_select('Year', year_opts, inline=True, classes='w-28', value=2024)

The returned value is the ui element — chain .on(), .bind_value(), .props(), etc. on it.
Width is controlled by the caller's wrapping container, or via classes= on the element itself.
"""

from nicegui import ui


def labeled_input(
    label: str,
    *,
    hint: str | None = None,
    compact: bool = False,
    classes: str = 'w-full',
    **kwargs,
) -> ui.input:
    """
    Render a ui.label above a ui.input and return the input element.

    Args:
        label:   Visible label text rendered above the field.
        hint:    Optional smaller description line below the label (form style only).
        compact: Use compact style (text-xs muted label, gap-0.5) for search/filter rows.
                 Default False uses form style (text-sm medium label, gap-1).
        classes: CSS classes applied to the input element. Defaults to 'w-full'.
        **kwargs: Forwarded to ui.input (placeholder, value, password, on_change, etc.).
    """
    if compact:
        with ui.column().classes('gap-0.5'):
            ui.label(label).classes('text-xs text-zinc-500')
            inp = ui.input(**kwargs).props('outlined dense').classes(classes)
    else:
        with ui.column().classes('gap-1'):
            ui.label(label).classes('text-sm font-medium text-zinc-700')
            if hint:
                ui.label(hint).classes('text-xs text-zinc-400')
            inp = ui.input(**kwargs).props('outlined dense').classes(classes)
    return inp


def labeled_select(
    label: str,
    options,
    *,
    hint: str | None = None,
    compact: bool = False,
    inline: bool = False,
    classes: str = 'w-full',
    **kwargs,
) -> ui.select:
    """
    Render a ui.label alongside a ui.select and return the select element.

    Args:
        label:   Visible label text.
        options: Passed as the first positional arg to ui.select.
        hint:    Optional smaller description below the label (above layout, form style only).
        compact: Use compact style (text-xs muted label) for filter rows.
                 Default False uses form style (text-sm medium label).
        inline:  Place the label to the left of the select instead of above it.
                 Overrides hint (not applicable in inline mode).
        classes: CSS classes applied to the select element. Defaults to 'w-full'.
        **kwargs: Forwarded to ui.select (value, on_change, multiple, clearable, etc.).
    """
    if inline:
        label_cls = 'text-xs text-zinc-500 whitespace-nowrap' if compact else 'text-sm font-medium text-zinc-700 whitespace-nowrap'
        with ui.row().classes('items-center gap-2'):
            ui.label(label).classes(label_cls)
            sel = ui.select(options, **kwargs).props('outlined dense').classes(classes)
    elif compact:
        with ui.column().classes('gap-0.5'):
            ui.label(label).classes('text-xs text-zinc-500')
            sel = ui.select(options, **kwargs).props('outlined dense').classes(classes)
    else:
        with ui.column().classes('gap-1'):
            ui.label(label).classes('text-sm font-medium text-zinc-700')
            if hint:
                ui.label(hint).classes('text-xs text-zinc-400')
            sel = ui.select(options, **kwargs).props('outlined dense').classes(classes)
    return sel
