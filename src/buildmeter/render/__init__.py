"""Render backend selection — auto-detect rich, fall back to native.

get_renderer(is_tty, log_path) returns a RichRenderer when `import rich` succeeds
(opt-in via `pip install buildmeter[rich]`), else NativeRenderer. Both implement
the same sink interface (begin/tick/end/error_dump) consumed by cli.py — so the
backend choice is transparent to the render loop.

The stateless render_bar/render_frame + ESC are also exported (tests, external
use); they're always the native impls."""
try:
    import rich  # noqa: F401  — probe; RichRenderer imports the actual names lazily
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False


def get_renderer(is_tty, log_path=''):
    """Pick the render backend: rich if installed, else native ANSI."""
    if _RICH_AVAILABLE:
        from buildmeter.render.rich import RichRenderer
        return RichRenderer(is_tty, log_path)
    from buildmeter.render.native import NativeRenderer
    return NativeRenderer(is_tty, log_path)


from buildmeter.render.native import ESC, render_bar, render_frame

__all__ = ["ESC", "render_bar", "render_frame", "get_renderer", "_RICH_AVAILABLE"]
