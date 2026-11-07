// On-device text recognition (Apple Vision) for captured frames: prints one recognized line per row.
// Usage: swift ocr.swift IMAGE [IMAGE ...]   (macOS only; no network, no extra installs)
import Foundation
import Vision
import AppKit

for path in CommandLine.arguments.dropFirst() {
    guard let img = NSImage(contentsOfFile: path),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print("\u{1}ERROR\t\(path)")
        continue
    }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = false
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    print("\u{1}FILE\t\(path)")
    for obs in (req.results ?? []) {
        if let top = obs.topCandidates(1).first {
            let b = obs.boundingBox
            print(String(format: "%.3f\t%.3f\t", b.minX, 1 - b.maxY) + top.string)
        }
    }
}
