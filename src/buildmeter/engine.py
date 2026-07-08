"""buildmeter.engine — build-output parsers + denominator discovery.

Pure logic, no rendering. Each build profile (kernel kbuild / buildroot / a
future uboot or SDK) is a BuildParser subclass: it implements feed(line)->bool
+ .done/.discovered/.current_label/.finalize against the abstract base, and
self-registers via @register. Adding a tool = inherit + @register, core stays
untouched. Plus the dry-run recipe unwrap, ANSI strip, error-signature regex,
and the file-replay two-pass total.

Renderer-agnostic: renderers consume BuildParser objects via the base interface
+ getattr for optional per-profile signals (ninja_total, ...)."""
import re
from abc import ABC, abstractmethod

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


# --- Parser registry --------------------------------------------------------- #
# @register on a BuildParser subclass adds it to PARSERS by .name; cli looks it
# up by profile id. Adding a build tool = new subclass + @register, nothing else.
PARSERS = {}


def register(cls):
    """Class decorator: register a BuildParser subclass into PARSERS by .name."""
    PARSERS[cls.name] = cls
    return cls


# --- Parser base contract ---------------------------------------------------- #

class BuildParser(ABC):
    """One build-stream parser per profile (kernel kbuild / buildroot / a future
    uboot or SDK). Subclasses parse a tool's stdout into progress units; cli and
    both renderers drive every parser through this interface — so adding a tool
    means inherit + @register + implement these, with the core untouched.

    Optional per-profile signals (Buildroot's ninja_total/ninja_pct/finalizing/
    inner_count) are deliberately NOT here: renderers getattr() them with
    defaults, so a minimal subclass that sets none of them still renders fine.
    """
    name = ''        # profile id — cli choice + PARSERS key; subclass overrides
    unit = 'units'   # count noun on the bar ('units' / 'pkgs')

    def _prepare(self, line):
        """Shared preprocessing: strip ANSI + unwrap `make -n` echo recipes, so
        live output and dry-run recipes feed the same regex."""
        return unwrap_recipe(strip_ansi(line.rstrip('\n')))

    @abstractmethod
    def feed(self, line):
        """Ingest one raw line; return True if it counted as a progress line."""

    @abstractmethod
    def finalize(self):
        """Called at EOF — rotation fixups, counting the final unit, etc."""

    @property
    @abstractmethod
    def done(self):
        """Completed progress units so far."""

    @property
    @abstractmethod
    def discovered(self):
        """Total units seen (for the two-pass file-replay denominator)."""

    @property
    @abstractmethod
    def current_label(self):
        """What's building now — shown on the bar ([pkg: stage] / 'CC path')."""


# --- Parsers ----------------------------------------------------------------- #

@register
class BuildrootParser(BuildParser):
    """buildroot emits (ANSI reverse-video) `>>> pkg-name  Stage` lines as each
    package moves through Extracting -> Patching -> Configuring -> Building ->
    Installing-to-*. Progress unit = distinct packages.

    There is no single reliable "package done" signal (Installing-to-staging is
    mid-life for some packages). The robust signal is rotation: when a NEW
    package name appears, the previous one is done. So .done counts packages
    rotated past; the currently-active package isn't counted as done."""
    name = 'buildroot'
    unit = 'pkgs'
    # `>>> name version stage` separated by SINGLE spaces (verified:
    # `>>> host-attr 2.5.2 Extracting`). host-skeleton has NO version, so its line
    # is `>>> host-skeleton  Extracting` (2 spaces) — the ONLY pkg the old \s{2,}
    # matched, so .current froze on host-skeleton and never rotated to real pkgs.
    # \s+ accepts 1+ spaces → both shapes match.
    _re = re.compile(r'^>>>\s+(\S+)\s+(\S.*?)\s*$')
    # make-based pkg-internal step lines (autotools CC/CCLD/LIBTOOL, kbuild
    # CC/LD/AR/AS). Counted as in-pkg activity so a multi-minute configure/build
    # of ONE pkg shows steps ticking instead of a frozen N/(N+1) bar. meson/cmake
    # pkgs emit different formats and won't match — they just don't get the
    # in-pkg step count (a future profile can add them).
    _inner_re = re.compile(r'^\s*(CC|CCLD|LD|AR|AS|LIBTOOL)\b')
    # meson pkgs: ninja prints `[done/total] desc` per edge when its stdout is a
    # pipe (non-TTY) — piped through buildroot make → buildmeter. done/total is a
    # per-package denominator, so it beats the make step count (real %).
    _ninja_re = re.compile(r'^\[(\d+)/(\d+)\]\s+(.+)')
    # cmake+ninja pkgs print a DIFFERENT shape: `[NN%] desc` (percent, NO total).
    # Percent-only in-pkg progress (ccache, blake3, jpeg-turbo, ...).
    _ninja_pct_re = re.compile(r'^\[\s*(\d+)%\]\s+(.+)')
    # buildroot package-less tail stages: `>>>   Finalizing target directory`,
    # `>>>   Generating filesystem image rootfs.tar` (2+ spaces after >>>, a
    # capitalised phrase, no pkg+version). Once seen, every package is built and
    # rsync/mkfs/fakeroot image-gen follows — minutes of output with NO >>> pkg
    # line, so the bar would otherwise freeze at the last pkg count. Sticky:
    # once finalizing flips on it stays on (the build never returns to pkg builds).
    _tail_re = re.compile(r'^>>>\s{2,}([A-Z][^\n]*?)\s*$')
    _TAIL_PHASE_MAP = [
        (re.compile(r'Finalizing'), 'finalizing target'),
        (re.compile(r'Sanitizing'), 'sanitizing rpath'),
        (re.compile(r'Sanity check|Copying overlay'), 'applying overlay'),
        (re.compile(r'post-build'), 'running post-build'),
        (re.compile(r'common tables|root filesystems'), 'building device tables'),
        (re.compile(r'Generating filesystem image'), 'generating filesystem image'),
        (re.compile(r'post-image'), 'running post-image scripts'),
    ]

    def __init__(self):
        self.seen = set()
        self.current = None
        self.current_stage = None
        self._done = 0
        self.inner_count = 0  # in-pkg make steps for the CURRENT pkg (resets on rotation)
        self.ninja_done = 0   # ninja [done] for the current pkg (resets on rotation)
        self.ninja_total = 0  # ninja [total] — per-pkg denominator while a ninja pkg is active
        self.ninja_pct = -1   # cmake+ninja [NN%] for the current pkg (-1 = none seen)
        self.ninja_desc = ''
        self.finalizing = False      # buildroot tail (image-gen) reached?
        self.finalizing_phase = ''   # friendly label for the current tail stage

    def feed(self, line):
        target = self._prepare(line)
        # buildroot package-less tail stage (image generation)? Checked before the
        # pkg regex: these `>>>   <Phrase>` lines have no pkg+version so _re won't
        # match, but we flag finalizing so the bar switches to spinner+phase
        # instead of freezing at the last pkg count through the image-gen minutes.
        tm = self._tail_re.match(target)
        if tm:
            phase = self._tail_phase(tm.group(1))
            if phase:
                self.finalizing = True
                self.finalizing_phase = phase
            return True
        m = self._re.match(target)
        if m:
            pkg, stage = m.group(1), m.group(2)
            if self.current is not None and pkg != self.current:
                self._done += 1  # previous package rotated out -> completed
                self.inner_count = 0  # new pkg → reset its in-pkg step count
                self.ninja_done = 0    # ... and its ninja counter
                self.ninja_total = 0
                self.ninja_pct = -1
            self.seen.add(pkg)
            self.current, self.current_stage = pkg, stage
            return True
        # ninja [done/total] line (meson/cmake-ninja pkgs)? strongest in-pkg
        # signal — carries a real per-package denominator. Checked before the
        # make-step fallback so a ninja pkg shows %, not just a step count.
        nj = self._ninja_re.match(target)
        if nj:
            self.ninja_done = int(nj.group(1))
            self.ninja_total = int(nj.group(2))
            self.ninja_desc = nj.group(3)
            self.ninja_pct = -1   # meson shape; clear any cmake pct
            return False
        npc = self._ninja_pct_re.match(target)
        if npc:
            # cmake+ninja: `[NN%]` (percent, no total). Real in-pkg %, but no
            # denominator to render as done/total — show percent only.
            self.ninja_pct = int(npc.group(1))
            self.ninja_desc = npc.group(2)
            self.ninja_total = 0   # cmake shape; clear any meson total
            return False
        # not a >>> pkg line — a make-based in-pkg step? count as activity.
        if self._inner_re.match(target):
            self.inner_count += 1
        return False

    def finalize(self):
        # The active package at clean EOF never rotates (no successor) -> count
        # it as done so the bar reaches 100%, not (N-1)/N.
        if self.current is not None:
            self._done += 1
            self.current = None

    def _tail_phase(self, phrase):
        """Map a buildroot tail-stage phrase to a friendly label, or '' if it
        isn't a recognised tail stage (don't false-trigger finalizing)."""
        for rx, label in self._TAIL_PHASE_MAP:
            if rx.search(phrase):
                return label
        return ''

    @property
    def done(self):
        return self._done

    @property
    def discovered(self):
        return len(self.seen)

    @property
    def current_label(self):
        if not self.current:
            return ''
        return f'{self.current}: {self.current_stage}'


@register
class KernelParser(BuildParser):
    """kbuild prints every build step as `  TYPE  path` where TYPE is an
    upper-case action word — CC, HOSTCC, HOSTLD, YACC, LEX, AR, LD, MODPOST,
    UPD, CHK, GZIP, OBJCOPY, ... dozens across kbuild versions. Enumerating
    them is a losing game (every release adds one), so we match the SHAPE:
    leading indent + an upper-case token (the action) + optional [M] + whitespace
    + a target. Captures every step kbuild announces — including types we've
    never seen (CHKSHA1, GEN, CALL, DESCEND, ...) — with no maintenance list."""
    name = 'kernel'
    unit = 'units'
    _re = re.compile(r'^\s+([A-Z][A-Z0-9]+)(?:\s\[M\])?\s+(\S+)')

    def __init__(self):
        self.count = 0
        self.last = None

    def feed(self, line):
        m = self._re.match(self._prepare(line))
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


def two_pass_total(kind, path):
    """Scan a captured log to get the accurate total (denominator) up front."""
    p = PARSERS[kind]()
    with open(path) as f:
        for line in f:
            p.feed(line)
    return p.discovered
