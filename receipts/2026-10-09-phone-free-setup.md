# Phone-free setup (stubs from Apple's public iOS update)

Date: 2026-10-09. `$WORK` below denotes the scratch directory used for this test.

`sdk-free/setup.sh` no longer needs a connected iPhone. New source choice: phone (default when one is
connected) or `--ipsw` / `SDKFREE_IPSW=auto|<build>` (`--build <build>` pins, `--device <model>` /
`SDKFREE_DEVICE` picks the model, default `iPhone16,2`). The cache comes down with
`ipsw extract --dyld --dyld-arch arm64e --remote <url>` into `$SDKFREE_HOME/cache/dsc`
(`SDKFREE_TMP` moves the scratch) and is deleted after the stubs are cut. The same cutter
(`ipsw dyld tbd`) runs on a cache from either source. `SDKFREE_DSC_DIR=<dir>` feeds a pre-fetched cache
(test hook, like `SDKFREE_TBD_DIR`).

Why `extract --remote` and not `download ipsw --dyld`: on Linux both end in the same place
(download the system image, mount it with apfs-fuse), but `extract --remote` downloads only the
2.4 GB `.dmg.aea` of the system image instead of the IPSW feeds' full file set, and the
`download ipsw --dyld` run on this host failed before the mount step
with "failed to find apfs-fuse". The `extract --remote` run is the
one that produced the cache below.

apfs-fuse is provisioned by setup.sh itself: pinned commit
`66b86bd525e8cb90f9012543be89b1f092b75cf3` of github.com/sgan81/apfs-fuse (submodule lzfse included),
built with `cmake -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_CXX_FLAGS="-include cstdint"` — the
policy flag for CMake >= 4, the cstdint force-include because GCC 15 no longer leaks `uint32_t`
(PList.cpp fails without it). Binary installed to `$SDKFREE_HOME/bin/apfs-fuse` and passed as
`IPSW_APFS_FUSE_PATH`. Without fuse3/cmake/git/gcc the run stops with the pacman line to install:
`pacman -S --needed fuse3 cmake git gcc bzip2 zlib`.

## Cache and stub equivalence (iPhone16,2, build 24A446 = iOS 27.0.1)

Cache source: Apple public IPSW `iPhone16,2_27.0.1_24A446_Restore.ipsw`, extracted to
`$WORK/dsc27/24A446__iPhone16,2/`
(dyld_shared_cache_arm64e + 77 subcaches, ~4.6 GB). Reference stubs cut from the connected phone:
`$WORK/tbd27/` (27 files).

sha256 of all 27 stubs, ipsw-cut vs phone-cut (full table in
`$WORK/sdkfree-ipsw/stub-compare.txt`):

- 26 of 27 byte-identical, including install-name, current-version and the full export list:
  CoreFoundation, CoreGraphics, Foundation, QuartzCore, SafariServices, libSystem.B.dylib,
  libc++.1.dylib, libc++abi.dylib, libobjc.A.dylib, and all 17 libswift*.dylib stubs.
- `UIKit.tbd` differs (host ipsw 3.1.724; byte-identical with the pinned 3.1.731 — see the chroot run below):
  phone `dd0107688aee06305b40a184128d09bf0bb0c807e0ef7f3133760ad8837824b7`,
  ipsw `9067a9ae58f22e473016dda99ac7627bb7871e36841bf20b4f16667ae17212af`. The diff is 11 inserted
  lines: duplicate entries of 4 Swift-mangled class symbols
  (`_TtC5UIKit24UIKitMagicMorphAnimation`, `_TtC5UIKit40UIViewInProcessLayerAnimationCoordinator`,
  `_TtCC5UIKit24UIKitMagicMorphAnimation15UIKitParameters`,
  `_TtCE5UIKitC12AnimationKit25InProcessAnimationManagerP33_..._BacklightStateDisplayObserver`) that
  the phone's runtime-captured symbol table lists once more. Unique symbol sets are identical
  (0 added, 0 missing); linking is unaffected.
- Tools: same `ipsw` 3.1.724 cut both sides, so no tool-version skew.

`UIKitCore` is folded into the UIKit stub on iOS 27 (setup.sh notes it and moves on). `tbd27` also
carries five stubs setup.sh does not cut by design (`SafariServices` — shipped as an authored stub in
`sdk-free/headers`, `libswiftCoreImage`, `libswiftXPC`, `libswiftos`, `libswiftsimd`); the ipsw cache
contains all of them and they cut byte-identical, so widening the IMAGES list later needs no new work.

## Chroot run (no phone, no Xcode)

`SDKFREE_HOME=$WORK/sdkfree-ipsw`, no phone in the chroot, cache fed with `SDKFREE_DSC_DIR=$WORK/dsc27`,
build pinned with `SDKFREE_IPSW=24A446`. setup.sh skipped the download (cache present), resolved the build to
iOS 27.0.1 (24A446), wrote `SystemVersion.plist` (`ProductVersion` 27.0.1, `ProductBuildVersion` 24A446) and
assembled the sysroot (11 MB).

Stub comparison in that run (ipsw 3.1.731, the version install-toolchain.sh pins and every install gets):
all 22 stubs setup.sh cuts are byte-identical to tbd27, including `UIKit.tbd`. The earlier UIKit delta
(phone `dd010768...`, ipsw `9067a9ae...`, 11 duplicate lines of 4 Swift-mangled class symbols, zero
unique-symbol difference) shows up only with the older ipsw 3.1.724 that happened to be on this host — a
tool-version difference, not a cache difference. The five stubs outside the IMAGES list
(SafariServices, libswiftCoreImage, libswiftXPC, libswiftos, libswiftsimd) cut byte-identical from the
same cache; SafariServices is already shipped as an authored stub in `sdk-free/headers`.
Full per-file table: `$WORK/sdkfree-ipsw/stub-compare.txt`.

Swift pipeline: `build-stdlib.sh` rebuilt the standard library res for the unchanged compiler
(swift-6.4-RELEASE), `overlays.sh` rebuilt the framework overlays against the new sysroot —
`$SDKFREE_HOME/swift/res/iphoneos` carries Swift, SwiftOnoneSupport, _Concurrency, ObjectiveC, Darwin,
Dispatch, CoreGraphics and Foundation modules.

Flutter counter (`sdk-free/flutter-build.sh "$WORK/apps/sdkfree-counter"`): built
`$WORK/apps/sdkfree-counter/build/ios-sdkfree/release/Runner-unsigned.ipa`
(`dev.omarchy.sdkfree.counter`, release, iOS 15.0+, 6.75 MB). Full log:
`$WORK/sdkfree-ipsw/test-run.log`.

