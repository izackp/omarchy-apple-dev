import AppKit
// stdin: "<space> c1 [c2 c3]" per line (space: rgb|p3|gray); stdout: float32 bit patterns of
// the color converted to extended sRGB (rgb/p3) or extended gray (gray).
let srgb = CGColorSpace(name: CGColorSpace.extendedSRGB)!
let egray = CGColorSpace(name: CGColorSpace.extendedGray)!
let spaces: [String: CGColorSpace] = ["rgb": NSColorSpace.genericRGB.cgColorSpace!,
  "p3": CGColorSpace(name: CGColorSpace.displayP3)!, "gray": NSColorSpace.genericGray.cgColorSpace!]
var out = ""
while let line = readLine() {
  let f = line.split(separator: " ")
  let comps = f.dropFirst().map { CGFloat(Double($0)!) } + [1]
  let c = CGColor(colorSpace: spaces[String(f[0])]!, components: comps)!
  let d = c.converted(to: f[0] == "gray" ? egray : srgb, intent: .defaultIntent, options: nil)!
  out += d.components!.dropLast().map { String(Float($0).bitPattern, radix: 16) }.joined(separator: " ") + "\n"
}
print(out, terminator: "")
