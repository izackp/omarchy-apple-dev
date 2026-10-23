#!/usr/bin/env bash
# Offline check of the Darwin SDK staleness rules in install-toolchain.sh (issue #78 follow-up).
#   1. tools/sdk-state.sh: stamp reading and the current/stale decision.
#   2. The real step 6 block and the real --repair block, cut out of install-toolchain.sh and run
#      against stubs (swift, sdk_build_and_install, sdk_install_from). Nothing is built or installed.
#   3. Two mutants of those blocks must FAIL the same cases (old "skip when registered" behaviour).
#   4. Optional drift check: XTOOL_SRC=<xtool checkout at XTOOL_SHA> compares XTOOL_SDK_VERSION with
#      the stamp that source would write.
# Run: bash tests/sdk-state/check.sh
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
fail=0
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1"; fail=1; }

# shellcheck source=../../tools/sdk-state.sh
. "$ROOT/tools/sdk-state.sh"
EXP=$(sed -n 's/^XTOOL_SDK_VERSION="\(.*\)"$/\1/p' "$ROOT/install-toolchain.sh")
[ -n "$EXP" ] && ok "XTOOL_SDK_VERSION is set ($EXP)" || { bad "XTOOL_SDK_VERSION missing"; exit 1; }
STALE="epoch=3,darwinTools=1.0.0,oam=1.3.0"

mk() { mkdir -p "$T/$1"; [ -n "${2-}" ] && printf '%s\n' "$2" >"$T/$1/darwin-sdk-version.txt"; return 0; }
mk cur "$EXP"; mk old "$STALE"; mk nover ""
[ "$(sdk_state "$T/cur" "$EXP")" = current ] && ok "stamp equal: current" || bad "stamp equal: current"
[ "$(sdk_state "$T/old" "$EXP")" = stale ] && ok "older stamp: stale" || bad "older stamp: stale"
[ "$(sdk_state "$T/nover" "$EXP")" = stale ] && ok "no stamp file: stale" || bad "no stamp file: stale"
[ "$(sdk_state "$T/absent" "$EXP")" = stale ] && ok "missing dir: stale" || bad "missing dir: stale"
[ "$(sdk_bundle_version "$T/cur")" = "$EXP" ] && ok "newline trimmed" || bad "newline trimmed"

# Cut the real blocks out of the installer.
sed -n '/^sdk_reg=none$/,/^swift sdk list   # must print: darwin$/p' "$ROOT/install-toolchain.sh" | sed '$d' >"$T/step6.sh"
awk '/^  # repair: newest cached bundle/{on=1} on&&/^  swift sdk list   # must print: darwin$/{exit} on' "$ROOT/install-toolchain.sh" >"$T/repair.sh"
[ -s "$T/step6.sh" ] && [ -s "$T/repair.sh" ] && ok "blocks extracted" || { bad "blocks extracted"; exit 1; }

# run BLOCK.sh: env STUB_REG=1 means swift sdk list prints darwin.
run() { # block bundle-dir cache-dir xip(yes|no) srcapp(yes|no) reg(0|1)
  ( set +e
    DARWIN_SDK_BUNDLE="$2" SDK_CACHE="$3" XTOOL_SDK_VERSION="$EXP" STUB_REG="$6"
    export STUB_REG
    XCODE_XIP=""; [ "$4" = yes ] && { XCODE_XIP="$T/Xcode.xip"; : >"$XCODE_XIP"; }
    SDK_SRC="$T/nosrc"; [ "$5" = yes ] && { SDK_SRC="$T/src"; mkdir -p "$SDK_SRC/Xcode.app"; }
    swift() { [ "$STUB_REG" = 1 ] && echo darwin; return 0; }
    sdk_build_and_install() { echo "BUILD $(basename "$1")"; }
    sdk_install_from() { echo "REGISTER $(basename "$1")"; }
    matching_xcode() { echo 27; }
    # shellcheck disable=SC1090
    . "$1" ) 2>&1
}
want() { # name output pattern(grep -E) must(1|0)
  if echo "$2" | grep -Eq "$3"; then [ "$4" = 1 ] && ok "$1" || bad "$1 (unexpected: $3)"; else [ "$4" = 1 ] && bad "$1 (missing: $3)" || ok "$1"; fi
}

check_step6() { # block-file label
  local b=$1 l=$2 out
  out=$(run "$b" "$T/cur" "$T/c0" no no 1); want "$l current: skipped" "$out" "current .*skipping" 1; want "$l current: no build" "$out" "BUILD" 0
  out=$(run "$b" "$T/old" "$T/c0" yes no 1); want "$l stale+xip: note" "$out" "older xtool" 1; want "$l stale+xip: rebuilt" "$out" "BUILD Xcode.xip" 1
  out=$(run "$b" "$T/old" "$T/c0" no yes 1); want "$l stale+Xcode.app: rebuilt" "$out" "BUILD Xcode.app" 1
  out=$(run "$b" "$T/old" "$T/c0" no no 1); want "$l stale, no source: asks for xip" "$out" "One download left" 1; want "$l stale, no source: no skip" "$out" "skipping" 0
  out=$(run "$b" "$T/nover" "$T/c0" yes no 1); want "$l no stamp+xip: rebuilt" "$out" "BUILD Xcode.xip" 1
  out=$(run "$b" "$T/absent" "$T/c0" yes no 0); want "$l none+xip: rebuilt, no note" "$out" "BUILD Xcode.xip" 1; want "$l none: no stale note" "$out" "older xtool" 0
}
check_repair() {
  local b=$1 l=$2 out
  mkdir -p "$T/cs/darwin-iPhoneOS99.xtoolsdk" "$T/cc/darwin-iPhoneOS99.xtoolsdk"
  printf '%s\n' "$STALE" >"$T/cs/darwin-iPhoneOS99.xtoolsdk/darwin-sdk-version.txt"
  printf '%s\n' "$EXP" >"$T/cc/darwin-iPhoneOS99.xtoolsdk/darwin-sdk-version.txt"
  out=$(run "$b" "$T/cur" "$T/cc" no no 1); want "$l cache current: registered" "$out" "REGISTER darwin-iPhoneOS99" 1
  out=$(run "$b" "$T/cur" "$T/cs" no no 1); want "$l cache stale, no xip: not registered" "$out" "REGISTER" 0; want "$l cache stale, no xip: says so" "$out" "older xtool" 1
  mkdir -p "$T/cm/darwin-iPhoneOS98.xtoolsdk" "$T/cm/darwin-iPhoneOS99.xtoolsdk"
  printf '%s\n' "$EXP" >"$T/cm/darwin-iPhoneOS98.xtoolsdk/darwin-sdk-version.txt"
  printf '%s\n' "$STALE" >"$T/cm/darwin-iPhoneOS99.xtoolsdk/darwin-sdk-version.txt"
  touch -d '2 hours ago' "$T/cm/darwin-iPhoneOS98.xtoolsdk"
  out=$(run "$b" "$T/cur" "$T/cm" no no 1); want "$l newest stale, older current: registers the older" "$out" "REGISTER darwin-iPhoneOS98" 1
  out=$(run "$b" "$T/cur" "$T/cs" yes no 1); want "$l cache stale+xip: rebuilt" "$out" "BUILD Xcode.xip" 1; want "$l cache stale+xip: not registered stale" "$out" "REGISTER darwin-iPhoneOS99" 0
  out=$(run "$b" "$T/cur" "$T/cs" no yes 1); want "$l cache stale+Xcode.app: rebuilt" "$out" "BUILD Xcode.app" 1; want "$l cache stale+Xcode.app: not registered stale" "$out" "REGISTER darwin-iPhoneOS99" 0
  out=$(run "$b" "$T/cur" "$T/cx" no yes 1); want "$l cache absent+Xcode.app: rebuilt" "$out" "BUILD Xcode.app" 1
}
check_step6 "$T/step6.sh" "step6"
check_repair "$T/repair.sh" "repair"

# Mutants: the old behaviour must be caught.
sed 's/\[ "\$sdk_reg" = current \]/[ "$sdk_reg" != none ]/' "$T/step6.sh" >"$T/step6.mut"
sed 's/= current \]; then cached=\$c; break; fi/!= none ]; then cached=$c; break; fi/' "$T/repair.sh" >"$T/repair.mut"
cmp -s "$T/step6.sh" "$T/step6.mut" && bad "mutant 1 did not change the block"
cmp -s "$T/repair.sh" "$T/repair.mut" && bad "mutant 2 did not change the block"
n=$(fail=0; check_step6 "$T/step6.mut" m1 | grep -c '^FAIL'); [ "$n" -gt 0 ] && ok "mutant 1 (skip when registered) killed by $n checks" || bad "mutant 1 survived"
n=$(fail=0; check_repair "$T/repair.mut" m2 | grep -c '^FAIL'); [ "$n" -gt 0 ] && ok "mutant 2 (repair ignores stale cache) killed by $n checks" || bad "mutant 2 survived"

# Optional drift check against the pinned xtool source.
if [ -n "${XTOOL_SRC:-}" ]; then
  sha=$(sed -n 's/^XTOOL_SHA=//p' "$ROOT/install-toolchain.sh")
  [ "$(git -C "$XTOOL_SRC" rev-parse HEAD)" = "$sha" ] && ok "XTOOL_SRC is at XTOOL_SHA" || bad "XTOOL_SRC is not at XTOOL_SHA"
  f="$XTOOL_SRC/Sources/XToolSupport/SDKBuilder.swift"
  e=$(sed -n 's/.*static let sdkEpoch = \([0-9]*\).*/\1/p' "$f"); d=$(sed -n 's/.*static let darwinToolsVersion = "\(.*\)".*/\1/p' "$f"); o=$(sed -n 's/.*static let oamVersion = "\(.*\)".*/\1/p' "$f")
  [ "epoch=$e,darwinTools=$d,oam=$o" = "$EXP" ] && ok "XTOOL_SDK_VERSION equals the pinned source's stamp" || bad "XTOOL_SDK_VERSION differs from the pinned source ($e/$d/$o)"
else
  echo "skip drift check (set XTOOL_SRC to an xtool checkout at XTOOL_SHA)"
fi
[ "$fail" = 0 ] && echo "ALL OK" || echo "FAILED"
exit "$fail"
