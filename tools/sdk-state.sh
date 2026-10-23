# Sourced by install-toolchain.sh and tests/sdk-state/check.sh. No side effects.
#
# xtool stamps every Darwin SDK it builds with darwin-sdk-version.txt, and `xtool dev` refuses an SDK
# whose stamp is not the one its own build writes. The installer builds a portable darwin.xtoolsdk,
# which xtool registers as a "slim" SDK: it cannot be updated in place. So after the xtool pin moves,
# an SDK registered earlier is stale and only a rebuild from Xcode.xip fixes it.

# sdk_bundle_version DIR: the stamp inside a Darwin SDK bundle; empty if it has none (an SDK from
# before the stamp existed, which xtool treats as "develop").
sdk_bundle_version() {
  local f="$1/darwin-sdk-version.txt"
  [ -f "$f" ] && tr -d '\r\n' <"$f"
  return 0
}

# sdk_state DIR EXPECTED: prints "current" when DIR carries the stamp EXPECTED, else "stale".
sdk_state() {
  if [ "$(sdk_bundle_version "$1")" = "$2" ]; then echo current; else echo stale; fi
}

# sdk_stale_note HAS EXPECTED: what to tell the user about a stale SDK.
sdk_stale_note() {
  echo "The Darwin SDK was built by an older xtool (stamp '${1:-none}'; the pinned xtool needs '$2')."
  echo "xtool dev would stop with 'Darwin SDK is out of date, and was installed in slim mode'."
  echo "A slim SDK cannot be updated in place, so it is rebuilt from Xcode.xip."
}
