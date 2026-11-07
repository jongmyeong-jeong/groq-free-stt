# Groq Free STT

This repository is a starting point for a local transcription application. The application has not been implemented or released yet.

The research in `docs/research/` and the UI source in `reference/prototype/` preserve prior investigation and the approved screen prototype. The prototype is a historical reference, not the product: its controls use mock data, and the surrounding legacy web code contains calls for a previous backend. Do not use it with a live backend or personal data.

The new application will keep its data in `~/.groq-free-stt/`. Existing `~/.lonta/` data remains untouched and is not migrated automatically. The intended first release is 0.1.0.

Decisions and requirements: [decision map](https://github.com/jongmyeong-jeong/lonta/issues/2), [screen flow](https://github.com/jongmyeong-jeong/lonta/issues/8), and [migration](https://github.com/jongmyeong-jeong/lonta/issues/9).

## 로컬 실행 및 데이터

macOS에서 Python 3를 설치한 후 `make setup`을 실행하고 `.env`에
`GROQ_API_KEY`를 입력한다. `make start`는 `http://127.0.0.1:8765`에서
로컬 저장 API를 실행한다. 기존 `.env`는 setup이 덮어쓰지 않는다.

작업 데이터는 `~/.groq-free-stt/jobs.sqlite3`에 저장한다. 기존 `~/.lonta`는
읽거나 이관하지 않는다. 현재는 저장 및 시스템 녹음 API의 초기 구현이며 전사 및 웹 화면은
후속 구현 범위다. `make test`로 격리된 임시 HOME에서 통합 테스트를 실행한다.

API는 JSON을 사용한다. `POST /jobs`에 `name`과 base64 인코딩한 `source`를
보내 작업을 만들고, `GET /jobs` 또는 `GET /jobs/{id}`로 조회한다.
`PATCH /jobs/{id}`는 `status`, `segments`, `usage`, `result`를 저장한다.
상태는 `transcribing`, `paused`, `waiting_limit`, `completed`, `stopped`다.
미완료 상태의 작업은 하나만 허용한다. 정지하면 부분 전사와 결과는 지우고
원본과 사용량은 보존한다. `GET /jobs/{id}/source`는 원본을 base64로 돌려준다.
`DELETE /jobs/{id}/source`는 원본만, `DELETE /jobs/{id}`는 전체 항목을 지운다.

긴 업로드는 `POST /uploads`에 `application/octet-stream` 본문과 URL 인코딩한
`X-Filename` 헤더를 보내면 디스크로 스트리밍한다. 기존 JSON 업로드는 메모리
사용을 제한하기 위해 원본 64 MB까지 허용한다. FFmpeg와 FFprobe가 필요하다.
`POST /jobs/{id}/transcribe`로 전사를 시작하고 `GET /jobs/{id}/md` 또는
`GET /jobs/{id}/txt`로 결과를 다운로드한다. 실패한 구간은 부분 결과에 표시한다.
이 API는 아직 화면에 연결되지 않았으며 한도 제어와 재시작 자동 재개는 후속 작업이다.

## 시스템 오디오 구현 상태

macOS 14.2 이상에서 `make setup`은 Swift 도구와 코드 서명을 사용해 시스템
오디오 helper를 빌드한다. 최초 캡처 시 시스템 오디오 녹음 권한이 요청될 수
있으며, 거부된 경우 시스템 설정의 개인정보 보호 및 보안에서 허용해야 한다.
무음과 권한 거부는 확실히 구별할 수 없다. 현재 helper는 48kHz 모노 Float32
시스템 오디오만 지원하며, 다른 형식은 녹음을 시작하지 않는다.

서버 API의 `POST /system-recording/start`, `/pause`, `/resume`, `/stop`과
`/heartbeat`는 녹음을 제어한다. 클라이언트는 녹음 중 15초 이내마다 heartbeat를
보내야 한다. 시작 요청은 `starting`을 즉시 반환하며 권한 응답을 최대 5분
기다린다. 일시 정지와 재개는 helper가 명령 처리를 확인한 뒤 성공한다.
통신이 중단되거나 서버가 종료되면 helper가 종료되며,
`GET /system-recordings`에서 복구 성공 또는 실패를 조회할 수 있다. 복구된
녹음의 원본은 `GET /system-recordings/{id}/source`에서 가져올 수 있다.
복구는 자동 전사를 시작하지 않고 PCM 원본을 남긴다. helper가 아직 파일을
쓰는 동안은 복구를 기다리며, 실패한 helper의 유효한 부분 원본은 실패 상태로
표시하고 사용자가 가져갈 수 있다. 손상된 원본도 보관한다. `make stop`은 서버를
정상 종료하며 먼저 녹음을 마무리한다. PCM은 장시간 녹음에 디스크 공간을
사용하며, WAV 형식의 크기 제한을 넘거나 파일이 손상되면 복구에 실패한다.
강제 종료나 저장 장치 장애에서 마지막 구간의 보존은 보장하지 않는다.

**아직 제품 화면에 연결되지 않았고 실제 시스템 오디오, 권한, 긴 무음,
일시 정지 및 강제 종료에 대한 실기기 검증이 필요하다.** 현재 테스트는
helper를 대체한 프로세스와 복구 API를 검증한다.
