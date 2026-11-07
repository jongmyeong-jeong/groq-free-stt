import Foundation
import CoreAudio
import Darwin

if #available(macOS 14.2, *) {
    let path = CommandLine.arguments[1]
    let capture = try Capture(path: path)
    capture.start = mach_absolute_time()
    capture.transitions = [(0, false), (1, true), (48001, false)]
    var info = mach_timebase_info_data_t()
    mach_timebase_info(&info)
    let ticksPerSecond = UInt64(1_000_000_000) * UInt64(info.denom) / UInt64(info.numer)
    func send(_ time: UInt64) {
        var samples: [Float] = [1, 1]
        samples.withUnsafeMutableBytes { bytes in
            var list = AudioBufferList(mNumberBuffers: 1, mBuffers: AudioBuffer(mNumberChannels: 1, mDataByteSize: UInt32(bytes.count), mData: bytes.baseAddress))
            var timestamp = AudioTimeStamp()
            timestamp.mFlags = .hostTimeValid
            timestamp.mHostTime = time
            capture.consume(&list, timestamp: timestamp)
        }
    }
    send(capture.start)
    usleep(50_000) // callback scheduling delay must not alter the audio timeline
    send(capture.start + ticksPerSecond)
    try capture.file.synchronize()
    try capture.file.close()
    close(capture.lockFD)
    let data = try Data(contentsOf: URL(fileURLWithPath: path))
    guard data.count == 48002 * 2,
          Array(data.prefix(4)) == [255, 127, 0, 0],
          Array(data.suffix(4)) == [0, 0, 255, 127] else {
        fputs("Unexpected timing or pause output: \(data.count) bytes\n", stderr)
        exit(1)
    }
    let failedPath = path + "-failed.pcm"
    let failedCapture = try Capture(path: failedPath)
    failedCapture.finish()
    guard (try Data(contentsOf: URL(fileURLWithPath: failedPath))).isEmpty,
          FileManager.default.fileExists(atPath: URL(fileURLWithPath: failedPath).deletingPathExtension().path + ".failed") else {
        fputs("Failed initialization produced recovered audio\n", stderr)
        exit(1)
    }
} else { exit(77) }
