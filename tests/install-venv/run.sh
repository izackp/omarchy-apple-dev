#!/usr/bin/env bash
# Checks the pymobiledevice3 venv install: created from the resolved
# interpreter, kept while healthy, rebuilt when unhealthy. It sources only the
# venv helper functions from install-toolchain.sh. Interpreters, venvs, and pip
# are stubs. No network and no real venv build.
set -uo pipefail
here=$(dirname "$(readlink -f "$0")")
script=${INSTALLER:-$here/../../install-toolchain.sh}
sdk_version=$(sed -n 's/^XTOOL_SDK_VERSION="\(.*\)"$/\1/p' "$script")
fail=0
CLEANUP=()
tmpworld() { # new temp dir, registered for cleanup
  local d
  d=$(mktemp -d)
  CLEANUP+=("$d")
  printf '%s\n' "$d"
}
cleanup() {
  local p
  for p in "${CLEANUP[@]:-}"; do
    [ -n "$p" ] && rm -rf -- "$p"
  done
}
trap cleanup EXIT
ok() { echo "ok   $1"; }
bad() { echo "FAIL $1: $2"; fail=1; }

# Stub python3. "--version" prints its version. "-m venv DIR" lays out a stub
# venv: its python imports packages only while DIR/.imports_ok exists, its pip
# records installs there, and creation appends the invoked path to $FAKE_LOG.
fake_python() { # DIR VERSION
  mkdir -p "$1"
  sed "s/@VER@/$2/" >"$1/python3" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  --version) echo "Python @VER@"; exit 0 ;;
  -c) printf '%s\n' "$(readlink -f -- "$0")"; exit 0 ;;
  -m)
    [ "$2" = "venv" ] || exit 2
    venv=$3
    mkdir -p "$venv/bin"
    printf 'home = %s\n' "$(dirname "$0")" >"$venv/pyvenv.cfg"
    printf '%s\n' "$0" >"$venv/.creator"
    printf '%s\n' "$0" >>"$FAKE_LOG"
    cat >"$venv/bin/python" <<'VENV'
#!/usr/bin/env bash
d=$(cd "$(dirname "$0")/.." && pwd)
[ -f "$d/.imports_ok" ] && exit 0
echo "ModuleNotFoundError: No module named 'pymobiledevice3'" >&2
exit 1
VENV
    cat >"$venv/bin/pip" <<'PIP'
#!/usr/bin/env bash
d=$(cd "$(dirname "$0")/.." && pwd)
case "${1:-}" in
  install)
    touch "$d/.imports_ok"
    echo pymobiledevice3 >"$d/.installed"
    n=$(cat "$d/.pip_runs" 2>/dev/null || echo 0)
    echo $((n + 1)) >"$d/.pip_runs"
    exit 0 ;;
  show)
    [ -f "$d/.installed" ] && { echo "Version: 11.26.0"; exit 0; }
    echo "no package metadata" >&2
    exit 1 ;;
esac
exit 2
PIP
    chmod +x "$venv/bin/python" "$venv/bin/pip"
    exit 0 ;;
esac
exit 2
EOF
  chmod +x "$1/python3"
}

fake_shim() { # LOCATION TARGET: a mise-style shim; the -c probe names the target
  { printf '#!/usr/bin/env bash\n'
    printf 'if [ "${1:-}" = "-c" ]; then\n  printf "%%s\\n" "%s"\n  exit 0\nfi\n' "$2"
    printf 'echo "fake mise: unexpected argument ${1:-}" >&2\nexit 1\n'
  } >"$1"
  chmod +x "$1"
}

home= fpath= venv_dir= rmode= rmodefile= swiftdir=
new_world() { # fake toolchain: latest -> py3.14, re-pointable to py3.15
  home=$(tmpworld)
  fake_python "$home/tools/py3.14/bin" 3.14.0
  fake_python "$home/tools/py3.15/bin" 3.15.0
  mkdir -p "$home/tools/latest/bin"
  ln -s "$home/tools/py3.14/bin/python3" "$home/tools/latest/bin/python3"
  fpath="$home/tools/latest/bin"
  venv_dir="$home/pymobile3-venv"
  swiftdir=
  : >"$home/creates.log"
}
repin() { # the toolchain update: latest now points at the new minor
  ln -sfn "$home/tools/py3.15/bin/python3" "$home/tools/latest/bin/python3"
}
make_venv() { # VERSION HEALTHY: stub venv built directly, not by the code under test
  FAKE_LOG=/dev/null "$home/tools/py$1/bin/python3" -m venv "$venv_dir"
  if [ "${2:-}" = healthy ]; then
    "$venv_dir/bin/pip" install >/dev/null 2>&1
  fi
}
run_repair() { # BLOCK HELPERS: runs the extracted --repair block; one shared order log
  local block=$1 helpers=$2 p="$fpath:$PATH"
  mkdir -p "$home/repodir/sdk-free"
  printf '#!/usr/bin/env bash\necho setup >>"$SETUP_LOG"\n' >"$home/repodir/sdk-free/setup.sh"
  chmod +x "$home/repodir/sdk-free/setup.sh"
  if [ -n "$swiftdir" ]; then p="$swiftdir:$p"; fi
  if [ -n "$rmodefile" ]; then
    mkdir -p "$home/.config/omarchy-apple-dev"
    printf '%s\n' "$rmodefile" >"$home/.config/omarchy-apple-dev/mode"
  fi
  env HOME="$home" PATH="$p" FAKE_LOG="$home/order.log" SETUP_LOG="$home/order.log" \
    MODE="$rmode" MODE_FILE="$home/.config/omarchy-apple-dev/mode" VENV="$venv_dir" \
    REPO_DIR="$home/repodir" SDK_CACHE="$home/sdkcache" SDK_SRC="$home/sdk-src" \
    XTOOL_SDK_VERSION="$sdk_version" SDK_HELPERS="$here/../../tools/sdk-state.sh" \
    bash -c '
      set -uo pipefail
      sdk_install_from() { :; }
      install_darwin_tools() { :; }
      install_oam() { :; }
      survive_status() { :; }
      toolchain_first_on_path() { :; }
      source "$SDK_HELPERS"
      source "$2"
      source "$0"
    ' "$block" --repair "$helpers" 2>&1
}
run_helpers() { # BLOCK SNIPPET WORKDIR VENV: source the helpers, run a snippet
  env HOME="$home" PATH="$fpath:$PATH" FAKE_LOG="$home/creates.log" bash -c '
    set -uo pipefail
    cd "$3" || exit 9
    source "$1"
    VENV="$4"
    eval "$2"
  ' _ "$1" "$2" "$3" "$4" 2>&1
}
refused() { # NAME RC OUTPUT TARGET: the guard must refuse and keep the target
  local name=$1 rc=$2 out=$3 target=${4:-}
  if [ "$rc" -eq 0 ]; then bad "$name" "not refused (rc 0): $out"; return 1; fi
  case "$out" in
    *"refusing to remove"*) ;;
    *) bad "$name" "no refusal message: $out"; return 1 ;;
  esac
  if [ -n "$target" ] && [ ! -e "$target" ]; then
    bad "$name" "target was removed"
    return 1
  fi
  return 0
}

check_resolved() { # fresh install: the venv records the resolved interpreter
  local block=$1 out rc
  new_world
  out=$(run_helpers "$block" "venv_ensure" "$home" "$venv_dir")
  rc=$?
  if [ "$rc" -ne 0 ]; then bad "resolved" "venv_ensure rc $rc: $out"; return 1; fi
  if ! grep -qF "home = $home/tools/py3.14/bin" "$venv_dir/pyvenv.cfg"; then
    bad "resolved" "pyvenv.cfg lacks the real interpreter dir: $(cat "$venv_dir/pyvenv.cfg" 2>/dev/null)"
    return 1
  fi
  if grep -qF "latest" "$venv_dir/pyvenv.cfg"; then
    bad "resolved" "pyvenv.cfg names the symlink"
    return 1
  fi
  if [ "$(cat "$venv_dir/.creator")" != "$home/tools/py3.14/bin/python3" ]; then
    bad "resolved" "creator is $(cat "$venv_dir/.creator")"
    return 1
  fi
  case "$out" in
    *"$home/tools/py3.14/bin/python3"*) ;;
    *) bad "resolved" "interpreter path not printed: $out"; return 1 ;;
  esac
  case "$out" in
    *"Python 3.14.0"*) ;;
    *) bad "resolved" "version not printed: $out"; return 1 ;;
  esac
  ok "resolved: created from the resolved interpreter, path and version printed"
}

check_healthy_kept() { # healthy venv survives a toolchain swap untouched
  local block=$1 out rc
  new_world
  run_helpers "$block" "venv_ensure" "$home" "$venv_dir" >/dev/null
  if [ "$(wc -l <"$home/creates.log")" != 1 ]; then
    bad "kept" "setup did not create exactly one venv"
    return 1
  fi
  repin
  out=$(run_helpers "$block" "venv_ensure" "$home" "$venv_dir")
  rc=$?
  if [ "$rc" -ne 0 ]; then bad "kept" "rc $rc: $out"; return 1; fi
  if [ "$(wc -l <"$home/creates.log")" != 1 ]; then
    bad "kept" "healthy venv was recreated"
    return 1
  fi
  if ! grep -qF "py3.14" "$venv_dir/pyvenv.cfg"; then
    bad "kept" "venv changed although healthy"
    return 1
  fi
  if [ "$(cat "$venv_dir/.pip_runs")" != 1 ]; then
    bad "kept" "pip re-ran although healthy"
    return 1
  fi
  ok "kept: healthy venv left alone after the symlink moved"
}

check_unhealthy_rebuilt() { # broken imports: old venv removed, new one installed
  local block=$1 out rc
  new_world
  run_helpers "$block" "venv_ensure" "$home" "$venv_dir" >/dev/null
  rm -f "$venv_dir/.imports_ok" # the old interpreter moved; imports fail now
  mkdir -p "$venv_dir"
  : >"$venv_dir/stale-marker"
  repin
  out=$(run_helpers "$block" "venv_ensure" "$home" "$venv_dir")
  rc=$?
  if [ "$rc" -ne 0 ]; then bad "rebuilt" "rc $rc: $out"; return 1; fi
  if [ "$(wc -l <"$home/creates.log")" != 2 ]; then
    bad "rebuilt" "expected two creations, log says $(wc -l <"$home/creates.log")"
    return 1
  fi
  if ! grep -qF "home = $home/tools/py3.15/bin" "$venv_dir/pyvenv.cfg"; then
    bad "rebuilt" "not rebuilt from the new interpreter: $(cat "$venv_dir/pyvenv.cfg" 2>/dev/null)"
    return 1
  fi
  if [ -e "$venv_dir/stale-marker" ]; then
    bad "rebuilt" "old venv content survived"
    return 1
  fi
  if [ "$(cat "$venv_dir/.pip_runs")" != 1 ]; then
    bad "rebuilt" "pip did not reinstall into the new venv"
    return 1
  fi
  case "$out" in
    *"Python 3.15.0"*) ;;
    *) bad "rebuilt" "new interpreter version not printed: $out"; return 1 ;;
  esac
  ok "rebuilt: unhealthy venv removed and rebuilt from the new interpreter"
}

check_guard() { # the rm guard refuses anything outside a safe venv path
  local block=$1 out rc other
  new_world
  out=$(run_helpers "$block" "venv_reset" "$home" "")
  rc=$?
  refused "guard-empty" "$rc" "$out" || return 1
  mkdir -p "$home/wd"
  : >"$home/wd/pym3"
  out=$(run_helpers "$block" "venv_reset" "$home/wd" "pym3")
  rc=$?
  refused "guard-relative" "$rc" "$out" "$home/wd/pym3" || return 1
  other=$(tmpworld)
  : >"$other/outside"
  out=$(run_helpers "$block" "venv_reset" "$home" "$other/outside")
  rc=$?
  refused "guard-outside-home" "$rc" "$out" "$other/outside" || return 1
  out=$(run_helpers "$block" "venv_reset" "$home" "$home")
  rc=$?
  refused "guard-home-itself" "$rc" "$out" || return 1
  out=$(run_helpers "$block" "venv_reset" "$home" "$home/../sibling")
  rc=$?
  refused "guard-home-dotdot" "$rc" "$out" || return 1
  ok "guard: empty, relative, outside-HOME, HOME itself, and HOME/.. refused"
}

check_home_symlink() { # HOME is a symlink to the real dir: first install reaches creation
  local block=$1 out rc link orig
  new_world
  orig=$home
  link="$home/homelink"
  ln -s "$home" "$link"
  home="$link"
  venv_dir="$home/pymobile3-venv"
  out=$(run_helpers "$block" "venv_ensure" "$home" "$venv_dir")
  rc=$?
  if [ "$rc" -ne 0 ]; then bad "home-symlink" "rc $rc: $out"; return 1; fi
  if ! grep -qF "home = $orig/tools/py3.14/bin" "$venv_dir/pyvenv.cfg"; then
    bad "home-symlink" "venv not created: $(cat "$venv_dir/pyvenv.cfg" 2>/dev/null)"
    return 1
  fi
  ok "home-symlink: symlinked HOME reaches venv creation"
}

check_home_slash() { # HOME ends in a slash: first install reaches creation
  local block=$1 out rc orig
  new_world
  orig=$home
  home="$home/"
  venv_dir="$home/pymobile3-venv"
  out=$(run_helpers "$block" "venv_ensure" "$home" "$venv_dir")
  rc=$?
  if [ "$rc" -ne 0 ]; then bad "home-slash" "rc $rc: $out"; return 1; fi
  if ! grep -qF "home = $orig/tools/py3.14/bin" "$venv_dir/pyvenv.cfg"; then
    bad "home-slash" "venv not created: $(cat "$venv_dir/pyvenv.cfg" 2>/dev/null)"
    return 1
  fi
  ok "home-slash: HOME with a trailing slash reaches venv creation"
}

check_shim_resolved() { # python3 is a mise-style shim: create from the real interpreter
  local block=$1 out rc
  new_world
  rm "$home/tools/latest/bin/python3"
  fake_shim "$home/tools/latest/bin/python3" "$home/tools/py3.14/bin/python3"
  out=$(run_helpers "$block" "venv_ensure" "$home" "$venv_dir")
  rc=$?
  if [ "$rc" -ne 0 ]; then bad "shim" "rc $rc: $out"; return 1; fi
  if [ "$(cat "$venv_dir/.creator")" != "$home/tools/py3.14/bin/python3" ]; then
    bad "shim" "creator is $(cat "$venv_dir/.creator")"
    return 1
  fi
  if ! grep -qF "home = $home/tools/py3.14/bin" "$venv_dir/pyvenv.cfg"; then
    bad "shim" "pyvenv.cfg not from the real interpreter: $(cat "$venv_dir/pyvenv.cfg" 2>/dev/null)"
    return 1
  fi
  case "$out" in
    *"$home/tools/py3.14/bin/python3"*) ;;
    *) bad "shim" "real interpreter path not printed: $out"; return 1 ;;
  esac
  case "$out" in
    *"Python 3.14.0"*) ;;
    *) bad "shim" "version not printed: $out"; return 1 ;;
  esac
  ok "shim: mise-style shim resolves to the real interpreter"
}

row_fails() { # FN BLOCK: true when the row fails; row temp dirs stay cleanable
  local fn=$1 blk=$2 holder
  holder=$(tmpworld)
  ( TMPDIR="$holder"
    export TMPDIR
    "$fn" "$blk"
  ) >/dev/null 2>&1
  [ $? -ne 0 ]
}

order_str() { # one-line view of the order log for failure messages
  tr '\n' '|' <"$home/order.log"
}

check_repair_no_xcode_explicit() { # MODE=no-xcode: venv rebuilt before setup exec
  local block=$1 out rc
  new_world
  make_venv 3.14
  repin
  : >"$home/order.log"
  rmode=no-xcode
  rmodefile=
  out=$(run_repair "$block" "$helpers_block")
  rc=$?
  if [ "$rc" -ne 0 ]; then bad "repair-explicit" "rc $rc: $out"; return 1; fi
  if [ "$(wc -l <"$home/order.log")" != 2 ]; then
    bad "repair-explicit" "want rebuild then setup, got: $(order_str)"
    return 1
  fi
  if ! grep -qF "$home/tools/py3.15/bin/python3" <<<"$(sed -n '1p' "$home/order.log")"; then
    bad "repair-explicit" "venv not rebuilt before setup: $(order_str)"
    return 1
  fi
  if [ "$(sed -n '2p' "$home/order.log")" != "setup" ]; then
    bad "repair-explicit" "setup did not run after the rebuild: $(order_str)"
    return 1
  fi
  if ! grep -qF "home = $home/tools/py3.15/bin" "$venv_dir/pyvenv.cfg"; then
    bad "repair-explicit" "venv still stale: $(cat "$venv_dir/pyvenv.cfg" 2>/dev/null)"
    return 1
  fi
  if [ ! -f "$venv_dir/.imports_ok" ]; then
    bad "repair-explicit" "pymobiledevice3 not reinstalled"
    return 1
  fi
  ok "repair-explicit: no-xcode repair rebuilds the venv before setup runs"
}

check_repair_no_xcode_saved() { # mode saved in the mode file: same order
  local block=$1 out rc
  new_world
  make_venv 3.14
  repin
  : >"$home/order.log"
  rmode=
  rmodefile=no-xcode
  out=$(run_repair "$block" "$helpers_block")
  rc=$?
  if [ "$rc" -ne 0 ]; then bad "repair-saved" "rc $rc: $out"; return 1; fi
  if [ "$(wc -l <"$home/order.log")" != 2 ]; then
    bad "repair-saved" "want rebuild then setup, got: $(order_str)"
    return 1
  fi
  if ! grep -qF "$home/tools/py3.15/bin/python3" <<<"$(sed -n '1p' "$home/order.log")"; then
    bad "repair-saved" "venv not rebuilt before setup: $(order_str)"
    return 1
  fi
  if [ "$(sed -n '2p' "$home/order.log")" != "setup" ]; then
    bad "repair-saved" "setup did not run after the rebuild: $(order_str)"
    return 1
  fi
  if ! grep -qF "home = $home/tools/py3.15/bin" "$venv_dir/pyvenv.cfg"; then
    bad "repair-saved" "venv still stale: $(cat "$venv_dir/pyvenv.cfg" 2>/dev/null)"
    return 1
  fi
  ok "repair-saved: saved no-xcode mode rebuilds the venv before setup runs"
}

check_repair_no_xcode_healthy() { # healthy venv: untouched, setup still runs
  local block=$1 out rc
  new_world
  make_venv 3.14 healthy
  repin
  : >"$home/order.log"
  rmode=no-xcode
  rmodefile=
  out=$(run_repair "$block" "$helpers_block")
  rc=$?
  if [ "$rc" -ne 0 ]; then bad "repair-healthy" "rc $rc: $out"; return 1; fi
  if [ "$(wc -l <"$home/order.log")" != 1 ] || [ "$(cat "$home/order.log")" != "setup" ]; then
    bad "repair-healthy" "want setup only, got: $(order_str)"
    return 1
  fi
  if ! grep -qF "home = $home/tools/py3.14/bin" "$venv_dir/pyvenv.cfg"; then
    bad "repair-healthy" "healthy venv was changed"
    return 1
  fi
  if [ "$(cat "$venv_dir/.pip_runs")" != 1 ]; then
    bad "repair-healthy" "pip re-ran although healthy"
    return 1
  fi
  ok "repair-healthy: healthy venv untouched, setup still runs"
}

check_repair_full_rebuilds() { # full mode: repair still rebuilds a broken venv
  local block=$1 out rc
  new_world
  make_venv 3.14
  repin
  swiftdir="$home/swiftbin"
  mkdir -p "$swiftdir"
  printf '#!/usr/bin/env bash\necho "Swift version 6.4.0"\n' >"$swiftdir/swift"
  chmod +x "$swiftdir/swift"
  mkdir -p "$home/sdkcache/darwin-iPhoneOS27.0.xtoolsdk"
  printf '%s\n' "$sdk_version" >"$home/sdkcache/darwin-iPhoneOS27.0.xtoolsdk/darwin-sdk-version.txt"
  : >"$home/order.log"
  rmode=full
  rmodefile=
  out=$(run_repair "$block" "$helpers_block")
  rc=$?
  if [ "$rc" -ne 0 ]; then bad "repair-full" "rc $rc: $out"; return 1; fi
  if ! grep -qF "home = $home/tools/py3.15/bin" "$venv_dir/pyvenv.cfg"; then
    bad "repair-full" "venv not rebuilt: $(cat "$venv_dir/pyvenv.cfg" 2>/dev/null)"
    return 1
  fi
  if [ ! -f "$venv_dir/.imports_ok" ]; then
    bad "repair-full" "pymobiledevice3 not reinstalled"
    return 1
  fi
  if [ "$(wc -l <"$home/order.log")" != 1 ]; then
    bad "repair-full" "unexpected order log: $(order_str)"
    return 1
  fi
  ok "repair-full: full-mode repair rebuilds the broken venv"
}

check_mutations() { # HELPER-BLOCK REPAIR-BLOCK: the suite must catch the bugs it exists for
  local mblock
  mblock=$(tmpworld)/mutated.sh
  # Creation from the PATH entry bakes in a shim or symlink (the review bug).
  sed 's|py=$("$(command -v python3)" -c .*|py=$(command -v python3)|' "$1" >"$mblock"
  if row_fails check_resolved "$mblock"; then
    ok "mutation-readlink: caught"
  else
    bad "mutation-readlink" "suite did not catch shim-based creation"
  fi
  # A neutered health check leaves a broken venv in place.
  sed 's/^venv_healthy() {/venv_healthy() { return 0 #/' "$1" >"$mblock"
  if row_fails check_unhealthy_rebuilt "$mblock"; then
    ok "mutation-health: caught"
  else
    bad "mutation-health" "suite did not catch a neutered health check"
  fi
  # A guard pattern open to everything removes paths outside HOME.
  sed 's|"\$HOME"/?\*) ;;|*) ;;|g' "$1" >"$mblock"
  if row_fails check_guard "$mblock"; then
    ok "mutation-guard: caught"
  else
    bad "mutation-guard" "suite did not catch an opened rm guard"
  fi
  # Comparing the resolved venv with the raw HOME refuses a symlinked HOME.
  sed 's|"\$home"/?\*|"\$HOME"/?*|' "$1" >"$mblock"
  if row_fails check_home_symlink "$mblock"; then
    ok "mutation-home: caught"
  else
    bad "mutation-home" "suite did not catch an unresolved HOME compare"
  fi
  # The venv check must run before the no-xcode exec, not after the SDK work.
  sed '/^  venv_ensure /d' "$2" >"$mblock"
  if row_fails check_repair_no_xcode_explicit "$mblock"; then
    ok "mutation-repair-order: caught"
  else
    bad "mutation-repair-order" "suite did not catch a late venv check in --repair"
  fi
}

block=$(tmpworld)/block.sh
sed -n '/^venv_healthy()/,/^# Register the SDK bundle/p' "$script" | sed '$d' >"$block"
helpers_block=$block
rblock=$(tmpworld)/repair-block.sh
sed -n '/^if \[ "\${1:-}" = "--repair" \]; then$/,/^resolve_mode$/p' "$script" | sed '$d' >"$rblock"
echo "== install-venv: installer=$script"
if [ ! -s "$block" ]; then
  echo "note: no venv helpers found; rows will fail (fix not applied?)"
fi
if [ ! -s "$rblock" ]; then
  echo "note: no --repair block found; repair rows will fail"
fi
check_resolved "$block"
check_healthy_kept "$block"
check_unhealthy_rebuilt "$block"
check_guard "$block"
check_home_symlink "$block"
check_home_slash "$block"
check_shim_resolved "$block"
check_repair_no_xcode_explicit "$rblock"
check_repair_no_xcode_saved "$rblock"
check_repair_no_xcode_healthy "$rblock"
check_repair_full_rebuilds "$rblock"
check_mutations "$block" "$rblock"
exit "$fail"
