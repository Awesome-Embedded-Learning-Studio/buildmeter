"""buildmeter.render.native — stdlib-only ANSI renderer.

Two-line display updated in place via cursor-up: [bar + count + ETA + log path]
over [latest raw make line]. Works in any terminal; the rich backend
(buildmeter/render/rich.py) is the planned fancier alternative."""
import os
import re

ESC = '\x1b'


def bar(pct, width):
    filled = int(pct / 100 * width)
    return '█' * filled + '░' * (width - filled)


def fmt_dur(s):
    s = int(s)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def term_width(fallback=120):
    try:
        return os.get_terminal_size(2).columns
    except (OSError, ValueError):
        return fallback


# Braille spinner — one char that changes each frame so the redraw is visibly
# alive during long silent phases. The vmlinux link (LD .tmp_vmlinux*) emits no
# CC lines for minutes; without a heartbeat the frozen bar looks like a hang
# and users Ctrl+C a build that's actually fine.
_SPINNER = '⠋⠙⠹⠸⠼⠴⠦⠧'

# Post-link phase labels: tell the user WHAT is slow in the tail the dry-run
# pre-scan couldn't enumerate. The vmlinux link is the big one (minutes, silent).
_FINAL_PHASES = [
    (re.compile(r'LD\s+\S*vmlinux'), 'linking vmlinux (slowest step — minutes, normal)'),
    (re.compile(r'SORTTAB|SYSMAP|kallsyms'), 'finalizing vmlinux symbols'),
    (re.compile(r'CC\s+\S*boot/compressed'), 'building zImage decompressor'),
    (re.compile(r'OBJCOPY|GZIP\b|XZ\b|LZ4\b'), 'packing zImage'),
]


def _finalizing_phase(raw):
    """Map the latest raw make line to a post-link phase label (generic
    fallback if nothing matches) — gives the user a reason for the silence
    instead of a frozen bar."""
    for rx, label in _FINAL_PHASES:
        if rx.search(raw or ''):
            return label
    return 'finalizing post-link'


def render_bar(parser, total, elapsed, log_path='', bar_width=28, last_raw=''):
    """Line 1 of the display: the progress bar + count + ETA + the tee'd log path.

    A dry-run --total can UNDERCOUNT a clean build: `make -n` halts at the
    vmlinux link because vmlinux.a is a recipe product dry-run never generates,
    so the post-link CCs it would drive (zImage decompressor, vdso,
    .vmlinux.export, asm-offsets dependents) are never enumerated — ~6% short
    on a clean multi_v7 kernel tree (measured: dry-run 4875 vs real 5196).

    When `done` runs PAST such a total, the post-link tail is dominated by the
    vmlinux link — a minutes-long silence with NO CC lines, which looks exactly
    like a hang. So instead of a misleading 'done/total (100%)' with a collapsing
    ETA, show a spinner + elapsed + a phase label: the spinner keeps the redraw
    visibly alive, the phase says WHY it's slow. (done == total is the normal
    accurate-100% finish, not this case.)"""
    done = parser.done
    if total and done > total:
        spinner = _SPINNER[int(elapsed * 8) % len(_SPINNER)]
        head = (f'{bar(100.0, bar_width)} {spinner} {_finalizing_phase(last_raw)} '
                f'· {done} {parser.unit} · {fmt_dur(elapsed)} elapsed')
    elif total:
        pct = min(done / total * 100, 100.0)
        rate = done / elapsed if elapsed > 0.3 else 0.0
        rem = max(total - done, 0)
        eta = (rem / rate) if rate else None
        eta_s = fmt_dur(eta) if eta else '--'
        head = f'{bar(pct, bar_width)} {done}/{total} {parser.unit} ({pct:.0f}%) {rate:.1f}/s ETA {eta_s}'
    else:
        rate = done / elapsed if elapsed > 0.3 else 0.0
        head = f'{done} {parser.unit} done  {rate:.1f}/s  elapsed {fmt_dur(elapsed)}'
    if log_path:
        head += f'  log: {log_path}'
    return head


def render_frame(parser, total, elapsed, log_path, last_raw):
    """Two-line display: [bar + ETA + log path] over [latest raw make line].
    The live raw line gives the 'it's alive' flow the bare bar lacked (so the
    bar doesn't feel frozen / ninja-quiet during a long compile); the full
    output is tee'd to log_path by the caller for later reference."""
    cols = term_width()
    l1 = render_bar(parser, total, elapsed, log_path, last_raw=last_raw)
    if len(l1) > cols:
        l1 = l1[:cols - 1] + '…'
    l2 = ('  ' + last_raw) if last_raw else ''
    if len(l2) > cols:
        l2 = l2[:cols - 1] + '…'
    return l1, l2
