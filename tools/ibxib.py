"""General iOS xib compiler: XIB -> object graph -> NIBArchive.

tools/ibtool's compile_xib grew one golden at a time and fixes its object
creation order by hand. This compiler relies on three facts measured on 130
nibs from Apple's ibtool 27 (tools/nibkeys-mine.py, the PhotoDay corpus):

- Object order is the depth-first preorder of the reference graph from the
  root, following each object's references in value order. Building the graph
  in any order and renumbering reproduces Apple's table.
- Each class writes its keys in one fixed order (tools/nibkeys.json). A
  UIClassSwapper writes its original class's keys, then UIClassName and
  UIOriginalClassName.
- NSString, NSNumber, UIFont, UIImageNibPlaceholder and UIColor objects are
  shared by value.

So a builder only has to get the graph right: which objects, which keys, which
values. finalize_dfs() orders keys and objects and hands the result to
tools/ibtool's finalize() for the key and class tables.

compile_xib() raises Unsupported for anything it does not know; tools/ibtool
then falls back to its older compiler.
"""
import json
import math
import os
import struct
import xml.etree.ElementTree as ET

_TOOLS = os.path.dirname(os.path.abspath(__file__))
import nibarchive as N  # noqa: E402

with open(os.path.join(_TOOLS, "nibkeys.json")) as _f:
    KEY_ORDER = json.load(_f)

SWAP_TAIL = ("UIClassName", "UIOriginalClassName")
CONTAINERS = {"NSArray", "NSMutableArray", "NSDictionary", "NSMutableDictionary"}
INTERNED = {"NSString", "NSNumber", "UIFont", "UIFontDescriptor", "UIImageNibPlaceholder",
            "UIColor"}

# nibarchive names the two boolean value types the other way round from what
# UIKit decodes: a "false"-typed value is YES (UIOpaque on an opaque view,
# UIHidden on a hidden one).
YES = (N.FALSE, None)
NO = (N.TRUE, None)


class Unsupported(Exception):
    pass


def int_fit(v):
    """The narrowest integer value type holding v, as Apple's encoder picks; a
    negative value is always 64-bit (probe segmented: selectedSegmentIndex -1)."""
    if v < 0:
        return (N.INT64, v)
    for t, bits in ((N.INT8, 8), (N.INT16, 16), (N.INT32, 32)):
        if -(1 << (bits - 1)) <= v < (1 << (bits - 1)):
            return (t, v)
    return (N.INT64, v)


def _f32(v):
    return struct.unpack("<f", struct.pack("<f", v))[0]


# ---------------------------------------------------------------- finalize

def _sort_keys(o):
    if o.cls in CONTAINERS:
        return
    cls = o.cls
    tail = ()
    if cls == "UIClassSwapper":
        orig = [v for k, v in o.values if k == "UIOriginalClassName"]
        cls = o.original_class
        tail = SWAP_TAIL
    order = KEY_ORDER.get(cls)
    if order is None:
        raise Unsupported(f"no key order known for class {cls}")
    pos = {k: i for i, k in enumerate(order)}
    pos.update({k: len(order) + i for i, k in enumerate(tail)})
    for k, _ in o.values:
        if k not in pos:
            raise Unsupported(f"{cls}: key {k} not seen in Apple's nibs")
    o.values.sort(key=lambda kv: pos[kv[0]])


def _signature(o, objs, memo):
    """Structural identity of an interned object."""
    if o.idx in memo:
        return memo[o.idx]
    if o.cls == "NSNumber":
        return ("NSNumber", _number_value(o))
    memo[o.idx] = None  # cycle guard
    parts = [o.cls]
    for k, v in o.values:
        if v.type == N.OBJREF:
            parts.append((k, _signature(objs[v.payload], objs, memo)))
        else:
            parts.append((k, v.type, v.payload))
    memo[o.idx] = tuple(parts)
    return memo[o.idx]


def _number_value(o):
    (k, v), = o.values
    return float(v.payload)


def finalize_dfs(I, b, root):
    """Order keys, share equal leaf objects, renumber depth-first from the root.
    NSNumbers are shared by value, as -isEqual: compares them (@22 == @22.0): the
    first one encoded stands for the rest (VCAccountInfo: an <integer> 22 after
    a double 22.0; VCSplash: a <real> 12 after an integer 12)."""
    for o in b.objects:
        _sort_keys(o)
    memo, canon, alias = {}, {}, {}
    for o in b.objects:
        if o.cls in INTERNED and o.cls != "NSNumber" and not getattr(o, "unshared", False):
            sig = _signature(o, b.objects, memo)
            alias[o.idx] = canon.setdefault(sig, o.idx)
    nb = I.Builder()
    made = {}
    numbers = {}

    def visit(o):
        o = b.objects[alias.get(o.idx, o.idx)]
        if o.cls == "NSNumber":
            first = numbers.setdefault(_number_value(o), o.idx)
            o = b.objects[first]
        if o.idx in made:
            return made[o.idx]
        n = nb.new(o.cls)
        made[o.idx] = n
        for k, v in o.values:
            if v.type == N.OBJREF:
                n.add(k, N.OBJREF, visit(b.objects[v.payload]).idx)
            else:
                n.add(k, v.type, v.payload)
        return n

    import sys
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
    return I.finalize(nb, visit(root))


# ---------------------------------------------------------------- context

class X:
    """One compile: the builder, document resources, and what later passes need."""

    def __init__(self, I, path, doc):
        self.I = I
        self.b = I.Builder()
        self.path = path
        self.where = os.path.basename(path)
        self.doc = doc
        self.resources, self.named = I._parse_color_resources(doc, path)
        self.ids = {}         # xib id -> Obj
        self.els = {}         # xib id -> element
        self.connections = []  # (kind, source obj, element)
        self.pairs = []       # (owner element, target obj, None for runtime attributes | (key path, rect))
        self.accessibility = []  # (target obj, label)
        self.subtree = {}     # obj idx -> (guide placeholders, constraints, kids)
        self.frames = {}      # element -> frame after resize_top

    def new(self, cls, orig=None):
        o = self.b.new(cls)
        o.original_class = orig
        return o

    def put(self, o, key, value):
        """Add a key; value is a (type, payload) pair or an Obj."""
        if hasattr(value, "idx"):
            value = (N.OBJREF, value.idx)
        o.add(key, *value)

    def s(self, text):
        return self.b.string(text)

    def array(self, items, cls="NSArray"):
        a = self.new(cls)
        a.add("NSInlinedValue", *YES)
        for it in items:
            a.add("UINibEncoderEmptyKey", N.OBJREF, it.idx)
        return a


def _bool(el, name):
    return el.get(name) == "YES"


def _fail(el, what, where):
    raise Unsupported(f"<{el.tag} id={el.get('id')}> {what} ({where})")


# ---------------------------------------------------------------- colors

def color(x, el):
    """A <color> element -> UIColor."""
    if el is None:
        return None
    b, I = x.b, x.I
    a = el.attrib
    if "systemColor" in a:
        try:
            return I.build_color(b, a["systemColor"], x.where, x.resources)
        except I.XibError as e:
            raise Unsupported(str(e))
    if "name" in a:
        try:
            return I.build_named_color(b, a["name"], x.named, x.where)
        except I.XibError as e:
            raise Unsupported(str(e))
    space = a.get("customColorSpace") if a.get("colorSpace") == "custom" else a.get("colorSpace")
    if "white" in a:
        w, al = float(a["white"]), float(a.get("alpha", "1"))
        if space in ("genericGamma22GrayColorSpace", "calibratedWhite"):
            if space == "calibratedWhite" and (w, al) not in ((1.0, 1.0), (0.0, 0.0), (0.0, 1.0)):
                return _white_components(x, x.new("UIColor"), _f32(_encode_srgb(_generic_gamma(w))), al)
            name = {(1.0, 1.0): "whiteColor", (0.0, 0.0): "clearColor",
                    (0.0, 1.0): "blackColor"}.get((w, al))
            if name:
                c = x.new("UIColor")
                x.put(c, "UISystemColorName", x.s(name))
                return _white_components(x, c, w, al)
            return _white_components(x, x.new("UIColor"), w, al)
        _fail(el, f"white color in {space}", x.where)
    if "red" in a:
        comps = [float(a[k]) for k in ("red", "green", "blue")] + [float(a.get("alpha", "1"))]
        if space == "sRGB":
            return _rgb_components(x, x.new("UIColor"), comps)
        if space == "calibratedRGB":
            return _rgb_components(x, x.new("UIColor"), generic_to_srgb(comps[:3]) + [comps[3]],
                                   converted=True)
        _fail(el, f"RGB color in {space} needs a color-space conversion", x.where)
    _fail(el, f"color {dict(a)}", x.where)


# Interface Builder stores calibrated (Generic RGB / Generic Gray) colors as
# extended sRGB. The conversion is ColorSync's matrix/TRC path: Generic RGB's
# gamma 1.8, its primaries and sRGB's (from the profiles' rXYZ/gXYZ/bXYZ, each
# matrix row-scaled so white maps to D50), then the sRGB curve, mirrored for
# negative values. Constants are float32, as ColorSync computes.
_GENERIC_RGB = ((0.4542999267578125, 0.3533477783203125, 0.156646728515625),
                (0.241912841796875, 0.6736297607421875, 0.0844573974609375),
                (0.014892578125, 0.09063720703125, 0.719573974609375))
_SRGB = ((0.436065673828125, 0.3851470947265625, 0.14306640625),
         (0.2224884033203125, 0.7168731689453125, 0.06060791015625),
         (0.013916015625, 0.097076416015625, 0.7140960693359375))
_D50 = (0.9642, 1.0, 0.8249)


def _inv3(m):
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return ((e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det), \
        ((f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det), \
        ((d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det)


def _white_scaled(m):
    return tuple(tuple(v * _D50[r] / sum(m[r]) for v in m[r]) for r in range(3))


_GEN_TO_SRGB = (lambda a, b: tuple(tuple(sum(a[r][k] * b[k][c] for k in range(3))
                                         for c in range(3)) for r in range(3)))(
    _inv3(_white_scaled(_SRGB)), _white_scaled(_GENERIC_RGB))


def _generic_gamma(v):
    v = _f32(v)
    return (-1.0 if v < 0 else 1.0) * abs(v) ** _f32(1.8)


def _encode_srgb(v):
    a = abs(v)
    out = a * 12.92 if a <= 0.0031308 else _f32(1.055) * a ** _f32(1 / 2.4) - _f32(0.055)
    return -out if v < 0 else out


def generic_to_srgb(rgb):
    lin = [_generic_gamma(v) for v in rgb]
    return [_encode_srgb(sum(_GEN_TO_SRGB[r][k] * lin[k] for k in range(3))) for r in range(3)]


# Display P3: its profile's primaries, sRGB's curve.
_DISPLAY_P3 = ((0.5151214599609375, 0.2919769287109375, 0.1571044921875),
               (0.2411956787109375, 0.6922454833984375, 0.0665740966796875),
               (-0.0010528564453125, 0.0418853759765625, 0.7840728759765625))
_P3_TO_SRGB = (lambda a, b: tuple(tuple(sum(a[r][k] * b[k][c] for k in range(3))
                                        for c in range(3)) for r in range(3)))(
    _inv3(_white_scaled(_SRGB)), _white_scaled(_DISPLAY_P3))


def _decode_srgb(v):
    a = abs(_f32(v))
    out = a / 12.92 if a <= 0.04045 else ((a + 0.055) / 1.055) ** 2.4
    return -out if v < 0 else out


def p3_to_srgb(rgb):
    """Display P3 -> extended sRGB, as float32 (within ~1e-7 of ColorSync)."""
    lin = [_decode_srgb(v) for v in rgb]
    return [_f32(_encode_srgb(sum(_P3_TO_SRGB[r][k] * lin[k] for k in range(3)))) for r in range(3)]


def to_srgb(space, rgb):
    """RGB components in a document color space -> sRGB, or None if unknown."""
    if space == "sRGB":
        return list(rgb)
    if space == "calibratedRGB":
        return [_f32(v) for v in generic_to_srgb(rgb)]
    if space == "displayP3":
        return p3_to_srgb(rgb)
    return None


def _white_components(x, c, w, al):
    x.put(c, "UIColorComponentCount", x.b.int8(2))
    for k, v in (("UIWhite", w), ("UIAlpha", al)):
        x.put(c, k, x.b.float32(v))
        if _f32(v) != v:
            x.put(c, k + "-Double", x.b.float64(v))
    comps = (w,) if al == 1.0 else (w, al)
    x.put(c, "NSWhite", (N.DATA, " ".join(f"{v:.3g}" for v in comps).encode()))
    x.put(c, "NSColorSpace", x.b.int8(4))
    return c


def _rgb_components(x, c, comps, converted=False):
    """converted: components computed in float32 (a color-space conversion), so
    they have no -Double values except the document's own alpha."""
    x.put(c, "UIColorComponentCount", x.b.int8(4))
    for i, (k, v) in enumerate(zip(("UIRed", "UIGreen", "UIBlue", "UIAlpha"), comps)):
        if converted and i < 3:
            v = _f32(v)
        x.put(c, k, x.b.float32(v))
        if _f32(v) != v:
            x.put(c, k + "-Double", x.b.float64(v))
    shown = comps[:3] if comps[3] == 1.0 else comps
    x.put(c, "NSRGB", (N.DATA, " ".join(f"{v:.3g}" for v in shown).encode()))
    x.put(c, "NSColorSpace", x.b.int8(2))
    return c


def keyed_color(x, el, key):
    return color(x, el.find(f"color[@key='{key}']"))


# ---------------------------------------------------------------- views

def _frame(x, el):
    if el in x.frames:
        return x.frames[el]
    r = el.find("rect[@key='frame']")
    if r is None:
        return (0.0, 0.0, 1000.0, 1000.0)
    return tuple(float(r.get(k, "0")) for k in ("x", "y", "width", "height"))


# Portrait screen sizes of the document's <device>. retina3_5 is no longer a
# device Xcode 27 knows; it lays such a view out at 393x852 (PhotoDay
# TableHeaderWidget golden).
DEVICE_SIZES = {"retina3_5": (393, 852), "retina4_0": (320, 568), "retina4_7": (375, 667),
                "retina5_5": (414, 736), "retina6_0": (390, 844), "retina6_1": (414, 896),
                "retina6_12": (393, 852), "retina6_5": (414, 896), "retina6_72": (430, 932)}


def _resize_axis(x, el, pos, size, old, new, flex_lo, flex_size, flex_hi):
    """One axis of UIKit autoresizing. Shares of the change go to the flexible
    parts in proportion to their sizes; a margin's share is rounded down
    (PhotoDay TableWidget/TableHeaderWidget goldens: centered indicators)."""
    delta = new - old
    parts = [p for p, f in ((pos, flex_lo), (size, flex_size), (old - pos - size, flex_hi)) if f]
    if not delta or not parts:
        return pos, size
    if flex_size and len(parts) == 1:
        return pos, size + delta
    if flex_lo and flex_hi and not flex_size:
        total = pos + (old - pos - size)
        return pos + (math.floor(delta * pos / total) if total else delta / 2), size
    if flex_hi and len(parts) == 1:
        return pos, size
    if flex_lo and len(parts) == 1:
        return pos + delta, size
    _fail(el, "autoresizing with a flexible size and margin", x.where)


def resize_top(x, el):
    """Below iOS 17.0, Apple lays a top-level <view> without simulated metrics
    out at the document device's screen size, autoresizing its subviews (probe
    resize-device: 320x568 on retina6_12 becomes 393x852 at 16.9, stays at 17.0)."""
    dev = x.doc.find("device")
    if x.I.MIN_DEPLOYMENT is None or x.I.MIN_DEPLOYMENT >= (17, 0):
        return
    if el.tag != "view" or dev is None or el.find("freeformSimulatedSizeMetrics") is not None:
        return
    if dev.get("id") not in DEVICE_SIZES or dev.get("orientation", "portrait") != "portrait":
        return
    w, h = DEVICE_SIZES[dev.get("id")]
    fx, fy, ow, oh = _frame(x, el)
    if (ow, oh) == (w, h):
        return
    x.frames[el] = (fx, fy, float(w), float(h))

    def walk(parent, old, new):
        subs = parent.find("subviews")
        for ch in (subs if subs is not None else ()):
            if ch.get("translatesAutoresizingMaskIntoConstraints") == "NO" and not _bool(ch, "fixedFrame"):
                _fail(ch, "auto layout under a view resized to the device", x.where)
            m = _mask(x, ch, 0)
            cx, cy, cw, ch_h = _frame(x, ch)
            nx, nw = _resize_axis(x, ch, cx, cw, old[0], new[0], m & 1, m & 2, m & 4)
            ny, nh = _resize_axis(x, ch, cy, ch_h, old[1], new[1], m & 8, m & 16, m & 32)
            x.frames[ch] = (nx, ny, nw, nh)
            if (nw, nh) != (cw, ch_h):
                walk(ch, (cw, ch_h), (nw, nh))
    walk(el, (ow, oh), (w, h))


def _mask(x, el, default):
    m = el.find("autoresizingMask[@key='autoresizingMask']")
    if m is None:
        return default
    v = 0
    for flag, bit in x.I.RESIZE_FLAGS.items():
        if m.get(flag) == "YES":
            v |= bit
    return v


CONTENT_MODES = {"scaleToFill": 0, "scaleAspectFit": 1, "scaleAspectFill": 2, "redraw": 3,
                 "center": 4, "top": 5, "bottom": 6, "left": 7, "right": 8, "topLeft": 9,
                 "topRight": 10, "bottomLeft": 11, "bottomRight": 12}
# Classes whose hugging priority defaults to 750: Apple stores the priority
# only when it differs from the class default.
# A segmented control always stores its hugging, {250, 250} included (probe segmented).
INTRINSIC_HUGGING = {"UISwitch": "750", "UIActivityIndicatorView": "750", "UISegmentedControl": "750"}
VIEW_ATTRS = {"id", "key", "contentMode", "opaque", "clipsSubviews", "hidden", "alpha",
              "userInteractionEnabled", "clearsContextBeforeDrawing", "multipleTouchEnabled",
              "translatesAutoresizingMaskIntoConstraints", "customClass", "customModule",
              "customModuleProvider", "userLabel", "misplaced", "ambiguous", "fixedFrame",
              "autoresizesSubviews", "horizontalHuggingPriority", "verticalHuggingPriority",
              "horizontalCompressionResistancePriority",
              "verticalCompressionResistancePriority", "tag", "semanticContentAttribute",
              "insetsLayoutMarginsFromSafeArea", "preservesSuperviewLayoutMargins",
              "restorationIdentifier"}
VIEW_SUBS = {"rect", "autoresizingMask", "subviews", "constraints", "color",
             "viewLayoutGuide", "keyboardLayoutGuide", "userDefinedRuntimeAttributes",
             "connections", "point", "freeformSimulatedSizeMetrics", "nil"}
VIEW_COLORS = {"backgroundColor", "tintColor", "textColor", "highlightedColor", "shadowColor",
               "color"}


def _custom_class(x, el):
    cls = el.get("customClass")
    if not cls:
        return None
    module = el.get("customModule")
    if el.get("customModuleProvider") == "target" and x.I.TARGET_MODULE:
        module = x.I.TARGET_MODULE
    return f"_TtC{len(module)}{module}{len(cls)}{cls}" if module else cls


def new_view_obj(x, el, cls):
    swift = _custom_class(x, el)
    if swift:
        o = x.new("UIClassSwapper", orig=cls)
        o.swap = (swift, cls)
    else:
        o = x.new(cls)
        o.swap = None
    if el.get("id"):
        x.ids[el.get("id")] = o
        x.els[el.get("id")] = el
    return o


def finish_swap(x, o):
    if o.swap:
        x.put(o, "UIClassName", x.s(o.swap[0]))
        x.put(o, "UIOriginalClassName", x.s(o.swap[1]))


def check(x, el, attrs, subs):
    extra = set(el.attrib) - attrs
    if extra:
        _fail(el, f"unsupported attributes {sorted(extra)}", x.where)
    for ch in el:
        if ch.tag not in subs:
            _fail(el, f"unsupported subelement <{ch.tag} key={ch.get('key')}>", x.where)
        if ch.tag == "color" and ch.get("key") not in x.colors_ok:
            _fail(el, f"unsupported color key {ch.get('key')}", x.where)
        if ch.tag == "accessibility" and set(ch.attrib) != {"key", "label"}:
            _fail(ch, f"accessibility {sorted(ch.attrib)}", x.where)


def view_keys(x, el, o, top, default_mask=36, opaque=True):
    """The UIView part of any view: geometry, flags, colors, subviews,
    constraints, layout guides. Records the view's subtree for UINibObjectsKey."""
    b = x.b
    fx, fy, w, h = _frame(x, el)
    x.put(o, "UIBounds", b.geom(0.0, 0.0, w, h))
    x.put(o, "UICenter", b.geom(fx + w / 2, fy + h / 2))
    kids = []
    subs = el.find("subviews")
    if subs is not None:
        for ch in subs:
            kids.append(build(x, ch))
        x.put(o, "UISubviews", x.array(kids, "NSMutableArray"))
    bg = keyed_color(x, el, "backgroundColor")
    if bg is not None:
        x.put(o, "UIBackgroundColor", bg)
    x.put(o, "UIDeepDrawRect", YES)
    if opaque and el.get("opaque") != "NO":
        x.put(o, "UIOpaque", YES)
    if _bool(el, "hidden"):
        x.put(o, "UIHidden", YES)
    if el.get("userInteractionEnabled") == "NO":
        x.put(o, "UIUserInteractionDisabled", YES)
    if _bool(el, "multipleTouchEnabled"):
        x.put(o, "UIMultipleTouchEnabled", YES)
    x.put(o, "UIAutoresizeSubviews", YES)
    # A top-level <view> defaults to flexible width and height; any other
    # top-level element keeps the subview default (TinyLabel golden).
    mask = _mask(x, el, 18 if top and el.tag == "view" else default_mask)
    if mask:
        x.put(o, "UIAutoresizingMask", b.int8(mask))
    if _bool(el, "clipsSubviews"):
        x.put(o, "UIClipsToBounds", YES)
    if el.get("clearsContextBeforeDrawing") == "NO":
        x.put(o, "UIClearsContextBeforeDrawing", NO)
    if el.get("alpha") is not None:
        x.put(o, "UIAlpha", b.float32(float(el.get("alpha"))))
    mode = el.get("contentMode", "scaleToFill")
    if mode not in CONTENT_MODES:
        _fail(el, f"contentMode {mode}", x.where)
    if mode != "scaleToFill":
        x.put(o, "UIContentMode", b.int8(CONTENT_MODES[mode]))
    hug_default = INTRINSIC_HUGGING.get(o.original_class or o.cls, "250")
    hug = (el.get("horizontalHuggingPriority", "250"), el.get("verticalHuggingPriority", "250"))
    if hug != (hug_default, hug_default):
        x.put(o, "UIViewContentHuggingPriority", x.s(f"{{{hug[0]}, {hug[1]}}}"))
    if (el.get("horizontalCompressionResistancePriority")
            or el.get("verticalCompressionResistancePriority")):
        x.put(o, "UIViewContentCompressionResistancePriority", x.s(
            f"{{{el.get('horizontalCompressionResistancePriority', '750')}, "
            f"{el.get('verticalCompressionResistancePriority', '750')}}}"))
    if el.get("preservesSuperviewLayoutMargins") == "YES":
        x.put(o, "UIViewPreservesSuperviewMargins", YES)
        x.put(o, "UIViewPreservesSpecificSuperviewMargins", b.int8(15))
    if el.get("insetsLayoutMarginsFromSafeArea") == "NO":
        x.put(o, "UIViewInsetsLayoutMarginsFromSafeArea", b.int8(0))
    tint = keyed_color(x, el, "tintColor")
    if tint is not None:
        x.put(o, "UITintColor", tint)
    x.put(o, "UIViewSemanticContentAttribute", b.int8(0))
    x.put(o, "UIViewLargeContentStoredProperties", (N.NIL, None))
    x.subtree[o.idx] = {"el": el, "kids": kids, "cons": [], "guides": []}
    if el.find("userDefinedRuntimeAttributes") is not None:
        x.pairs.append((el, o, None))
    conns = el.find("connections")
    if conns is not None:
        x.connections.append((o, conns))
    return o


def view(x, el):
    check(x, el, VIEW_ATTRS, VIEW_SUBS)
    o = new_view_obj(x, el, "UIView")
    view_keys(x, el, o, top=el in x.tops)
    finish_swap(x, o)
    return o


# fontDescription weight -> (PostScript name, CTFont UI usage, UIFontTraits)
SYSTEM_WEIGHTS = {"regular": (".SFUI-Regular", "CTFontRegularUsage", 0),
                  "medium": (".SFUI-Medium", "CTFontMediumUsage", 0),
                  "semibold": (".SFUI-Semibold", "CTFontDemiUsage", 2)}


def system_font(x, size, weight="regular", bold=False):
    """A system UIFont: the name, a descriptor naming the UI usage, the size."""
    b = x.b
    if bold:
        name, usage, traits = ".SFUI-Bold", "CTFontEmphasizedUsage", 2
    elif weight in SYSTEM_WEIGHTS:
        name, usage, traits = SYSTEM_WEIGHTS[weight]
    else:
        raise Unsupported(f"system font weight {weight}")
    attrs = x.new("NSMutableDictionary" if bold else "NSDictionary")
    attrs.add("NSInlinedValue", *YES)
    entries = [("NSCTFontUIUsageAttribute", x.s(usage))]
    if bold:
        entries.append(("NSCTFontUIUsageOverrideAttribute", x.s("CTFontBoldUsage")))
    num = x.new("NSNumber")
    x.put(num, "NS.dblval", b.float64(size))
    entries.append(("NSFontSizeAttribute", num))
    for k, v in entries:
        attrs.add("UINibEncoderEmptyKey", N.OBJREF, x.s(k).idx)
        attrs.add("UINibEncoderEmptyKey", N.OBJREF, v.idx)
    desc = x.new("UIFontDescriptor")
    x.put(desc, "UIFontDescriptorAttributes", attrs)
    x.put(desc, "UIFontDescriptorOptions", b.int64(2147517444))
    f = x.new("UIFont")
    x.put(f, "UIFontName", x.s(name))
    x.put(f, "UIFontDescriptor", desc)
    x.put(f, "UIFontPointSize", b.float64(size))
    x.put(f, "UIFontTextStyleForScaling", (N.NIL, None))
    x.put(f, "UIFontPointSizeForScaling", b.float64(0.0))
    x.put(f, "UIFontMaximumPointSizeAfterScaling", b.float64(0.0))
    x.put(f, "UIFontTraits", b.int8(traits))
    x.put(f, "UISystemFont", YES)
    x.put(f, "NSName", x.s(name))
    x.put(f, "NSSize", b.float64(size))
    return f


def font(x, el):
    """A <fontDescription>."""
    if el is None:
        return None
    a = el.attrib
    if a.get("style"):
        try:
            return x.I.build_font(x.b, el, x.where)
        except x.I.XibError as e:
            raise Unsupported(str(e))
    size = float(a["pointSize"])
    if a.get("type") == "system":
        return system_font(x, size, a.get("weight", "regular"))
    if a.get("type") == "boldSystem":
        return system_font(x, size, bold=True)
    if a.get("name") == ".AppleSystemUIFont" and "type" not in a:
        return system_font(x, size)
    raise Unsupported(f"fontDescription {dict(a)}")


LABEL_ATTRS = VIEW_ATTRS | {
    "text", "textAlignment", "lineBreakMode", "baselineAdjustment", "adjustsFontSizeToFit",
    "minimumFontSize", "minimumScaleFactor", "numberOfLines", "adjustsLetterSpacingToFitWidth",
    "preferredMaxLayoutWidth", "adjustsFontForContentSizeCategory", "highlighted", "enabled"}
LABEL_SUBS = VIEW_SUBS | {"fontDescription", "nil"}


def label(x, el):
    check(x, el, LABEL_ATTRS, LABEL_SUBS)
    b = x.b
    o = new_view_obj(x, el, "UILabel")
    view_keys(x, el, o, top=el in x.tops)
    if el.get("text") is None:
        _fail(el, "label without text (attributed?)", x.where)
    x.put(o, "UIText", x.s(el.get("text")))
    fd = el.find("fontDescription")
    x.put(o, "UIFont", font(x, fd))
    tc = keyed_color(x, el, "textColor")
    if tc is None:
        tc = x.I.build_color(b, "labelColor", x.where)
    x.put(o, "UITextColor", tc)
    x.put(o, "UIShadowOffset", b.geom(0.0, -1.0))
    ta = el.get("textAlignment", "left")
    x.put(o, "UITextAlignment", b.int8(x.I.TEXT_ALIGNMENTS[ta]))
    if el.get("adjustsFontSizeToFit") != "NO":
        x.put(o, "UIAdjustsFontSizeToFit", YES)
    ps = fd.get("pointSize") if fd is not None else None
    if el.get("adjustsFontSizeToFit") != "NO":
        msf = el.get("minimumScaleFactor")
        mfs = el.get("minimumFontSize")
        if mfs is None and msf is not None and ps:
            # Rounded down to a half point (0.2 x 28 -> 5.5; 0.5 x 14 -> 7;
            # the only two cases in the PhotoDay goldens).
            mfs_v = math.floor(_f32(float(msf)) * float(ps) * 2) / 2
        else:
            mfs_v = float(mfs or "8.5")
        x.put(o, "UIMinimumFontSize", b.float32(mfs_v))
        if msf is not None:
            x.put(o, "UIMinimumScaleFactor", b.float32(float(msf)))
        elif mfs is not None and ps:
            x.put(o, "UIMinimumScaleFactor", b.float32(float(mfs) / float(ps)))
    nol = el.get("numberOfLines", "1")
    if nol != "1":
        x.put(o, "UINumberOfLines", int_fit(int(nol)))
    lbm = el.get("lineBreakMode", "tailTruncation")
    if lbm != "tailTruncation":
        x.put(o, "UILineBreakMode", b.int8(x.I.LINE_BREAK_MODES[lbm]))
    if el.get("baselineAdjustment", "alignBaselines") != "alignBaselines":
        x.put(o, "UIBaselineAdjustment", b.int8({"none": 2, "alignCenters": 1}[el.get("baselineAdjustment")]))
    if _bool(el, "adjustsLetterSpacingToFitWidth"):
        x.put(o, "UIAdjustsLetterSpacingToFit", YES)
    if _bool(el, "adjustsFontForContentSizeCategory"):
        x.put(o, "UIAdjustsFontForContentSizeCategory", YES)
    if el.get("preferredMaxLayoutWidth") is not None:
        x.put(o, "UIPreferredMaxLayoutWidth", b.float32(float(el.get("preferredMaxLayoutWidth"))))
    x.put(o, "UIDisableUpdateTextColorOnTraitCollectionChange",
          NO if el.find("nil[@key='textColor']") is not None else YES)
    finish_swap(x, o)
    return o


SYMBOL_SCALES = {"default": 0, "unspecified": -1, "small": 1, "medium": 2, "large": 3}
SYMBOL_WEIGHTS = {"ultraLight": 1, "thin": 2, "light": 3, "regular": 4, "medium": 5,
                  "semibold": 6, "bold": 7, "heavy": 8, "black": 9}


def symbol_configuration(x, el, scale_attr="scale", weight_attr="weight"):
    """UIImageSymbolConfiguration from an element's scale/weight attributes, or None."""
    scale, weight = el.get(scale_attr), el.get(weight_attr)
    if scale is None and weight is None:
        return None
    c = x.new("UIImageSymbolConfiguration")
    if scale is not None:
        x.put(c, "UISymbolScale", x.b.int8(SYMBOL_SCALES[scale]))
    if weight is not None:
        x.put(c, "UISymbolWeight", x.b.int8(SYMBOL_WEIGHTS[weight]))
    return c


def image(x, name, catalog=None, ref=None):
    """An image by name: a named-resource placeholder (width/height 1 when the
    catalog is not available to ibtool), or an SF Symbol for catalog="system",
    with the imageReference's symbol scale."""
    o = x.new("UIImageNibPlaceholder")
    if catalog == "system":
        x.put(o, "UIImageWidth", x.b.float32(0.0))
        x.put(o, "UIImageHeight", x.b.float32(0.0))
        x.put(o, "UIResourceName", x.s(name))
        x.put(o, "UISystemSymbolResourceName", x.s(name))
        if ref is not None and ref.get("variableValue") is not None:
            x.put(o, "UIImageVariableValue", x.b.float64(float(ref.get("variableValue"))))
        cfg = symbol_configuration(x, ref, "symbolScale", "symbolWeight") if ref is not None else None
        x.put(o, "UISymbolImageConfiguration", cfg or (N.NIL, None))
    elif catalog is None:
        x.put(o, "UIImageWidth", x.b.float32(1.0))
        x.put(o, "UIImageHeight", x.b.float32(1.0))
        x.put(o, "UIResourceName", x.s(name))
        x.put(o, "UISystemSymbolResourceName", (N.NIL, None))
        x.put(o, "UISymbolImageConfiguration", (N.NIL, None))
    else:
        raise Unsupported(f"image catalog {catalog}")
    x.put(o, "UIResourceCatalogName", (N.NIL, None))
    return o


def image_ref(x, el, key):
    """An element's image: an <imageReference key=...> child or image/catalog attributes."""
    r = el.find(f"imageReference[@key='{key}']")
    if r is not None:
        extra = set(r.attrib) - {"key", "image", "catalog", "symbolScale", "symbolWeight",
                                 "variableValue", "renderingMode"}
        if extra or r.get("renderingMode"):
            _fail(r, f"imageReference attributes {sorted(extra)}", x.where)
        return image(x, r.get("image"), r.get("catalog"), r)
    if el.get(key) is not None:
        return image(x, el.get(key), el.get("catalog"))
    return None


IMAGE_VIEW_ATTRS = VIEW_ATTRS | {"image", "catalog", "highlightedImage", "adjustsImageSizeForAccessibilityContentSizeCategory"}
IMAGE_VIEW_SUBS = VIEW_SUBS | {"imageReference", "preferredSymbolConfiguration"}


def image_view(x, el):
    check(x, el, IMAGE_VIEW_ATTRS, IMAGE_VIEW_SUBS)
    if el.get("highlightedImage") or el.get("adjustsImageSizeForAccessibilityContentSizeCategory"):
        _fail(el, "imageView attributes", x.where)
    o = new_view_obj(x, el, "UIImageView")
    p = el.find("preferredSymbolConfiguration[@key='preferredSymbolConfiguration']")
    # An image view with a symbol configuration is never opaque (8/8 goldens).
    view_keys(x, el, o, top=el in x.tops, opaque=p is None)
    img = image_ref(x, el, "image")
    if img is not None:
        x.put(o, "UIImage", img)
    if p is not None:
        x.put(o, "UIPreferredSymbolConfiguration", symbol_configuration(x, p))
    finish_swap(x, o)
    return o


CONTROL_ATTRS = {"contentHorizontalAlignment", "contentVerticalAlignment", "enabled",
                 "selected", "highlighted"}
H_ALIGN = {"center": 0, "left": 1, "right": 2, "fill": 3, "leading": 4, "trailing": 5}
V_ALIGN = {"center": 0, "top": 1, "bottom": 2, "fill": 3}


def control_keys(x, el, o):
    b = x.b
    h = el.get("contentHorizontalAlignment", "center")
    if h != "center":
        x.put(o, "UIContentHorizontalAlignment", b.int8(H_ALIGN[h]))
    v = el.get("contentVerticalAlignment", "center")
    if v != "center":
        x.put(o, "UIContentVerticalAlignment", b.int8(V_ALIGN[v]))
    if el.get("enabled") == "NO":
        x.put(o, "UIDisabled", YES)
    if _bool(el, "selected"):
        x.put(o, "UISelected", YES)
    if _bool(el, "highlighted"):
        x.put(o, "UIHighlighted", YES)


BUTTON_ATTRS = VIEW_ATTRS | CONTROL_ATTRS | {"buttonType", "lineBreakMode",
                                             "adjustsImageWhenHighlighted",
                                             "adjustsImageWhenDisabled", "showsTouchWhenHighlighted",
                                             "reversesTitleShadowWhenHighlighted"}
BUTTON_SUBS = VIEW_SUBS | {"fontDescription", "state", "inset", "buttonConfiguration", "accessibility"}
BUTTON_STATES = {"normal": 0, "highlighted": 1, "disabled": 2, "selected": 4}
BUTTON_TYPES = {"system": 1, "detailDisclosure": 2, "infoLight": 3, "infoDark": 4,
                "contactAdd": 5, "roundedRect": 1, "close": 7}


def _tint_background(x, el):
    """A plain configuration's background: the button's tint at alpha 0.18,
    clearColor without one (probe buttoncfg)."""
    t = el.find("color[@key='tintColor']")
    if t is None:
        return x.I.build_clear_color(x.b, x.where)
    c = x.new("UIColor")
    c.unshared = True  # one per configuration, equal or not (VCSort golden)
    a = t.attrib
    space = a.get("customColorSpace") if a.get("colorSpace") == "custom" else a.get("colorSpace")
    if "systemColor" in a:
        spec = x.I.SYSTEM_COLORS.get(a["systemColor"]) or x.resources.get(a["systemColor"])
        if spec is None:
            _fail(t, f"tint {a['systemColor']}", x.where)
        if spec[0] == "white":
            return _white_components(x, c, spec[1], 0.18)
        return _rgb_components(x, c, list(spec[1:4]) + [0.18])
    if "white" in a and space == "genericGamma22GrayColorSpace":
        return _white_components(x, c, float(a["white"]), 0.18)
    if "red" in a and space == "sRGB":
        return _rgb_components(x, c, [float(a[k]) for k in ("red", "green", "blue")] + [0.18])
    _fail(t, "tint color form under a buttonConfiguration", x.where)


def _background_configuration(x, el):
    bg = x.new("UIBackgroundConfiguration")
    b = x.b
    clear = x.I.build_clear_color(b, x.where)
    for k, v in (("customView", (N.NIL, None)), ("hasCustomized-customView", NO),
                 ("cornerRadius", b.float64(17.0)), ("hasCustomized-cornerRadius", NO),
                 ("maskedCorners", b.int8(15)), ("hasCustomized-maskedCorners", NO),
                 ("backgroundColor", _tint_background(x, el)), ("hasCustomized-backgroundColor", NO),
                 ("backgroundColorTransformerIdentifier", b.int8(0)),
                 ("hasCustomized-backgroundColorTransformerIdentifier", NO),
                 ("visualEffect", (N.NIL, None)), ("hasCustomized-visualEffect", NO),
                 ("visualEffectGroupName", (N.NIL, None)), ("hasCustomized-visualEffectGroupName", NO),
                 ("visualEffectblurClippingMode", b.int8(0)),
                 ("image", (N.NIL, None)), ("hasCustomized-image", NO),
                 ("imageContentMode", b.int8(0)), ("hasCustomized-imageContentMode", NO),
                 ("backgroundInsets", x.s("{0, 0, 0, 0}")), ("hasCustomized-backgroundInsets", NO),
                 ("edgesAddingLayoutMarginsToBackgroundInsets", b.int8(0)),
                 ("hasCustomized-edgesAddingLayoutMarginsToBackgroundInsets", NO),
                 ("strokeColor", clear), ("hasCustomized-strokeColor", YES),
                 ("strokeColorTransformerIdentifier", b.int8(0)),
                 ("hasCustomized-strokeColorTransformerIdentifier", NO),
                 ("strokeWidth", b.float64(1.0)), ("hasCustomized-strokeWidth", YES),
                 ("strokeOutset", b.float64(0.0)), ("hasCustomized-strokeOutset", NO),
                 ("strokeLocation", b.int8(0)), ("defaultStyle", b.int8(0)),
                 ("defaultStyle_v2", b.int8(0))):
        x.put(bg, k, v)
    return bg


def button_configuration(x, el, cfg_el):
    """<buttonConfiguration style="plain"> -> UIButtonConfiguration (PhotoDay
    goldens, probe buttoncfg)."""
    if set(cfg_el.attrib) != {"key", "style"} or cfg_el.get("style") != "plain":
        _fail(cfg_el, "buttonConfiguration shape", x.where)
    b = x.b
    cfg = x.new("UIButtonConfiguration")
    for k, v in (("UIButtonConfigurationButtonStyle", b.int8(0)),
                 ("UIButtonConfigurationBackground", _background_configuration(x, el)),
                 ("UIButtonConfigurationCornerStyle", b.int8(0)),
                 ("UIButtonConfigurationButtonSize", b.int8(0)),
                 ("UIButtonConfigurationMacIdiomStyle", b.int8(0))):
        x.put(cfg, k, v)
    for k in ("BaseBackgroundColor", "BaseForegroundColor", "Image",
              "PreferredSymbolConfigurationForImage", "SymbolContentTransition"):
        x.put(cfg, "UIButtonConfiguration" + k, (N.NIL, None))
    x.put(cfg, "UIButtonConfigurationShowsActivityIndicator", NO)
    for k in ("Title", "Subtitle"):
        t = x.new("NSAttributedString")
        x.put(t, "NSString", x.s(""))
        x.put(cfg, "UIButtonConfiguration" + k, t)
    for ch in cfg_el:
        if ch.tag != "directionalEdgeInsets" or ch.get("key") != "contentInsets":
            _fail(ch, "buttonConfiguration child", x.where)
        ins = "{" + ", ".join(f"{float(ch.get(k, '0')):g}" for k in ("top", "leading", "bottom", "trailing")) + "}"
        x.put(cfg, "UIButtonConfigurationContentInset", x.s(ins))
    x.put(cfg, "UIButtonConfigurationImagePlacement", b.int8(2))
    x.put(cfg, "UIButtonConfigurationImagePadding", b.float32(0.0))
    x.put(cfg, "UIButtonConfigurationTitlePadding", b.float32(1.0))
    x.put(cfg, "UIButtonConfigurationTitleAlignment", b.int8(0))
    return cfg


def button(x, el):
    check(x, el, BUTTON_ATTRS, BUTTON_SUBS)
    b = x.b
    o = new_view_obj(x, el, "UIButton")
    view_keys(x, el, o, top=el in x.tops)
    control_keys(x, el, o)
    acc = el.find("accessibility[@key='accessibilityConfiguration']")
    if acc is not None:
        x.accessibility.append((o, acc.get("label")))
    cfg_el = el.find("buttonConfiguration[@key='configuration']")
    if cfg_el is not None:
        # A configured button keeps only the configuration: no stateful
        # content, font or insets (the document's <state> title is dropped).
        if el.findall("inset") or el.find("fontDescription") is not None:
            _fail(el, "configured button with insets or font", x.where)
        x.put(o, "UIAdjustsImageWhenHighlighted", YES)
        x.put(o, "UIAdjustsImageWhenDisabled", YES)
        if el.get("buttonType") is not None:
            x.put(o, "UIButtonType", b.int8(BUTTON_TYPES[el.get("buttonType")]))
        x.put(o, "UIButtonConfiguration", button_configuration(x, el, cfg_el))
        finish_swap(x, o)
        return o
    states = el.findall("state")
    if states:
        d = x.new("NSMutableDictionary")
        d.add("NSInlinedValue", *YES)
        for st in states:
            num = x.new("NSNumber")
            x.put(num, "NS.intval", b.int8(BUTTON_STATES[st.get("key")]))
            c = x.new("UIButtonContent")
            for k in st.attrib:
                if k not in ("key", "title", "image", "catalog", "backgroundImage"):
                    _fail(st, f"state attribute {k}", x.where)
            if st.get("title") is not None:
                x.put(c, "UITitle", x.s(st.get("title")))
            if st.get("image") is not None:
                x.put(c, "UIImage", image(x, st.get("image"), st.get("catalog")))
            if st.get("backgroundImage") is not None:
                x.put(c, "UIBackgroundImage", image(x, st.get("backgroundImage"), st.get("catalog")))
            for ch in st:
                if ch.tag != "color" or ch.get("key") not in ("titleColor", "titleShadowColor"):
                    _fail(st, f"state child {ch.tag} {ch.get('key')}", x.where)
            tc = keyed_color(x, st, "titleColor")
            if tc is not None:
                x.put(c, "UITitleColor", tc)
            sc = keyed_color(x, st, "titleShadowColor")
            if sc is not None:
                x.put(c, "UIShadowColor", sc)
            d.add("UINibEncoderEmptyKey", N.OBJREF, num.idx)
            d.add("UINibEncoderEmptyKey", N.OBJREF, c.idx)
        x.put(o, "UIButtonStatefulContent", d)
    x.put(o, "UIAdjustsImageWhenHighlighted", YES)
    x.put(o, "UIAdjustsImageWhenDisabled", YES)
    if el.get("buttonType") is not None:
        x.put(o, "UIButtonType", b.int8(BUTTON_TYPES[el.get("buttonType")]))
    fd = el.find("fontDescription")
    # Default font: 15pt for a system button, 18pt for a custom one (PhotoDay goldens).
    default = 15.0 if el.get("buttonType") == "system" else 18.0
    x.put(o, "UIFont", font(x, fd) if fd is not None else system_font(x, default))
    for ins in el.findall("inset"):
        key = {"imageEdgeInsets": "UIImageEdgeInsets", "titleEdgeInsets": "UITitleEdgeInsets",
               "contentEdgeInsets": "UIContentEdgeInsets"}.get(ins.get("key"))
        if key is None:
            _fail(ins, f"inset {ins.get('key')}", x.where)
        x.put(o, key, b.geom(*(float(ins.get(k, "0")) for k in ("minY", "minX", "maxY", "maxX"))))
    x.put(o, "UIButtonConfiguration", (N.NIL, None))
    finish_swap(x, o)
    return o


SCROLL_ATTRS = VIEW_ATTRS | {"showsHorizontalScrollIndicator", "showsVerticalScrollIndicator",
                             "bouncesZoom", "bounces", "alwaysBounceVertical",
                             "alwaysBounceHorizontal", "pagingEnabled", "scrollEnabled",
                             "keyboardDismissMode", "directionalLockEnabled",
                             "contentInsetAdjustmentBehavior"}


def scroll_keys(x, el, o):
    b = x.b
    for attr in ("pagingEnabled", "directionalLockEnabled",
                 "alwaysBounceHorizontal", "keyboardDismissMode"):
        if el.get(attr) is not None:
            _fail(el, f"scrollView {attr}", x.where)
    insets = el.get("contentInsetAdjustmentBehavior", "automatic")
    if insets not in CONTENT_INSET_ADJUSTMENT:
        _fail(el, f"contentInsetAdjustmentBehavior {insets}", x.where)
    if _bool(el, "alwaysBounceVertical"):
        x.put(o, "UIAlwaysBounceVertical", YES)
    if el.get("showsHorizontalScrollIndicator") == "NO":
        x.put(o, "UIShowsHorizontalScrollIndicator", NO)
    if el.get("showsVerticalScrollIndicator") == "NO":
        x.put(o, "UIShowsVerticalScrollIndicator", NO)
    if el.get("bounces") == "NO":
        x.put(o, "UIBounces", NO)
    if el.get("scrollEnabled") == "NO":
        x.put(o, "UIScrollDisabled", YES)
    if el.get("bouncesZoom") != "NO":
        x.put(o, "UIBouncesZoom", YES)
    x.put(o, "UIScrollViewIndicatorInsetAdjustmentBehavior", b.int8(1))
    x.put(o, "UIScrollViewContentInsetAdjustmentBehavior", b.int8(CONTENT_INSET_ADJUSTMENT[insets]))


CONTENT_INSET_ADJUSTMENT = {"automatic": 0, "scrollableAxes": 1, "never": 2, "always": 3}


def scroll_view(x, el):
    check(x, el, SCROLL_ATTRS, VIEW_SUBS)
    o = new_view_obj(x, el, "UIScrollView")
    view_keys(x, el, o, top=el in x.tops)
    scroll_keys(x, el, o)
    finish_swap(x, o)
    return o


TRAIT_ENUMS = {
    "autocapitalizationType": ("UIAutocapitalizationType",
                               {"none": 0, "words": 1, "sentences": 2, "allCharacters": 3}),
    "autocorrectionType": ("UIAutocorrectionType", {"default": 0, "no": 1, "yes": 2}),
    "spellCheckingType": ("UISpellCheckingType", {"default": 0, "no": 1, "yes": 2}),
    "smartQuotesType": ("UITextSmartQuotesType", {"default": 0, "no": 1, "yes": 2}),
    "smartDashesType": ("UITextSmartDashesType", {"default": 0, "no": 1, "yes": 2}),
    "smartInsertDeleteType": ("UITextSmartInsertDeleteType", {"default": 0, "no": 1, "yes": 2}),
    "keyboardType": ("UIKeyboardType", {"default": 0, "alphabet": 1, "asciiCapable": 1,
                                        "numbersAndPunctuation": 2, "URL": 3, "numberPad": 4,
                                        "phonePad": 5, "namePhonePad": 6, "emailAddress": 7,
                                        "decimalPad": 8, "twitter": 9, "webSearch": 10,
                                        "asciiCapableNumberPad": 11}),
    "returnKeyType": ("UIReturnKeyType", {"default": 0, "go": 1, "google": 2, "join": 3,
                                          "next": 4, "route": 5, "search": 6, "send": 7,
                                          "yahoo": 8, "done": 9, "emergencyCall": 10,
                                          "continue": 11}),
}


def text_input_traits(x, el, o):
    t = el.find("textInputTraits[@key='textInputTraits']")
    if t is None:
        return
    for k, v in t.attrib.items():
        if k == "key":
            continue
        if k in TRAIT_ENUMS:
            key, table = TRAIT_ENUMS[k]
            x.put(o, key, x.b.int8(table[v]))
        elif k == "textContentType":
            x.put(o, "UITextContentType", x.s(v))
        elif k == "secureTextEntry" and v == "YES":
            x.put(o, "UISecureTextEntry", YES)
            # a secure field turns inline predictions off
            x.put(o, "UITextInlinePredictionType", x.b.int8(1))
        else:
            _fail(t, f"text input trait {k}={v}", x.where)


TEXT_FIELD_ATTRS = VIEW_ATTRS | CONTROL_ATTRS | {
    "text", "placeholder", "textAlignment", "minimumFontSize", "clearButtonMode",
    "adjustsFontSizeToFit", "borderStyle", "clearsOnBeginEditing",
    "adjustsFontForContentSizeCategory"}
TEXT_FIELD_SUBS = VIEW_SUBS | {"fontDescription", "textInputTraits"}
CLEAR_BUTTON_MODES = {"never": 0, "whileEditing": 1, "unlessEditing": 2, "always": 3}


def text_field(x, el):
    check(x, el, TEXT_FIELD_ATTRS, TEXT_FIELD_SUBS)
    for a in ("borderStyle", "clearsOnBeginEditing", "adjustsFontForContentSizeCategory"):
        if el.get(a) is not None:
            _fail(el, f"textField {a}", x.where)
    b = x.b
    o = new_view_obj(x, el, "UITextField")
    view_keys(x, el, o, top=el in x.tops)
    control_keys(x, el, o)
    if el.get("text") is not None:
        x.put(o, "UIText", x.s(el.get("text")))
    if el.get("placeholder") is not None:
        x.put(o, "UIPlaceholder", x.s(el.get("placeholder")))
    x.put(o, "UIFont", font(x, el.find("fontDescription")) or system_font(x, 14.0))
    tc = keyed_color(x, el, "textColor")
    if tc is None:
        _fail(el, "textField without textColor", x.where)
    x.put(o, "UITextColor", tc)
    x.put(o, "UITextAlignment", b.int8(x.I.TEXT_ALIGNMENTS[el.get("textAlignment", "left")]))
    if el.get("adjustsFontSizeToFit") != "NO":
        x.put(o, "UIAdjustsFontSizeToFit", YES)
    x.put(o, "UIMinimumFontSize", b.float32(float(el.get("minimumFontSize", "0"))))
    x.put(o, "UIClearButtonOffset", b.geom(3.0, 1.0))
    if el.get("clearButtonMode") is not None:
        x.put(o, "UIClearButtonMode", b.int8(CLEAR_BUTTON_MODES[el.get("clearButtonMode")]))
    x.put(o, "UIDisableTextColorUpdateOnTraitCollectionChange", YES)
    text_input_traits(x, el, o)
    finish_swap(x, o)
    return o


SWITCH_ATTRS = VIEW_ATTRS | CONTROL_ATTRS | {"on"}
SWITCH_SUBS = VIEW_SUBS


def switch(x, el):
    check(x, el, SWITCH_ATTRS, SWITCH_SUBS)
    if el.find("color[@key='onTintColor']") is not None or el.find("color[@key='thumbTintColor']") is not None:
        _fail(el, "switch tint colors", x.where)
    o = new_view_obj(x, el, "UISwitch")
    view_keys(x, el, o, top=el in x.tops)
    control_keys(x, el, o)
    x.put(o, "UISwitchEnabled", YES)
    if _bool(el, "on"):
        x.put(o, "UISwitchOn", YES)
    # UISwitch resets its frame to its intrinsic size when decoded, so Apple
    # restores the document frame with a key-value pair.
    fx, fy, w, h = _frame(x, el)
    x.pairs.append((el, o, ("frame", f"{{{{{fx:g}, {fy:g}}}, {{{w:g}, {h:g}}}}}")))
    finish_swap(x, o)
    return o


# style -> (UIActivityIndicatorViewStyle-Modern, UIActivityIndicatorViewStyle)
ACTIVITY_STYLES = {"whiteLarge": (0, 0), "medium": (100, 1)}
ACTIVITY_ATTRS = VIEW_ATTRS | {"style", "animating", "hidesWhenStopped"}


def activity_indicator(x, el):
    check(x, el, ACTIVITY_ATTRS, VIEW_SUBS)
    if el.get("hidesWhenStopped") == "NO" or el.get("style") not in ACTIVITY_STYLES:
        _fail(el, "activityIndicatorView style", x.where)
    o = new_view_obj(x, el, "UIActivityIndicatorView")
    view_keys(x, el, o, top=el in x.tops)
    modern, legacy = ACTIVITY_STYLES[el.get("style")]
    x.put(o, "UIActivityIndicatorViewStyle-Modern", x.b.int8(modern))
    x.put(o, "UIActivityIndicatorViewStyle", x.b.int8(legacy))
    c = keyed_color(x, el, "color")
    if c is not None:
        x.put(o, "UIColor", c)
        x.put(o, "UITintColor", c)
        x.put(o, "UIDisableUpdateColorOnTraitCollectionChange", YES)
    else:
        # No color element: nil; medium also tints secondaryLabelColor (goldens).
        x.put(o, "UIColor", (N.NIL, None))
        if el.get("style") == "medium":
            x.put(o, "UITintColor", x.I.build_color(x.b, "secondaryLabelColor", x.where, x.resources))
    if _bool(el, "animating"):
        x.put(o, "UIAnimating", YES)
    finish_swap(x, o)
    return o


SEGMENTED_ATTRS = VIEW_ATTRS | CONTROL_ATTRS | {"segmentControlStyle", "selectedSegmentIndex"}


def segmented_control(x, el):
    """<segmentedControl> with titled segments (probe segmented; PhotoDay goldens)."""
    check(x, el, SEGMENTED_ATTRS, VIEW_SUBS | {"segments"})
    if el.get("segmentControlStyle", "plain") != "plain":
        _fail(el, "segmentControlStyle", x.where)
    b = x.b
    o = new_view_obj(x, el, "UISegmentedControl")
    view_keys(x, el, o, top=el in x.tops)
    control_keys(x, el, o)
    v = el.get("contentVerticalAlignment", "center")
    if v != "center":
        x.put(o, "UIContentVerticalAlignment2", b.int8(V_ALIGN[v]))
    x.put(o, "UIPointerInteractionEnabled", YES)
    segs = []
    for n, s in enumerate(el.find("segments") if el.find("segments") is not None else ()):
        if s.tag != "segment" or set(s.attrib) != {"title"} or len(s):
            _fail(s, "segment shape", x.where)
        q = x.new("UISegment")
        # Segments are unsized until layout: (0, 0, 0, 32) at any control height.
        x.put(q, "UIBounds", b.geom(0.0, 0.0, 0.0, 32.0))
        x.put(q, "UICenter", b.geom(0.0, 16.0))
        x.put(q, "UIDeepDrawRect", YES)
        x.put(q, "UIAutoresizeSubviews", YES)
        x.put(q, "UIViewSemanticContentAttribute", b.int8(0))
        x.put(q, "UIViewLargeContentStoredProperties", (N.NIL, None))
        x.put(q, "UISegmentInfo", x.s(s.get("title")))
        x.put(q, "UISegmentStyle", b.int8(5))
        # As Apple's ibtool 27 writes them: none, 3, then 1 for every later one.
        if n:
            x.put(q, "UISegmentPosition", b.int8(3 if n == 1 else 1))
        segs.append(q)
    sel = int(el.get("selectedSegmentIndex", "-1"))
    # The selected segment's view is last (frontmost).
    views = [q for n, q in enumerate(segs) if n != sel] + ([segs[sel]] if 0 <= sel < len(segs) else [])
    x.put(o, "UISubviews", x.array(views, "NSMutableArray"))
    x.put(o, "UISegments", x.array(segs, "NSMutableArray"))
    x.put(o, "UISelectedSegmentIndex", int_fit(sel))
    finish_swap(x, o)
    return o


CELL_ATTRS = VIEW_ATTRS | {"selectionStyle", "indentationWidth", "reuseIdentifier"}
CELL_SUBS = VIEW_SUBS | {"tableViewCellContentView"}
CONTENT_VIEW_ATTRS = VIEW_ATTRS | {"tableViewCell"}


def table_view_cell(x, el):
    """<tableViewCell>: the content view plus a separator Apple synthesizes
    (1pt, inset 8, at the bottom), which the objects list leaves out."""
    check(x, el, CELL_ATTRS, CELL_SUBS)
    if el.get("selectionStyle", "default") != "default":
        _fail(el, f"selectionStyle {el.get('selectionStyle')}", x.where)
    o = new_view_obj(x, el, "UITableViewCell")
    view_keys(x, el, o, top=el in x.tops)
    cv_el = el.find("tableViewCellContentView[@key='contentView']")
    if cv_el is None:
        _fail(el, "no contentView", x.where)
    check(x, cv_el, CONTENT_VIEW_ATTRS, VIEW_SUBS)
    cv = new_view_obj(x, cv_el, "UITableViewCellContentView")
    view_keys(x, cv_el, cv, top=False)
    _, _, w, h = _frame(x, el)
    sep = x.new("_UITableViewCellSeparatorView")
    x.put(sep, "UIBounds", x.b.geom(0.0, 0.0, w - 8.0, 1.0))
    x.put(sep, "UICenter", x.b.geom(8.0 + (w - 8.0) / 2, h - 0.5))
    for k in ("UIDeepDrawRect", "UIOpaque", "UIAutoresizeSubviews"):
        x.put(sep, k, YES)
    x.put(sep, "UIViewSemanticContentAttribute", x.b.int8(0))
    x.put(sep, "UIViewLargeContentStoredProperties", (N.NIL, None))
    x.put(o, "UISubviews", x.array([cv, sep], "NSMutableArray"))
    x.subtree[o.idx]["kids"] = [cv]
    if el.get("reuseIdentifier"):
        x.put(o, "UIReuseIdentifier", x.s(el.get("reuseIdentifier")))
    x.put(o, "UIIndentationWidth", x.b.float32(float(el.get("indentationWidth", "10"))))
    x.put(o, "UISystemBackgroundView", (N.NIL, None))
    x.put(o, "UIContentConfigurationView", (N.NIL, None))
    x.put(o, "UIContentView", cv)
    for k in ("UITextLabel", "UIDetailTextLabel", "UIImageView"):
        x.put(o, k, (N.NIL, None))
    finish_swap(x, o)
    return o


TABLE_ATTRS = SCROLL_ATTRS | {"style", "separatorStyle", "allowsSelection", "rowHeight",
                              "estimatedRowHeight", "sectionHeaderHeight",
                              "sectionFooterHeight", "contentViewInsetsToSafeArea"}


def table_view(x, el):
    """<tableView> with no cells of its own (the PhotoDay widgets register
    their cells in code)."""
    check(x, el, TABLE_ATTRS, VIEW_SUBS - {"subviews"})
    for attr, ok in (("style", "plain"), ("separatorStyle", "none")):
        if el.get(attr) != ok:
            _fail(el, f"{attr} {el.get(attr)}", x.where)
    b = x.b
    o = new_view_obj(x, el, "UITableView")
    view_keys(x, el, o, top=el in x.tops)
    scroll_keys(x, el, o)
    x.put(o, "UITableViewStyle", b.int8(0))
    x.put(o, "UIStyle", b.int8(0))
    x.put(o, "UISeparatorStyleIOS9AndLater", b.int8(0))
    x.put(o, "UIPrefetchingEnabled", YES)
    for key, attr, default in (("UIRowHeight", "rowHeight", "-1"),
                               ("UISectionHeaderHeight", "sectionHeaderHeight", "-1"),
                               ("UISectionFooterHeight", "sectionFooterHeight", "-1"),
                               ("UIEstimatedRowHeight", "estimatedRowHeight", "-1")):
        x.put(o, key, b.float32(float(el.get(attr, default))))
    x.put(o, "UIEstimatedSectionHeaderHeight", b.float32(0.0))
    x.put(o, "UIEstimatedSectionFooterHeight", b.float32(0.0))
    x.put(o, "UIFillerRowHeight", b.float64(0.0))
    x.put(o, "UISectionHeaderTopPadding", b.float64(-1.0))
    x.put(o, "UIAllowsSelection", NO if el.get("allowsSelection") == "NO" else YES)
    x.put(o, "UIInsetsContentViewsToSafeArea",
          NO if el.get("contentViewInsetsToSafeArea") == "NO" else YES)
    x.put(o, "UISeparatorInsetReference", b.int8(0))
    finish_swap(x, o)
    return o


BUILDERS = {"switch": switch, "activityIndicatorView": activity_indicator,
            "tableView": table_view,
            "segmentedControl": segmented_control, "tableViewCell": table_view_cell,
            "textField": text_field, "scrollView": scroll_view, "view": view, "label": label, "button": button, "imageView": image_view}


def build(x, el):
    f = BUILDERS.get(el.tag)
    if f is None:
        _fail(el, "unsupported element", x.where)
    return f(x, el)


# ---------------------------------------------------------------- constraints

def _attr_code(I, a):
    return I.ATTRIBUTES.get(a, I.MARGIN_V2.get(a, 0))


def _constraint(x, c, owner_el, owner):
    I, b = x.I, x.b
    o = x.new("NSLayoutConstraint")
    x.ids[c.get("id")] = o  # outlets may point at a constraint (VCFilter)

    def item(i):
        if i is None:
            return owner
        if i not in x.ids:
            _fail(c, f"constraint item {i} unknown", x.where)
        return x.ids[i]

    for k in c.attrib:
        if k not in ("firstItem", "firstAttribute", "secondItem", "secondAttribute",
                     "constant", "id", "relation", "multiplier", "symbolic", "priority",
                     "identifier"):
            _fail(c, f"constraint attribute {k}", x.where)
    fa, sa = c.get("firstAttribute"), c.get("secondAttribute")
    x.put(o, "NSFirstItem", item(c.get("firstItem")))
    x.put(o, "NSFirstAttributeV2", b.int8(_attr_code(I, fa)))
    x.put(o, "NSFirstAttribute", b.int8(I.ATTRIBUTES.get(I.MARGIN_BASE.get(fa, fa), 0)))
    rel = I.RELATIONS.get(c.get("relation", "equal"))
    if rel is not None:
        x.put(o, "NSRelation", b.int8(rel))
    I._add_multiplier(b, o, c)
    if c.get("secondItem") is not None:
        x.put(o, "NSSecondItem", item(c.get("secondItem")))
        x.put(o, "NSSecondAttributeV2", b.int8(_attr_code(I, sa)))
        x.put(o, "NSSecondAttribute", b.int8(I.ATTRIBUTES.get(I.MARGIN_BASE.get(sa, sa), 0)))
    if c.get("priority") is not None:
        x.put(o, "NSPriority", int_fit(int(float(c.get("priority")))))

    def margin(a):
        return 8.0 if a in ("leadingMargin", "topMargin") else \
            -8.0 if a in ("bottomMargin", "trailingMargin") else 0.0
    is_margin = margin(fa) != 0.0 or margin(sa) != 0.0
    legacy = margin(sa) - margin(fa)
    if c.get("symbolic") == "YES":
        x.put(o, "NSSymbolicConstant", x.s("NSSpace"))
        x.put(o, "NSShouldBeArchived", YES)
    elif c.get("constant") is not None:
        v = float(c.get("constant"))
        x.put(o, "NSConstantV2", b.float64(v))
        x.put(o, "NSShouldBeArchived", YES)
        x.put(o, "NSConstant", b.float64(v + legacy if is_margin else v))
    elif is_margin:
        x.put(o, "NSShouldBeArchived", YES)
        x.put(o, "NSConstant", b.float64(legacy))
    else:
        x.put(o, "NSShouldBeArchived", YES)
    if c.get("identifier") is not None:
        x.put(o, "NSLayoutIdentifier", x.s(c.get("identifier")))
    return o


def _guides(x, el, o):
    """Layout guides a view declares: the safe area (archived in
    UIViewLayoutGuides, with its system constraints) and the keyboard guide
    (archived only where a constraint uses it). Every guide gets a placeholder
    in the objects list, used or not."""
    st = x.subtree[o.idx]
    for g in el:
        if g.tag == "viewLayoutGuide" and g.get("key") == "safeArea":
            cache = []
            guide = x.I._safe_area_guide(x.b, o, cache)
            guide.original_class = None
            for c in guide.values:
                pass
            x.ids[g.get("id")] = guide
            st["guides"].append(("UILayoutGuide", guide))
            # UIViewLayoutGuides only when a constraint uses the guide
            # (PhotoDay goldens: 32 used, 11 unused).
            if g.get("id") in x.constraint_items:
                st["archived_guides"] = [guide]
        elif g.tag == "keyboardLayoutGuide" and g.get("key") == "keyboard":
            k = x.new("UIKeyboardLayoutGuide")
            x.put(k, "UILayoutGuideOwningView", o)
            x.put(k, "UILayoutGuideIdentifier", x.s("UIViewKeyboardLayoutGuide"))
            x.put(k, "UILayoutGuideOwningViewIsLocked", YES)
            x.put(k, "UILayoutGuideShouldBeArchived", NO)
            x.put(k, "UILayoutGuideAllowsNegativeDimensions", NO)
            x.put(k, "UILayoutGuideSystemConstraints", (N.NIL, None))
            x.put(k, "UIKeyboardLayoutGuideFollowsUndocked", NO)
            x.ids[g.get("id")] = k
            st["guides"].append(("UIKeyboardLayoutGuide", k))
        elif (g.tag == "viewLayoutGuide" and g.get("key") in ("contentLayoutGuide", "frameLayoutGuide")
              and g.get("id") not in x.constraint_items):
            # A scroll view's unused content/frame guide: only a placeholder
            # in the objects list (VCSettings golden).
            st["guides"].append(("UILayoutGuide", None))
        elif g.tag in ("viewLayoutGuide", "keyboardLayoutGuide"):
            _fail(g, f"layout guide {g.get('key')}", x.where)


OWNER_ORDER = ("width", "height", "centerX", "centerY", "trailing", "leading", "bottom", "top")
OTHER_ORDER = ("centerX", "trailing", "leading", "centerY", "bottom", "top", "width", "height")
RELATION_ORDER = {"lessThanOrEqual": 0, "greaterThanOrEqual": 1, "equal": 2}


def constraint_order(I, view_el, cons):
    """Apple's order of a view's constraints (tools/ibtool._constraint_order,
    extended on the PhotoDay corpus)."""
    owner = view_el.get("id")
    par = I._constraint_order_parents(view_el, owner)
    guides, index, origin = I._constraint_order_frames(view_el)
    guides = guides + [g.get("id") for g in view_el.findall("keyboardLayoutGuide")]

    def key(c):
        first, second = c.get("firstItem", owner), c.get("secondItem")
        fa, sa = c.get("firstAttribute"), c.get("secondAttribute")
        rel = RELATION_ORDER[c.get("relation", "equal")]
        if first == owner and second is None:
            # the view's own size constraints come first
            return (0, 0.0, 0.0, 1, OTHER_ORDER.index(fa), rel, 0)
        guide_item = first in guides and second is not None and second not in guides \
            and second != owner
        try:
            item, attr, other, oattr, gkey = I._constraint_order_placement(
                c, first, second, fa, sa, guide_item, owner, guides, index, origin, par,
                "x", False)
        except I.XibError as e:
            raise Unsupported(str(e))
        if other == owner:
            if attr not in OWNER_ORDER:
                raise Unsupported(f"constraint {c.get('id')}: {attr}/{oattr} to the owner view")
            return gkey + (0, OWNER_ORDER.index(attr), 0, 0)
        if attr not in OTHER_ORDER or (oattr is not None and oattr not in OTHER_ORDER):
            raise Unsupported(f"constraint {c.get('id')}: attribute {attr}/{oattr}")
        other_idx = 0 if guide_item else index.get(other, -1)
        return gkey + (1, OTHER_ORDER.index(attr), rel, other_idx)

    # Groups at the same frame origin tie; Apple then orders them by the item's
    # document id, as strings (VCTermsModal: 1zG before Qcb, c9Q before pWd).
    def key2(c):
        k = key(c)
        first, second = c.get("firstItem", owner), c.get("secondItem")
        item = second if first == owner and second is not None else first
        return k[:3] + (item or "",) + k[3:]
    return sorted(cons, key=key2)


def constraints_pass(x):
    """After every view exists: guides, then each view's constraints in Apple's order."""
    x.constraint_items = {i for c in x.doc.iter("constraint")
                          for i in (c.get("firstItem"), c.get("secondItem")) if i}
    for idx, st in list(x.subtree.items()):
        _guides(x, st["el"], x.b.objects[idx])
    for idx, st in x.subtree.items():
        o = x.b.objects[idx]
        el = st["el"]
        safe = st.get("archived_guides")
        if safe:
            x.put(o, "UIViewLayoutGuides", x.array(safe, "NSMutableArray"))
        cel = el.find("constraints")
        if cel is None:
            continue
        ordered = constraint_order(x.I, el, cel.findall("constraint"))
        cons = [_constraint(x, c, el, o) for c in ordered]
        st["cons"] = cons
        x.put(o, "UIViewAutolayoutConstraints", x.array(cons, "NSMutableArray"))
    # A view whose position constraints decide stores the flag; one still
    # sized by its autoresizing mask (fixedFrame) does not.
    referenced = set()
    for st in x.subtree.values():
        cel = st["el"].find("constraints")
        for c in (cel.findall("constraint") if cel is not None else ()):
            referenced.update(filter(None, (c.get("firstItem"), c.get("secondItem"))))
            referenced.add(st["el"].get("id"))
    for idx, st in x.subtree.items():
        el = st["el"]
        if (el.get("translatesAutoresizingMaskIntoConstraints") == "NO"
                and (el.get("id") in referenced or el in x.tops)):
            x.put(x.b.objects[idx], "UIViewDoesNotTranslateAutoresizingMaskIntoConstraints", YES)


# ---------------------------------------------------------------- objects list

def _guide_placeholder(x, cls):
    g = x.new(cls)
    x.put(g, "UILayoutGuideOwningView", (N.NIL, None))
    x.put(g, "UILayoutGuideIdentifier", x.s(""))
    x.put(g, "UILayoutGuideOwningViewIsLocked", NO)
    x.put(g, "UILayoutGuideShouldBeArchived", NO)
    x.put(g, "UILayoutGuideAllowsNegativeDimensions", NO)
    if cls == "UIKeyboardLayoutGuide":
        x.put(g, "UIKeyboardLayoutGuideFollowsUndocked", NO)
    return g


def _rest(x, o):
    """Everything under a top-level object, breadth first: as each view is
    reached, its guide placeholders (keyboard first), constraints and subviews
    (VCPickSubjectsGroup golden)."""
    out = []
    queue = [o]
    while queue:
        st = x.subtree.get(queue.pop(0).idx)
        if st is None:
            continue
        for cls, _ in sorted(st["guides"], key=lambda g: g[0] != "UIKeyboardLayoutGuide"):
            out.append(_guide_placeholder(x, cls))
        out += st["cons"] + st["kids"]
        queue += st["kids"]
    return out


# ---------------------------------------------------------------- connections

EVENT_MASKS = {"touchUpInside": 64, "valueChanged": 4096, "editingChanged": 131072,
               "touchDown": 1, "editingDidEnd": 262144, "editingDidBegin": 65536,
               "primaryActionTriggered": 8192, "editingDidEndOnExit": 524288}


def connections_pass(x, proxies):
    outlets, events = [], []
    collections = {}  # (source idx, property) -> outlets entry
    for src, conns in x.connections:
        for c in conns:
            dst = c.get("destination")
            if dst not in x.ids:
                _fail(c, f"connection destination {dst}", x.where)
            if c.tag == "outlet":
                outlets.append(("UIRuntimeOutletConnection", c.get("property"), src, x.ids[dst]))
            elif c.tag == "outletCollection":
                # One connection per property; its destinations, in document
                # order, in an array of the collection class (PhotoDay goldens).
                cls = c.get("collectionClass")
                if cls is None:
                    _fail(c, "outlet collection without collectionClass", x.where)
                k = (src.idx, c.get("property"))
                if k not in collections:
                    collections[k] = ("UIRuntimeOutletCollectionConnection", c.get("property"), src, [], cls)
                    outlets.append(collections[k])
                collections[k][3].append(x.ids[dst])
            elif c.tag == "action":
                ev = c.get("eventType")
                if ev is not None and ev not in EVENT_MASKS:
                    _fail(c, f"eventType {ev}", x.where)
                events.append((c.get("selector"), src, x.ids[dst], ev))
            else:
                _fail(c, "connection kind", x.where)
    # Apple sorts outlets by property name, then actions by event mask (none
    # first) and selector.
    # Outlet collections come first (VCSortList golden).
    outlets.sort(key=lambda t: (len(t) == 4, t[1]))
    events.sort(key=lambda t: (EVENT_MASKS.get(t[3], 0), t[0]))
    out = []
    for cls, label, src, dst, *coll in outlets:
        o = x.new(cls)
        x.put(o, "UILabel", x.s(label))
        x.put(o, "UISource", src)
        if coll:
            x.put(o, "UIDestination", x.array(dst, coll[0]))
            x.put(o, "runtimeCollectionClassName", x.s(coll[0]))
            x.put(o, "addsContentToExistingCollection", NO)
        else:
            x.put(o, "UIDestination", dst)
        out.append(o)
    for sel, src, dst, ev in events:
        o = x.new("UIRuntimeEventConnection")
        x.put(o, "UILabel", x.s(sel))
        x.put(o, "UISource", src)
        x.put(o, "UIDestination", dst)
        if ev is not None:
            x.put(o, "UIEventMask", int_fit(EVENT_MASKS[ev]))
        out.append(o)
    return out


# Apple's order of key-value-pair owners is not derivable from the document: it
# disagrees with tree order, id order and archive order, sometimes within one
# file. Owners in UINibObjectsKey order is the closest rule (36 of 54 goldens).
# Within an owner, pairs follow document order. PAIR_OWNER_ORDER (xib ids), when
# set, overrides the owner order, to compare against a golden modulo it.
PAIR_OWNER_ORDER = None


def pairs_pass(x, listed):
    groups = {}
    for el, target, spec in x.pairs:
        out = groups.setdefault(target.idx, (el, target, []))[2]
        if spec is not None:
            key_path, rect = spec
            p = x.new("UINibKeyValuePair")
            x.put(p, "UIObject", target)
            x.put(p, "UIValue", x.b.nsvalue_rect(rect))
            x.put(p, "UIKeyPath", x.s(key_path))
            out.append(p)
        else:
            out += runtime_attributes(x, el, target)
    pos = {o.idx: n for n, o in enumerate(listed)}
    if PAIR_OWNER_ORDER is not None:
        rank = {i: n for n, i in enumerate(PAIR_OWNER_ORDER)}
        key = lambda g: rank.get(g[0].get("id"), len(rank))  # noqa: E731
    else:
        key = lambda g: pos.get(g[1].idx, len(pos))  # noqa: E731
    return [p for g in sorted(groups.values(), key=key) for p in g[2]]


def runtime_attributes(x, el, target):
    """<userDefinedRuntimeAttributes> -> UINibKeyValuePair objects."""
    pairs = []
    for a in el.find("userDefinedRuntimeAttributes"):
        t, kp = a.get("type"), a.get("keyPath")
        if t == "color":
            v = keyed_color(x, a, "value")
        elif t == "image":
            v = image(x, a.get("value"), a.get("catalog"))
        elif t == "number":
            num = a.find("*[@key='value']")
            v = x.new("NSNumber")
            if num.tag == "integer":
                x.put(v, "NS.intval", int_fit(int(num.get("value"))))
            elif num.tag == "real":
                x.put(v, "NS.dblval", x.b.float64(float(num.get("value"))))
            else:
                _fail(a, f"number <{num.tag}>", x.where)
        elif t == "boolean":
            v = x.new("NSNumber")
            x.put(v, "NS.intval", x.b.int8(1 if a.get("value") == "YES" else 0))
        elif t == "string":
            v = x.s(a.get("value"))
        else:
            _fail(a, f"runtime attribute type {t}", x.where)
        p = x.new("UINibKeyValuePair")
        x.put(p, "UIObject", target)
        x.put(p, "UIValue", v)
        x.put(p, "UIKeyPath", x.s(kp))
        pairs.append(p)
    return pairs


# ---------------------------------------------------------------- document

def compile_xib(path, I):
    doc = ET.parse(path).getroot()
    if doc.get("targetRuntime") != "iOS.CocoaTouch":
        raise Unsupported("not an iOS xib")
    objects = doc.find("objects")
    if objects is None:
        raise Unsupported("no <objects>")
    x = X(I, path, doc)
    x.colors_ok = VIEW_COLORS
    root = x.new("NSObject")
    proxies = []
    x.tops = [el for el in objects if el.tag != "placeholder"]
    if not x.tops:
        raise Unsupported("no top-level object")
    for el in objects:
        if el.tag != "placeholder":
            continue
        extra = set(el.attrib) - {"placeholderIdentifier", "id", "userLabel", "customClass",
                                  "customModule", "customModuleProvider"}
        if extra or any(ch.tag != "connections" for ch in el):
            _fail(el, "placeholder shape", x.where)
        if el.get("placeholderIdentifier") not in ("IBFilesOwner", "IBFirstResponder"):
            _fail(el, f"placeholder {el.get('placeholderIdentifier')}", x.where)
        p = x.new("UIProxyObject")
        x.put(p, "UIProxiedObjectIdentifier", x.s(el.get("placeholderIdentifier")))
        x.ids[el.get("id")] = p
        proxies.append(p)
        if el.find("connections") is not None:
            x.connections.append((p, el.find("connections")))
    for el in x.tops:
        resize_top(x, el)
    tops = [build(x, el) for el in x.tops]
    constraints_pass(x)
    conns = connections_pass(x, proxies)
    listed = list(proxies)
    for t in tops:
        listed += [t] + _rest(x, t)
    pairs = pairs_pass(x, listed)
    empty = x.array([])
    x.put(root, "UINibTopLevelObjectsKey", x.array(proxies + tops))
    x.put(root, "UINibObjectsKey", x.array(listed))
    x.put(root, "UINibConnectionsKey", x.array(conns) if conns else empty)
    x.put(root, "UINibVisibleWindowsKey", empty)
    acc = []
    for target, label in x.accessibility:
        # Apple's key is spelled "Accessibile" (VCSplash golden).
        a = x.new("UIRuntimeAccessibilityConfiguration")
        x.put(a, "UIAccessibileObject", target)
        x.put(a, "UIAccessibilityLabel", x.s(label))
        acc.append(a)
    x.put(root, "UINibAccessibilityConfigurationsKey", x.array(acc) if acc else empty)
    x.put(root, "UINibTraitStorageListsKey", empty)
    x.put(root, "UINibKeyValuePairsKey", x.array(pairs) if pairs else empty)
    x.put(root, "IBUINibLNEVersionKey", x.b.int8(1))
    for o in x.b.objects:
        if not hasattr(o, "original_class"):
            o.original_class = None
    return finalize_dfs(I, x.b, root)
