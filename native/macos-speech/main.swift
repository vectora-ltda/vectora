// vectora-speech — reconhecimento de fala nativo do macOS (framework Speech).
//
// Helper de linha de comando usado pelo backend do Vectora como motor de
// transcrição do sistema no macOS. Prefere reconhecimento no próprio
// dispositivo (sem enviar áudio para servidores da Apple) quando o idioma
// suporta.
//
// Uso:
//   vectora-speech check [--locale pt-BR]
//   vectora-speech transcribe <arquivo> [--locale pt-BR] [--require-on-device]
//
// Saída: um objeto JSON em stdout. Código 0 em sucesso, 1 em erro.
// Compilado por `scons macos-speech` (swiftc) apenas no macOS.

import Foundation
import Speech

struct Output: Encodable {
    var ok: Bool
    var text: String?
    var locale: String?
    var onDevice: Bool?
    var authorized: Bool?
    var available: Bool?
    var supportsOnDevice: Bool?
    var error: String?
}

func emit(_ output: Output) -> Never {
    let encoder = JSONEncoder()
    encoder.keyEncodingStrategy = .convertToSnakeCase
    if let data = try? encoder.encode(output), let line = String(data: data, encoding: .utf8) {
        print(line)
    } else {
        print("{\"ok\":false,\"error\":\"falha ao codificar a saída\"}")
    }
    exit(output.ok ? 0 : 1)
}

func fail(_ message: String) -> Never {
    emit(Output(ok: false, error: message))
}

func option(_ name: String, in args: [String]) -> String? {
    guard let index = args.firstIndex(of: name), index + 1 < args.count else { return nil }
    return args[index + 1]
}

/// Pede autorização de reconhecimento de fala, esperando a resposta.
func requestAuthorization() -> SFSpeechRecognizerAuthorizationStatus {
    let current = SFSpeechRecognizer.authorizationStatus()
    if current != .notDetermined {
        return current
    }
    let semaphore = DispatchSemaphore(value: 0)
    var status = SFSpeechRecognizerAuthorizationStatus.notDetermined
    SFSpeechRecognizer.requestAuthorization { result in
        status = result
        semaphore.signal()
    }
    // A resposta chega por uma fila do sistema; o run loop principal
    // continua livre enquanto espera.
    let deadline = Date().addingTimeInterval(120)
    while semaphore.wait(timeout: .now()) == .timedOut {
        if Date() > deadline { break }
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
    }
    return status
}

func makeRecognizer(_ localeId: String?) -> SFSpeechRecognizer? {
    if let localeId, !localeId.isEmpty {
        return SFSpeechRecognizer(locale: Locale(identifier: localeId))
    }
    return SFSpeechRecognizer()
}

let args = Array(CommandLine.arguments.dropFirst())
guard let command = args.first else {
    fail("uso: vectora-speech check|transcribe <arquivo> [--locale xx-YY]")
}
let localeId = option("--locale", in: args)

switch command {
case "check":
    let status = requestAuthorization()
    let recognizer = makeRecognizer(localeId)
    emit(
        Output(
            ok: true,
            locale: recognizer?.locale.identifier,
            authorized: status == .authorized,
            available: recognizer?.isAvailable ?? false,
            supportsOnDevice: recognizer?.supportsOnDeviceRecognition ?? false
        )
    )

case "transcribe":
    guard args.count >= 2 else { fail("informe o arquivo de áudio") }
    let url = URL(fileURLWithPath: args[1])
    guard FileManager.default.fileExists(atPath: url.path) else {
        fail("arquivo não encontrado: \(url.path)")
    }
    guard requestAuthorization() == .authorized else {
        fail("reconhecimento de fala não autorizado nos Ajustes do Sistema")
    }
    guard let recognizer = makeRecognizer(localeId) else {
        fail("idioma não suportado: \(localeId ?? "padrão do sistema")")
    }
    guard recognizer.isAvailable else {
        fail("reconhecimento de fala indisponível para \(recognizer.locale.identifier)")
    }

    let requireOnDevice = args.contains("--require-on-device")
    let onDevice = recognizer.supportsOnDeviceRecognition
    if requireOnDevice && !onDevice {
        fail("reconhecimento no dispositivo indisponível para \(recognizer.locale.identifier)")
    }

    let request = SFSpeechURLRecognitionRequest(url: url)
    request.shouldReportPartialResults = false
    request.requiresOnDeviceRecognition = onDevice
    if #available(macOS 13.0, *) {
        request.addsPunctuation = true
    }

    var finished = false
    var transcript = ""
    var failure: String?
    let task = recognizer.recognitionTask(with: request) { result, error in
        if let result {
            transcript = result.bestTranscription.formattedString
            if result.isFinal { finished = true }
        }
        if let error {
            failure = error.localizedDescription
            finished = true
        }
    }

    let deadline = Date().addingTimeInterval(600)
    while !finished && Date() < deadline {
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
    }
    if !finished {
        task.cancel()
        fail("tempo esgotado na transcrição")
    }
    if let failure, transcript.isEmpty {
        fail(failure)
    }
    emit(
        Output(
            ok: true,
            text: transcript,
            locale: recognizer.locale.identifier,
            onDevice: onDevice
        )
    )

default:
    fail("comando desconhecido: \(command)")
}
