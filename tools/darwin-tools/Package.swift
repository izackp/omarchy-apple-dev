// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "darwin-tools",
    platforms: [.macOS(.v13)],
    dependencies: [
        // AssetKit 1.0.0 plus actool 27.0 parity fixes: named colors in every Xcode color space
        // (FINDINGS.md 24, 27), per-size icon renditions (26), actool's BOM layout and
        // single-size icon form (37), the Liquid Glass pre-render of Icon Composer icons, HEIC images,
        // alternate app icons, gray and opacity encoding, Display P3 wide-gamut renditions, and actool's
        // BITMAPKEYS descriptors as value masks, the exact TVL row stride, and symbol templates with relative
        // paths, arcs and weight layers (FINDINGS.md, "Assets.car files that Apple's assetutil rejects"). The
        // macosx platform compiles with macOS 26's 13-attribute
        // rendition-key schema so AppKit resolves symbols and images (60). izackp's fork adds
        // template-rendering-intent on PNG imagesets (renditionFlags 0x8, as Xcode writes), without
        // which a template PNG tab bar icon stays untinted.
        .package(url: "https://github.com/izackp/AssetKit", revision: "39496d779f6539f84364452e52adc61c689d03d1"),
        .package(url: "https://github.com/tayloraswift/swift-png", from: "4.5.0"),
    ],
    targets: [
        .target(
            name: "DarwinAssets",
            dependencies: [
                .product(name: "AssetKit", package: "AssetKit"),
                .product(name: "PNG", package: "swift-png"),
            ]
        ),
        // Linux stand-in for Apple's actool: SwiftBuild under xtool, and ship.sh for the app icon.
        .executableTarget(name: "actool", dependencies: ["DarwinAssets", .product(name: "AssetKit", package: "AssetKit")]),
    ]
)
