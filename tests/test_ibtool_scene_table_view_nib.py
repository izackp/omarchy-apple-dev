"""The refusal path and the outlet order of the table view controller scene
nib in tools/ibtool.

The self-test and the corpus never reach the refusal for an outlet on a
table view controller whose destination is outside the scene's view, and
never tie an XML outlet with a cell outlet of the same label, where the
no-provenance rank decides the order. Both are pinned here with variants
of the NetNewsWire storyboards: the refusal by its exact message, the tie
by a hash of the view nib (characterization of our output, not Apple's).
"""
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
SRC = HERE / "ibtool" / "src" / "NetNewsWire"
FLAGS = ["--module", "NetNewsWire", "--target-device", "iphone",
         "--target-device", "ipad", "--minimum-deployment-target", "17.0"]
TIE_CELL = ('id="4Ue-UW-e0l" customClass="VibrantTableViewCell"'
            ' customModule="NetNewsWire" customModuleProvider="target">')


def compile_variant(xml, name="Variant.storyboard"):
    """(returncode, stderr, view nib bytes or None) for one storyboard text."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "NetNewsWire")
        src.mkdir()
        (src / name).write_text(xml)
        out = Path(tmp, "out")
        p = subprocess.run([sys.executable, str(TOOLS / "ibtool"), *FLAGS,
                            "--compilation-directory", str(out),
                            str(src / name)],
                           capture_output=True, text=True, timeout=280)
        nib = out / "Variant.storyboardc" / "1m3-fZ-n7g-view-xcc-i3-tPS.nib"
        return p.returncode, p.stderr, nib.read_bytes() if nib.exists() else None


class SceneTableViewNib(unittest.TestCase):
    def variant(self, old, new, source="Settings.storyboard"):
        xml = (SRC / source).read_text()
        self.assertEqual(xml.count(old), 1, f"anchor not unique: {old}")
        return xml.replace(old, new)

    def test_outlet_destination_outside_view(self):
        xml = self.variant(
            '<outlet property="articleThemeDetailLabel" destination="DUf-DZ-3Nm" id="QHN-Le-zyw"/>',
            '<outlet property="articleThemeDetailLabel" destination="out-of-scene" id="QHN-Le-zyw"/>')
        rc, err, _ = compile_variant(xml)
        self.assertNotEqual(rc, 0)
        self.assertEqual(
            err,
            "error: ibtool: Variant.storyboard: outlet 'articleThemeDetailLabel' "
            "destination 'out-of-scene' is not in this scene's view\n")

    def test_cell_outlet_label_tie(self):
        """A cell outlet with the same label as the controller's XML outlet
        sorts by the no-provenance rank: the XML outlet decodes first. The
        hash is characterization of our output, not Apple's."""
        xml = self.variant(
            TIE_CELL,
            TIE_CELL + '\n<connections><outlet property="nameTextField"'
                       ' destination="LUW-uv-piz" id="tie-cell-1"/></connections>',
            source="Inspector.storyboard")
        rc, err, data = compile_variant(xml)
        self.assertEqual(rc, 0, err)
        self.assertIsNotNone(data)
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         "5a400329d8fc8f78e8aa0551449e5f18e98c5185ad0987534fb693aa4783d822")


if __name__ == "__main__":
    unittest.main()
