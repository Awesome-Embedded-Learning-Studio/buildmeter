"""buildmeter.render.rich — Rich backend (NOT YET IMPLEMENTED).

Planned: rich.Progress / Live / Panel for a fancier dashboard, auto-selected by
buildmeter/render/__init__.py when `import rich` succeeds (opt-in via
`pip install buildmeter[rich]`). The detection shim is already in place; this
module's real implementation is the remaining work.

Until then, render/__init__.py always selects native regardless of whether rich
is installed, so these stubs are never reached. They're kept import-safe (no
top-level raise) so the future `from buildmeter.render.rich import ...` doesn't
crash at import time before the backend exists."""


def render_bar(parser, total, elapsed, log_path='', bar_width=28, last_raw=''):
    raise NotImplementedError(
        "buildmeter rich backend not yet implemented; the native ANSI backend "
        "is in use. (planned: pip install buildmeter[rich])"
    )


def render_frame(parser, total, elapsed, log_path, last_raw):
    raise NotImplementedError(
        "buildmeter rich backend not yet implemented; the native ANSI backend "
        "is in use. (planned: pip install buildmeter[rich])"
    )
