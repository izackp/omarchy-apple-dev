"""The refusal path of the table view controller scene nib in tools/ibtool.

The self-test and the corpus never reach the refusal for an outlet on a
table view controller whose destination is outside the scene's view, so it
is pinned here with a variant of the NetNewsWire Settings storyboard that
changes one outlet destination and checks the exact XibError text.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
SRC = HERE / "ibtool" / "src" / "NetNewsWire" / "Settings.storyboard"
FLAGS = ["--module", "NetNewsWire", "--target-device", "iphone",
         "--target-device", "ipad", "--minimum-deployment-target", "17.0"]


def compile_variant(xml):
    """(returncode, stderr) for one storyboard text."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "NetNewsWire")
        src.mkdir()
        (src / "Variant.storyboard").write_text(xml)
        out = Path(tmp, "out")
        p = subprocess.run([sys.executable, str(TOOLS / "ibtool"), *FLAGS,
                            "--compilation-directory", str(out),
                            str(src / "Variant.storyboard")],
                           capture_output=True, text=True, timeout=280)
        return p.returncode, p.stderr


class SceneTableViewNib(unittest.TestCase):
    def variant(self, old, new):
        xml = SRC.read_text()
        self.assertEqual(xml.count(old), 1, f"anchor not unique: {old}")
        return xml.replace(old, new)

    def test_outlet_destination_outside_view(self):
        xml = self.variant(
            '<outlet property="articleThemeDetailLabel" destination="DUf-DZ-3Nm" id="QHN-Le-zyw"/>',
            '<outlet property="articleThemeDetailLabel" destination="out-of-scene" id="QHN-Le-zyw"/>')
        rc, err = compile_variant(xml)
        self.assertNotEqual(rc, 0)
        self.assertIn(
            "error: ibtool: Variant.storyboard: outlet 'articleThemeDetailLabel' "
            "destination 'out-of-scene' is not in this scene's view", err)


if __name__ == "__main__":
    unittest.main()
