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
읽거나 이관하지 않는다. 현재는 저장 기반 API이며 녹음, 전사 및 웹 화면은
후속 구현 범위다. `make test`로 격리된 임시 HOME에서 통합 테스트를 실행한다.

API는 JSON을 사용한다. `POST /jobs`에 `name`과 base64 인코딩한 `source`를
보내 작업을 만들고, `GET /jobs` 또는 `GET /jobs/{id}`로 조회한다.
`PATCH /jobs/{id}`는 `status`, `segments`, `usage`, `result`를 저장한다.
상태는 `transcribing`, `paused`, `waiting_limit`, `completed`, `stopped`다.
미완료 상태의 작업은 하나만 허용한다. 정지하면 부분 전사와 결과는 지우고
원본과 사용량은 보존한다. `GET /jobs/{id}/source`는 원본을 base64로 돌려준다.
`DELETE /jobs/{id}/source`는 원본만, `DELETE /jobs/{id}`는 전체 항목을 지운다.
