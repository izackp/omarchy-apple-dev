"""tools/ibxib.py's color-space conversions against ColorSync.

The expected values are CoreGraphics' own conversions on macOS 15
(CGColor.converted(to: extendedSRGB / extendedGray)), which is what Apple's
ibtool stores. ColorSync evaluates its curves with a pow approximation that no
libm or Accelerate function reproduces bit for bit, so these check closeness:
every one of ~100k sampled colors is within 3.4e-7 (Generic RGB / Gray) or
1.5e-6 (Display P3). The rows with a component below 1/32 pin the curve's
linear segment, without which they are up to 0.0055 off.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import ibxib  # noqa: E402

# (source space, components, ColorSync's extended sRGB / extended gray)
SAMPLES = [
    ("rgb", [0.02834747652200631, 0.8357651039198697, 0.43276706790505337],
     [-0.13870975375175476, 0.8508490324020386, 0.5075616240501404]),
    ("rgb", [0.762280082457942, 0.0021060533511106927, 0.4453871940548014],
     [0.8148327469825745, 0.1500885784626007, 0.5198374390602112]),
    ("rgb", [0.9014274576114836, 0.030589983033553536, 0.0254458609934608],
     [0.9311685562133789, 0.14263975620269775, 0.00169021834153682]),
    ("rgb", [0.13436424411240122, 0.8474337369372327, 0.763774618976614],
     [0.09147361665964127, 0.8673340678215027, 0.8074377179145813]),
    ("rgb", [0.2550690257394217, 0.49543508709194095, 0.4494910647887381],
     [0.3142090439796448, 0.5631083846092224, 0.5243584513664246]),
    ("gray", [0.0014652014652014652], [0.0011831502197310328]),
    ("gray", [0.0017094017094017094], [0.001380341942422092]),
    ("gray", [0.5245421245421246], [0.5952610373497009]),
    ("p3", [0.13436424411240122, 0.8474337369372327, 0.763774618976614],
     [-0.40274807810783386, 0.862704873085022, 0.763217031955719]),
    ("p3", [0.2550690257394217, 0.49543508709194095, 0.4494910647887381],
     [0.14109641313552856, 0.5025877356529236, 0.44848763942718506]),
]
TOLERANCE = {"rgb": 3.5e-7, "gray": 3.5e-7, "p3": 1.5e-6}


def convert(space, comps):
    if space == "gray":
        return [ibxib._encode_srgb(ibxib._generic_gamma(comps[0]))]
    return ibxib.to_srgb("calibratedRGB" if space == "rgb" else "displayP3", comps)


class ColorSyncConversions(unittest.TestCase):
    def test_within_tolerance(self):
        for space, comps, want in SAMPLES:
            with self.subTest(space=space, comps=comps):
                for got, w in zip(convert(space, comps), want):
                    self.assertAlmostEqual(got, w, delta=TOLERANCE[space])


if __name__ == "__main__":
    unittest.main()
