import Foundation
import CoreAudio
import Darwin

// The helper writes headerless 48 kHz mono signed 16-bit little-endian PCM.
// stdin is owned by the server: EOF stops capture even after a server crash.
@available(macOS 14.2, *)
final class Capture {
    let queue = DispatchQueue(label: "stt.capture")
    let file: FileHandle
    var tap: AudioObjectID = 0
    var device: AudioObjectID = 0
    var io: AudioDeviceIOProcID?
    var paused = false
    var start = DispatchTime.now().uptimeNanoseconds
    var framesWritten: Int64 = 0
    var failed = false

    init(path: String) throws {
        guard FileManager.default.createFile(atPath: path, contents: nil, attributes: [.posixPermissions: 0o600]) else { throw Failure("Cannot create recording") }
        file = try FileHandle(forWritingTo: URL(fileURLWithPath: path))
    }
    struct Failure: Error { let message: String; init(_ message: String) { self.message = message } }
    func check(_ status: OSStatus, _ operation: String) throws {
        if status != noErr { throw Failure("\(operation): \(status)") }
    }
    func startCapture() throws {
        let description = CATapDescription(monoGlobalTapButExcludeProcesses: [])
        description.isPrivate = true
        description.muteBehavior = .unmuted
        try check(AudioHardwareCreateProcessTap(description, &tap), "Create audio tap")
        var address = AudioObjectPropertyAddress(mSelector: kAudioTapPropertyFormat, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var format = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        try check(AudioObjectGetPropertyData(tap, &address, 0, nil, &size, &format), "Read tap format")
        guard format.mSampleRate == 48000, format.mChannelsPerFrame == 1,
              format.mFormatID == kAudioFormatLinearPCM, format.mBitsPerChannel == 32,
              format.mBytesPerFrame == 4, format.mFramesPerPacket == 1,
              format.mFormatFlags & kAudioFormatFlagIsBigEndian == 0,
              format.mFormatFlags & kAudioFormatFlagIsFloat != 0 else {
            throw Failure("Unsupported system audio format; expected 48 kHz mono float PCM")
        }
        let config: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Groq Free STT capture",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: description.uuid.uuidString, kAudioSubTapDriftCompensationKey: true]]
        ]
        try check(AudioHardwareCreateAggregateDevice(config as CFDictionary, &device), "Create audio device")
        try check(AudioDeviceCreateIOProcIDWithBlock(&io, device, queue) { [weak self] _, input, _, _, _ in
            self?.consume(input)
        }, "Create audio callback")
        start = DispatchTime.now().uptimeNanoseconds
        try check(AudioDeviceStart(device, io), "Start audio capture")
    }
    func pad(to target: Int64) throws {
        var remaining = max(0, target - framesWritten)
        let zeros = Data(count: 9600)
        while remaining > 0 {
            let n = Int(min(remaining, 4800))
            try file.write(contentsOf: zeros.prefix(n * 2))
            framesWritten += Int64(n)
            remaining -= Int64(n)
        }
    }
    func elapsedFrames() -> Int64 {
        Int64((DispatchTime.now().uptimeNanoseconds - start) / 1_000_000) * 48
    }
    func consume(_ list: UnsafePointer<AudioBufferList>) {
        guard !failed else { return }
        let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: list))
        guard let buffer = buffers.first, let data = buffer.mData else { return }
        let count = Int(buffer.mDataByteSize) / MemoryLayout<Float>.size
        guard count > 0 else { return }
        do {
            // The callback marks the end of this buffer. Fill any gap before it.
            let bufferStart = max(0, elapsedFrames() - Int64(count))
            try pad(to: bufferStart)
            // A pause transition may already have written silence for part of this buffer.
            let skip = min(count, Int(max(0, framesWritten - bufferStart)))
            var output = Data(capacity: (count - skip) * 2)
            let samples = data.assumingMemoryBound(to: Float.self)
            for i in skip..<count {
                let value = paused || !samples[i].isFinite ? 0 : max(-1, min(1, samples[i]))
                var sample = Int16((value * 32767).rounded()).littleEndian
                withUnsafeBytes(of: &sample) { output.append(contentsOf: $0) }
            }
            try file.write(contentsOf: output)
            framesWritten += Int64(count - skip)
        } catch {
            failed = true
            // Do not report an ongoing recording after a storage failure.
            DispatchQueue.global().async { exit(1) }
        }
    }
    func command(_ command: String) {
        queue.sync {
            do { try pad(to: elapsedFrames()) } catch { failed = true }
            if command == "pause" { paused = true }
            if command == "resume" { paused = false }
        }
    }
    func finish() {
        if device != 0, let io = io { AudioDeviceStop(device, io); AudioDeviceDestroyIOProcID(device, io) }
        queue.sync { do { try pad(to: elapsedFrames()); try file.synchronize(); try file.close() } catch { failed = true } }
        if device != 0 { AudioHardwareDestroyAggregateDevice(device) }
        if tap != 0 { AudioHardwareDestroyProcessTap(tap) }
    }
}

if #available(macOS 14.2, *) {
    do {
        guard CommandLine.arguments.count == 2 else { throw Capture.Failure("Missing output path") }
        let capture = try Capture(path: CommandLine.arguments[1])
        do { try capture.startCapture() } catch { capture.finish(); throw error }
        print("ready"); fflush(stdout)
        while let command = readLine() {
            if command == "stop" { break }
            capture.command(command)
        }
        capture.finish()
        exit(capture.failed ? 1 : 0)
    } catch { fputs("\(error)\n", stderr); exit(1) }
} else { fputs("macOS 14.2 or later required\n", stderr); exit(1) }
