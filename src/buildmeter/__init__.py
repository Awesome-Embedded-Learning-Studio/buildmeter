"""buildmeter — live progress meters for GNU make-based builds.

Parses kernel kbuild (CC/LD/AR/AS) / U-Boot (kbuild) / buildroot (`>>> pkg`)
build output and renders an in-place progress bar. Native stdlib ANSI renderer
by default; a rich backend is planned as an opt-in (`pip install buildmeter[rich]`).

See docs/DESIGN.md for the make-based build progress design (why a dry-run
pre-scan is the only first-use denominator, the ~6% vmlinux-link undercount,
and the finalizing-heartbeat UX fix)."""
__version__ = "0.1.0"
