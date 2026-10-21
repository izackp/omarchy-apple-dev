#!/usr/bin/env python3
"""Remove named frameworks from a built app before it is signed.

  strip-frameworks.py <Runner.app> <Name> [<Name> ...]

Each Name is a framework without `.framework` (AgoraAudioBeautyExtension).
On a Mac this is an archive strip and an `xcodebuild -exportArchive`, because
Xcode has already signed the bundle; here signing comes later, so removing
the directory is the whole step.

A framework that another image in the app still hard-loads is not removed:
dyld would refuse to launch the app. The script checks @rpath, @loader_path
and @executable_path loads and names the image that loads it. A weak load
(LC_LOAD_WEAK_DYLIB) is fine. A name that is not in the app is reported and
skipped.
"""
import os
import re
import shutil
import struct
import sys
from pathlib import Path

LC_LOAD_DYLIB = 0xC
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001F
LC_LOAD_UPWARD_DYLIB = 0x80000023
HARD_LOADS = {LC_LOAD_DYLIB, LC_REEXPORT_DYLIB, LC_LOAD_UPWARD_DYLIB}
DYLIB_LOADS = HARD_LOADS | {LC_LOAD_WEAK_DYLIB}
NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _slice_loads(data, off):
    """(cmd, install name) of each dylib load in the thin Mach-O at data[off:]."""
    magic = struct.unpack_from("<I", data, off)[0]
    if magic == 0xFEEDFACF:
        header = 32
    elif magic == 0xFEEDFACE:
        header = 28
    else:
        return []
    ncmds = struct.unpack_from("<I", data, off + 16)[0]
    loads, at = [], off + header
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<2I", data, at)
        if cmd in DYLIB_LOADS:
            name_off = struct.unpack_from("<I", data, at + 8)[0]
            raw = data[at + name_off:at + size]
            loads.append((cmd, raw.split(b"\0", 1)[0].decode(errors="replace")))
        at += size
    return loads


def dylib_loads(path):
    """(cmd, install name) of each dylib load in a Mach-O file, every slice of a fat
    one; [] for anything that is not Mach-O."""
    with open(path, "rb") as f:
        head = f.read(4)
    if len(head) < 4:
        return []
    big = struct.unpack(">I", head)[0]
    if big not in (0xCAFEBABE, 0xCAFEBABF) and struct.unpack("<I", head)[0] not in (0xFEEDFACF, 0xFEEDFACE):
        return []
    data = Path(path).read_bytes()
    if big in (0xCAFEBABE, 0xCAFEBABF):
        nfat = struct.unpack_from(">I", data, 4)[0]
        entry, fmt = (32, ">2I2QI") if big == 0xCAFEBABF else (20, ">5I")
        loads = []
        for i in range(nfat):
            offset = struct.unpack_from(fmt, data, 8 + i * entry)[2]
            loads += _slice_loads(data, offset)
        return loads
    return _slice_loads(data, 0)


def hard_users(app, name, removing):
    """Images outside the frameworks being removed that hard-load <name>.framework."""
    target = (app / "Frameworks" / f"{name}.framework").resolve()
    bundles = [app] + [p for p in app.rglob("*") if p.is_dir() and p.suffix in (".app", ".appex")]
    users = []
    for path in sorted(app.rglob("*")):
        if not path.is_file() or path.is_symlink() or any(r in path.parents for r in removing):
            continue
        executable_dir = next((p for p in path.parents if p.suffix in (".app", ".appex")), app)
        executable_dirs = bundles if app / "Frameworks" in path.parents else [executable_dir]
        roots = {"@rpath": [app / "Frameworks"], "@loader_path": [path.parent],
                 "@executable_path": executable_dirs}
        for cmd, lib in dylib_loads(path):
            prefix, separator, relative = lib.partition("/")
            if (cmd in HARD_LOADS and separator and prefix in roots
                    and any(target in (root / relative).resolve().parents for root in roots[prefix])):
                users.append(path.relative_to(app))
                break
    return users


def strip(app, names):
    """Remove Frameworks/<name>.framework for each name. Returns (removed, absent, freed bytes).
    Exits naming the loader when a framework is still hard-loaded by the rest of the app."""
    app = Path(app)
    for name in names:
        if not NAME.match(name) or name.startswith("."):
            sys.exit(f"strip-frameworks: '{name}' is not a framework name")
    present = [n for n in names if (app / "Frameworks" / f"{n}.framework").is_dir()]
    absent = [n for n in names if n not in present]
    removing = [app / "Frameworks" / f"{n}.framework" for n in present]
    for name in present:
        users = hard_users(app, name, removing)
        if users:
            sys.exit(f"strip-frameworks: {name}.framework is loaded by {', '.join(map(str, users))}; "
                     "removing it would stop the app at launch")
    freed = 0
    for path in removing:
        freed += sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())
        for d in [path, *path.rglob("*")]:
            if d.is_dir() and not d.is_symlink():
                os.chmod(d, 0o755)  # SwiftPM products are read-only
        shutil.rmtree(path)
    return present, absent, freed


def main(argv):
    if len(argv) < 2:
        sys.exit("usage: strip-frameworks.py <Runner.app> <Name> [<Name> ...]")
    removed, absent, freed = strip(argv[0], argv[1:])
    for name in absent:
        print(f"{name}.framework is not in the app; skipped")
    if removed:
        print(f"removed {', '.join(n + '.framework' for n in removed)} ({freed // 1024} KiB)")


if __name__ == "__main__":
    main(sys.argv[1:])
