"""buildmeter.render.rich — Rich backend (NOT YET IMPLEMENTED).

Planned: rich.Progress / Live / Panel for a fancier dashboard (rounded panels,
colors, spinners, multi-stage layout), opt-in via `pip install buildmeter[rich]`.

For now the native ANSI backend (buildmeter/render/native.py) is used
unconditionally — see buildmeter/render/__init__.py. This module is a placeholder
so the selection shim has a target when the rich implementation lands."""

raise NotImplementedError(
    "buildmeter rich backend not yet implemented; the native ANSI backend is in "
    "use. (planned: pip install buildmeter[rich])"
)
