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
    if getattr(parser, 'finalizing', False):
        # buildroot tail: image generation (rsync/mkfs/fakeroot) — every package
        # is built but output has no >>> pkg lines for minutes. Show a full bar +
        # spinner + phase so it doesn't read as a hang (same lesson as the kernel
        # post-link tail). Only BuildrootParser sets .finalizing.
        spinner = _SPINNER[int(elapsed * 20) % len(_SPINNER)]
        head = (f'{bar(100.0, bar_width)} {spinner} {parser.finalizing_phase} '
                f'· {done} {parser.unit} done · {fmt_dur(elapsed)}')
    elif total and done > total:
        spinner = _SPINNER[int(elapsed * 20) % len(_SPINNER)]
        head = (f'{bar(100.0, bar_width)} {spinner} {_finalizing_phase(last_raw)} '
                f'· {done} {parser.unit} · {fmt_dur(elapsed)} elapsed')
    elif total and parser.unit == 'units':
        # kernel: dry-run total is meaningful → real % (done/total).
        pct = min(done / total * 100, 100.0)
        rate = done / elapsed if elapsed > 0.3 else 0.0
        rem = max(total - done, 0)
        eta = (rem / rate) if rate else None
        eta_s = fmt_dur(eta) if eta else '--'
        head = f'{bar(pct, bar_width)} {done}/{total} {parser.unit} ({pct:.0f}%) {rate:.1f}/s ETA {eta_s}'
    else:
        # indeterminate: buildroot always (its total is info-only, below), or
        # kernel when no --total was pre-scanned.
        rate = done / elapsed if elapsed > 0.3 else 0.0
        head = f'{done} {parser.unit} done  {rate:.1f}/s  elapsed {fmt_dur(elapsed)}'
        if total:
            # buildroot: show-targets total as an info label, NOT a %. Incremental
            # builds skip already-built pkgs (no >>> line) so `done` undercounts —
            # done/total would mislead. The label gives context without lying.
            head += f'  All Packages: {total}'
    # buildroot's package-level % is coarse (one pkg can configure for minutes
    # while the bar sits at N/(N+1) pkgs), so append the current pkg:stage — and,
    # for the active pkg, a finer in-pkg signal: ninja done/total (%) for
    # meson/cmake-ninja pkgs, else the make step count. Applied in ALL bar modes
    # (total / indeterminate / finalizing) so a buildroot run with no global total
    # still shows in-pkg progress. Kernel (unit=units) skips it.
    if parser.unit == 'pkgs' and parser.current_label and not getattr(parser, 'finalizing', False):
        label = parser.current_label
        if getattr(parser, 'ninja_total', 0):  # meson ninja: [done/total]
            ipct = parser.ninja_done * 100 // parser.ninja_total
            label += f' · ninja {parser.ninja_done}/{parser.ninja_total} ({ipct}%)'
        elif getattr(parser, 'ninja_pct', -1) >= 0:  # cmake ninja: [NN%]
            label += f' · ninja {parser.ninja_pct}%'
        elif getattr(parser, 'inner_count', 0):  # autotools/make pkg: step count
            label += f' · {parser.inner_count} steps'
        head += f'  [{label}]'
    if log_path:
        head += f'  log: {log_path}'
    return head


def render_frame(parser, total, elapsed, log_path, raw_lines, tail=5):
    """bar (l1) over up to `tail` recent raw build lines (sliding window). The
    body is padded with blanks to a FIXED `tail` slot count, so the cursor-up
    in-place redraw leaves no residue when the window is still filling (fewer
    than `tail` lines seen so far) — lines_drawn stays constant across frames.
    The live raw lines give the 'it's alive' flow the bare bar lacked; the full
    output is tee'd to log_path by the caller for later reference."""
    cols = term_width()
    last_raw = raw_lines[-1] if raw_lines else ''
    l1 = render_bar(parser, total, elapsed, log_path, last_raw=last_raw)
    if len(l1) > cols:
        l1 = l1[:cols - 1] + '…'
    body = []
    for line in list(raw_lines)[-tail:]:
        s = ('  ' + line) if line else ''
        if len(s) > cols:
            s = s[:cols - 1] + '…'
        body.append(s)
    body += [''] * (tail - len(body))  # fixed slot count → no redraw residue
    return l1, body


class NativeRenderer:
    """Stateful ANSI sink — the original two-line cursor-up display. cli drives
    it via begin(total) → tick(per line) → end → error_dump. Wraps the stateless
    render_frame/render_bar above; behavior is identical to the pre-backend cli."""

    def __init__(self, is_tty, log_path='', tail=5):
        self.is_tty = is_tty
        self.log = log_path
        self.tail = max(1, tail)
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

    def tick(self, parser, total, elapsed, recent_lines):
        if self.is_tty:
            # Refresh ~10fps so the live raw lines keep flowing.
            if (elapsed - self.last_render) >= 0.1:
                self.last_render = elapsed
                l1, body = render_frame(parser, total, elapsed, self.log, recent_lines, self.tail)
                if self.lines_drawn == 0:
                    sys.stderr.write(l1)
                    for b in body:
                        sys.stderr.write('\n' + b)
                    self.lines_drawn = 1 + len(body)
                else:
                    # cursor up to the bar line, redraw bar + body (each cleared).
                    # lines_drawn is fixed (tail slots padded) so no residue as
                    # the window fills from 1 → tail lines.
                    sys.stderr.write(ESC + f'[{self.lines_drawn}A\r' + ESC + '[K' + l1)
                    for b in body:
                        sys.stderr.write('\n' + ESC + '[K' + b)
                sys.stderr.flush()
        else:
            fire = (self.snap_event_step and (parser.done - self.last_snap_done) >= self.snap_event_step) or \
                   (self.snap_time_step and (elapsed - self.last_snap_time) >= self.snap_time_step)
            if fire:
                if self.snap_event_step:
                    self.last_snap_done = parser.done
                self.last_snap_time = elapsed
                last_raw = recent_lines[-1] if recent_lines else ''
                sys.stderr.write(render_bar(parser, total, elapsed, self.log, last_raw=last_raw) + '\n')

    def end(self, parser, total, elapsed, recent_lines):
        if self.is_tty:
            l1, body = render_frame(parser, total, elapsed, self.log, recent_lines, self.tail)
            if self.lines_drawn == 0:
                sys.stderr.write(l1)
                for b in body:
                    sys.stderr.write('\n' + b)
                sys.stderr.write('\n')
            else:
                sys.stderr.write(ESC + f'[{self.lines_drawn}A\r' + ESC + '[K' + l1)
                for b in body:
                    sys.stderr.write('\n' + ESC + '[K' + b)
                sys.stderr.write('\n')
            sys.stderr.write(ESC + '[?25h')  # show cursor
            sys.stderr.flush()
        else:
            last_raw = recent_lines[-1] if recent_lines else ''
            sys.stderr.write(render_bar(parser, total, elapsed, self.log, last_raw=last_raw) + '\n')

    def error_dump(self, recent):
        sys.stderr.write('\n' + '=' * 60 + '\n')
        sys.stderr.write('errors detected during build — last lines:\n')
        for r in recent:
            sys.stderr.write('  ' + r + '\n')
        sys.stderr.write('=' * 60 + '\n')
        sys.stderr.flush()
