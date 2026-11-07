import Foundation
import Darwin

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

