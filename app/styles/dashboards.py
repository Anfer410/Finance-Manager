GRID    = {'left': '3%', 'right': '3%', 'bottom': '3%', 'top': '14%', 'containLabel': True}
TT_AXIS = {'trigger': 'axis', 'backgroundColor': '#fff', 'borderColor': '#e4e4e7',
            'textStyle': {'color': '#09090b', 'fontSize': 12}}
LEGEND  = {'textStyle': {'color': '#71717a', 'fontSize': 12}}

_LEGEND_POSITIONS = {
    'top':    {'top': 'top',  'left':  'center', 'orient': 'horizontal'},
    'bottom': {'bottom': 0,   'left':  'center', 'orient': 'horizontal'},
    'left':   {'left': 0,     'top':   'middle',  'orient': 'vertical'},
    'right':  {'right': 0,    'top':   'middle',  'orient': 'vertical'},
}


def legend_pos(position: str = 'top', **extra) -> dict:
    """Merge LEGEND style with position keys and optional extra overrides."""
    pos = _LEGEND_POSITIONS.get(position, _LEGEND_POSITIONS['top'])
    return {**LEGEND, **pos, **extra}


def grid_for_legend(position: str = 'top') -> dict:
    """Return GRID with an expanded margin on whichever side the legend sits."""
    base = dict(GRID)
    if position == 'bottom':
        base['bottom'] = '18%'
    elif position == 'left':
        base['left'] = '22%'
    elif position == 'right':
        base['right'] = '22%'
    return base

C_SPEND   = '#f87171'
C_INCOME  = '#4ade80'
C_PAYROLL = '#60a5fa'
C_NET_POS = '#4ade80'
C_NET_NEG = '#f87171'
BANK_COLORS = ['#60a5fa', '#fbbf24', '#a78bfa', '#fb923c', '#34d399', '#e879f9', '#38bdf8']

COST_TYPES    = ["variable", "fixed"]
FIXED_COLOR   = "#60a5fa"
VAR_COLOR     = "#fb923c"