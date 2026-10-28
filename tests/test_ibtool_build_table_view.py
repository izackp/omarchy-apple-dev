"""tools/ibtool build_table_view: the refusal paths.

The self-test and the corpus in tests/ibtool/src never reach the refusal
branches of <tableView> building, so each one is pinned here with a small
storyboard that triggers it. The expected text is the XibError message the
compiler prints, verbatim.
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
sys.path.insert(0, str(TOOLS))


def _load_ibtool():
    loader = SourceFileLoader("ibtool", str(TOOLS / "ibtool"))
    spec = importlib.util.spec_from_loader("ibtool", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


IBTOOL = _load_ibtool()

STORYBOARD = """<?xml version="1.0" encoding="UTF-8"?>
<document type="com.apple.InterfaceBuilder3.CocoaTouch.Storyboard.XIB" version="3.0" toolsVersion="24506" targetRuntime="iOS.CocoaTouch" propertyAccessControl="none" useAutolayout="YES" useTraitCollections="YES" useSafeAreas="YES" colorMatched="YES">
    <device id="retina6_1" orientation="portrait" appearance="light"/>
    <scenes>
        <scene sceneID="sc-1">
            <objects>
                <tableViewController storyboardIdentifier="TVC" id="vc-1" sceneMemberID="viewController">
%s
                </tableViewController>
            </objects>
        </scene>
    </scenes>
</document>
"""

TABLE = """<tableView key="view" clipsSubviews="YES" contentMode="scaleToFill" alwaysBounceVertical="YES" dataMode="%s" style="%s" separatorStyle="%s" rowHeight="-1" estimatedRowHeight="-1" sectionHeaderHeight="18" sectionFooterHeight="18" id="tv-1">
    <rect key="frame" x="0.0" y="0.0" width="414" height="896"/>
    <autoresizingMask key="autoresizingMask" widthSizable="YES" heightSizable="YES"/>
%s
</tableView>"""

STATIC_CELL = """<sections>
    <tableViewSection headerTitle="Section" id="sec-1">
        <cells>
            <tableViewCell clipsSubviews="YES" contentMode="scaleToFill" preservesSuperviewLayoutMargins="YES" selectionStyle="none" indentationWidth="10" style="IBUITableViewCellStyleDefault" id="cell-1">
                <rect key="frame" x="0.0" y="0.0" width="414" height="44"/>
                <autoresizingMask key="autoresizingMask" flexibleMaxX="YES" flexibleMaxY="YES"/>
                <tableViewCellContentView key="contentView" opaque="NO" clipsSubviews="YES" multipleTouchEnabled="YES" contentMode="center" preservesSuperviewLayoutMargins="YES" tableViewCell="cell-1" id="cv-1">
                    <rect key="frame" x="0.0" y="0.0" width="414" height="44"/>
                    <autoresizingMask key="autoresizingMask"/>
                    <subviews>
                        <button opaque="NO" contentMode="scaleToFill" contentHorizontalAlignment="center" contentVerticalAlignment="center" buttonType="system" lineBreakMode="middleTruncation" translatesAutoresizingMaskIntoConstraints="NO" id="btn-1">
                            <rect key="frame" x="0.0" y="0.0" width="414" height="44"/>
                            <connections>
                                <action selector="tapped:" destination="vc-1" eventType="%s" id="act-1"/>
                            </connections>
                        </button>
                    </subviews>
                </tableViewCellContentView>
            </tableViewCell>
        </cells>
    </tableViewSection>
</sections>"""

FOOTER = """<view key="tableFooterView" contentMode="scaleToFill" id="foot-1">
    <rect key="frame" x="0.0" y="852" width="414" height="44"/>
    <autoresizingMask key="autoresizingMask" flexibleMaxX="YES" flexibleMaxY="YES"/>
    <subviews>
        <button opaque="NO" contentMode="scaleToFill" contentHorizontalAlignment="center" contentVerticalAlignment="center" buttonType="system" lineBreakMode="middleTruncation" translatesAutoresizingMaskIntoConstraints="NO" id="fbtn-1">
            <rect key="frame" x="0.0" y="0.0" width="414" height="44"/>
            <connections>
                <action selector="tapped:" destination="vc-1" eventType="%s" id="fact-1"/>
            </connections>
        </button>
    </subviews>
</view>"""


class TableViewRefusals(unittest.TestCase):
    """Each case is one storyboard whose table view trips one refusal."""

    def compile_error(self, table_xml):
        """Compile one storyboard; return the XibError message."""
        with tempfile.TemporaryDirectory() as tmp:
            story = os.path.join(tmp, "t.storyboard")
            with open(story, "w") as fh:
                fh.write(STORYBOARD % table_xml)
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                IBTOOL.compile_storyboard("t.storyboard", "out")
            except IBTOOL.XibError as e:
                return str(e)
            finally:
                os.chdir(cwd)
        self.fail("the table view was accepted")

    def table(self, data_mode="static", style="insetGrouped", separator="default", body=""):
        return TABLE % (data_mode, style, separator, body)

    def test_unsupported_attribute(self):
        table = self.table().replace('id="tv-1"', 'bogusAttr="x" id="tv-1"')
        self.assertEqual(
            self.compile_error(table),
            "<tableView> has unsupported attribute(s) ['bogusAttr'] (t.storyboard)")

    def test_unsupported_style(self):
        self.assertEqual(
            self.compile_error(self.table(style="plain")),
            "<tableView> style='plain' not supported (t.storyboard)")

    def test_unsupported_separator_style(self):
        self.assertEqual(
            self.compile_error(self.table(separator="none")),
            "<tableView> separatorStyle='none' not supported (t.storyboard)")

    def test_unsupported_child(self):
        table = self.table().replace(
            "</tableView>", '        <foo id="foo-1"/>\n</tableView>')
        self.assertEqual(
            self.compile_error(table),
            "<tableView> has unsupported <foo> (t.storyboard)")

    def test_unsupported_outlet(self):
        body = ('        <connections>\n'
                '            <outlet property="selection" destination="vc-1" id="out-1"/>\n'
                '        </connections>\n')
        self.assertEqual(
            self.compile_error(self.table(body=body)),
            "<tableView> outlet 'selection' not supported (t.storyboard)")

    def test_footer_action_event_type(self):
        self.assertEqual(
            self.compile_error(self.table(body=FOOTER % "touchDown")),
            "footer action eventType 'touchDown' not supported (t.storyboard)")

    def test_section_attributes_with_header_title(self):
        body = """<sections>
    <tableViewSection headerTitle="Section" bogus="1" id="sec-1">
        <cells/>
    </tableViewSection>
</sections>"""
        self.assertEqual(
            self.compile_error(self.table(body=body)),
            "<tableViewSection> unsupported attributes (t.storyboard)")

    def test_section_attributes_without_header_title(self):
        body = """<sections>
    <tableViewSection bogus="1" id="sec-1">
        <cells/>
    </tableViewSection>
</sections>"""
        self.assertEqual(
            self.compile_error(self.table(body=body)),
            "<tableViewSection> unsupported attributes (t.storyboard)")

    def test_cell_action_event_type(self):
        self.assertEqual(
            self.compile_error(self.table(body=STATIC_CELL % "touchDown")),
            "action eventType 'touchDown' not supported at cell 'cell-1' (t.storyboard)")

    def test_prototypes_with_sections(self):
        body = """<sections>
    <tableViewSection id="sec-1">
        <cells/>
    </tableViewSection>
</sections>"""
        self.assertEqual(
            self.compile_error(self.table(data_mode="prototypes", body=body)),
            "<tableView> prototypes with sections not supported (t.storyboard)")

    def test_unsupported_data_mode(self):
        self.assertEqual(
            self.compile_error(self.table(data_mode="freeform")),
            "<tableView> dataMode='freeform' not supported (t.storyboard)")


if __name__ == "__main__":
    unittest.main()
