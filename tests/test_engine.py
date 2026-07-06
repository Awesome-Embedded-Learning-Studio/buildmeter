"""Engine parser regression tests — feed sample build output, assert counts."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from buildmeter.engine import BuildrootParser, KernelParser, unwrap_recipe


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


class TestBuildrootParser(unittest.TestCase):
    def test_counts_distinct_packages(self):
        p = BuildrootParser()
        for line in BUILDROOT_SAMPLE.splitlines():
            p.feed(line)
        p.finalize()
        # host-sed done when busybox rotates in; busybox done at finalize.
        self.assertEqual(p.done, 2)
        self.assertEqual(p.discovered, 2)


if __name__ == "__main__":
    unittest.main()
