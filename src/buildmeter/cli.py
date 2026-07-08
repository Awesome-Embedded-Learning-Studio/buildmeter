#!/usr/bin/env python3
"""buildmeter.cli — command-line entry.

Live progress meter for GNU make-based builds (kernel kbuild / U-Boot / buildroot):
parses the build tool's stdout and renders an in-place progress bar on stderr
(stdout stays clean for piping / logging). Backend is auto-selected by
buildmeter.render: rich if installed, else the stdlib ANSI native renderer.

Replay a captured log (demo / regression test):
  python3 -m buildmeter kernel   build-kernel.log
  python3 -m buildmeter buildroot buildroot-build.log

Wrap a live build (pipe through):
  make -C linux 2>&1 | python3 -m buildmeter kernel

Total denominator: file replay two-passes the log for an accurate %; stdin/live
is indeterminate (count + rate + ETA, no %). Pass --total N to override (e.g.
from a `make -n -k` dry-run pre-scan). NOTE: when passing BOTH a file and
--total, put the file FIRST (`kind FILE --total N`); argparse rejects
`kind --total N FILE` (nargs='?' positional after an option-value)."""
# Script-style entry shim: when invoked as `python3 path/to/cli.py` (the way
# callers like rk-forge's lib/progress.sh invoke it via a $..._PY path var), put
# the package root (this file's ../.., i.e. .../src) on sys.path so
# `from buildmeter...` resolves. No-op under `python -m buildmeter` (the package
# is already importable then).
import os
import sys

_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../src
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

import argparse
import collections
import re
import select
import time

from buildmeter.engine import ERR_RE, PARSERS, strip_ansi, two_pass_total
from buildmeter.render import ESC, get_renderer
from buildmeter.render.native import _SPINNER, fmt_dur


def main():
    ap = argparse.ArgumentParser(prog="buildmeter", description="live build progress meter")
    ap.add_argument("kind", choices=["buildroot", "kernel", "uboot"])
    ap.add_argument("source", nargs="?", help="log file to replay (default: stdin)")
    ap.add_argument("--total", type=int, help="override total denominator")
    ap.add_argument("--speed", type=float, default=0,
                    help="replay: ms to sleep per line (demo slow-mo)")
    ap.add_argument("--count-only", action="store_true",
                    help="don't render; just count progress units in the input and "
                         "print the total to stdout (for the make -n pre-scan)")
    ap.add_argument("--log", default="",
                    help="path of the tee'd full build log (shown on the bar line)")
    ap.add_argument("--ignore-errors", default="",
                    help="regex of error signatures to IGNORE (tolerated noise); e.g. "
                         "uboot passes its BINMAN_NOISE so 'Error 103'/'images are "
                         "invalid' don't trigger a false error dump")
    ap.add_argument("--tail", type=int, default=5,
                    help="number of recent raw build lines to show under the bar as a "
                         "sliding window (default 5); wrapper scripts set this from "
                         "$FORGE_PROGRESS_TAIL")
    args = ap.parse_args()

    # Pre-scan: feed the whole input through the parser, print the unit count,
    # exit. Used by the `make -n -k | buildmeter --count-only` denominator grab.
    # The dry-run can run 15-30s on a big tree with NO output (make -n is
    # enumerating silently), so on a TTY show a spinner — same lesson as the
    # finalizing heartbeat, don't let silence read like a hang. Spinner goes to
    # stderr (stdout is the count, read by the caller); the line is cleared
    # before the count is printed.
    if args.count_only:
        p = PARSERS[args.kind]()
        src = open(args.source) if args.source else sys.stdin
        is_tty = sys.stderr.isatty()
        start = time.monotonic()
        last_render = 0.0
        try:
            for line in src:
                p.feed(line)
                if is_tty:
                    elapsed = time.monotonic() - start
                    if elapsed - last_render >= 0.1:
                        last_render = elapsed
                        sp = _SPINNER[int(elapsed * 20) % len(_SPINNER)]
                        sys.stderr.write(f'\r{sp} scanning dry-run (make -n)… {fmt_dur(elapsed)} elapsed')
                        sys.stderr.flush()
        finally:
            if is_tty:
                sys.stderr.write('\r' + ESC + '[K')  # clear the spinner line
                sys.stderr.flush()
            if args.source:
                src.close()
        print(p.discovered)
        return

    is_file = bool(args.source)
    f = open(args.source) if is_file else sys.stdin

    # File replay: two-pass for an accurate total. Live stdin can't pre-scan, so
    # it runs indeterminate unless --total is given (e.g. from a `make -n -k` dry-run).
    total = args.total
    if total is None and is_file:
        total = two_pass_total(args.kind, args.source)
        f.seek(0)

    parser = PARSERS[args.kind]()
    is_tty = sys.stderr.isatty()
    renderer = get_renderer(is_tty, args.log, args.tail)

    # Ringbuffer of recent raw lines + error scan — so a make failure surfaces
    # its error lines instead of being hidden behind the bar.
    recent = collections.deque(maxlen=200)
    saw_error = False
    ignore_re = re.compile(args.ignore_errors) if args.ignore_errors else None
    # Sliding window of recent NON-ignored lines shown under the bar (--tail, fed
    # by the wrapper scripts from $FORGE_PROGRESS_TAIL). Separate from `recent`
    # (which holds EVERY line for the error dump) so tolerated noise like uboot's
    # "Error 103" (--ignore-errors) never lands on display.
    displayed = collections.deque(maxlen=max(args.tail, 1))
    start = time.monotonic()

    renderer.begin(total)

    def feed_line(line):
        # Per-line ingest shared by both the file-replay and live-stdin loops.
        nonlocal saw_error
        raw = strip_ansi(line.rstrip("\n"))
        recent.append(raw)
        if ERR_RE.search(raw) and not (ignore_re and ignore_re.search(raw)):
            saw_error = True
        # Feed the display window with NON-ignored lines only: tolerated noise
        # like uboot's "make: *** [.binman_stamp] Error 103" (--ignore-errors)
        # must NOT appear on the bar — it would read as a failure on a build
        # that's actually fine. Ignored lines still go into `recent` (ringbuffer)
        # for the error dump, just not into `displayed`.
        if raw.strip() and not (ignore_re and ignore_re.search(raw)):
            displayed.append(raw)
        parser.feed(line)

    try:
        if is_file:
            # File replay: readline never blocks (a file is always readable), so
            # a plain loop is fine and fastest.
            for line in f:
                feed_line(line)
                elapsed = time.monotonic() - start
                renderer.tick(parser, total, elapsed, list(displayed))
                if args.speed:
                    time.sleep(args.speed / 1000.0)
        else:
            # Live stdin: poll with a 0.1s timeout so the bar keeps refreshing
            # (elapsed ticks up, spinner spins) EVEN WHEN the build stalls and
            # emits nothing for a while. A blocking `for line in stdin` freezes
            # the bar at the last frame during a stall — indistinguishable from
            # buildmeter itself hanging (e.g. make deadlocked on WSL2 I/O, waiting
            # a page for minutes). select() on the stdin fd; we read raw bytes and
            # split on newlines ourselves (readline would re-block on a partial
            # line even after select reports the fd readable).
            fd = f.fileno()
            buf = ''
            while True:
                ready, _, _ = select.select([fd], [], [], 0.1)
                if ready:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break  # EOF — upstream pipe closed
                    buf += chunk.decode('utf-8', errors='replace')
                    while '\n' in buf:
                        line, buf = buf.split('\n', 1)
                        feed_line(line + '\n')
                # refresh every iteration, with or without new input
                elapsed = time.monotonic() - start
                renderer.tick(parser, total, elapsed, list(displayed))
            if buf:  # flush a trailing line with no final newline
                feed_line(buf)
                elapsed = time.monotonic() - start
                renderer.tick(parser, total, elapsed, list(displayed))
    except KeyboardInterrupt:
        # Ctrl+C: let finally redraw + restore (native) / stop Live (rich), exit
        # quietly (no traceback). make, upstream in the pipe, gets the signal too
        # and stops — user's intent.
        pass
    finally:
        parser.finalize()
        elapsed = time.monotonic() - start
        renderer.end(parser, total, elapsed, list(displayed))
        if saw_error:
            renderer.error_dump(recent)
        if is_file:
            f.close()


if __name__ == "__main__":
    main()
