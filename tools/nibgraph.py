#!/usr/bin/env python3
"""Structural diff of two compiled nibs, walking both object graphs from the root.

Unlike `nibarchive.py diff`, which compares object tables line by line, this
pairs objects by where they are reached (root.UINibObjectsKey[3].UISubviews[0]),
so one missing object does not shift every later line. It prints each place the
graphs disagree: a class, the key list of an object, or a scalar value.

Usage: tools/nibgraph.py diff A.nib B.nib [--max N]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nibarchive as N  # noqa: E402


def _vals(a, i):
    return [(a.key(v.key_idx), v) for v in a.object_values(a.objects[i])]


def _label(a, i):
    s = a.object_string(i)
    return f"{a.object_class(i)} {s!r}" if s is not None else a.object_class(i)


def diff(a, b, limit=40):
    """List of human-readable differences between archives a and b."""
    out = []
    seen = set()

    def walk(i, j, path):
        if len(out) >= limit or (i, j) in seen:
            return
        seen.add((i, j))
        ca, cb = a.object_class(i), b.object_class(j)
        if ca != cb or a.object_string(i) != b.object_string(j):
            out.append(f"{path}: {_label(a, i)} != {_label(b, j)}")
            return
        va, vb = _vals(a, i), _vals(b, j)
        ka = [k for k, _ in va]
        kb = [k for k, _ in vb]
        if ka != kb and not (set(ka) == set(kb) == {"NSInlinedValue", "UINibEncoderEmptyKey"}):
            out.append(f"{path} ({ca}): keys\n    - {ka}\n    + {kb}")
            # keep walking the keys both sides have, so deeper problems show too
        if ka.count("UINibEncoderEmptyKey") != kb.count("UINibEncoderEmptyKey"):
            out.append(f"{path} ({ca}): {ka.count('UINibEncoderEmptyKey')} items != "
                       f"{kb.count('UINibEncoderEmptyKey')}: "
                       f"{[_label(a, v.payload) for k, v in va if v.type == N.OBJREF][:12]} vs "
                       f"{[_label(b, v.payload) for k, v in vb if v.type == N.OBJREF][:12]}")
            return
        nb = {}
        items_b = [v for k, v in vb if k == "UINibEncoderEmptyKey"]
        for k, v in vb:
            if k != "UINibEncoderEmptyKey":
                nb.setdefault(k, v)
        n = 0
        for k, v in va:
            if k == "UINibEncoderEmptyKey":
                w, sub = items_b[n], f"{path}[{n}]"
                n += 1
            elif k in nb:
                w, sub = nb[k], f"{path}.{k}"
            else:
                continue
            if v.type == N.OBJREF and w.type == N.OBJREF:
                walk(v.payload, w.payload, sub)
            elif v.type != w.type or v.payload != w.payload:
                out.append(f"{sub}: {a.render(v)} != {b.render(w)}")
            if len(out) >= limit:
                return

    walk(0, 0, "root")
    if not out and (len(a.objects) != len(b.objects)):
        out.append(f"object count {len(a.objects)} != {len(b.objects)} (unreachable objects?)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("diff")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--max", type=int, default=40)
    args = ap.parse_args(argv)
    d = diff(N.load(args.a), N.load(args.b), args.max)
    print("\n".join(d))
    return 1 if d else 0


if __name__ == "__main__":
    sys.exit(main())
