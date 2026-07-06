# buildmeter

Live progress meters for **GNU make-based builds** — Linux kernel (Kbuild), U-Boot, Buildroot. Solves the "72-minute build, no idea if it's 10% or 90%" problem.

Renders an in-place progress bar on stderr (stdout stays clean for piping/logging). Two backends, **auto-selected** at import time:

- **rich** (if installed) — a bordered, colored `Panel` updated via `rich.live.Live`.
- **native** (always) — a stdlib-only two-line ANSI bar, zero dependencies.

```
╭─ buildmeter ─────────────────────────────────────────────╮
│ ████████████████████░░░░░░  3584/4875 units  (74%)        │
│ ⏱ ETA 1:23   14.2/s   ⏲ 4:12                              │
│ → CC drivers/net/wireless/realtek/rtl8733bu/core.c        │
│ log: /tmp/forge-kernel-184682.log                         │
╰───────────────────────────────────────────────────────────╯
```

## Install

Zero-dependency (native ANSI):

```bash
pip install buildmeter
```

Rich dashboard (bordered Panel + color):

```bash
pip install buildmeter[rich]
```

`import rich` is probed at runtime — present → rich backend; absent → native. No config, no crash on missing rich.

## Use

Wrap a live build (pipe through):

```bash
make -C linux     2>&1 | python -m buildmeter kernel
make -C buildroot 2>&1 | python -m buildmeter buildroot
```

Replay a captured log (regression test / demo):

```bash
python -m buildmeter kernel build-kernel.log
```

Pre-scan a denominator (`make -n -k` dry-run, then real build with `%`):

```bash
TOTAL=$(make -C linux -j"$(nproc)" -k -n zImage 2>/dev/null | python -m buildmeter kernel --count-only)
make -C linux -j"$(nproc)" zImage 2>&1 | python -m buildmeter kernel --total "$TOTAL"
```

A script-style entry works too (`python3 path/to/buildmeter/cli.py …`) — used by wrappers like rk-forge's `lib/progress.sh`, which sets `$FORGE_PROGRESS_PY` to that path.

CLI contract: `buildmeter <kernel|buildroot> [logfile] [--total N] [--count-only] [--log PATH] [--ignore-errors REGEX] [--speed MS]`.

## How it works

**Numerator (done)** — parse make stdout: `CC/LD/AR/AS` lines (kernel/uboot kbuild) or `>>> pkg` lines (buildroot packages).

**Denominator (total)** — GNU make's two-phase model means there's no static way to know the compile count up front. The only first-use option is a `make -n -k` dry-run pre-scan (~15s). Measured: **~6% short** on a clean kernel tree — the dry-run halts at the vmlinux link (`vmlinux.a` is a recipe product dry-run never generates), so the post-link CCs (zImage decompressor, vdso, …) are never enumerated. buildmeter caps the bar at 100% and switches to a `finalizing post-link` phase instead of misreading as >100%. Full analysis (alternatives, root cause): **`docs/DESIGN.md`**.

**buildroot two-layer** — package-level (`X/total pkgs`) is the only stable cross-pkg signal, but one package can configure/build for minutes while the bar sits frozen at N/(N+1). buildmeter also counts in-package make steps (`CC/LD/LIBTOOL/AR/AS`) and shows `[busybox: Building · 142 steps]` — steps ticking reads as progress. (Recursive per-pkg dry-run was rejected: 15s+/pkg × dozens = minutes of pre-scan, and buildroot pkgs are infrastructure wraps, not bare make. meson/cmake pkgs emit different formats and aren't matched yet.)

**Silence-misread UX** — long silent phases don't read as hangs:
- **finalizing post-link** — braille spinner + phase label + elapsed (the vmlinux link is minutes of silence).
- **count-only pre-scan** — spinner + elapsed while the dry-run enumerates (15-30s).
- **tolerated noise** — `--ignore-errors` lines (e.g. uboot binman `Error 103`) are kept off the live raw line and out of the error dump.

## Project layout

```
src/buildmeter/
  engine.py           # parsers (kernel kbuild / buildroot pkg) + denominator discovery
  cli.py              # argparse + render loop (the entry, both python -m and script-style)
  render/
    __init__.py       # get_renderer(): try import rich → RichRenderer, else NativeRenderer
    native.py         # NativeRenderer — stdlib ANSI (default, zero-dep)
    rich.py           # RichRenderer — rich.live.Live Panel (opt-in via buildmeter[rich])
tests/test_engine.py  # parser regression (live/dry-run unwrap/buildroot rotation)
docs/DESIGN.md        # full design: 9 alternatives, vmlinux-link root cause, UX fixes
```

## License

MIT.
