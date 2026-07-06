# buildmeter

Live progress meters for **GNU make-based builds** — Linux kernel (Kbuild), U-Boot, Buildroot. Solves the "72-minute build, no idea if it's 10% or 90%" problem.

Renders an in-place progress bar on stderr (stdout stays clean for piping/logging):

```
████████████████████████████ ⠋ linking vmlinux (slowest step — minutes, normal) · 5176 units · 4:12 elapsed
  LD .tmp_vmlinux1
```

## Install

Zero-dependency (stdlib ANSI renderer):

```bash
pip install buildmeter
```

Fancier rich dashboard (planned):

```bash
pip install buildmeter[rich]
```

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

## How it works

- **Numerator (done)**: parse make stdout — `CC/LD/AR/AS` lines (kernel/uboot kbuild) or `>>> pkg` lines (buildroot).
- **Denominator (total)**: GNU make's two-phase model means there's no static way to know the compile count up front. The only first-use option is a `make -n -k` dry-run pre-scan (~15s). Measured: ~6% short on a clean kernel tree (the dry-run halts at the vmlinux link — see `docs/DESIGN.md`). The bar caps at 100% and switches to a `finalizing post-link` phase with a heartbeat (spinner + phase label + elapsed) so the minutes-long silent vmlinux link doesn't look like a hang.

Full design — alternatives considered, the vmlinux-link root cause, the finalizing-heartbeat UX fix: **`docs/DESIGN.md`**.

## Project layout

```
src/buildmeter/
  engine.py           # parsers (kernel/buildroot) + denominator discovery (renderer-agnostic)
  cli.py              # argparse + render loop (the entry)
  render/
    native.py         # stdlib ANSI renderer (default, zero-dep)
    rich.py           # rich backend (planned, opt-in via buildmeter[rich])
tests/test_engine.py
docs/DESIGN.md
```

## License

MIT.
