"""Render backend selection — auto-detect rich, fall back to native.

Probes `import rich` at import time. Once a real rich backend lands in
buildmeter/render/rich.py, it'll be selected automatically for anyone who has
rich installed (`pip install buildmeter[rich]`), with the zero-dependency native
ANSI backend as the fallback. The detection is wired NOW so the switch is a
one-liner later (select on _RICH_AVAILABLE) — see the TODO below.

Until that backend is implemented, native is used regardless of whether rich is
installed. Both backends expose the same surface — render_bar(parser, total,
elapsed, log_path, bar_width, last_raw) and render_frame(parser, total, elapsed,
log_path, last_raw), plus the ESC constant — consumed by cli.py's render loop.
"""
try:
    import rich  # noqa: F401  — probe only, not yet consumed
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False

# TODO: once render/rich.py implements a real backend, flip this to select on
# _RICH_AVAILABLE:
#     if _RICH_AVAILABLE:
#         from buildmeter.render.rich import ESC, render_bar, render_frame
#     else:
#         from buildmeter.render.native import ESC, render_bar, render_frame
from buildmeter.render.native import ESC, render_bar, render_frame

__all__ = ["ESC", "render_bar", "render_frame", "_RICH_AVAILABLE"]
