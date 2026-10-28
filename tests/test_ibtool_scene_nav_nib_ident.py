"""tools/ibtool `_scene_nav_nib_ident`: the navigation-controller scene nib
that carries a storyboardIdentifier (oracle: SettingsNavigationViewController.nib).

The refusals this pins are the ones the corpus in tests/ibtool/src never
reaches, so nothing else in the tree holds them in place. The compiled shape
is checked against the decoded values of the nib the function returns."""
import importlib.machinery
import importlib.util
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"

_loader = importlib.machinery.SourceFileLoader("ibtool", str(TOOLS / "ibtool"))
_spec = importlib.util.spec_from_loader("ibtool", _loader)
ibtool = importlib.util.module_from_spec(_spec)
_loader.exec_module(ibtool)

NAV_BODY = """                    <navigationBar key="navigationBar" contentMode="scaleToFill" id="bar-id"{bar_attrs}>
                        <rect key="frame" x="0.0" y="96" width="414" height="54"/>
                        <autoresizingMask key="autoresizingMask"/>
                    </navigationBar>
                    <connections>
                        <segue destination="root-id" kind="relationship" relationship="rootViewController" id="seg-root"/>
                    </connections>"""

ROOT_ITEM = """                    <navigationItem key="navigationItem" title="Root" id="item-root-id"/>"""

STORYBOARD = """<?xml version="1.0" encoding="UTF-8"?>
<document type="com.apple.InterfaceBuilder3.CocoaTouch.Storyboard.XIB" version="3.0" toolsVersion="24765" targetRuntime="iOS.CocoaTouch" initialViewController="nav-id">
    <scenes>
        <scene sceneID="nav-scene">
            <objects>
                <navigationController storyboardIdentifier="NavIdent" id="nav-id" sceneMemberID="viewController">
{body}
                </navigationController>
                <placeholder placeholderIdentifier="IBFirstResponder" id="fr-nav" userLabel="First Responder" sceneMemberID="firstResponder"/>
            </objects>
            <point key="canvasLocation" x="0" y="0"/>
        </scene>
        <scene sceneID="root-scene">
            <objects>
                <viewController id="root-id" sceneMemberID="viewController"{root_attrs}>
                    <view key="view" contentMode="scaleToFill" id="root-view">
                        <rect key="frame" x="0.0" y="0.0" width="414" height="896"/>
                        <autoresizingMask key="autoresizingMask" widthSizable="YES" heightSizable="YES"/>
                    </view>
{item}
                </viewController>
                <placeholder placeholderIdentifier="IBFirstResponder" id="fr-root-id" userLabel="First Responder" sceneMemberID="firstResponder"/>
            </objects>
            <point key="canvasLocation" x="400" y="0"/>
        </scene>
    </scenes>
</document>
"""

WHERE = "nav.storyboard"


def build(body=None, item=None, bar_attrs="", root_attrs=""):
    """(navigationController, root viewController) elements of a minimal storyboard."""
    doc = ET.fromstring(STORYBOARD.format(body=body if body is not None else NAV_BODY.format(bar_attrs=bar_attrs),
                                           item=item if item is not None else ROOT_ITEM,
                                           root_attrs=root_attrs))
    return (doc.find("scenes/scene/objects/navigationController"),
            doc.find("scenes/scene/objects/viewController"))


def item_with(body):
    """A root <navigationItem> holding the given <barButtonItem> markup."""
    return ('                    <navigationItem key="navigationItem" title="Root" id="item-root-id">\n'
            + body
            + "\n                    </navigationItem>")


def bar_button_item(inner):
    return ('                        <barButtonItem key="rightBarButtonItem" systemItem="done" id="btn-id">\n'
            + inner
            + "\n                        </barButtonItem>")


class SceneNavNibIdent(unittest.TestCase):
    def compile(self, **kw):
        nav, root = build(**kw)
        return ibtool._scene_nav_nib_ident(nav, root, "root-id-view-root-view", WHERE)

    def refuses(self, message, **kw):
        with self.assertRaises(ibtool.XibError) as cm:
            self.compile(**kw)
        self.assertEqual(str(cm.exception), message)

    def decoded(self, **kw):
        """The (key, rendered value) pairs of the nib the function returns."""
        data, name = self.compile(**kw)
        self.assertEqual(name, "NavIdent")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "NavIdent.nib"
            path.write_bytes(data)
            archive = ibtool.N.load(str(path))
        return [(archive.key(v.key_idx), archive.render(v))
                for obj in archive.objects for v in archive.object_values(obj)]

    def get(self, values, key):
        return [v for k, v in values if k == key]

    def test_extra_navigation_controller_attribute(self):
        nav, root = build()
        nav.set("title", "Nav")
        with self.assertRaises(ibtool.XibError) as cm:
            ibtool._scene_nav_nib_ident(nav, root, "root-id-view-root-view", WHERE)
        self.assertEqual(
            str(cm.exception),
            "<navigationController> attributes {'storyboardIdentifier': 'NavIdent', "
            "'id': 'nav-id', 'sceneMemberID': 'viewController', 'title': 'Nav'} "
            "not supported (" + WHERE + ")")

    def test_navigation_bar_is_not_plain(self):
        body = NAV_BODY.format(bar_attrs="").replace(' contentMode="scaleToFill"', "")
        self.refuses("<navigationController> needs a plain navigationBar (" + WHERE + ")",
                     body=body)

    def test_navigation_bar_without_frame(self):
        body = NAV_BODY.format(bar_attrs="").replace(
            '<rect key="frame" x="0.0" y="96" width="414" height="54"/>', "")
        self.refuses("<navigationBar> frame/autoresizingMask not supported (" + WHERE + ")",
                     body=body)

    def test_navigation_bar_autoresizing_mask_with_attributes(self):
        body = NAV_BODY.format(bar_attrs="").replace(
            '<autoresizingMask key="autoresizingMask"/>',
            '<autoresizingMask key="autoresizingMask" widthSizable="YES"/>')
        self.refuses("<navigationBar> frame/autoresizingMask not supported (" + WHERE + ")",
                     body=body)

    def test_navigation_controller_extra_child(self):
        body = NAV_BODY.format(bar_attrs="").replace(
            "<connections>", '<view key="view" id="extra-view"/>\n                    <connections>')
        self.refuses("<navigationController> shape not supported (" + WHERE + ")", body=body)

    def test_navigation_controller_without_root_segue(self):
        body = NAV_BODY.format(bar_attrs="").replace(
            '<segue destination="root-id" kind="relationship" relationship="rootViewController" id="seg-root"/>', "")
        self.refuses("<navigationController> shape not supported (" + WHERE + ")", body=body)

    def test_bar_item_runtime_attributes(self):
        item = item_with(bar_button_item("""                            <userDefinedRuntimeAttributes>
                                <userDefinedRuntimeAttribute type="boolean" keyPath="enabled" value="YES"/>
                            </userDefinedRuntimeAttributes>"""))
        self.refuses(
            "bar item runtime attributes in a navigation scene unsupported (" + WHERE + ")",
            item=item)

    def test_bar_item_action_to_another_destination(self):
        item = item_with(bar_button_item("""                            <connections>
                                <action selector="doThing:" destination="other-id" id="act-1"/>
                            </connections>"""))
        self.refuses(
            "<barButtonItem> action {'selector': 'doThing:', 'destination': 'other-id', "
            "'id': 'act-1'} unsupported (" + WHERE + ")",
            item=item)

    def test_bar_item_action_with_event_type(self):
        item = item_with(bar_button_item("""                            <connections>
                                <action selector="doThing:" destination="root-id" eventType="touchUpInside" id="act-1"/>
                            </connections>"""))
        self.refuses(
            "<barButtonItem> action {'selector': 'doThing:', 'destination': 'root-id', "
            "'eventType': 'touchUpInside', 'id': 'act-1'} unsupported (" + WHERE + ")",
            item=item)

    def test_compiles_the_scene_nib(self):
        values = self.decoded()
        self.assertEqual(self.get(values, "UIBarPosition"), ["int 2"])
        self.assertEqual(self.get(values, "UIBarTranslucence"), ["int 1"])
        self.assertEqual(self.get(values, "UIIsLocked"), ["bool false"])
        self.assertEqual(self.get(values, "IBUINibLNEVersionKey"), ["int 1"])
        self.assertTrue(any(v.startswith("string '{250, 250}'")
                            for v in self.get(values, "UIViewContentHuggingPriority")))
        self.assertEqual(len(self.get(values, "UIClassName")), 2)
        self.assertEqual(len(self.get(values, "UIStoryboardIdentifier")), 2)
        self.assertTrue(any(v.startswith("string 'UINavigationController'")
                            for v in self.get(values, "UIClassName")))

    def test_clears_selection_on_view_will_appear(self):
        values = self.decoded(root_attrs=' clearsSelectionOnViewWillAppear="NO"')
        self.assertEqual(self.get(values, "UIClearsSelectionOnViewWillAppear"), ["bool true"])

    def test_navigation_bar_insets_layout_margins_from_safe_area(self):
        values = self.decoded(bar_attrs=' insetsLayoutMarginsFromSafeArea="NO"')
        self.assertEqual(self.get(values, "UIViewInsetsLayoutMarginsFromSafeArea"), ["int 0"])

    def test_bar_item_action_becomes_an_event_connection(self):
        item = item_with(bar_button_item("""                            <connections>
                                <action selector="doThing:" destination="root-id" id="act-1"/>
                            </connections>"""))
        values = self.decoded(item=item)
        self.assertTrue(any(v.startswith("string 'doThing:'")
                            for v in self.get(values, "UILabel")))


if __name__ == "__main__":
    unittest.main()
