# Shipping details for `ship.sh`

`ship.sh` builds a release `.app` from an xtool project, signs it for the App
Store, validates it offline and, with `--upload`, uploads it. The short
version is in the [README](../README.md). This page holds the signing options
and resource rules. `ship.sh` never submits a build for review.

## What the build step adds

- Compiles the app's `AppIcon` set with the Linux `actool --app-icon`.
  Xcode's single-size 1024 icon, with its dark and tinted variants, is
  expanded to the App Store sizes; `APP_ICON=<name>` picks another set.
- Stamps the build-environment keys App Store processing reads (`DTXcode`,
  `DTSDKName`, …, and a UTC `CFBundleVersion`). A `.xip` install records the
  Xcode version and build automatically; if they cannot be read, set
  `XCODE_VERSION` and `XCODE_BUILD`.
- Wraps each SwiftPM `.dynamic` product as a `.framework` (App Store
  processing rejects a loose `.dylib`).
- Signs inside-out with `rcodesign`, packages `xtool/<App>.ipa`, then
  validates offline: bundle layout, Info.plist keys and version formats,
  Mach-O arch and minimum OS, icons in `Assets.car`, profile type and app id,
  entitlements, team id and every sealed hash. Any `FAIL` stops it.
- Without an App Store Connect key it signs with a local TEST identity. That
  output proves the pipeline, and Apple will reject that signature.

## Resources: the SwiftPM bundle and `ROOT_RESOURCES`

SwiftPM keeps a target's resources in its resource bundle, where only
`Bundle.module` finds them. `ship.sh` moves the files Apple needs at the app
root (storyboards, nibs and the app's own `PrivacyInfo.xcprivacy`) and leaves
the rest in the bundle. If app code reads other resources through
`Bundle.main`, set `ROOT_RESOURCES` to the space-separated patterns to move as
well, for example `ROOT_RESOURCES='*.lproj *.wav'` for localized strings,
`InfoPlist.strings` and sounds. The patterns match only top-level entries of
each resource bundle, and they apply to the app and to each extension bundle.
Keep the list short: copying everything into an upload once stalled App Store
processing (`--device` builds still copy everything).

## Requested capabilities: `ASC_ENTITLEMENTS_PATH`

For HTTPS authentication callbacks, set `ASC_ENTITLEMENTS_PATH` to the app's
requested entitlements plist when running `ship.sh`. The signer copies its
exact `com.apple.developer.associated-domains` values (`applinks:` and
`webcredentials:`), never the provisioning profile's wildcard. Enable the
`ASSOCIATED_DOMAINS` capability for the bundle ID in the Apple Developer
portal and recreate the provisioning profile first; this option does not
enable capabilities through the API. Signing fails if the profile does not
grant the requested domains. Offline verification accepts concrete domain
values against a `*` profile grant and rejects mismatches. Existing
entitlements and builds without this option are unchanged. Confirm the HTTPS
callback on a signed device build.

The same file can request push and Sign in with Apple. With `aps-environment`
in it, the app is signed with the environment the profile grants (an App Store
profile grants `production`, as Xcode signs it), so a file that says
`development` works for both device and App Store builds. With
`com.apple.developer.applesignin` (`["Default"]`), the app is signed with it.
Enable `PUSH_NOTIFICATIONS` or `APPLE_ID_AUTH` for the bundle ID first;
signing fails if the profile does not grant what the file requests, and
offline verification checks both values. Only the main app gets these values.

## Signing like an existing Xcode setup: `ASC_EXPORT_OPTIONS`

Set `ASC_EXPORT_OPTIONS` to an `ExportOptions.plist`, as used by
`xcodebuild -exportArchive` or `flutter build ipa --export-options-plist`.
The signer checks these fields:

| Field | Rule |
|---|---|
| `method` | Must be `app-store-connect` or the older `app-store`. A missing value means development in Xcode and is refused here. |
| `teamID` | Must match the profiles' team. |
| `signingCertificate` | Must name a distribution certificate or give the SHA-1 of the local one. |
| `provisioningProfiles` | Maps bundle IDs to existing App Store profiles by name or UUID, instead of profiles made by `asc.py`. Each must list the local Apple Distribution certificate. |
| `uploadSymbols`, `signingStyle`, `destination` and other Xcode-only export keys | Reported and ignored. |

`rcodesign` uses the local private key. Signing stops if a chosen profile
does not list its certificate. The [export-options tests](../tests/test_export_options.py)
cover these rules offline.

## Key handling

- The App Store Connect `.p8` team key stays wherever you keep it; only its
  path, the key id and the issuer id go in the environment. Give it App
  Manager (with Certificates, Identifiers & Profiles) or Admin role.
- The Apple Distribution private key `ship.sh` creates stays on this machine
  in `~/.config/omarchy-apple-dev/distribution/`.
- `ship.sh` registers the bundle id and creates the App Store profile through
  the API. It does not enable capabilities through the API: enable
  `ASSOCIATED_DOMAINS`, `PUSH_NOTIFICATIONS` or `APPLE_ID_AUTH` in the portal
  and recreate the profile yourself before signing a build that requests them.
