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
    "nosymbolic",
    # File's Owner customClass/customModule leave the nib unchanged (its golden
    # equals swap.nib's).
    "owner-module",
    "swap",
    "tools-new",
]


class Probes(unittest.TestCase):
    def test_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in BYTE_IDENTICAL:
                with self.subTest(probe=name):
                    out = Path(tmp, f"{name}.nib")
                    p = subprocess.run([sys.executable, str(IBTOOL), *FLAGS, "--compile",
                                        str(out), str(SRC / f"{name}.xib")],
                                       capture_output=True, text=True, timeout=280)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertEqual(out.read_bytes(), (GOLDEN / f"{name}.nib").read_bytes())


if __name__ == "__main__":
    unittest.main()
