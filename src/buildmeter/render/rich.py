"""buildmeter.render.rich — Rich backend (Live-updated Panel).

Auto-selected by buildmeter/render/__init__.py when `import rich` succeeds
(opt-in via `pip install buildmeter[rich]`). Renders a bordered cyan Panel with
a colored bar, braille spinner + phase label for the post-link tail, and the
latest raw make line — updated in place via rich.live.Live (auto-refresh,
~10fps), so no manual cursor-up.

Reuses native's bar/fmt_dur/_finalizing_phase/_SPINNER so the two backends stay
visually consistent; the difference is color, the border, and the Live-driven
redraw. Non-TTY (CI / redirect) prints plain single-line snapshots (no Panel
border) so logs stay readable — same info as native's render_bar."""
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from buildmeter.render.native import _SPINNER, _finalizing_phase, bar, fmt_dur


class RichRenderer:
    """Stateful rich sink — same begin/tick/end/error_dump contract as
    NativeRenderer. TTY: Live Panel; non-TTY: plain snapshot lines."""

    def __init__(self, is_tty, log_path='', tail=5):
        self.is_tty = is_tty
        self.log = log_path
        self.tail = max(1, tail)
        self.console = Console(stderr=True)
        self.live = None
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
            self.live = Live(self._renderable(None, total, 0.0, []),
                             console=self.console, refresh_per_second=10)
            self.live.start()

    def _head(self, parser, total, elapsed, last_raw):
        done = parser.done if parser else 0
        unit = parser.unit if parser else 'units'
        if getattr(parser, 'finalizing', False):
            # buildroot tail (image-gen): full bar + spinner + phase. Only
            # BuildrootParser sets .finalizing; kernel uses the done>total branch.
            spinner = _SPINNER[int(elapsed * 20) % len(_SPINNER)]
            return Text.assemble(
                (bar(100.0, 28), 'bold green'), ' ',
                (spinner, 'bold yellow'), ' ',
                (parser.finalizing_phase, 'yellow'), ' · ',
                (f'{done} {unit} done', 'bold'), ' · ',
                (f'{fmt_dur(elapsed)} elapsed', 'cyan'),
            )
        if total and done > total:
            spinner = _SPINNER[int(elapsed * 20) % len(_SPINNER)]
            return Text.assemble(
                (bar(100.0, 28), 'bold green'), ' ',
                (spinner, 'bold yellow'), ' ',
                (_finalizing_phase(last_raw), 'yellow'), ' · ',
                (f'{done} {unit}', 'bold'), ' · ',
                (f'{fmt_dur(elapsed)} elapsed', 'cyan'),
            )
        # buildroot in-pkg tag (ninja % / make steps) — built once, appended in
        # BOTH total and indeterminate modes, so a buildroot run with no global
        # total still shows in-pkg progress. Kernel (unit=units) gets an empty tag.
        pkg_tag = Text('')
        if unit == 'pkgs' and parser is not None and parser.current_label:
            label = parser.current_label
            if getattr(parser, 'ninja_total', 0):  # meson ninja: [done/total]
                ipct = parser.ninja_done * 100 // parser.ninja_total
                label += f' · ninja {parser.ninja_done}/{parser.ninja_total} ({ipct}%)'
            elif getattr(parser, 'ninja_pct', -1) >= 0:  # cmake ninja: [NN%]
                label += f' · ninja {parser.ninja_pct}%'
            elif getattr(parser, 'inner_count', 0):  # autotools/make pkg
                label += f' · {parser.inner_count} steps'
            pkg_tag = Text(f'  [{label}]', style='yellow')
        if total and unit == 'units':
            # kernel: dry-run total → real % (done/total).
            pct = min(done / total * 100, 100.0)
            rate = done / elapsed if elapsed > 0.3 else 0.0
            rem = max(total - done, 0)
            eta = fmt_dur(rem / rate) if rate else '--'
            return Text.assemble(
                (bar(pct, 28), 'bold green'), ' ',
                (f'{done}/{total} {unit} ', 'bold'),
                (f'({pct:.0f}%) ', 'cyan'),
                (f'{rate:.1f}/s ETA ', ''), (eta, 'magenta'),
                pkg_tag,
            )
        # indeterminate: buildroot always (total is info-only below), or kernel
        # without a pre-scanned --total.
        rate = done / elapsed if elapsed > 0.3 else 0.0
        parts = [
            (f'{done} {unit} done  ', 'bold'),
            (f'{rate:.1f}/s  elapsed ', ''),
            (fmt_dur(elapsed), 'cyan'),
        ]
        if total:
            # buildroot: show-targets total as info, not % (incremental undercounts).
            parts.append((f'  All Packages: {total}', 'magenta'))
        parts.append(pkg_tag)
        return Text.assemble(*parts)

    def _renderable(self, parser, total, elapsed, recent_lines):
        last_raw = recent_lines[-1] if recent_lines else ''
        body = self._head(parser, total, elapsed, last_raw)
        # Panel 内可用宽 = console 宽 − 边框(2) − padding(2) − 行首 '  ' 缩进(2)。
        # 截断超长行加 …(rsync 这类长命令不截会被 rich wrap 撑高 Panel、错位)。
        # 显示最近 tail 行(滑动窗口),跟 native renderer 对齐。
        w = max(10, self.console.width - 6)
        for line in list(recent_lines)[-self.tail:]:
            raw = line if len(line) <= w else line[:w - 1] + '…'
            body.append('\n  ' + raw, style='dim')
        if self.log:
            lp = f'log: {self.log}'
            lp = lp if len(lp) <= w else lp[:w - 1] + '…'
            body.append('\n  ' + lp, style='dim')
        return Panel(body, title='buildmeter', title_align='left', border_style='cyan')

    def _bar_line(self, parser, total, elapsed, last_raw=''):
        """Non-TTY snapshot: a single styled line (no Panel border)."""
        done = parser.done
        unit = parser.unit
        if total and done > total:
            return Text.assemble(
                (bar(100.0, 28), 'green'), ' ',
                (_finalizing_phase(last_raw), 'yellow'), ' · ',
                f'{done} {unit} · {fmt_dur(elapsed)} elapsed')
        if total:
            pct = min(done / total * 100, 100.0)
            rate = done / elapsed if elapsed > 0.3 else 0.0
            rem = max(total - done, 0)
            eta = fmt_dur(rem / rate) if rate else '--'
            return Text.assemble(
                (bar(pct, 28), 'green'),
                f' {done}/{total} {unit} ({pct:.0f}%) {rate:.1f}/s ETA {eta}')
        rate = done / elapsed if elapsed > 0.3 else 0.0
        return Text(f'{done} {unit} done  {rate:.1f}/s  elapsed {fmt_dur(elapsed)}')

    def tick(self, parser, total, elapsed, recent_lines):
        if self.is_tty and self.live is not None:
            self.live.update(self._renderable(parser, total, elapsed, recent_lines))
        else:
            fire = (self.snap_event_step and (parser.done - self.last_snap_done) >= self.snap_event_step) or \
                   (self.snap_time_step and (elapsed - self.last_snap_time) >= self.snap_time_step)
            if fire:
                if self.snap_event_step:
                    self.last_snap_done = parser.done
                self.last_snap_time = elapsed
                last_raw = recent_lines[-1] if recent_lines else ''
                self.console.print(self._bar_line(parser, total, elapsed, last_raw))

    def end(self, parser, total, elapsed, recent_lines):
        if self.is_tty and self.live is not None:
            self.live.update(self._renderable(parser, total, elapsed, recent_lines))
            self.live.stop()
        else:
            last_raw = recent_lines[-1] if recent_lines else ''
            self.console.print(self._bar_line(parser, total, elapsed, last_raw))

    def error_dump(self, recent):
        self.console.print('errors detected during build — last lines:', style='bold red')
        for r in recent:
            self.console.print(r, style='red')
