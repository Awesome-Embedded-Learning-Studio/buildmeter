"""buildmeter.engine — build-output parsers + denominator discovery.

Pure logic, no rendering. One parser per build tool (kernel kbuild CC/LD/AR/AS,
buildroot `>>> pkg` package stages); each exposes feed(line)->bool, .done,
.discovered, .current_label. Plus the dry-run recipe unwrap, ANSI strip,
error-signature regex, and the file-replay two-pass total.

Renderer-agnostic: render/native.py (and the future render/rich.py) consume
parser objects without importing this module's internals."""
import re

# --- ANSI / dry-run recipe unwrapping ---------------------------------------- #

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mK]')


def strip_ansi(s):
    """Strip ANSI color/style escapes (buildroot wraps >>> lines in reverse-video)."""
    return _ANSI_RE.sub('', s)


# `make -n` dry-run prints recipe lines verbatim, so the progress marker sits
# INSIDE an `echo '...'` / `echo "..."` argument rather than at line start:
#   kernel dry-run:   set -e;  echo '  CC      init/main.o';  $(CC) ...
#   buildroot dry-run: echo "[7m>>> busybox 1.38.0 Building[27m"
# Extract the echo content so the same regex matches both live (echo output)
# and dry-run (echo command) forms. Best-effort: no echo -> line unchanged.
_ECHO_RE = re.compile(r"""echo\s+['"](.+?)['"]""")


def unwrap_recipe(line):
    m = _ECHO_RE.search(line)
    return m.group(1) if m else line


# Error signatures surfaced on exit so the bar doesn't hide make failures.
ERR_RE = re.compile(
    r'FATAL ERROR|Lexical error|Syntax error|'
    r'error:|undefined reference|'
    r'cannot find -l|ld returned|'
    r'\*\*\* \[.*\] Error'
)


# --- Parsers ----------------------------------------------------------------- #

class BuildrootParser:
    """buildroot emits (ANSI reverse-video) `>>> pkg-name  Stage` lines as each
    package moves through Extracting -> Patching -> Configuring -> Building ->
    Installing-to-*. Progress unit = distinct packages.

    There is no single reliable "package done" signal (Installing-to-staging is
    mid-life for some packages). The robust signal is rotation: when a NEW
    package name appears, the previous one is done. So .done counts packages
    rotated past; the currently-active package isn't counted as done."""
    name = 'buildroot'
    unit = 'pkgs'
    _re = re.compile(r'^>>>\s+(\S+)\s{2,}(\S.*?)\s*$')

    def __init__(self):
        self.seen = set()
        self.current = None
        self.current_stage = None
        self.done = 0

    def feed(self, line):
        m = self._re.match(unwrap_recipe(strip_ansi(line.rstrip('\n'))))
        if not m:
            return False
        pkg, stage = m.group(1), m.group(2)
        if self.current is not None and pkg != self.current:
            self.done += 1  # previous package rotated out -> completed
        self.seen.add(pkg)
        self.current, self.current_stage = pkg, stage
        return True

    def finalize(self):
        # The active package at clean EOF never rotates (no successor) -> count
        # it as done so the bar reaches 100%, not (N-1)/N.
        if self.current is not None:
            self.done += 1
            self.current = None

    @property
    def discovered(self):
        return len(self.seen)

    @property
    def current_label(self):
        if not self.current:
            return ''
        return f'{self.current}: {self.current_stage}'


class KernelParser:
    """kbuild emits `  CC path/foo.o` / `  CC [M] drivers/...` / `  LD ...` /
    `  AR ...` / `  AS ...` lines. Progress unit = compile/link/archive steps."""
    name = 'kernel'
    unit = 'units'
    _re = re.compile(r'^\s+(CC|LD|AR|AS)(?:\s\[M\])?\s+(\S+)')

    def __init__(self):
        self.count = 0
        self.last = None

    def feed(self, line):
        m = self._re.match(unwrap_recipe(strip_ansi(line.rstrip('\n'))))
        if not m:
            return False
        self.count += 1
        self.last = (m.group(1), m.group(2))
        return True

    def finalize(self):
        pass  # kernel counts on each CC/LD/AR line; no rotation, no off-by-one

    @property
    def done(self):
        return self.count

    @property
    def discovered(self):
        return self.count

    @property
    def current_label(self):
        if not self.last:
            return ''
        return f'{self.last[0]} {self.last[1]}'


PARSERS = {'buildroot': BuildrootParser, 'kernel': KernelParser}


def two_pass_total(kind, path):
    """Scan a captured log to get the accurate total (denominator) up front."""
    p = PARSERS[kind]()
    with open(path) as f:
        for line in f:
            p.feed(line)
    return p.discovered
