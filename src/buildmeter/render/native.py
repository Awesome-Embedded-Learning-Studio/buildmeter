"""buildmeter.render.native — stdlib-only ANSI renderer.

Two-line display updated in place via cursor-up: [bar + count + ETA + log path]
over [latest raw make line]. Works in any terminal; the rich backend
(buildmeter/render/rich.py) is the alternative when rich is installed.

Exposes both the stateless render_bar/render_frame functions (tests, external
use) and a NativeRenderer class — the stateful sink cli.py drives via
begin(total) → tick(per line) → end → error_dump."""
import os
import re
import sys

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
        spinner = _SPINNER[int(elapsed * 20) % len(_SPINNER)]
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


class NativeRenderer:
    """Stateful ANSI sink — the original two-line cursor-up display. cli drives
    it via begin(total) → tick(per line) → end → error_dump. Wraps the stateless
    render_frame/render_bar above; behavior is identical to the pre-backend cli."""

    def __init__(self, is_tty, log_path=''):
        self.is_tty = is_tty
        self.log = log_path
        self.last_render = 0.0
        self.lines_drawn = 0
        self.last_snap_done = 0
        self.last_snap_time = 0.0
        self.snap_event_step = 0
        self.snap_time_step = 0.0

    def begin(self, total):
        if total:
            self.snap_event_step = max(1, total // 20)
            self.snap_time_step = 0.0
        else:
            self.snap_event_step = 0
            self.snap_time_step = 3.0
        if self.is_tty:
            sys.stderr.write(ESC + '[?25l')  # hide cursor
            sys.stderr.flush()

    def tick(self, parser, total, elapsed, last_raw):
        if self.is_tty:
            # Refresh ~10fps so the live raw line keeps flowing.
            if (elapsed - self.last_render) >= 0.1:
                self.last_render = elapsed
                l1, l2 = render_frame(parser, total, elapsed, self.log, last_raw)
                if self.lines_drawn == 0:
                    sys.stderr.write(l1 + '\n' + l2)
                    self.lines_drawn = 2
                else:
                    sys.stderr.write(ESC + '[1A\r' + ESC + '[K' + l1 + '\n' + ESC + '[K' + l2)
                sys.stderr.flush()
        else:
            fire = (self.snap_event_step and (parser.done - self.last_snap_done) >= self.snap_event_step) or \
                   (self.snap_time_step and (elapsed - self.last_snap_time) >= self.snap_time_step)
            if fire:
                if self.snap_event_step:
                    self.last_snap_done = parser.done
                self.last_snap_time = elapsed
                sys.stderr.write(render_bar(parser, total, elapsed, self.log) + '\n')

    def end(self, parser, total, elapsed, last_raw):
        if self.is_tty:
            l1, l2 = render_frame(parser, total, elapsed, self.log, last_raw)
            if self.lines_drawn == 0:
                sys.stderr.write(l1 + '\n' + l2 + '\n')
            else:
                sys.stderr.write(ESC + '[1A\r' + ESC + '[K' + l1 + '\n' + ESC + '[K' + l2 + '\n')
            sys.stderr.write(ESC + '[?25h')  # show cursor
            sys.stderr.flush()
        else:
            sys.stderr.write(render_bar(parser, total, elapsed, self.log) + '\n')

    def error_dump(self, recent):
        sys.stderr.write('\n' + '=' * 60 + '\n')
        sys.stderr.write('errors detected during build — last lines:\n')
        for r in recent:
            sys.stderr.write('  ' + r + '\n')
        sys.stderr.write('=' * 60 + '\n')
        sys.stderr.flush()
