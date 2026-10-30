#!/usr/bin/env python3
"""Mine the per-class key order of Apple's ibtool from compiled nibs.

UIKit's encodeWithCoder: writes each class's keys in one fixed order, so every
object of a class in every golden nib agrees on it. This merges the observed
key sequences into one order per class (a UIClassSwapper is filed under its
UIOriginalClassName, with its own two keys last) and writes tools/nibkeys.json,
which tools/ibxib.py emits keys by. Any disagreement between nibs is an error.

Usage: tools/nibkeys-mine.py DIR... (each searched for *.nib, storyboardc included)
"""
import glob
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nibarchive as N  # noqa: E402

SWAP_TAIL = ("UIClassName", "UIOriginalClassName")


def class_of(a, i):
    cls = a.object_class(i)
    if cls == "UIClassSwapper":
        for v in a.object_values(a.objects[i]):
            if a.key(v.key_idx) == "UIOriginalClassName":
                return a.object_string(v.payload)
    return cls


def merge(seqs):
    """One order consistent with every sequence (Kahn's algorithm, ties by first
    appearance), or None if two sequences disagree."""
    after = defaultdict(set)
    first = {}
    for s in seqs:
        for i, k in enumerate(s):
            first.setdefault(k, (len(first)))
            if i:
                after[s[i - 1]].add(k)
    indeg = {k: 0 for k in first}
    for k, nxt in after.items():
        for n in nxt:
            indeg[n] += 1
    ready = sorted((k for k, d in indeg.items() if d == 0), key=first.get)
    out = []
    while ready:
        k = ready.pop(0)
        out.append(k)
        for n in after[k]:
            indeg[n] -= 1
            if indeg[n] == 0:
                ready.append(n)
        ready.sort(key=first.get)
    return out if len(out) == len(first) else None


def main(dirs):
    seqs = defaultdict(list)
    for d in dirs:
        for p in glob.glob(os.path.join(d, "**", "*.nib"), recursive=True):
            if not os.path.isfile(p):
                continue
            a = N.load(p)
            for i, o in enumerate(a.objects):
                keys = [a.key(v.key_idx) for v in a.object_values(o)]
                keys = [k for k in dict.fromkeys(keys) if k not in SWAP_TAIL]
                seqs[class_of(a, i)].append(keys)
    order = {}
    for cls, ss in sorted(seqs.items()):
        m = merge(ss)
        if m is None:
            sys.exit(f"{cls}: nibs disagree on key order")
        order[cls] = m
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nibkeys.json")
    with open(path, "w") as f:
        json.dump(order, f, indent=1, sort_keys=True)
        f.write("\n")
    print(f"{len(order)} classes -> {path}")


if __name__ == "__main__":
    main(sys.argv[1:] or [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "ibtool")])
