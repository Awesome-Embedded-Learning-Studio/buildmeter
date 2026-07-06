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
import time

from buildmeter.engine import ERR_RE, PARSERS, strip_ansi, two_pass_total
from buildmeter.render import get_renderer


def main():
    ap = argparse.ArgumentParser(prog="buildmeter", description="live build progress meter")
    ap.add_argument("kind", choices=["buildroot", "kernel"])
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
    args = ap.parse_args()

    # Pre-scan: feed the whole input through the parser, print the unit count,
    # exit. Used by the `make -n -k | buildmeter --count-only` denominator grab.
    if args.count_only:
        p = PARSERS[args.kind]()
        src = open(args.source) if args.source else sys.stdin
        try:
            for line in src:
                p.feed(line)
        finally:
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
    renderer = get_renderer(is_tty, args.log)

    # Ringbuffer of recent raw lines + error scan — so a make failure surfaces
    # its error lines instead of being hidden behind the bar.
    recent = collections.deque(maxlen=200)
    saw_error = False
    ignore_re = re.compile(args.ignore_errors) if args.ignore_errors else None
    last_raw = ""
    start = time.monotonic()

    renderer.begin(total)
    try:
        for line in f:
            raw = strip_ansi(line.rstrip("\n"))
            recent.append(raw)
            if ERR_RE.search(raw) and not (ignore_re and ignore_re.search(raw)):
                saw_error = True
            if raw.strip():
                last_raw = raw
            parser.feed(line)
            elapsed = time.monotonic() - start
            renderer.tick(parser, total, elapsed, last_raw)
            if args.speed:
                time.sleep(args.speed / 1000.0)
    except KeyboardInterrupt:
        # Ctrl+C: let finally redraw + restore (native) / stop Live (rich), exit
        # quietly (no traceback). make, upstream in the pipe, gets the signal too
        # and stops — user's intent.
        pass
    finally:
        parser.finalize()
        elapsed = time.monotonic() - start
        renderer.end(parser, total, elapsed, last_raw)
        if saw_error:
            renderer.error_dump(recent)
        if is_file:
            f.close()


if __name__ == "__main__":
    main()
