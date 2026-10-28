"""Refusal and event-connection paths of tools/ibtool _scene_view_nib.

The corpus in tests/ibtool/src only exercises valid scene views, so the
<layoutGuides> refusals, the unroutable-outlet refusals, and the whole
UIRuntimeEventConnection block have no coverage there. These tests pin the
exact XibError messages (compiled through the CLI, as a user would hit them)
and the event-connection bytes.

A plain-view scene never produces routed actions: _scene_element drops the
events of subviews and only a <button> root can return any, and the storyboard
caller only routes <view>/<collectionView> roots. The event-connection tests
therefore call _scene_view_nib directly with _scene_element stubbed to return
one routed action; everything downstream (connection bytes) is real.
"""
import importlib.machinery
import importlib.util
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
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
                <viewController id="vc1" sceneMemberID="viewController">{vc_extra}{view_el}
                </viewController>
                <placeholder placeholderIdentifier="IBFirstResponder" id="p1" userLabel="First Responder" sceneMemberID="firstResponder"/>
            </objects>
        </scene>
    </scenes>
</document>"""

VIEW = """
                    <view key="view" contentMode="scaleToFill" id="v1">
                        <rect key="frame" x="0.0" y="0.0" width="375" height="667"/>
                        <autoresizingMask key="autoresizingMask" widthSizable="YES" heightSizable="YES"/>
                        <subviews>{subviews}</subviews>
                    </view>"""

SLIDER = """
                            <slider opaque="NO" contentMode="scaleToFill" id="sl1">
                                <rect key="frame" x="8.0" y="8.0" width="100" height="31"/>
                                <autoresizingMask key="autoresizingMask" flexibleMaxX="YES" flexibleMaxY="YES"/>
                                <connections>
                                    <outlet property="{prop}" destination="{dst}" id="c1"/>
                                </connections>
                            </slider>"""

LEGACY = """
                    <layoutGuides>
                        <viewControllerLayoutGuide type="top" id="gT"/>
                        <viewControllerLayoutGuide type="bottom" id="gB"/>
                    </layoutGuides>"""

LEGACY_BAD = """
                    <layoutGuides>
                        <viewControllerLayoutGuide type="top" id="gT"/>
                        <stackView opaque="NO" contentMode="scaleToFill" id="x1"/>
                    </layoutGuides>"""


def compile_storyboard(xml, work):
    sb = work / "Main.storyboard"
    sb.write_text(xml)
    p = subprocess.run([sys.executable, str(IBTOOL), *FLAGS,
                        "--compilation-directory", str(work / "out"), str(sb)],
                       capture_output=True, text=True, timeout=300)
    return p.returncode, p.stderr


class SceneViewNibRefusals(unittest.TestCase):
    def check(self, xml, message):
        with tempfile.TemporaryDirectory() as tmp:
            rc, err = compile_storyboard(xml, Path(tmp))
        self.assertEqual(rc, 1, err)
        self.assertEqual(err.strip(), f"error: ibtool: {message}")

    def test_layout_guides_on_collection_view_scene(self):
        cv = """
                    <collectionView key="view" clipsSubviews="YES" multipleTouchEnabled="YES" contentMode="scaleToFill" dataMode="prototypes" id="cv1">
                        <rect key="frame" x="0.0" y="0.0" width="375" height="667"/>
                        <autoresizingMask key="autoresizingMask"/>
                    </collectionView>"""
        self.check(SB.format(vc_extra=LEGACY, view_el=cv),
                   "<layoutGuides> on a <collectionView> scene not supported"
                   " (Main.storyboard)")

    def test_layout_guides_unsupported_children(self):
        self.check(SB.format(vc_extra=LEGACY_BAD, view_el=VIEW.format(subviews="")),
                   "<layoutGuides> has unsupported ['stackView'] (Main.storyboard)")

    def test_outlet_needs_upstream_placeholder(self):
        # The outlet targets the top layout guide: its id lands in the nib's
        # id map but never creates an upstream placeholder.
        vc_outlet = """
                    <connections>
                        <outlet property="topGuide" destination="gT" id="c9"/>
                    </connections>"""
        self.check(SB.format(vc_extra=LEGACY + vc_outlet,
                             view_el=VIEW.format(subviews="")),
                   "outlet 'topGuide' needs an upstream placeholder"
                   " (Main.storyboard)")

    def test_subview_outlet_to_view_controller_unroutable(self):
        self.check(SB.format(vc_extra="",
                             view_el=VIEW.format(
                                 subviews=SLIDER.format(prop="value", dst="vc1"))),
                   "outlet 'value' unroutable (Main.storyboard)")

    def test_subview_outlet_foreign_destination(self):
        self.check(SB.format(vc_extra="",
                             view_el=VIEW.format(
                                 subviews=SLIDER.format(prop="value", dst="zzz"))),
                   "unroutable outlet {'property': 'value', 'destination': 'zzz',"
                   " 'id': 'c1'} (Main.storyboard)")


def stub_events(events):
    """Replace _scene_element with one returning a fixed-view stub and events.
    A None source in an event stands for the stub's own view object."""
    def fake(sb, el, where, resources, named, id_map, state):
        v = sb.b.new("UIView")
        if el.get("id"):
            id_map[el.get("id")] = v
        return v, [], [(sel, mask, v if src is None else src, dst)
                       for (sel, mask, src, dst) in events]
    return fake


class SceneViewNibEvents(unittest.TestCase):
    def setUp(self):
        self.orig = ibtool._scene_element

    def tearDown(self):
        ibtool._scene_element = self.orig

    def scene(self, events):
        # The keyed view carries an action to the VC so the scene has an
        # upstream placeholder; actions add no outlet to the routing loop.
        vc = ET.fromstring(
            "<viewController id=\"vc1\">"
            "<connections><outlet property=\"delegate\" destination=\"v1\" id=\"c1\"/>"
            "</connections>"
            "<view key=\"view\" id=\"v1\"><connections>"
            "<action selector=\"tapped:\" destination=\"vc1\" id=\"a1\"/>"
            "</connections></view></viewController>")
        root = ET.fromstring(
            "<view contentMode=\"scaleToFill\" id=\"v1\">"
            "<rect key=\"frame\" x=\"0.0\" y=\"0.0\" width=\"10.0\" height=\"10.0\"/>"
            "<autoresizingMask key=\"autoresizingMask\"/></view>")
        ibtool._scene_element = stub_events(events)
        return ibtool._scene_view_nib(vc, root, "Main.storyboard", {}, None)

    def test_action_unroutable(self):
        with self.assertRaises(ibtool.XibError) as cm:
            self.scene([("tapped:", 64, None, "other")])
        self.assertEqual(str(cm.exception),
                         "action 'tapped:' unroutable in scene view"
                         " (Main.storyboard)")

    def test_action_connection_bytes(self):
        data = self.scene([("tapped:", 64, None, "vc1")])
        a = nibarchive.parse(data)
        conns = [i for i, _ in enumerate(a.objects)
                 if a.object_class(i) == "UIRuntimeEventConnection"]
        self.assertEqual(len(conns), 1)
        vals = {a.key(v.key_idx): v for v in a.object_values(a.objects[conns[0]])}
        self.assertTrue(a.render(vals["UILabel"]).startswith("string 'tapped:'"))
        self.assertEqual(a.render(vals["UIEventMask"]), "int 64")
        dst = vals["UIDestination"].payload
        self.assertEqual(a.object_class(dst), "UIProxyObject")
        proxy = {a.key(v.key_idx): v for v in a.object_values(a.objects[dst])}
        self.assertTrue(a.render(proxy["UIProxiedObjectIdentifier"])
                        .startswith("string 'UpstreamPlaceholder-1'"))
        outlets = {i for i, _ in enumerate(a.objects)
                   if a.object_class(i) == "UIRuntimeOutletConnection"}
        labels = []
        for i in sorted(outlets):
            ov = {a.key(v.key_idx): v for v in a.object_values(a.objects[i])}
            labels.append(a.render(ov["UILabel"]).split(" -> ")[0])
        self.assertEqual(labels, ["string 'delegate'", "string 'view'"])


if __name__ == "__main__":
    unittest.main()
