# 시스템 오디오 녹음 통합 조사

조사일: 2026-10-26 (Asia/Seoul). 대상: [시스템 오디오 녹음 코드를 통합할 때의 제약 조사](https://github.com/jongmyeong-jeong/lonta/issues/4). 출시 버전: 해당 없음(조사 문서). 제품 코드와 UI는 변경하지 않았다.

## 결론

**기존 Swift 녹음 엔진을 앱 번들 형태의 독립 녹음 프로세스로 재사용하는 경로가 작다.** 기존 Python 서버와 UI를 유지하면서 로컬 제어 API와 저장 파일을 연결할 수 있다. 다만 이 방식은 후보이며, 통합·권한·장치 전환을 실행해 검증한 결론은 아니다. macOS 14.2 이상을 전제로 하고, 타임스탬프의 의미와 녹음 종료/복구 동작은 사용자와 결정해야 한다.

첫 출시의 확정 조건은 macOS 우선 로컬 웹 앱, 시스템 소리·마이크 각각 녹음 및 파일 업로드, 한도 대기 후 자동 전사 재개와 완료 구간 유지다. 동시 녹음/믹싱은 범위 밖이다. 참고 프로젝트의 화면은 조사·재사용 대상에서 제외했다.

## Apple 공식 자료로 확인한 사실

- Apple의 Core Audio tap 샘플은 **macOS 14.2 이상**을 요구한다. tap을 HAL aggregate device의 입력으로 사용하며, `NSAudioCaptureUsageDescription`을 포함해야 한다. tap이 포함된 aggregate device에서 녹음을 처음 시작할 때 시스템 오디오 권한 창이 뜬다. [Apple: Capturing system audio with Core Audio taps](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps)
- `NSAudioCaptureUsageDescription`은 시스템 오디오 접근 목적을 사용자에게 설명하는 문자열이다. [Apple: NSAudioCaptureUsageDescription](https://developer.apple.com/documentation/bundleresources/information-property-list/nsaudiocaptureusagedescription)
- mono global tap의 제외 프로세스 목록에 없는 출력 프로세스들은 함께 모노로 들어간다. 따라서 빈 제외 목록을 사용하는 현재 구현은 특정 회의 앱만을 고르는 기능이 아니다. [Apple: initMonoGlobalTapButExcludeProcesses](https://developer.apple.com/documentation/coreaudio/catapdescription/initmonoglobaltapbutexcludeprocesses:)
- macOS는 code signing requirement로 코드의 동일성을 판별하며, 이는 개인정보 접근 허용을 기억하는 데 관계된다. bundle ID 문자열만 유지한다고 모든 배포·업데이트에서 권한이 보존된다고 단정할 수는 없다. [Apple: TN3127](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements)

## 현재 녹음 구현에서 확인한 사실

참고 소스의 기준 경로는 `/Users/jongmyeong/Dev/Tools/system-audio-recorder`다. 이 폴더는 Git 저장소가 아니어서 아래의 절대 경로·행과 문서 끝 SHA-256으로 조사 스냅샷을 식별한다. 원격 커밋 링크가 있는 것처럼 취급하지 않았다.

| 영역 | 코드로 확인한 동작 | 근거 |
| --- | --- | --- |
| 최소 환경 | Swift tools 5.9, deployment target와 Info.plist 최소 OS가 14.2다. | [Package.swift:1](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Package.swift:1), [Info.plist:17](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Resources/Info.plist:17) |
| 수집 | `monoGlobalTapButExcludeProcesses: []`, private tap, unmuted, 기본 출력 장치를 붙인 private aggregate device를 만든다. 가상 장치 설치 단계는 없다. | [SystemAudioRecorder.swift:207](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/SystemAudioRecorder.swift:207) |
| 파일 | 입력을 변환하여 48kHz 모노 AAC 64kbps로 쓴다. `.aac`에 먼저 기록하고 종료 시 passthrough export로 `.m4a`에 담는다. 전사에 넣기 전 크기·분할 관리는 별도다. | [SystemAudioRecorder.swift:61](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/SystemAudioRecorder.swift:61), [SystemAudioRecorder.swift:286](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/SystemAudioRecorder.swift:286) |
| 일시 정지 | paused일 때 들어온 버퍼를 버린다. 해당 시간은 파일 길이와 화면 시간에 포함되지 않는다. | [SystemAudioRecorder.swift:108](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/SystemAudioRecorder.swift:108), [RecorderController.swift:164](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/RecorderController.swift:164) |
| 장치 변경 | 기본 출력 장치 알림을 받으면 tap/aggregate를 걷고 다시 만들며 같은 파일에 쓴다. 재시작 실패는 2초 후 재시도한다. 장치 변경 중 연속성이나 누락 없음은 이 코드만으로 증명되지 않는다. | [SystemAudioRecorder.swift:75](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/SystemAudioRecorder.swift:75), [SystemAudioRecorder.swift:137](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/SystemAudioRecorder.swift:137) |
| 정상 종료 | 종료 요청 또는 SIGINT/SIGTERM/SIGHUP에서 controller.shutdown을 호출하여 녹음을 마무리한다. | [main.swift:51](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/main.swift:51), [main.swift:81](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/main.swift:81), [RecorderController.swift:194](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/RecorderController.swift:194) |
| 강제 종료 복구 | 시작할 때 앱의 파일명 규칙과 일치하는 남은 AAC를 찾아 M4A로 바꾼다. 빈 AAC는 지운다. 기존 M4A가 있으면 변환하지 않는다. 마지막 버퍼까지 무손실 복구된다는 보장은 없다. | [RecorderController.swift:235](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/RecorderController.swift:235), [main.swift:66](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/main.swift:66) |
| 제어 | native 프로세스가 recorder를 소유한다. status/start/pause/resume/stop/reset/quit API가 있고 127.0.0.1에 바인딩한다. POST는 Host/Origin 및 `X-Sysrec: 1`을 검사한다. | [WebServer.swift:35](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/WebServer.swift:35), [WebServer.swift:121](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/WebServer.swift:121) |

## 권한·서명·실행 주체

참고 도구는 앱 번들에 사용 목적 문자열을 넣고, `open`으로 번들을 실행한다. 기본 빌드는 ad-hoc 서명에 명시적인 designated requirement를 붙인다. 이 경로를 유지하는 편이 이미 성공한 권한 경험을 활용하기 쉽다. Python에서 Swift 바이너리를 직접 자식 프로세스로 실행해도 같은 권한 주체가 된다고 가정해서는 안 된다. [Info.plist:21](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Resources/Info.plist:21), [build.sh:10](/Users/jongmyeong/Dev/Tools/system-audio-recorder/build.sh:10), [sysrec:29](/Users/jongmyeong/Dev/Tools/system-audio-recorder/sysrec:29)

직접 바이너리를 실행하면 터미널에 권한이 귀속되어 무음이 들어왔다는 내용과 동일 identifier 요건으로 세 번 재빌드 후 권한 창이 다시 뜨지 않았다는 내용은 **이 도구 README의 로컬 실험 기록**이다. Apple의 모든 환경에 대한 보장으로 확대하지 않았다. 새 제품의 bundle ID·서명·실행 경로로 최초 허용, 거부 후 재허용, 재빌드 후 재실행을 검증해야 한다. [README.md:54](/Users/jongmyeong/Dev/Tools/system-audio-recorder/README.md:54), [README.md:71](/Users/jongmyeong/Dev/Tools/system-audio-recorder/README.md:71)

`heardSound`는 0이 아닌 샘플을 받은 적이 있는지만 나타낸다. 권한 거부와 실제 무음을 확실히 구별하는 권한 상태 API로 취급하면 안 된다. [SystemAudioRecorder.swift:37](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/SystemAudioRecorder.swift:37), [SystemAudioRecorder.swift:175](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/SystemAudioRecorder.swift:175)

## 시간·수명·복구의 차이

- **무음:** README는 출력이 멈춘 150초 후 5초를 재생한 실험에서 파일이 5.5초였다고 기록한다. 코드는 전달받은 frame 수로만 길이를 누적하며 별도 무음 패딩이나 wall-clock 매핑을 저장하지 않는다. 따라서 현 구현을 그대로 쓰면 전사의 시각은 파일 재생 위치이고 실제 회의 경과 시각과 달라질 수 있다. 이는 해당 환경의 관찰이며 모든 장치에서 같은 양상이 나온다는 뜻은 아니다. [README.md:41](/Users/jongmyeong/Dev/Tools/system-audio-recorder/README.md:41), [SystemAudioRecorder.swift:152](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/SystemAudioRecorder.swift:152), [RecorderController.swift:164](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/RecorderController.swift:164)
- **시스템 녹음과 탭 종료:** native 프로세스가 계속 살아 있으면 브라우저를 닫아도 녹음은 계속된다. README도 이를 명시한다. 시스템 녹음을 브라우저 소유 마이크와 똑같은 수명으로 표현할지는 제품 결정이다. [RecorderController.swift:3](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/RecorderController.swift:3), [README.md:18](/Users/jongmyeong/Dev/Tools/system-audio-recorder/README.md:18)
- **현재 마이크:** lonta는 브라우저의 getUserMedia/MediaRecorder가 캡처하며, 화면 unmount 때 recorder와 tracks를 멈춘다. beforeunload는 경고하고 마지막 청크의 flush를 시도할 뿐, finalize와 영구 복구를 보장하지 않는다. [Recording.jsx:143](https://github.com/jongmyeong-jeong/lonta/blob/b351bae9a7e7a3fe8aa5795f96a9314c24384c07/web/src/pages/Recording.jsx#L143), [Recording.jsx:339](https://github.com/jongmyeong-jeong/lonta/blob/b351bae9a7e7a3fe8aa5795f96a9314c24384c07/web/src/pages/Recording.jsx#L339)
- **현재 Python 서버:** startup은 진행 중 전사를 실패로 바꾸고 녹음 임시 폴더를 정리한다. 종료 시 전사 작업을 최대 120초 기다린다. 새 요구인 완료된 전사 구간 유지와 자동 재개는 이 동작으로 충족되지 않는다. native AAC 복구와 전사 작업의 지속성은 서로 다른 문제다. [server.py:243](https://github.com/jongmyeong-jeong/lonta/blob/b351bae9a7e7a3fe8aa5795f96a9314c24384c07/app/server.py#L243), [recordings.py:142](https://github.com/jongmyeong-jeong/lonta/blob/b351bae9a7e7a3fe8aa5795f96a9314c24384c07/app/recordings.py#L142)

## 최소 통합 후보와 남은 결정

다음은 구현 지시가 아닌 **검토 후보**다.

1. 기존 Python 서버와 웹 UI는 유지한다. Swift의 recorder/controller를 번들 helper로 묶어 `open`으로 실행하고, 별도 localhost API를 Python 서버가 호출한다. 브라우저는 기존 서버 하나와 대화하므로 helper의 Origin 정책을 완화할 이유가 없다. 재사용한다면 현재 Host/Origin/header 계약과 포트 충돌·프로세스 중복 실행 처리를 함께 다뤄야 한다.
2. helper는 제품이 정한 음성 저장 폴더에 AAC→M4A를 마무리한다. Python은 stop 응답 직후 파일이 완성됐다고 가정하지 않고 `stopping`에서 `done`으로 바뀌었는지와 확정 경로를 확인한 뒤, 마이크·업로드와 공통 전사 작업을 등록한다. 현재 status는 파일명만 제공하므로 임의 경로 연결 대신 확정 저장 루트와 작업 ID의 매핑이 필요하다. [RecorderController.swift:42](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/RecorderController.swift:42), [RecorderController.swift:174](/Users/jongmyeong/Dev/Tools/system-audio-recorder/Sources/sysrec/RecorderController.swift:174)
3. 녹음 완료 파일·작업 ID·분할 결과·성공 구간·대기 시점을 영구적으로 연결한다. helper가 복구한 파일을 자동 전사에 넣을지 사용자에게 확인할지, Python 서버 종료 시 helper도 종료할지, 탭을 닫았을 때 계속 녹음 중임을 어떻게 알릴지는 사람의 결정이다.

이 후보는 Swift 웹 서버 전체를 다시 만드는 경로보다 기존 성공 코드를 덜 바꾸지만, 두 프로세스의 생명주기와 포트를 관리해야 한다. 단일 native launcher가 Python까지 소유하는 방식도 가능하나 이번 조사에서는 실행·패키징 검증을 하지 않았다. 어느 구조든 첫 실행/설치 티켓과 화면 검토에서 결정한다.

후속 결정에 필요한 구체적 질문:

- macOS 최소 버전을 14.2로 명시하고, 사용자의 소스 빌드/도구 설치를 요구할 것인가? 배포 번들로 제공할 것인가?
- 타임스탬프는 파일 재생 시각으로 충분한가? 실제 경과 시각이 필요하면 무음/일시 정지와 장치 전환 구간을 어떻게 표현할 것인가?
- 녹음 중 탭·Python 서버·helper 종료와 복구 파일 발견 시, 어느 시점에 전사를 자동 시작할 것인가?

## 검증 범위

본 조사에서 수행: 위 소스의 정적 읽기, Apple 공식 문서 대조, 조사 기준 파일 SHA-256 생성, 보고서 링크와 코드 행 확인. **수행하지 않음:** 앱 빌드/실행, 실제 녹음, 권한 변경, 장치 변경, 강제 종료 실험, 통합/E2E 테스트. README에 적힌 무음·강제 종료 복구·재빌드 실험은 기존 도구 작성자의 기록이며 이번 조사에서 재현한 결과가 아니다. 장치 전환은 README도 실제 기기 실험을 하지 않았다고 명시한다. [README.md:42](/Users/jongmyeong/Dev/Tools/system-audio-recorder/README.md:42)

## 참고 소스 스냅샷

lonta의 기준 커밋: `b351bae9a7e7a3fe8aa5795f96a9314c24384c07`. sysrec는 Git 메타데이터가 없어 2026-10-26에 읽은 파일의 SHA-256을 아래에 남긴다. 오디오 파일과 UI 소스는 포함하지 않았다.

| sysrec 상대 경로 | SHA-256 |
| --- | --- |
| `Package.swift` | `934f2bd81bcf0db80f3566bc56e8cf273b28093109314e9ec5adb6ec286ad2fd` |
| `Resources/Info.plist` | `d5f9d613f2b95f2575192ef3c33bf0f03090a25ec4879094a86433d74c961a0d` |
| `build.sh` | `c4ec88c0b86acbbc5c14ca8fb37a7ae02cd5fcf4b7b8a714d35a5b8c88b7b9c8` |
| `sysrec` | `6c740d15bd4a90f6f4bbefdbff685ac42643c28ecb26538a466c3076d91a89ca` |
| `README.md` | `70327ca75e88586d8e0ac3d1dd0fd11a8c917f8cfbde7206b31118fa7181b8f9` |
| `Sources/sysrec/SystemAudioRecorder.swift` | `903bdc31f5c2e91710804783b6ca827c353992ed2a76cd60d8315744d25addac` |
| `Sources/sysrec/RecorderController.swift` | `8751d5ae598dfb6bbf7868033fdb5ec8c7f32b3513cdbe31424a76d05e54fe49` |
| `Sources/sysrec/WebServer.swift` | `3818bfa48fea836f1c02482c733ec97ab1e00dafd162d7992c7762d53b5dfb73` |
| `Sources/sysrec/main.swift` | `f964b1fd303238b67e8fc4de5b0084aef798041be1efecb3adb61b301c22c812` |
