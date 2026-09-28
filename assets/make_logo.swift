// make_logo.swift — из логотипа Алексея (assets/logo-source.webp: облако-геймпад и надпись на белом фоне)
// делает картинки для приложения:
//   ui/logo-mark.png        облако на прозрачном фоне (белые V, крестик и кнопки внутри остаются)
//   ui/logo-word.png        надпись «VastGame» — чёрная с прозрачностью (в тёмной теме страница её обращает)
//   assets/icon-1024.png    значок приложения: облако на скруглённом квадрате (dark — тёмном, light — белом)
// Запуск: swift assets/make_logo.swift assets/logo-source.webp dark|light
//
// Фон убирается заливкой от краёв картинки по почти белым точкам — белое внутри облака недостижимо и остаётся;
// на границе облака цвет «отделяется» от белого (альфа по самому тёмному каналу), края остаются гладкими.
import AppKit
import ImageIO

let args = CommandLine.arguments
guard args.count == 3, let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: args[1]) as CFURL, nil),
      let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { fatalError("make_logo.swift ИСТОЧНИК dark|light") }
let w = img.width, h = img.height
let cs = CGColorSpace(name: CGColorSpace.sRGB)!
let px = UnsafeMutablePointer<UInt8>.allocate(capacity: w * h * 4)
let ctx = CGContext(data: px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: cs,
                    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
ctx.draw(img, in: CGRect(x: 0, y: 0, width: w, height: h))   // строка 0 в памяти — верх картинки

func rgb(_ i: Int) -> (Int, Int, Int) { (Int(px[i * 4]), Int(px[i * 4 + 1]), Int(px[i * 4 + 2])) }
func minc(_ i: Int) -> Int { let (r, g, b) = rgb(i); return min(r, g, b) }

// 1. Фон: заливка от краёв по почти белому
var bg = [Bool](repeating: false, count: w * h)
var stack = [Int]()
for x in 0..<w { stack.append(x); stack.append((h - 1) * w + x) }
for y in 0..<h { stack.append(y * w); stack.append(y * w + w - 1) }
while let i = stack.popLast() {
    if bg[i] || minc(i) < 232 { continue }
    bg[i] = true
    let x = i % w, y = i / w
    if x > 0 { stack.append(i - 1) }; if x < w - 1 { stack.append(i + 1) }
    if y > 0 { stack.append(i - w) }; if y < h - 1 { stack.append(i + w) }
}
func nearBg(_ i: Int, _ r: Int) -> Bool {
    let x = i % w, y = i / w
    for dy in -r...r { for dx in -r...r {
        let xx = x + dx, yy = y + dy
        if xx >= 0, xx < w, yy >= 0, yy < h, bg[yy * w + xx] { return true }
    } }
    return false
}

// Граница облака и надписи: пустая строка между ними
var split = h / 2
for y in stride(from: h * 55 / 100, to: h * 75 / 100, by: 1) where (0..<w).allSatisfy({ bg[y * w + $0] }) { split = y; break }

func save(_ out: UnsafeMutablePointer<UInt8>, _ ow: Int, _ oh: Int, _ path: String) {
    let c = CGContext(data: out, width: ow, height: oh, bitsPerComponent: 8, bytesPerRow: ow * 4, space: cs,
                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    let rep = NSBitmapImageRep(cgImage: c.makeImage()!)
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: path))
}

// 2. Облако: фон прозрачный, у границы — «отделить от белого», внутри — как есть
var x0 = w, x1 = 0, y0 = h, y1 = 0
for y in 0..<split { for x in 0..<w where !bg[y * w + x] { x0 = min(x0, x); x1 = max(x1, x); y0 = min(y0, y); y1 = max(y1, y) } }
let mw = x1 - x0 + 1, mh = y1 - y0 + 1
let mark = UnsafeMutablePointer<UInt8>.allocate(capacity: mw * mh * 4)
mark.initialize(repeating: 0, count: mw * mh * 4)
for y in y0...y1 { for x in x0...x1 {
    let i = y * w + x, o = ((y - y0) * mw + (x - x0)) * 4
    if bg[i] { continue }
    let (r, g, b) = rgb(i)
    var a = 1.0
    if nearBg(i, 2) { a = min(1, Double(255 - min(r, g, b)) / 235) }
    if a <= 0 { continue }
    func un(_ c: Int) -> Double { max(0, min(255, (Double(c) - (1 - a) * 255) / a)) }
    // premultiplied: цвет × альфа
    mark[o] = UInt8(un(r) * a); mark[o + 1] = UInt8(un(g) * a); mark[o + 2] = UInt8(un(b) * a); mark[o + 3] = UInt8(a * 255)
} }
save(mark, mw, mh, "ui/logo-mark.png")

// 3. Надпись: альфа по темноте (текст ~#343a40 на белом), цвет чёрный
var t0 = w, t1 = 0, u0 = h, u1 = 0
for y in split..<h { for x in 0..<w where minc(y * w + x) < 200 { t0 = min(t0, x); t1 = max(t1, x); u0 = min(u0, y); u1 = max(u1, y) } }
let tw = t1 - t0 + 1, th = u1 - u0 + 1
let word = UnsafeMutablePointer<UInt8>.allocate(capacity: tw * th * 4)
word.initialize(repeating: 0, count: tw * th * 4)
for y in u0...u1 { for x in t0...t1 {
    let (r, g, b) = rgb(y * w + x)
    let a = max(0, min(1, (255 - Double(r + g + b) / 3) / (255 - 58)))
    word[((y - u0) * tw + (x - t0)) * 4 + 3] = UInt8(a * 255)
} }
save(word, tw, th, "ui/logo-word.png")

// 4. Значок 1024: скруглённый квадрат (как у значков macOS) и облако на нём
let S = 1024
let icon = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: S, pixelsHigh: S, bitsPerSample: 8, samplesPerPixel: 4,
                            hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: icon)
let body = NSBezierPath(roundedRect: NSRect(x: 100, y: 100, width: 824, height: 824), xRadius: 185, yRadius: 185)
if args[2] == "dark" {
    NSGradient(starting: NSColor(srgbRed: 0.13, green: 0.12, blue: 0.16, alpha: 1),
               ending: NSColor(srgbRed: 0.05, green: 0.05, blue: 0.07, alpha: 1))!.draw(in: body, angle: -90)
} else {
    NSGradient(starting: .white, ending: NSColor(srgbRed: 0.93, green: 0.93, blue: 0.95, alpha: 1))!.draw(in: body, angle: -90)
}
let mc = CGContext(data: mark, width: mw, height: mh, bitsPerComponent: 8, bytesPerRow: mw * 4, space: cs,
                   bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
let dw = 640.0, dh = dw * Double(mh) / Double(mw)
NSGraphicsContext.current!.cgContext.draw(mc.makeImage()!, in: CGRect(x: (1024 - dw) / 2, y: (1024 - dh) / 2 - 10, width: dw, height: dh))
NSGraphicsContext.restoreGraphicsState()
try! icon.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "assets/icon-1024.png"))
print("облако \(mw)×\(mh), надпись \(tw)×\(th), граница на строке \(split), значок — \(args[2])")
