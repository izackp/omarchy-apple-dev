"""Refusal paths of tools/ibtool `_scene_nav_nib`, the navigation-controller
scene without a storyboardIdentifier (corpus: the ShareActionExtension
MainInterface storyboard, which compiles clean). Each case compiles a variant
of that storyboard with one attribute or child changed and checks the exact
XibError text."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
SRC = HERE / "ibtool" / "src" / "ShareActionExtension" / "Base.lproj" / "MainInterface.storyboard"
FLAGS = ["--target-device", "iphone", "--target-device", "ipad",
         "--minimum-deployment-target", "17.0"]


def compile_variant(xml):
    """(returncode, stderr) for one storyboard text compiled through the CLI."""
    with tempfile.TemporaryDirectory() as tmp:
        sb = Path(tmp, "Variant.storyboard")
        sb.write_text(xml)
        p = subprocess.run([sys.executable, str(TOOLS / "ibtool"), *FLAGS,
                            "--compilation-directory", str(Path(tmp, "out")), str(sb)],
                           capture_output=True, text=True, timeout=300)
        return p.returncode, p.stderr


class SceneNavNib(unittest.TestCase):
    def variant(self, old, new):
        xml = SRC.read_text()
        self.assertEqual(xml.count(old), 1, f"anchor not unique: {old}")
        return xml.replace(old, new)

    def assert_refused(self, xml, message):
        rc, err = compile_variant(xml)
        self.assertEqual(rc, 1, err)
        self.assertEqual(err.strip(), f"error: ibtool: {message}")

    def test_navigation_controller_extra_attribute(self):
        self.assert_refused(
            self.variant('navigationController automaticallyAdjustsScrollViewInsets="NO"',
                         'navigationController automaticallyAdjustsScrollViewInsets="NO"'
                         ' extendedLayoutIncludesOpaqueBars="YES"'),
            "<navigationController> attributes {'automaticallyAdjustsScrollViewInsets': 'NO',"
            " 'extendedLayoutIncludesOpaqueBars': 'YES', 'id': 'jKX-MD-OMs',"
            " 'sceneMemberID': 'viewController'} not supported (Variant.storyboard)")

    def test_automatically_adjusts_scroll_view_insets_yes(self):
        self.assert_refused(
            self.variant('automaticallyAdjustsScrollViewInsets="NO"',
                         'automaticallyAdjustsScrollViewInsets="YES"'),
            "<navigationController> attributes {'automaticallyAdjustsScrollViewInsets': 'YES',"
            " 'id': 'jKX-MD-OMs', 'sceneMemberID': 'viewController'} not supported"
            " (Variant.storyboard)")

    def test_navigation_bar_not_plain(self):
        self.assert_refused(
            self.variant(' contentMode="scaleToFill" id="ERE-s0-7dc"',
                         ' id="ERE-s0-7dc"'),
            "<navigationController> needs a plain navigationBar (Variant.storyboard)")

    def test_navigation_bar_without_frame(self):
        self.assert_refused(
            self.variant('<rect key="frame" x="0.0" y="44" width="414" height="44"/>\n                        ', ""),
            "<navigationBar> frame/autoresizingMask not supported (Variant.storyboard)")

    def test_navigation_bar_autoresizing_mask_with_attributes(self):
        self.assert_refused(
            self.variant('<autoresizingMask key="autoresizingMask"/>\n                    </navigationBar>',
                         '<autoresizingMask key="autoresizingMask" widthSizable="YES"/>'
                         '\n                    </navigationBar>'),
            "<navigationBar> frame/autoresizingMask not supported (Variant.storyboard)")

    def test_navigation_controller_extra_child(self):
        self.assert_refused(
            self.variant('<nil name="viewControllers"/>',
                         '<nil name="viewControllers"/>\n                    <view key="view" id="extra-view"/>'),
            "<navigationController>/root navigationItem shape not supported (Variant.storyboard)")

    def test_toolbar_items_with_a_child(self):
        self.assert_refused(
            self.variant('<toolbarItems/>',
                         '<toolbarItems>\n                        <barButtonItem id="tb-1"/>'
                         '\n                    </toolbarItems>'),
            "<navigationController>/root navigationItem shape not supported (Variant.storyboard)")

    def test_root_navigation_item_with_a_child(self):
        self.assert_refused(
            self.variant('<navigationItem key="navigationItem" id="uKc-xP-YOE"/>',
                         '<navigationItem key="navigationItem" id="uKc-xP-YOE">\n'
                         '                        <barButtonItem key="rightBarButtonItem" systemItem="done"'
                         ' id="btn-1"/>\n                    </navigationItem>'),
            "<navigationController>/root navigationItem shape not supported (Variant.storyboard)")

    def test_root_navigation_item_extra_attribute(self):
        self.assert_refused(
            self.variant('<navigationItem key="navigationItem" id="uKc-xP-YOE"/>',
                         '<navigationItem key="navigationItem" title="Root" id="uKc-xP-YOE"/>'),
            "<navigationController>/root navigationItem shape not supported (Variant.storyboard)")


if __name__ == "__main__":
    unittest.main()
