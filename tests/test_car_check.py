"""tools/car_check.py against real cars (tests/car/README.md) and mutants of them, and the upload gate in tools/asc.py."""
import io
import os
import plistlib
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
CARS = HERE / "car"
sys.path.insert(0, str(TOOLS))
import car_check  # noqa: E402


def car(name):
    return (CARS / name).read_bytes()


def first_chunked_rendition(data):
    """(BOM, block index, TVL 1007 value offset in the file) of the first KCBC rendition of a car."""
    bom = car_check._Bom(data)
    for _key, block in bom.leaves("RENDITIONS"):
        csi = bom.block(block)
        stride, body = car_check._tvl_stride(csi)
        if stride is not None and csi[body:body + 4] == b"MLEC" and csi[body + 16:body + 20] == b"KCBC":
            pos = 184
            while pos < body:
                kind, size = struct.unpack_from("<2I", csi, pos)
                if kind == 1007:
                    return bom, block, bom.blocks[block][0] + pos + 8
                pos += 8 + size
    raise AssertionError("no chunked rendition in the fixture")


def with_stride_change(data, delta):
    bom, _block, offset = first_chunked_rendition(data)
    out = bytearray(data)
    struct.pack_into("<I", out, offset, struct.unpack_from("<I", data, offset)[0] + delta)
    return bytes(out)


def with_bitmapkeys_word(data, word_index, value):
    bom = car_check._Bom(data)
    _identifier, block = bom.leaves("BITMAPKEYS")[0]
    out = bytearray(data)
    struct.pack_into("<I", out, bom.blocks[block][0] + 4 * word_index, value)
    return bytes(out)


def first_chunk_offset(data):
    """File offset of the first KCBC chunk header of the first KCBC rendition of a car."""
    bom = car_check._Bom(data)
    for _key, block in bom.leaves("RENDITIONS"):
        csi = bom.block(block)
        stride, body = car_check._tvl_stride(csi)
        if stride is not None and csi[body:body + 4] == b"MLEC" and csi[body + 16:body + 20] == b"KCBC":
            return bom.blocks[block][0] + body + 16
    raise AssertionError("no chunked rendition in the fixture")


def with_bytes(data, offset, new):
    out = bytearray(data)
    out[offset:offset + len(new)] = new
    return bytes(out)


def with_one_bitmapkeys_row_dropped(data):
    bom = car_check._Bom(data)
    head = bom.block(bom.vars["BITMAPKEYS"])
    node = struct.unpack(">I", head[8:12])[0]
    count = struct.unpack(">H", bom.block(node)[2:4])[0]
    out = bytearray(data)
    struct.pack_into(">H", out, bom.blocks[node][0] + 2, count - 1)
    return bytes(out)


def with_all_identifiers_zero(data):
    """Every rendition key carries identifier 0."""
    bom = car_check._Bom(data)
    at = car_check._key_format(bom).index(17)
    out = bytearray(data)
    for key_block, _value in bom.leaves("RENDITIONS"):
        struct.pack_into("<H", out, bom.blocks[key_block][0] + 2 * at, 0)
    return bytes(out)


class CarCheckTest(unittest.TestCase):
    def test_cars_apple_validates_pass(self):
        # assetutil --validate-file rc 0 on each (tests/car/README.md)
        for name in ("colors-xcode-actool.car", "action-assetkit.car", "colors-assetkit-old-assetutil-U.car"):
            with self.subTest(name):
                self.assertEqual(car_check.check_car(car(name)), [])

    def test_old_assetkit_car_fails_on_bitmapkeys(self):
        # assetutil --validate-file: "invalid bitmapindex" (rc 1)
        problems = car_check.check_car(car("colors-assetkit-old.car"))
        self.assertTrue(problems)
        self.assertTrue(all("BITMAPKEYS" in p for p in problems), problems)

    def test_bitmapkeys_scale_mask_mutant_fails(self):
        problems = car_check.check_car(with_bitmapkeys_word(car("colors-xcode-actool.car"), 6, 0x0E))
        self.assertTrue(any("BITMAPKEYS" in p for p in problems), problems)

    def test_stride_mutants_fail_and_identity_passes(self):
        base = car("action-assetkit.car")
        self.assertEqual(car_check.check_car(with_stride_change(base, 0)), [])
        for delta in (16, -2):
            with self.subTest(delta=delta):
                problems = car_check.check_car(with_stride_change(base, delta))
                self.assertTrue(any("stride" in p for p in problems), problems)

    def test_truncated_car_fails(self):
        data = car("colors-xcode-actool.car")
        self.assertTrue(car_check.check_car(data[:len(data) // 2]))
        self.assertTrue(car_check.check_car(b"BOMStore" + b"\0" * 8))

    def test_a_dropped_bitmapkeys_row_fails(self):
        # The fixture has two rows; the tree keeps one while RENDITIONS still holds both identifiers.
        data = car("colors-assetkit-old-assetutil-U.car")
        self.assertEqual(car_check.check_car(data), [])
        problems = car_check.check_car(with_one_bitmapkeys_row_dropped(data))
        self.assertTrue(any("no row for identifier" in p for p in problems), problems)

    def test_a_corrupt_first_kcbc_chunk_fails(self):
        data = car("action-assetkit.car")
        problems = car_check.check_car(with_bytes(data, first_chunk_offset(data), b"XXXX"))
        self.assertTrue(any("no KCBC magic" in p for p in problems), problems)

    def test_writing_the_dmp2_marker_does_not_switch_off_the_chunk_check(self):
        # Offset +16 of the first chunk is body+32, where Apple's dmp2 record keeps its marker.
        data = car("action-assetkit.car")
        at = first_chunk_offset(data)
        problems = car_check.check_car(with_bytes(with_bytes(data, at, b"XXXX"), at + 16, b"dmp2"))
        self.assertTrue(any("no KCBC magic" in p for p in problems), problems)

    def test_an_unknown_mlec_layout_word_fails(self):
        data = car("action-assetkit.car")
        problems = car_check.check_car(with_bytes(data, first_chunk_offset(data) - 8, struct.pack("<I", 2)))
        self.assertTrue(any("compression word 2" in p for p in problems), problems)

    def test_chunked_dmp2_passes_and_its_framing_is_checked(self):
        # Xcode's chunked dmp2 (Intercom.framework): compression word 11, KCBC chunks, each payload a 16-byte
        # header (1, 2, record size, 0) then the dmp2 marker. Made here from the first chunked LZFSE rendition.
        data = car("action-assetkit.car")
        at = first_chunk_offset(data)
        count = struct.unpack_from("<I", data, at - 4)[0]
        out, pos, sizes = bytearray(with_bytes(data, at - 8, struct.pack("<I", 11))), at, []
        for _ in range(count):
            size = struct.unpack_from("<I", data, pos + 16)[0]
            out[pos + 20:pos + 40] = struct.pack("<4I", 1, 2, size - 16, 0) + b"dmp2"
            sizes.append((pos, size))
            pos += 20 + size
        dmp2 = bytes(out)
        self.assertEqual(car_check.check_car(dmp2), [])
        first, size = sizes[0]
        for mutant, expected in ((with_bytes(dmp2, first + 36, b"XXXX"), "not a dmp2 record"),
                                 (with_bytes(dmp2, first + 28, struct.pack("<I", size)), "not a dmp2 record"),
                                 (with_bytes(dmp2, first + 12, struct.pack("<I", struct.unpack_from("<I", dmp2, first + 12)[0] + 1)), "chunks cover"),
                                 (with_bytes(dmp2, at - 12, struct.pack("<I", 2)), "dmp2 body in form 2")):
            with self.subTest(expected):
                problems = car_check.check_car(mutant)
                self.assertTrue(any(expected in p for p in problems), problems)

    def test_a_non_atlas_identifier_zero_needs_its_row(self):
        data = with_all_identifiers_zero(car("action-assetkit.car"))
        problems = car_check.check_car(with_one_bitmapkeys_row_dropped(data))
        self.assertTrue(any("no row for identifier" in p for p in problems), problems)

    def test_an_unreadable_lzfse_block_fails(self):
        data = car("action-assetkit.car")
        at = first_chunk_offset(data)
        broken_magic = with_bytes(data, at + 20, b"zzzz")
        truncated = with_bytes(data, at + 16, struct.pack("<I", 8))
        for name, mutant in (("unknown block magic", broken_magic), ("truncated block", truncated)):
            with self.subTest(name):
                problems = car_check.check_car(mutant)
                self.assertTrue(any("cannot be read" in p for p in problems), problems)

    def test_unused_bom_slot_is_not_bounds_checked(self):
        # Apple's actool writes unused index slots as (0, 0), AssetKit as (offset, 0): length 0 means no block.
        data = bytearray(car("colors-xcode-actool.car"))
        index_off, = struct.unpack(">I", data[16:20])
        count, = struct.unpack(">I", data[index_off:index_off + 4])
        slot = next(i for i in range(count) if struct.unpack(">2I", data[index_off + 4 + 8 * i:index_off + 12 + 8 * i])[1] == 0)
        struct.pack_into(">2I", data, index_off + 4 + 8 * slot, len(data) + 1000, 0)
        self.assertEqual(car_check.check_car(bytes(data)), [])

    def test_car_without_renditions_fails(self):
        # assetutil --validate-file: "invalid bitmapindex" (rc 1) on this empty car of the 2026-10-05 builds.
        problems = car_check.check_car(car("empty-assetkit-old.car"))
        self.assertTrue(any("no renditions" in p for p in problems), problems)

    def test_check_ipa_reads_nested_bundles_and_extensions(self):
        good, bad_keys = car("colors-xcode-actool.car"), car("colors-assetkit-old.car")
        bad_stride = with_stride_change(car("action-assetkit.car"), 16)
        ipa = io.BytesIO()
        with zipfile.ZipFile(ipa, "w") as z:
            z.writestr("Payload/A.app/Assets.car", good)
            z.writestr("Payload/A.app/DesignSystem_DesignSystem.bundle/Assets.car", bad_keys)
            z.writestr("Payload/A.app/PlugIns/X.appex/Assets.car", bad_stride)
            z.writestr("Payload/A.app/Info.plist", b"")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "a.ipa")
            path.write_bytes(ipa.getvalue())
            found = car_check.check_ipa(path)
        self.assertEqual(sorted(found), [
            "Payload/A.app/Assets.car",
            "Payload/A.app/DesignSystem_DesignSystem.bundle/Assets.car",
            "Payload/A.app/PlugIns/X.appex/Assets.car",
        ])
        self.assertEqual(found["Payload/A.app/Assets.car"], [])
        self.assertTrue(found["Payload/A.app/DesignSystem_DesignSystem.bundle/Assets.car"])
        self.assertTrue(found["Payload/A.app/PlugIns/X.appex/Assets.car"])


class UploadGateTest(unittest.TestCase):
    def test_upload_refuses_a_bad_nested_car_before_any_network_or_key_use(self):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("tools/asc.py needs the cryptography module")
        with tempfile.TemporaryDirectory() as tmp:
            ipa = Path(tmp, "bad.ipa")
            with zipfile.ZipFile(ipa, "w") as z:
                z.writestr("Payload/A.app/Info.plist", plistlib.dumps({
                    "CFBundleIdentifier": "com.example.a", "CFBundleShortVersionString": "1.0", "CFBundleVersion": "1"}))
                z.writestr("Payload/A.app/PlugIns/X.appex/Assets.car", car("colors-assetkit-old.car"))
            env = {k: v for k, v in os.environ.items() if not k.startswith("ASC_")}
            run = subprocess.run([sys.executable, str(TOOLS / "asc.py"), "upload", str(ipa)],
                                 capture_output=True, text=True, env=env, timeout=60)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("structural check", run.stderr)
        self.assertIn("PlugIns/X.appex/Assets.car", run.stderr)
        # Without the gate the first thing upload does is read the API key, which this environment lacks.
        self.assertNotIn("ASC_KEY_ID", run.stderr)


if __name__ == "__main__":
    unittest.main()
