"""Probes tools/ibtool compiles byte-identically to Apple's ibtool 27.

tests/ibtool/src/probes holds single-question xibs; their goldens in
tests/ibtool/golden/probes were compiled by Apple's ibtool with the flags of
compile-golden.sh (iPhone + iPad, iOS 17.0, --module probes). Not every probe
compiles yet: the m-* family and owner-swap are open questions. Every probe
listed here must stay byte-identical; add a probe to the list when the change
it pins lands.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
IBTOOL = HERE.parent / "tools" / "ibtool"
SRC = HERE / "ibtool" / "src" / "probes"
GOLDEN = HERE / "ibtool" / "golden" / "probes"
FLAGS = ["--target-device", "iphone", "--target-device", "ipad",
         "--minimum-deployment-target", "17.0", "--module", "probes"]

BYTE_IDENTICAL = [
    "attr-top",
    "attr-trailing",
    # Plain button configurations: the background is the tint at alpha 0.18
    # (unshared), clearColor without a tint; custom-type buttons have no
    # UIButtonType (tools/ibxib.py).
    "button-config",
    # guide.trailing = item.trailing constraints: the item whose frame reaches
    # furthest right first, whatever the constants (tools/ibxib.py).
    "guide-trailing",
    "nosymbolic",
    # Segmented controls of 1-5 segments: positions none/3/1..., the selected
    # segment's view last, alignments, no selection (-1 as int64).
    "segmented",
    # File's Owner customClass/customModule leave the nib unchanged (its golden
    # equals swap.nib's).
    "owner-module",
    # Below iOS 17.0 a top-level view is laid out at the device's screen size
    # (compiled with --minimum-deployment-target 16.9, see DEPLOYMENT).
    "resize-device",
    "swap",
    # A xib declaring a deployment below iOS 14.0 with a system image compiles
    # to a nib bundle: objects-14.0+.nib and runtime.nib (tools/ibtool).
    "system-image-bundle",
    "tools-new",
]
# Probes compiled for another deployment target than FLAGS' 17.0.
DEPLOYMENT = {"resize-device": "16.9", "system-image-bundle": "15.0"}
# Storyboard probes, compiled into a --compilation-directory at their deployment
# target: below iOS 17.0 a scene without a <device> lays its framed root view out
# at 600x600 (scene-canvas); a storyboard imageView is opaque, and
# automaticallyAdjustsScrollViewInsets="NO" lands in the VC swapper (vc-insets).
STORYBOARDS = {"scene-canvas": "15.0", "vc-insets": "16.6"}


class Probes(unittest.TestCase):
    def test_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in BYTE_IDENTICAL:
                with self.subTest(probe=name):
                    out = Path(tmp, f"{name}.nib")
                    flags = [DEPLOYMENT.get(name, f) if f == "17.0" else f for f in FLAGS]
                    p = subprocess.run([sys.executable, str(IBTOOL), *flags, "--compile",
                                        str(out), str(SRC / f"{name}.xib")],
                                       capture_output=True, text=True, timeout=280)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertTreeEqual(out, GOLDEN / f"{name}.nib")

    def test_storyboards_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, target in STORYBOARDS.items():
                with self.subTest(probe=name):
                    flags = [target if f == "17.0" else f for f in FLAGS]
                    p = subprocess.run([sys.executable, str(IBTOOL), *flags,
                                        str(SRC / f"{name}.storyboard"),
                                        "--compilation-directory", tmp],
                                       capture_output=True, text=True, timeout=280)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertTreeEqual(Path(tmp, f"{name}.storyboardc"),
                                         GOLDEN / f"{name}.storyboardc")

    def assertTreeEqual(self, out, gold):
        """A file byte for byte, or a directory's files by name and bytes."""
        self.assertEqual(out.is_dir(), gold.is_dir(), out)
        if not gold.is_dir():
            self.assertEqual(out.read_bytes(), gold.read_bytes())
            return
        names = sorted(f.name for f in gold.iterdir())
        self.assertEqual(sorted(f.name for f in out.iterdir()), names)
        for f in names:
            self.assertEqual((out / f).read_bytes(), (gold / f).read_bytes(), f)


if __name__ == "__main__":
    unittest.main()
