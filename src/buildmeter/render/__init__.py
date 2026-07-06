"""Render backend selection.

Default: the stdlib ANSI `native` backend (zero dependencies). The `rich`
backend (rich.Progress/Live/Panel) is a planned opt-in via
`pip install buildmeter[rich]`; until it's implemented, native is always used.

Both backends expose the same surface — render_bar(parser, total, elapsed,
log_path, bar_width, last_raw) and render_frame(parser, total, elapsed,
log_path, last_raw), plus the ESC constant — consumed by cli.py's render loop.
"""
from buildmeter.render.native import ESC, render_bar, render_frame

__all__ = ["ESC", "render_bar", "render_frame"]
