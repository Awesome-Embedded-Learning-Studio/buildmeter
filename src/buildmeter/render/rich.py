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

    def __init__(self, is_tty, log_path=''):
        self.is_tty = is_tty
        self.log = log_path
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
            self.live = Live(self._renderable(None, total, 0.0, ''),
                             console=self.console, refresh_per_second=10)
            self.live.start()

    def _head(self, parser, total, elapsed, last_raw):
        done = parser.done if parser else 0
        unit = parser.unit if parser else 'units'
        if total and done > total:
            spinner = _SPINNER[int(elapsed * 20) % len(_SPINNER)]
            return Text.assemble(
                (bar(100.0, 28), 'bold green'), ' ',
                (spinner, 'bold yellow'), ' ',
                (_finalizing_phase(last_raw), 'yellow'), ' · ',
                (f'{done} {unit}', 'bold'), ' · ',
                (f'{fmt_dur(elapsed)} elapsed', 'cyan'),
            )
        if total:
            pct = min(done / total * 100, 100.0)
            rate = done / elapsed if elapsed > 0.3 else 0.0
            rem = max(total - done, 0)
            eta = fmt_dur(rem / rate) if rate else '--'
            return Text.assemble(
                (bar(pct, 28), 'bold green'), ' ',
                (f'{done}/{total} {unit} ', 'bold'),
                (f'({pct:.0f}%) ', 'cyan'),
                (f'{rate:.1f}/s ETA ', ''), (eta, 'magenta'),
            )
        rate = done / elapsed if elapsed > 0.3 else 0.0
        return Text.assemble(
            (f'{done} {unit} done  ', 'bold'),
            (f'{rate:.1f}/s  elapsed ', ''),
            (fmt_dur(elapsed), 'cyan'),
        )

    def _renderable(self, parser, total, elapsed, last_raw):
        body = self._head(parser, total, elapsed, last_raw)
        if last_raw:
            body.append('\n  ' + last_raw, style='dim')
        if self.log:
            body.append(f'\n  log: {self.log}', style='dim')
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

    def tick(self, parser, total, elapsed, last_raw):
        if self.is_tty and self.live is not None:
            self.live.update(self._renderable(parser, total, elapsed, last_raw))
        else:
            fire = (self.snap_event_step and (parser.done - self.last_snap_done) >= self.snap_event_step) or \
                   (self.snap_time_step and (elapsed - self.last_snap_time) >= self.snap_time_step)
            if fire:
                if self.snap_event_step:
                    self.last_snap_done = parser.done
                self.last_snap_time = elapsed
                self.console.print(self._bar_line(parser, total, elapsed, last_raw))

    def end(self, parser, total, elapsed, last_raw):
        if self.is_tty and self.live is not None:
            self.live.update(self._renderable(parser, total, elapsed, last_raw))
            self.live.stop()
        else:
            self.console.print(self._bar_line(parser, total, elapsed, last_raw))

    def error_dump(self, recent):
        self.console.print('errors detected during build — last lines:', style='bold red')
        for r in recent:
            self.console.print(r, style='red')
