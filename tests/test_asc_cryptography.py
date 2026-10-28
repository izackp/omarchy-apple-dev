"""tools/asc.py needs cryptography 42 or later: it reads Certificate.not_valid_after_utc, added in 42.0.0. On an
older module the command line must stop at once with a plain message naming both versions and the upgrade command,
not with an AttributeError in the middle of a command.

Only identity and test-identity reach not_valid_after_utc (certificate() and make_test_profile()), so only they
are guarded; validate, stamp, frameworks, given-identity and upload keep working on an older module.

Rows (expected results written before the code):
  1  old:     cryptography reported as 41.0.7 -> identity and test-identity exit non-zero; the message names 42,
              41.0.7 and `pip install -U cryptography`; no traceback
  2  current: the installed cryptography (when it is 42 or later) -> the version check does not stop asc.py
  3  strings: require_cryptography accepts 42.0.0, 42.0.0rc1, 43.0.0.dev1 and 50.0.1, and refuses 41.0.7,
              41.0.0.dev0 and 7.2
  4  others:  with 41.0.7, validate, stamp, frameworks, given-identity and upload are not stopped by the check
Mutants (each must fail its row; checked by hand when the test was written):
  M1  drop the call in the command-line block -> row 1
  M4  guard every command -> row 4
  M2  compare the minor version instead of the major -> rows 2 and 3
  M3  refuse 42 as well (< to <=) -> row 3
"""
import ast
import subprocess
import sys
import unittest
from pathlib import Path

ASC = Path(__file__).resolve().parents[1] / "tools/asc.py"
NAMES = {"require_cryptography", "die"}
CONSTANTS = {"MIN_CRYPTOGRAPHY"}

try:
    import cryptography
    INSTALLED = int(cryptography.__version__.split(".")[0])
except ImportError:
    INSTALLED = 0

# Runs the real asc.py as a command, with the cryptography module reporting the version given as argument.
DRIVER = """import cryptography, runpy, sys
cryptography.__version__ = sys.argv[1]
sys.argv = ["asc.py", sys.argv[2], "no-such-input", "no-such-output"]
runpy.run_path(%r, run_name="__main__")
""" % str(ASC)


def namespace():
    tree = ast.parse(ASC.read_text())
    keep = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name in NAMES)
            or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in CONSTANTS for t in n.targets))]
    if {n.name for n in keep if isinstance(n, ast.FunctionDef)} != NAMES:
        raise AssertionError("asc.py must contain require_cryptography and die")
    ns: dict = {"sys": sys}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "asc-cryptography.py", "exec"), ns)
    return ns


def run_cli(version, command):
    return subprocess.run([sys.executable, "-c", DRIVER, version, command], capture_output=True, text=True, check=False)


class AscCryptography(unittest.TestCase):
    @unittest.skipUnless(INSTALLED >= 42, "needs cryptography 42 or later to import tools/asc.py here")
    def test_row_1_old_module_stops_with_a_plain_message(self):
        for command in ("identity", "test-identity"):
            done = run_cli("41.0.7", command)
            self.assertNotEqual(done.returncode, 0, command)
            for text in ("cryptography 42", "41.0.7", "pip install -U cryptography"):
                self.assertIn(text, done.stderr, command)
            self.assertNotIn("Traceback", done.stderr, command)

    @unittest.skipUnless(INSTALLED >= 42, "needs cryptography 42 or later")
    def test_row_2_installed_module_passes_the_check(self):
        for command in ("identity", "test-identity"):
            self.assertNotIn("needs cryptography", run_cli(cryptography.__version__, command).stderr, command)

    def test_row_3_version_strings(self):
        ns = namespace()
        for good in ("42.0.0", "42.0.0rc1", "43.0.0.dev1", "50.0.1"):
            ns["require_cryptography"](good)
        for bad in ("41.0.7", "41.0.0.dev0", "7.2"):
            with self.assertRaises(SystemExit, msg=bad) as caught:
                ns["require_cryptography"](bad)
            self.assertIn(bad, str(caught.exception))

    @unittest.skipUnless(INSTALLED >= 42, "needs cryptography 42 or later to import tools/asc.py here")
    def test_row_4_other_commands_are_not_stopped(self):
        for command in ("validate", "stamp", "frameworks", "given-identity", "upload"):
            self.assertNotIn("needs cryptography", run_cli("41.0.7", command).stderr, command)


if __name__ == "__main__":
    unittest.main()
