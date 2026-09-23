// make_icon.swift — значок vastgame (свой рисунок, без чужих картинок):
// скруглённый квадрат с сине-фиолетовым градиентом и белая кнопка «играть».
// Запуск: swift assets/make_icon.swift assets/icon-1024.png
import AppKit
let px = 1024
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                           samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                           colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let s = CGFloat(px)
let bg = NSBezierPath(roundedRect: NSRect(x: 100, y: 100, width: s - 200, height: s - 200), xRadius: 185, yRadius: 185)
NSGradient(starting: NSColor(calibratedRed: 0.18, green: 0.44, blue: 0.93, alpha: 1),
           ending: NSColor(calibratedRed: 0.36, green: 0.16, blue: 0.78, alpha: 1))!.draw(in: bg, angle: -60)
// Треугольник «играть» со скруглёнными углами
let tri = NSBezierPath()
let c = NSPoint(x: s / 2 + 25, y: s / 2), r: CGFloat = 205
let pts = (0..<3).map { i -> NSPoint in
    let a = CGFloat(i) * 2 * .pi / 3
    return NSPoint(x: c.x + r * cos(a), y: c.y + r * sin(a))
}
tri.move(to: pts[0]); tri.line(to: pts[1]); tri.line(to: pts[2]); tri.close()
tri.lineJoinStyle = .round; tri.lineWidth = 80
NSColor.white.setFill(); NSColor.white.setStroke()
tri.fill(); tri.stroke()
NSGraphicsContext.restoreGraphicsState()
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
