import CoreGraphics
import Foundation
import ImageIO
import Vision

private struct Arguments {
    var imagePath: String?
    var recognitionLevel = "accurate"
    var languages = ["he-IL", "en-US"]
    var usesLanguageCorrection = true
    var revision: Int?
    var capabilitiesOnly = false
}

private struct BoundingBox: Codable {
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

private struct TextObservation: Codable {
    let text: String
    let confidence: Double
    let boundingBox: BoundingBox

    enum CodingKeys: String, CodingKey {
        case text
        case confidence
        case boundingBox = "bounding_box"
    }
}

private struct OCRResponse: Codable {
    let schemaVersion: String
    let framework: String
    let operatingSystemVersion: String
    let requestRevision: Int
    let recognitionLevel: String
    let recognitionLanguages: [String]
    let usesLanguageCorrection: Bool
    let imageWidth: Int
    let imageHeight: Int
    let inferenceTimingMs: Double
    let observations: [TextObservation]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case framework
        case operatingSystemVersion = "operating_system_version"
        case requestRevision = "request_revision"
        case recognitionLevel = "recognition_level"
        case recognitionLanguages = "recognition_languages"
        case usesLanguageCorrection = "uses_language_correction"
        case imageWidth = "image_width"
        case imageHeight = "image_height"
        case inferenceTimingMs = "inference_timing_ms"
        case observations
    }
}

private struct Capabilities: Codable {
    let framework: String
    let operatingSystemVersion: String
    let defaultRevision: Int
    let currentRevision: Int
    let supportedAccurateLanguages: [String]
    let supportedFastLanguages: [String]

    enum CodingKeys: String, CodingKey {
        case framework
        case operatingSystemVersion = "operating_system_version"
        case defaultRevision = "default_revision"
        case currentRevision = "current_revision"
        case supportedAccurateLanguages = "supported_accurate_languages"
        case supportedFastLanguages = "supported_fast_languages"
    }
}

private enum HelperError: Error, CustomStringConvertible {
    case invalidArgument(String)
    case imageDecode(String)
    case noRecognitionResult
    case unsupportedLanguages([String])

    var description: String {
        switch self {
        case let .invalidArgument(message):
            return message
        case let .imageDecode(path):
            return "Could not decode image at \(path)"
        case .noRecognitionResult:
            return "Vision returned no result collection"
        case let .unsupportedLanguages(languages):
            return "Unsupported Vision recognition languages: \(languages.joined(separator: ", "))"
        }
    }
}

private func operatingSystemVersion() -> String {
    let version = ProcessInfo.processInfo.operatingSystemVersion
    return "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)"
}

private func parseBool(_ value: String, option: String) throws -> Bool {
    switch value.lowercased() {
    case "true", "1", "yes":
        return true
    case "false", "0", "no":
        return false
    default:
        throw HelperError.invalidArgument("\(option) must be true or false")
    }
}

private func parseArguments(_ values: [String]) throws -> Arguments {
    var arguments = Arguments()
    var index = 0
    while index < values.count {
        let option = values[index]
        if option == "--capabilities" || option == "--version" {
            arguments.capabilitiesOnly = true
            index += 1
            continue
        }
        guard index + 1 < values.count else {
            throw HelperError.invalidArgument("Missing value for \(option)")
        }
        let value = values[index + 1]
        switch option {
        case "--image":
            arguments.imagePath = value
        case "--recognition-level":
            guard value == "accurate" || value == "fast" else {
                throw HelperError.invalidArgument(
                    "--recognition-level must be accurate or fast"
                )
            }
            arguments.recognitionLevel = value
        case "--languages":
            let languages = value.split(separator: ",").map(String.init).filter { !$0.isEmpty }
            guard !languages.isEmpty else {
                throw HelperError.invalidArgument("--languages must not be empty")
            }
            arguments.languages = languages
        case "--language-correction":
            arguments.usesLanguageCorrection = try parseBool(
                value, option: "--language-correction"
            )
        case "--revision":
            guard let revision = Int(value), revision > 0 else {
                throw HelperError.invalidArgument("--revision must be a positive integer")
            }
            arguments.revision = revision
        default:
            throw HelperError.invalidArgument("Unknown option: \(option)")
        }
        index += 2
    }
    return arguments
}

private func writeJSON<T: Encodable>(_ value: T) throws {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    let data = try encoder.encode(value)
    guard let text = String(data: data, encoding: .utf8) else {
        throw HelperError.invalidArgument("Could not encode UTF-8 JSON")
    }
    FileHandle.standardOutput.write(Data((text + "\n").utf8))
}

private func runOCR(_ arguments: Arguments) throws -> OCRResponse {
    guard let imagePath = arguments.imagePath else {
        throw HelperError.invalidArgument("--image is required")
    }
    let url = URL(fileURLWithPath: imagePath)
    guard
        let source = CGImageSourceCreateWithURL(url as CFURL, nil),
        let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
    else {
        throw HelperError.imageDecode(imagePath)
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = arguments.recognitionLevel == "fast" ? .fast : .accurate
    request.recognitionLanguages = arguments.languages
    request.usesLanguageCorrection = arguments.usesLanguageCorrection
    if let revision = arguments.revision {
        request.revision = revision
    }
    let supportedLanguages = try request.supportedRecognitionLanguages()
    let unsupportedLanguages = arguments.languages.filter {
        !supportedLanguages.contains($0)
    }
    if !unsupportedLanguages.isEmpty {
        throw HelperError.unsupportedLanguages(unsupportedLanguages)
    }

    let started = DispatchTime.now().uptimeNanoseconds
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([request])
    let finished = DispatchTime.now().uptimeNanoseconds

    guard let results = request.results else {
        throw HelperError.noRecognitionResult
    }
    let observations = results.compactMap { observation -> TextObservation? in
        guard let candidate = observation.topCandidates(1).first else {
            return nil
        }
        let box = observation.boundingBox
        return TextObservation(
            text: candidate.string,
            confidence: Double(candidate.confidence),
            boundingBox: BoundingBox(
                x: Double(box.origin.x),
                y: Double(box.origin.y),
                width: Double(box.size.width),
                height: Double(box.size.height)
            )
        )
    }

    return OCRResponse(
        schemaVersion: "1.0",
        framework: "Vision",
        operatingSystemVersion: operatingSystemVersion(),
        requestRevision: request.revision,
        recognitionLevel: arguments.recognitionLevel,
        recognitionLanguages: arguments.languages,
        usesLanguageCorrection: arguments.usesLanguageCorrection,
        imageWidth: image.width,
        imageHeight: image.height,
        inferenceTimingMs: Double(finished - started) / 1_000_000.0,
        observations: observations
    )
}

do {
    let arguments = try parseArguments(Array(CommandLine.arguments.dropFirst()))
    if arguments.capabilitiesOnly {
        let accurateRequest = VNRecognizeTextRequest()
        accurateRequest.recognitionLevel = .accurate
        let fastRequest = VNRecognizeTextRequest()
        fastRequest.recognitionLevel = .fast
        try writeJSON(
            Capabilities(
                framework: "Vision",
                operatingSystemVersion: operatingSystemVersion(),
                defaultRevision: VNRecognizeTextRequest.defaultRevision,
                currentRevision: VNRecognizeTextRequest.currentRevision,
                supportedAccurateLanguages: try accurateRequest.supportedRecognitionLanguages(),
                supportedFastLanguages: try fastRequest.supportedRecognitionLanguages()
            )
        )
    } else {
        try writeJSON(try runOCR(arguments))
    }
} catch {
    FileHandle.standardError.write(Data(("apple_vision_ocr: \(error)\n").utf8))
    exit(1)
}
