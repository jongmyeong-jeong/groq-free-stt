# Groq 무료 STT 제한과 요청 제어 조사

조사일: 2026-10-26 (Asia/Seoul). 대상: [Groq 무료 STT 제한과 요청 제어 가능 범위 조사](https://github.com/jongmyeong-jeong/groq-free-stt/issues/3). 제품 동작을 결정하지 않는 조사 기록이다. 실제 사용자 키·음성·계정은 사용하지 않았다.

## 확인한 사실

### 무료 제한과 적용 범위

공식 Free Plan 표의 공개 기본값은 다음과 같다. ASH는 시간당 오디오 초, ASD는 일일 오디오 초다. 예외가 있을 수 있으며 실제 조직 값은 사용자의 Console Limits가 기준이다. [Groq Rate Limits](https://console.groq.com/docs/rate-limits)

| 모델 | RPM | RPD | ASH | ASD |
| --- | ---: | ---: | ---: | ---: |
| `whisper-large-v3` | 20 | 2,000 | 7,200초 | 28,800초 |
| `whisper-large-v3-turbo` | 20 | 2,000 | 7,200초 | 28,800초 |

오디오 기준으로 시간당 2시간·일일 8시간이다. 모든 제한을 동시에 고려해야 한다. 이는 처리 속도 제한과 별개이므로 작은 압축 파일도 오디오 길이 때문에 기다릴 수 있다. [Groq Rate Limits](https://console.groq.com/docs/rate-limits)

조직 제한이 상한이다. 프로젝트마다 모델별로 더 낮은 한도를 설정할 수 있고 API 키는 생성한 프로젝트에 속한다. 프로젝트를 분리해도 합산 조직 상한을 넘길 수 없다. 따라서 다른 키·프로젝트·앱의 사용도 영향을 줄 수 있다. [Groq Projects](https://console.groq.com/docs/projects#rate-limit-management)

### 파일과 모델

- 무료 티어 파일 한도는 25 MB이며 직접 첨부도 25 MB다. 최소 파일 길이는 0.01초, 최소 **과금** 길이는 요청당 10초다. 최소 과금 길이가 ASH/ASD 차감에도 동일하게 적용되는지는 문서에서 확인하지 못했다. [Speech to Text](https://console.groq.com/docs/speech-to-text#working-with-audio-files)
- 지원 형식은 `flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm`이다. 다중 트랙은 첫 트랙만 전사한다. 16 kHz 모노로 전처리하며, 파일 축소에는 FLAC을 권장한다. [Speech to Text](https://console.groq.com/docs/speech-to-text#audio-preprocessing)
- 공식 가이드는 큰 파일에 중첩 분할·독립 전사·중첩 병합을 제안한다. 30초 모델 문맥을 업로드 최대 길이로 해석할 근거는 없다. [Speech to Text](https://console.groq.com/docs/speech-to-text#working-with-larger-audio-files), [Whisper 모델 설명](https://console.groq.com/docs/model/whisper-large-v3#key-model-details)
- 두 모델은 모두 다국어 전사를 지원한다. V3는 정확도 중심, Turbo는 속도 중심의 선택지다. 공식 일반 WER 수치는 특정 한국어 자료의 품질 보장이 아니다. 기본 모델 선택은 남은 제품 결정이다. [V3](https://console.groq.com/docs/model/whisper-large-v3), [Turbo](https://console.groq.com/docs/model/whisper-large-v3-turbo)
- API는 `json/text/verbose_json`, 단어·구간 타임스탬프, 선택적 ISO-639-1 언어를 지원한다. 타임스탬프에는 `verbose_json`을 사용한다. 번역은 별도 엔드포인트다. [Create transcription](https://console.groq.com/docs/api-reference#audio-create-transcription)

모델 소개의 100 MB 표기는 무료 파일 한도로 채택하면 안 된다. 티어와 첨부 방식을 명시한 전사 가이드의 25 MB를 기준으로 판단해야 한다. [모델 소개](https://console.groq.com/docs/model/whisper-large-v3#limits), [전사 가이드](https://console.groq.com/docs/speech-to-text#working-with-audio-files)

### 응답 헤더와 SDK

공식 헤더 설명의 `x-ratelimit-{limit,remaining,reset}-requests`는 **RPD**, `*-tokens`는 **TPM**이다. RPM/ASH/ASD 잔량으로 읽으면 안 된다. `retry-after`는 429에서만 제공되며 초 단위다. 오디오 전용 잔량 헤더의 존재·이름·항상 제공 여부는 공식 문서에서 확인하지 못했다. [Rate limit headers](https://console.groq.com/docs/rate-limits#rate-limit-headers)

Python SDK는 연결 오류·408·409·429·5xx를 기본 두 번 재시도한다. `max_retries=0`으로 숨은 재시도를 끌 수 있다. 전사는 `client.audio.transcriptions.with_raw_response.create(...)`의 `headers`와 `parse()`로 원시 헤더와 결과를 함께 얻는다. 실패 응답 헤더는 `RateLimitError.response.headers`에 접근할 수 있다. [SDK README](https://github.com/groq/groq-python/blob/55066d94acca028c3cb74e050f2711b80ade6298/README.md#retries), [전사 리소스](https://github.com/groq/groq-python/blob/55066d94acca028c3cb74e050f2711b80ade6298/src/groq/resources/audio/transcriptions.py#L30)

SDK 소스는 `Retry-After`를 직접 기다리는 범위를 `0 < 값 <= 60초`로 한정하고, 그 밖에는 짧은 지수 지연을 사용한다. 시간/일 한도 대기를 SDK 재시도에 맡기면 이른 재요청이 생길 수 있다. [SDK retry 구현](https://github.com/groq/groq-python/blob/55066d94acca028c3cb74e050f2711b80ade6298/src/groq/_base_client.py#L717)

### 현재 코드와 차이

기준 커밋은 `b351bae`다. 현재 클라이언트는 Turbo·한국어를 기본으로 쓰고 SDK 재시도를 명시하지 않으며, 정상 응답 헤더를 저장하지 않는다. 429를 자체 오류로 바꿀 때 구체적 헤더 정보를 보관하지 않는다. 배치는 크기 기반 분할 후 순차 요청하고, 429인 구간을 실패 처리한 뒤 다음 구간을 시도한다. [groq_client.py](../../reference/legacy-source/app/groq_client.py.txt#L139), [batch_transcribe.py](../../reference/legacy-source/app/batch_transcribe.py.txt#L372)

## 공식 자료로 확정하지 못한 부분

- 실제 사용자 조직/프로젝트의 현재 제한·남은 사용량. 사용자 계정에 접속하지 않았다.
- 시간/일 제한의 정확한 리셋·회복 알고리즘과 기준 시간대. 자정에 모두 복구된다고 가정할 수 없다.
- 두 Whisper 모델 간 오디오 사용량 풀 공유 여부, 실패·타임아웃·429 요청의 각 quota 차감 규칙, 초 반올림 규칙.
- 음성 한도 잔량을 호출 전에 조회·예약하는 공개 API와, 앱 밖 사용을 완전하게 동기화할 수 있는 수단.

위 항목은 확인한 공식 공개 문서에서 보장 계약을 찾지 못했다는 뜻이다. 해당 기능이 서버에 없다는 단정은 아니다.

## 설계 후보와 보장 범위 — 조사에 따른 추론

다음은 채택된 제품 결정이 아니라 [분할 전사와 무료 한도 대기·복구 동작 결정](https://github.com/jongmyeong-jeong/groq-free-stt/issues/6)에 넘기는 후보다.

1. 하나의 요청 관리자가 요청 전 RPM/RPD/ASH/ASD 예산을 원자적으로 예약하고 모든 파일·재시도를 통과시킨다. 분할은 압축 크기와 실제 업로드 오디오 길이를 함께 제한한다. 중첩과 재전송도 길이에 포함하고 짧은 조각은 보수적으로 최소 10초 예산을 잡는다. 이는 미확인 차감 규칙에 대한 안전 여유다.
2. SDK 재시도는 끄고 앱이 예약·요청·결과·실패를 기록한다. 429에서는 같은 관리 범위의 다음 요청도 멈추고 서버의 재시도 지시를 존중한다. 헤더가 없거나 비정상이면 정해 둔 보수적 지연으로 재평가한다. 에러 문구만 파싱하는 방식을 서버 계약으로 삼지 않는다.
3. 전사 구간 결과·예약 시각·전송 상태·재개 예정 시각을 영속 저장한다. 재시작해도 완료 구간을 재요청하지 않는다. 전송 중 죽어서 서버 성공 여부를 모르는 요청은 별도 상태로 남긴다. 원격 idempotency 보장을 확인하지 못했으므로 네트워크 오류 후 중복 처리나 중복 사용량이 전혀 없다고 약속하지 않는다.
4. 로컬 장부만으로 외부 앱·다른 키의 동시 사용을 알 수 없다. 새 키로 바꿨다고 조직 예산을 가득 채워 시작하거나 모델 전환으로 한도를 우회하지 않는다. 고정 기본값은 버전이 붙은 공개 기준이고, 실제 제한·헤더로 관찰 가능한 정보와 구분한다.

제안할 수 있는 약속은 **“앱이 관리하는 사용량을 요청 전에 제한하고, 서버가 알려 주는 한도에서 요청을 멈춰 자동 재개한다”**다. 공유 조직과 미확인 서버 사용량까지 포함한 **“429가 절대 발생하지 않는다”**는 이번 조사 근거로 보장할 수 없다.

## 다음 사람의 결정

- 기본 모델과 언어, 분할 길이·중첩·품질 기준.
- 공유 계정의 관찰 불가능한 사용량을 설명할 문구와 첫 실행/키 변경 때의 보수적 대기 정책.
- 취소, 장기 대기, 인증 실패, 전송 중 종료의 사용자 표시와 복구 규칙.
- 실제 제한 확인/변경 경험을 얼마나 제공할지. 숫자를 임의로 늘리는 기능은 기본 요구가 아니다.

## 검증

- 공식 공개 문서를 직접 조회하고 Context7에서 `/groq/groq-python`을 먼저 resolve한 뒤 SDK 문서를 조회했다.
- 프로젝트의 `uv.lock`과 설치 SDK는 모두 `groq==1.2.0`이었다. 공식 현재 소스와 로컬 설치 소스의 기본 재시도·원시 응답 접근을 대조했다.
- `httpx.MockTransport`로 합성 응답만 사용해 검증했다: 기본 SDK의 429는 전송 3회, `max_retries=0`은 1회; `RateLimitError.response.headers` 접근, 전사 원시 응답의 헤더 읽기·`parse()` 모두 통과했다. 외부 API 호출은 0회다.
- 실제 계정 한도·오디오 초 차감·한국어 전사 품질은 시험하지 않았다. 제품 코드는 변경하지 않았다. 출시 버전: 해당 없음(조사).
