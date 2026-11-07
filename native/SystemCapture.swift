import Foundation
import CoreAudio
import Darwin

// The helper writes headerless 48 kHz mono signed 16-bit little-endian PCM.
// stdin is owned by the server: EOF stops capture even after a server crash.
@available(macOS 14.2, *)
final class Capture {
    let queue = DispatchQueue(label: "stt.capture")
    let file: FileHandle
    let lockFD: Int32
    let failedURL: URL
    var tap: AudioObjectID = 0
    var device: AudioObjectID = 0
    var io: AudioDeviceIOProcID?
    var transitions: [(Int64, Bool)] = [(0, false)]
    var start: UInt64 = 0
    var started = false
    var framesWritten: Int64 = 0
    var failed = false

    init(path: String) throws {
        let base = URL(fileURLWithPath: path).deletingPathExtension().path
        let fd = open(base + ".lock", O_CREAT | O_RDWR | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { throw Failure("Cannot open recording lock") }
        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else { close(fd); throw Failure("Recording is owned") }
        do {
            let marker = URL(fileURLWithPath: base + ".failed")
            try Data().write(to: marker, options: .atomic)
            guard FileManager.default.createFile(atPath: path, contents: nil, attributes: [.posixPermissions: 0o600]) else { throw Failure("Cannot create recording") }
            file = try FileHandle(forWritingTo: URL(fileURLWithPath: path))
            lockFD = fd
            failedURL = marker
        } catch { close(fd); throw error }
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
        try check(AudioDeviceCreateIOProcIDWithBlock(&io, device, queue) { [weak self] _, input, inputTime, _, _ in
            self?.consume(input, timestamp: inputTime.pointee)
        }, "Create audio callback")
        start = mach_absolute_time()
        try check(AudioDeviceStart(device, io), "Start audio capture")
        started = true
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
    func frames(at hostTime: UInt64) -> Int64 {
        guard hostTime >= start else { return 0 }
        var info = mach_timebase_info_data_t()
        mach_timebase_info(&info)
        let nanoseconds = Double(hostTime - start) * Double(info.numer) / Double(info.denom)
        return Int64(nanoseconds * 48000 / 1_000_000_000)
    }
    func elapsedFrames() -> Int64 { frames(at: mach_absolute_time()) }
    func consume(_ list: UnsafePointer<AudioBufferList>, timestamp: AudioTimeStamp) {
        guard timestamp.mFlags.contains(.hostTimeValid) else {
            failed = true
            DispatchQueue.global().async { exit(1) }
            return
        }
        guard !failed else { return }
        let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: list))
        guard let buffer = buffers.first, let data = buffer.mData else { return }
        let count = Int(buffer.mDataByteSize) / MemoryLayout<Float>.size
        guard count > 0 else { return }
        do {
            // The input timestamp is the first frame, independent of callback scheduling.
            let bufferStart = frames(at: timestamp.mHostTime)
            try pad(to: bufferStart)
            // A pause transition may already have written silence for part of this buffer.
            let skip = min(count, Int(max(0, framesWritten - bufferStart)))
            var output = Data(capacity: (count - skip) * 2)
            let samples = data.assumingMemoryBound(to: Float.self)
            var transition = 0
            for i in skip..<count {
                let frame = bufferStart + Int64(i)
                while transition + 1 < transitions.count && transitions[transition + 1].0 <= frame { transition += 1 }
                let value = transitions[transition].1 || !samples[i].isFinite ? 0 : max(-1, min(1, samples[i]))
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
            if command == "pause" { transitions.append((elapsedFrames(), true)) }
            if command == "resume" { transitions.append((elapsedFrames(), false)) }
        }
    }
    func finish() {
        if device != 0, let io = io { AudioDeviceStop(device, io); AudioDeviceDestroyIOProcID(device, io) }
        queue.sync { do { if started { try pad(to: elapsedFrames()) }; try file.synchronize(); try file.close() } catch { failed = true } }
        if device != 0 { AudioHardwareDestroyAggregateDevice(device) }
        if tap != 0 { AudioHardwareDestroyProcessTap(tap) }
        if started && !failed {
            do { try FileManager.default.removeItem(at: failedURL) } catch { failed = true }
        }
        close(lockFD)
    }
}

