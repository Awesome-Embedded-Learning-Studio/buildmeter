"""Engine parser regression tests — feed sample build output, assert counts."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from buildmeter.engine import BuildrootParser, KernelParser, unwrap_recipe
from buildmeter.render.native import render_bar


# Live kernel kbuild output: 5 progress units (2 CC + 2 LD + 1 AR).
KERNEL_SAMPLE = """\
  CC      init/main.o
  CC      init/version.o
  LD      vmlinux.o
  AR      built-in.a
  LD      .tmp_vmlinux1
"""

# Dry-run form: the CC marker sits inside an `echo '...'` (make -n prints the
# recipe verbatim). unwrap_recipe must extract it so the same regex matches.
KERNEL_DRYRUN = """\
set -e;  echo '  CC      init/main.o';  $(CC) -c ...
set -e;  echo '  CC      init/version.o';  $(CC) -c ...
"""

# buildroot `>>> pkg  Stage` (pkg + 2+ spaces + stage). Two distinct packages.
BUILDROOT_SAMPLE = """\
>>> host-sed  1.38.0 Building
>>> busybox  1.38.0 Building
>>> busybox  1.38.0 Installing to target
"""

# meson/cmake-ninja pkg: ninja prints `[done/total] desc` per edge in non-TTY.
# ninja_done/total is a per-package denominator (real %, beats step count).
BUILDROOT_NINJA_SAMPLE = """\
>>> qt6base  6.9.1 Building
[1/5] Compiling a.o
[2/5] Compiling b.o
[3/5] Compiling c.o
>>> qt6base  6.9.1 Installing to target
"""

# buildroot tail: package-less `>>>   <Phrase>` stages (image generation). These
# set parser.finalizing so the bar shows a spinner+phase, not a frozen pkg count,
# through the minutes of rsync/mkfs/fakeroot that follow.
BUILDROOT_TAIL_SAMPLE = """\
>>> busybox  1.37.0 Installing to target
>>>   Finalizing target directory
>>>   Generating filesystem image rootfs.tar
rsync -auH target/ buildroot-fs/tar/target
"""


class TestKernelParser(unittest.TestCase):
    def test_counts_live_cc_ld_ar(self):
        p = KernelParser()
        for line in KERNEL_SAMPLE.splitlines():
            p.feed(line)
        self.assertEqual(p.done, 5)
        self.assertEqual(p.discovered, 5)

    def test_counts_dryrun_unwraps_echo(self):
        p = KernelParser()
        for line in KERNEL_DRYRUN.splitlines():
            p.feed(line)
        # Without unwrap_recipe the dry-run would count 0 (CC hidden in echo);
        # the engine must unwrap both CC lines.
        self.assertEqual(p.done, 2)

    def test_unwrap_recipe(self):
        self.assertEqual(unwrap_recipe("  echo '  CC      foo.o'; bar"), "  CC      foo.o")
        self.assertEqual(unwrap_recipe("  CC foo.o"), "  CC foo.o")  # no echo → unchanged

    def test_counts_host_and_codegen_steps(self):
        # kbuild host-tool + codegen steps (HOSTCC/YACC/LEX/HOSTLD/UPD/...) that
        # an old CC|LD|AR|AS-only regex missed. The shape-based regex catches
        # them all — CHKSHA1 here is never enumerated, proving the no-list claim.
        p = KernelParser()
        for line in [
            "  HOSTCC  scripts/genksyms/genksyms.o",
            "  YACC    scripts/genksyms/parse.tab.[ch]",
            "  LEX     scripts/genksyms/lex.lex.c",
            "  HOSTLD  scripts/genksyms/genksyms",
            "  UPD     include/generated/autoconf.h",
            "  CHKSHA1 certs/signing_key.x509",
            "  CC      init/main.o",
            "  LD      vmlinux",
        ]:
            p.feed(line)
        self.assertEqual(p.count, 8)

    def test_ignores_non_step_lines(self):
        # Shape regex must NOT count non-progress lines: make banners, gcc
        # diagnostics, "Building ..." (mixed case), even with leading indent.
        p = KernelParser()
        for line in [
            "make[1]: Entering directory '/src'",
            "  Building foo",            # mixed case — not an all-caps action
            "scripts/foo.c: In function:",
            "make: *** [foo] Error 1",
            "  CC real.o",               # the only real step
        ]:
            p.feed(line)
        self.assertEqual(p.count, 1)


class TestBuildrootParser(unittest.TestCase):
    def test_counts_distinct_packages(self):
        p = BuildrootParser()
        for line in BUILDROOT_SAMPLE.splitlines():
            p.feed(line)
        p.finalize()
        # host-sed done when busybox rotates in; busybox done at finalize.
        self.assertEqual(p.done, 2)
        self.assertEqual(p.discovered, 2)

    def test_single_space_pkg_line_rotates(self):
        # real buildroot `>>> name version stage` uses SINGLE spaces; host-skeleton
        # has no version (2 spaces). The old \s{2,} matched only host-skeleton and
        # froze .current; \s+ must rotate through BOTH shapes.
        p = BuildrootParser()
        for line in [
            ">>> host-skeleton  Extracting",               # no version, 2 spaces
            ">>> host-skeleton  Installing to host directory",
            ">>> host-attr 2.5.2 Building",                # version, single spaces
            ">>> host-attr 2.5.2 Installing to host directory",
        ]:
            p.feed(line)
        p.finalize()
        self.assertEqual(p.done, 2)   # both host-skeleton and host-attr rotated

    def test_cmake_ninja_percent(self):
        # cmake+ninja prints `[NN%] desc` (percent, NO total) — different shape
        # from meson's [done/total]. Must surface as ninja NN%.
        p = BuildrootParser()
        for line in [">>> host-ccache  4.12.3 Building",
                     "[ 16%] Building CXX object src/ccache/hash.cpp.o",
                     "[ 42%] Building CXX object src/ccache/core/cacheentry.cpp.o"]:
            p.feed(line)
        self.assertEqual(p.ninja_pct, 42)
        self.assertEqual(p.ninja_total, 0)   # cmake: no total

    def test_ninja_inpkg_progress(self):
        p = BuildrootParser()
        for line in BUILDROOT_NINJA_SAMPLE.splitlines():
            p.feed(line)
        p.finalize()
        # qt6base rotates to done at finalize; ninja counter captured 3/5.
        self.assertEqual(p.done, 1)
        self.assertEqual(p.ninja_done, 3)
        self.assertEqual(p.ninja_total, 5)

    def test_ninja_counter_resets_on_pkg_rotation(self):
        p = BuildrootParser()
        for line in [
            ">>> qt6base  6.9.1 Building",
            "[2/5] Compiling b.o",
            ">>> busybox  1.37.0 Building",   # rotate → ninja resets
        ]:
            p.feed(line)
        p.finalize()
        self.assertEqual(p.done, 2)        # qt6base + busybox
        self.assertEqual(p.ninja_done, 0)  # reset by rotation; busybox has none
        self.assertEqual(p.ninja_total, 0)

    def test_tail_stage_sets_finalizing(self):
        p = BuildrootParser()
        for line in BUILDROOT_TAIL_SAMPLE.splitlines():
            p.feed(line)
        self.assertTrue(p.finalizing)
        # last recognised tail stage wins
        self.assertEqual(p.finalizing_phase, 'generating filesystem image')


class TestRenderBar(unittest.TestCase):
    def test_indeterminate_shows_ninja_inpkg(self):
        # buildroot runs indeterminate (no global total); the ninja in-pkg tag
        # must still render so a Qt6/meson pkg shows progress, not a frozen bar.
        p = BuildrootParser()
        for line in [">>> qt6base  6.9.1 Building", "[6/8] Linking libQt6Core.so"]:
            p.feed(line)
        out = render_bar(p, None, 100.0, '')
        self.assertIn("ninja 6/8 (75%)", out)
        self.assertIn("qt6base", out)

    def test_finalizing_shows_phase_not_pkgtag(self):
        # once the image-gen tail is reached, the bar shows the phase + spinner,
        # and the per-package tag is suppressed (we're past package building).
        p = BuildrootParser()
        for line in [">>> busybox  1.37.0 Building",
                     ">>>   Generating filesystem image rootfs.tar"]:
            p.feed(line)
        out = render_bar(p, None, 200.0, '')
        self.assertIn("generating filesystem image", out)
        self.assertNotIn("busybox", out)


if __name__ == "__main__":
    unittest.main()
