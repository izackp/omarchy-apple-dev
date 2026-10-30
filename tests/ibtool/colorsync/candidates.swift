import AppKit
import Accelerate
let gray = NSColorSpace.genericGray.cgColorSpace!, eg = CGColorSpace(name: CGColorSpace.extendedGray)!
let n = 4096
var xs = (0..<n).map { Float(Double($0) / 4095) }
var cg = [Float](), a = [Float](), b = [Float](), c = [Float](), d = [Float]()
for x in xs {
  let col = CGColor(colorSpace: gray, components: [CGFloat(x), 1])!.converted(to: eg, intent: .defaultIntent, options: nil)!
  cg.append(Float(col.components![0]))
  func lin(_ x: Float) -> Float { x < 1/32 ? x / 16 : powf(x, 1.8) }
  func enc(_ y: Float) -> Float { y <= 0.0031308 ? y * 12.92 : 1.055 * powf(y, 1/2.4) - 0.055 }
  a.append(enc(lin(x)))
  let yd = Double(x) < 1/32 ? Double(x) / 16 : pow(Double(x), 1.8)
  b.append(Float(yd <= 0.0031308 ? yd * 12.92 : 1.055 * pow(yd, 1/2.4) - 0.055))
}
// Accelerate vvpowf for both stages
var e1 = [Float](repeating: 1.8, count: n), y = [Float](repeating: 0, count: n), cnt = Int32(n)
vvpowf(&y, &e1, &xs, &cnt)
for i in 0..<n where xs[i] < 1/32 { y[i] = xs[i] / 16 }
var e2 = [Float](repeating: 1/2.4, count: n), z = [Float](repeating: 0, count: n)
vvpowf(&z, &e2, &y, &cnt)
for i in 0..<n { c.append(y[i] <= 0.0031308 ? y[i] * 12.92 : 1.055 * z[i] - 0.055) }
func score(_ v: [Float]) -> Int { zip(v, cg).filter { $0.0.bitPattern == $0.1.bitPattern }.count }
print("powf", score(a), "double", score(b), "vvpowf", score(c), "of", n)
