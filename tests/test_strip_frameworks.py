"""flutter/tools/strip-frameworks.py on synthetic apps: thin and fat Mach-O images
with hand-written dylib load commands, no toolchain needed.

Rows (expected results written before the code):
  1  unused framework: removed, others kept, freed bytes reported
  2  hard load from Runner (LC_LOAD_DYLIB): refused, Runner named, nothing removed
  3  weak load (LC_LOAD_WEAK_DYLIB): removed
  4  load from another framework (LC_REEXPORT_DYLIB): refused, that framework named
  5  loads among the frameworks being removed only: both removed
  6  fat image hard-loading it in its second slice: refused
  7  absent name: reported, not an error
  8  bad names ('', '../x', '.hidden', 'A/B'): refused before anything is removed
  9  read-only framework (as SwiftPM products are): removed
  10 wired: flutter/build.sh runs the tool on the bundle before it zips Runner.ipa
  11 STRIP_FRAMEWORKS parsing in build.sh: spaces, commas and empty fields never
     hand the tool an empty name (the tool refuses one and would fail the build)
Mutants (each must fail its row; checked by hand when the test was written):
  M1  count LC_LOAD_WEAK_DYLIB as a hard load -> row 3
  M2  skip the fat header (read slice 0 at offset 0) -> row 6
  M3  do not skip images inside the frameworks being removed -> row 5
"""
import importlib.util
import os
import stat
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "flutter/tools/strip-frameworks.py"
spec = importlib.util.spec_from_file_location("strip_frameworks", TOOL)
strip_frameworks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strip_frameworks)

LOAD, WEAK, REEXPORT = 0xC, 0x80000018, 0x8000001F


def thin(loads):
    """A 64-bit arm64 Mach-O with one dylib command per (cmd, install name)."""
    cmds = b""
    for cmd, name in loads:
        raw = name.encode() + b"\0"
        size = (24 + len(raw) + 7) // 8 * 8
        cmds += struct.pack("<2I4I", cmd, size, 24, 2, 0x10000, 0x10000) + raw.ljust(size - 24, b"\0")
    header = struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 6, len(loads), len(cmds), 0, 0)
    return header + cmds


def fat(*slices):
    """A fat (32-bit header) Mach-O holding the given thin images, 16 KiB aligned."""
    head = struct.pack(">2I", 0xCAFEBABE, len(slices))
    offset, body, entries = 0x4000, b"", b""
    for s in slices:
        entries += struct.pack(">5I", 0x0100000C, 0, offset + len(body), len(s), 14)
        body += s.ljust((len(s) + 0x3FFF) // 0x4000 * 0x4000, b"\0")
    return (head + entries).ljust(0x4000, b"\0") + body


def rpath(name):
    return f"@rpath/{name}.framework/{name}"


class StripFrameworksTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = Path(tmp.name) / "Runner.app"
        (self.app / "Frameworks").mkdir(parents=True)
        (self.app / "Info.plist").write_bytes(b"not a Mach-O")

    def runner(self, loads):
        (self.app / "Runner").write_bytes(thin(loads))

    def framework(self, name, image=None):
        d = self.app / "Frameworks" / f"{name}.framework"
        d.mkdir()
        (d / name).write_bytes(image if image is not None else thin([]))
        (d / "Info.plist").write_bytes(b"x" * 2048)
        return d

    def strip(self, *names):
        return strip_frameworks.strip(self.app, list(names))

    def test_row1_unused_removed(self):
        self.runner([(LOAD, rpath("Flutter"))])
        self.framework("Flutter")
        self.framework("Unused")
        removed, absent, freed = self.strip("Unused")
        self.assertEqual((removed, absent), (["Unused"], []))
        self.assertGreater(freed, 2048)
        self.assertFalse((self.app / "Frameworks/Unused.framework").exists())
        self.assertTrue((self.app / "Frameworks/Flutter.framework").exists())

    def test_row2_hard_load_refused(self):
        self.runner([(LOAD, rpath("Agora"))])
        self.framework("Agora")
        with self.assertRaises(SystemExit) as e:
            self.strip("Agora")
        self.assertIn("loaded by Runner", str(e.exception.code))
        self.assertTrue((self.app / "Frameworks/Agora.framework").exists())

    def test_executable_relative_hard_load_refused(self):
        self.runner([(LOAD, "@executable_path/Frameworks/Required.framework/Required")])
        framework = self.framework("Required")
        with self.assertRaises(SystemExit) as error:
            self.strip("Required")
        self.assertIn("loaded by Runner", str(error.exception.code))
        self.assertTrue(framework.exists())

    def test_loader_relative_hard_load_refused(self):
        self.runner([])
        framework = self.framework("Required")
        self.framework("Plugin", thin([(LOAD, "@loader_path/../Required.framework/Required")]))
        with self.assertRaises(SystemExit) as error:
            self.strip("Required")
        self.assertIn("Frameworks/Plugin.framework/Plugin", str(error.exception.code))
        self.assertTrue(framework.exists())

    def test_extension_executable_path_uses_its_own_bundle(self):
        self.runner([])
        self.framework("Required")
        extension = self.app / "PlugIns/Share.appex"
        framework = extension / "Frameworks/Required.framework"
        framework.mkdir(parents=True)
        (framework / "Required").write_bytes(thin([]))
        (extension / "Share").write_bytes(
            thin([(LOAD, "@executable_path/Frameworks/Required.framework/Required")]))
        self.assertEqual(self.strip("Required")[0], ["Required"])
        self.assertTrue(framework.exists())

    def test_extension_hard_load_of_app_framework_refused(self):
        self.runner([])
        framework = self.framework("Required")
        extension = self.app / "PlugIns/Share.appex"
        extension.mkdir(parents=True)
        (extension / "Share").write_bytes(
            thin([(LOAD, "@executable_path/../../Frameworks/Required.framework/Required")]))
        with self.assertRaises(SystemExit) as error:
            self.strip("Required")
        self.assertIn("PlugIns/Share.appex/Share", str(error.exception.code))
        self.assertTrue(framework.exists())

    def test_row3_weak_load_removed(self):
        self.runner([(WEAK, rpath("Optional"))])
        self.framework("Optional")
        self.assertEqual(self.strip("Optional")[0], ["Optional"])

    def test_row4_load_from_framework_refused(self):
        self.runner([])
        self.framework("Base")
        self.framework("Plugin", thin([(REEXPORT, rpath("Base"))]))
        with self.assertRaises(SystemExit) as e:
            self.strip("Base")
        self.assertIn("Frameworks/Plugin.framework/Plugin", str(e.exception.code))

    def test_row5_loads_among_removed(self):
        self.runner([])
        self.framework("Base")
        self.framework("Ext", thin([(LOAD, rpath("Base"))]))
        self.assertEqual(sorted(self.strip("Base", "Ext")[0]), ["Base", "Ext"])

    def test_row6_fat_second_slice(self):
        (self.app / "Runner").write_bytes(fat(thin([]), thin([(LOAD, rpath("Agora"))])))
        self.framework("Agora")
        with self.assertRaises(SystemExit):
            self.strip("Agora")

    def test_row7_absent_skipped(self):
        self.runner([])
        self.assertEqual(self.strip("Missing")[:2], ([], ["Missing"]))

    def test_row8_bad_names(self):
        self.runner([])
        keep = self.framework("Keep")
        for bad in ("", "../x", ".hidden", "A/B"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                self.strip("Keep", bad)
            self.assertTrue(keep.exists())

    def test_row9_read_only(self):
        self.runner([])
        d = self.framework("ReadOnly")
        for p in [*d.rglob("*"), d]:
            os.chmod(p, stat.S_IRUSR | (stat.S_IXUSR if p.is_dir() else 0))
        self.assertEqual(self.strip("ReadOnly")[0], ["ReadOnly"])
        self.assertFalse(d.exists())


class BuildShWiringTest(unittest.TestCase):
    def test_row10_build_sh_strips_before_zip(self):
        build = (Path(__file__).resolve().parents[1] / "flutter/build.sh").read_text()
        call = build.find('tools/strip-frameworks.py" "$bundle" "${strip_names[@]}"')
        self.assertNotEqual(call, -1)
        self.assertLess(call, build.find("zip -qry Runner.ipa Payload"))

    def names_from(self, value):
        """The names build.sh hands the tool for a STRIP_FRAMEWORKS value: the guarded
        block from build.sh with the tool call replaced by a print of the names."""
        build = (Path(__file__).resolve().parents[1] / "flutter/build.sh").read_text().splitlines()
        start = next(i for i, l in enumerate(build) if l.startswith('if [ -n "${STRIP_FRAMEWORKS:-}" ]'))
        block = []
        for line in build[start:]:
            if "strip-frameworks.py" in line:
                block.append('    printf "%s\\n" ${strip_names[@]+"${strip_names[@]}"}')
            else:
                block.append(line)
            if line == "fi":  # the guard's closing line, after the call was replaced
                break
        out = subprocess.run(["bash", "-c", "set -euo pipefail\n" + "\n".join(block)],
                             env={"STRIP_FRAMEWORKS": value, "PATH": os.environ["PATH"]},
                             capture_output=True, text=True, check=True)
        return out.stdout.splitlines()  # splitlines, not split: an empty field is the bug

    def test_row11_separators_do_not_pass_empty_names(self):
        for value, want in [("A B", ["A", "B"]), ("A,B", ["A", "B"]), ("A,,B", ["A", "B"]),
                            ("A B,", ["A", "B"]), (",A", ["A"]), (" ", []), ("", [])]:
            with self.subTest(value=value):
                self.assertEqual(self.names_from(value), want)


if __name__ == "__main__":
    unittest.main()
