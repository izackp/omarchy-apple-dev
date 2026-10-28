"""Refusal paths and the no-runtime-attributes branch of the splitViewController
scene nib in tools/ibtool. Each case compiles a variant of the NetNewsWire
Main.storyboard split scene (tests/ibtool/src, RootSplit golden) with one
attribute or child changed, and checks the exact XibError text or the bytes."""
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
SRC = HERE / "ibtool" / "src" / "NetNewsWire" / "Base.lproj" / "Main.storyboard"
FLAGS = ["--module", "NetNewsWire", "--target-device", "iphone",
         "--target-device", "ipad", "--minimum-deployment-target", "17.0"]


def compile_variant(xml):
    """(returncode, stderr, split-nib bytes or None) for one storyboard text."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp, "NetNewsWire")
        src.mkdir()
        (src / "Variant.storyboard").write_text(xml)
        out = Path(tmp, "out")
        p = subprocess.run([sys.executable, str(TOOLS / "ibtool"), *FLAGS,
                            "--compilation-directory", str(out),
                            str(src / "Variant.storyboard")],
                           capture_output=True, text=True, timeout=280)
        nib = out / "Variant.storyboardc" / "RootSplitViewController.nib"
        return p.returncode, p.stderr, nib.read_bytes() if nib.exists() else None


class SceneSplitNib(unittest.TestCase):
    def variant(self, old, new):
        xml = SRC.read_text()
        self.assertEqual(xml.count(old), 1, f"anchor not unique: {old}")
        return xml.replace(old, new)

    def assert_refused(self, xml, message):
        rc, err, _ = compile_variant(xml)
        self.assertNotEqual(rc, 0)
        self.assertIn(f"error: ibtool: {message}", err)

    def test_extended_layout_includes_opaque_bars(self):
        self.assert_refused(
            self.variant('storyboardIdentifier="RootSplitViewController" extendedLayoutIncludesOpaqueBars="YES"',
                         'storyboardIdentifier="RootSplitViewController" extendedLayoutIncludesOpaqueBars="NO"'),
            "extendedLayoutIncludesOpaqueBars unsupported (Variant.storyboard)")

    def test_allow_double_column_style(self):
        self.assert_refused(
            self.variant('allowDoubleColumnStyle="YES"',
                         'allowDoubleColumnStyle="NO"'),
            "allowDoubleColumnStyle unsupported (Variant.storyboard)")

    def test_primary_background_style(self):
        self.assert_refused(
            self.variant('primaryBackgroundStyle="sidebar"',
                         'primaryBackgroundStyle="plain"'),
            "primaryBackgroundStyle='plain' unsupported (Variant.storyboard)")

    def test_preferred_display_mode(self):
        self.assert_refused(
            self.variant('preferredDisplayMode="beside"',
                         'preferredDisplayMode="automatic"'),
            "preferredDisplayMode='automatic' unsupported (Variant.storyboard)")

    def test_behavior(self):
        self.assert_refused(
            self.variant('behavior="tile"', 'behavior="automatic"'),
            "behavior='automatic' unsupported (Variant.storyboard)")

    def test_action_destination_outside_scene(self):
        self.assert_refused(
            self.variant('<action selector="markAllAsRead:" destination="QJM-al-rDe" id="wU6-dU-tbq"/>',
                         '<action selector="markAllAsRead:" destination="AJQ-jq-uMa" id="wU6-dU-tbq"/>'),
            "action destination outside scene (Variant.storyboard)")

    def test_no_runtime_attributes(self):
        """Without user-defined runtime attributes the split nib stores an
        empty UINibKeyValuePairsKey (the kvp=None branch). The hash is
        characterization of our output, not Apple's."""
        xml = SRC.read_text()
        while "<userDefinedRuntimeAttributes>" in xml:
            start = xml.index("<userDefinedRuntimeAttributes>")
            end = xml.index("</userDefinedRuntimeAttributes>") + len("</userDefinedRuntimeAttributes>")
            xml = xml[:start] + xml[end:]
        rc, err, data = compile_variant(xml)
        self.assertEqual(rc, 0, err)
        self.assertIsNotNone(data)
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         "56b5f5f0613c0e8f72da38ad23553cb9ff85e6d7a979ab3758afa5bfc4ae2e31")


    def test_no_custom_class(self):
        """Without customClass the split stores the plain UISplitViewController
        class name. The hash is characterization of our output, not Apple's."""
        old = ('customClass="RootSplitViewController" customModule="NetNewsWire"'
               ' customModuleProvider="target"')
        rc, err, data = compile_variant(self.variant(old, ""))
        self.assertEqual(rc, 0, err)
        self.assertIsNotNone(data)
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         "8488419da5a18415bdad3e52560a5a8269fddf475ad248ee2cb3ff838e4bbe62")


if __name__ == "__main__":
    unittest.main()
