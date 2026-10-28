"""Uncovered paths of tools/ibtool _scene_element.

The corpus in tests/ibtool/src compiles only valid scene views, so the
unsupported-subview refusal and the insetsLayoutMarginsFromSafeArea key have
no coverage there. These tests pin the exact XibError message (compiled
through the CLI, as a user would hit it) and the presence and value of the
insets key in our own compiled output (characterization of our output, not
Apple's).
"""
import importlib.machinery
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
IBTOOL = TOOLS / "ibtool"
FLAGS = ["--target-device", "iphone", "--target-device", "ipad",
         "--minimum-deployment-target", "17.0"]
sys.path.insert(0, str(TOOLS))
import nibarchive  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("ibtool_module", str(IBTOOL))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
ibtool = importlib.util.module_from_spec(_spec)
_loader.exec_module(ibtool)

SB = """<?xml version="1.0" encoding="UTF-8"?>
<document type="com.apple.InterfaceBuilder3.CocoaTouch.Storyboard.XIB" version="3.0" targetRuntime="iOS.CocoaTouch" useAutolayout="YES">
    <scenes>
        <scene sceneID="s0">
            <objects>
                <viewController id="vc1" sceneMemberID="viewController">
                    <view key="view" contentMode="scaleToFill" id="v1"{view_extra}>
                        <rect key="frame" x="0.0" y="0.0" width="375" height="667"/>
                        <autoresizingMask key="autoresizingMask" widthSizable="YES" heightSizable="YES"/>
                        <subviews>{subviews}</subviews>
                    </view>
                </viewController>
                <placeholder placeholderIdentifier="IBFirstResponder" id="p1" userLabel="First Responder" sceneMemberID="firstResponder"/>
            </objects>
        </scene>
    </scenes>
</document>"""


def compile_storyboard(xml, work):
    sb = work / "Main.storyboard"
    sb.write_text(xml)
    p = subprocess.run([sys.executable, str(IBTOOL), *FLAGS,
                        "--compilation-directory", str(work / "out"), str(sb)],
                       capture_output=True, text=True, timeout=300)
    return p.returncode, p.stderr


class SceneElementRefusals(unittest.TestCase):
    def test_unsupported_subview_tag(self):
        sub = ('<stackView opaque="NO" contentMode="scaleToFill" id="x1"/>')
        xml = SB.format(view_extra="", subviews=sub)
        with tempfile.TemporaryDirectory() as tmp:
            rc, err = compile_storyboard(xml, Path(tmp))
        self.assertEqual(rc, 1, err)
        self.assertEqual(err.strip(),
                         "error: ibtool: unsupported subview <stackView>"
                         " (Main.storyboard)")


class SceneElementInsetsKey(unittest.TestCase):
    def test_insets_layout_margins_from_safe_area(self):
        xml = SB.format(view_extra=' insetsLayoutMarginsFromSafeArea="YES"',
                        subviews="")
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            rc, err = compile_storyboard(xml, work)
            self.assertEqual(rc, 0, err)
            nib = work / "out" / "Main.storyboardc" / "vc1-view-v1.nib"
            a = nibarchive.parse(nib.read_bytes())
        views = []
        for i, _ in enumerate(a.objects):
            vals = {a.key(v.key_idx): v for v in a.object_values(a.objects[i])}
            if "UIBounds" in vals:
                views.append(vals)
        self.assertEqual(len(views), 1)
        self.assertIn("UIViewInsetsLayoutMarginsFromSafeArea", views[0])
        self.assertEqual(a.render(views[0]["UIViewInsetsLayoutMarginsFromSafeArea"]),
                         "int 0")


if __name__ == "__main__":
    unittest.main()
