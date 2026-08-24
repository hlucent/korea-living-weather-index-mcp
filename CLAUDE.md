# CLAUDE.md — korea-living-weather-index-mcp

## 절대 규칙

- DEVPLAN.md 하나만 먼저 읽고 시작한다. 다른 문서 재탐색 금지.
- 웹서치 금지 (API 스펙은 DEVPLAN.md에 이미 있음).
- 불확실하면 추측성 재설계 대신 기본값 1개로 구현 후 DEVLOG.md에 "확인 필요"로
  기록.
- 동일 오류 최대 3회까지만 재시도. 3회 실패 시 기록하고 사용자에게 보고.
- **역할은 "코드 구현 + 로컬 실측 테스트"까지다.** `fly launch`, `fly secrets
  set`, `flyctl deploy`, `fly logs` 등 fly.io 관련 명령은 절대 스스로 실행하지
  않는다.
- 배포 준비(코드 구현, 로컬 테스트, git commit/push)가 끝나면 아래 "작업
  순서"의 정지 시점에서 멈추고, 사용자에게 PowerShell에서 `fly launch
  --no-deploy`부터 진행하도록 안내한다.

## 이 프로젝트 고유 사항

### API 특성

- **오퍼레이션 2개**: `getUVIdxV5`(자외선지수), `getAirDiffusionIdxV5`(대기정체지수)
- **인증키는 쿼리 파라미터 `serviceKey`** (공공데이터포털 표준 방식 — URL 경로 삽입 아님)
- 응답은 XML이 기본이며, `dataType=JSON` 지정 시 JSON으로 온다
- **시간 필드 범위가 오퍼레이션마다 다르다**:
  - 자외선지수: `h0, h3, h6, ..., h75` (0~75시간, 26개 필드)
  - 대기정체지수: `h3, h6, ..., h78` (3~78시간, 26개 필드)
  - 이 차이를 코드에서 명확히 구분할 것 (딕셔너리나 상수로 분리 관리 권장)
- **areaNo가 필수(1)로 표기되어 있으나 "공백이면 전체지점조회"라는 설명이
  공존** — 실측 필요 항목 (아래 참고)

### 등급 매핑 로직 (docstring에 반드시 명시)

자외선지수 (범위 기반):
```python
def uv_grade(value: float) -> str:
    if value >= 11: return "위험"
    if value >= 8: return "매우높음"
    if value >= 6: return "높음"
    if value >= 3: return "보통"
    return "낮음"  # 0~2
```

대기정체지수 (값 기반, 25/50/75/100 근사 매핑 — 정확히 일치하지 않을 경우
가장 가까운 단계로 근사):
```python
def air_diffusion_grade(value: float) -> str:
    if value >= 87.5: return "매우높음"   # 100 근사
    if value >= 62.5: return "높음"       # 75 근사
    if value >= 37.5: return "보통"       # 50 근사
    return "낮음"                          # 25 근사
```
실측 시 실제 값이 정확히 25/50/75/100 중 하나로만 오는지 먼저 확인하고,
그렇다면 근사 로직 대신 정확히 일치하는 값으로 매핑해도 된다. DEVLOG.md에
확인 결과를 기록할 것.

### 실측 필요 항목 (2-6절 절차 그대로 적용)

1. **`areaNo=` 빈 문자열이 실제로 "전체지점조회"로 동작하는지, 아니면
   ERROR-10(잘못된 요청 파라메터)이 발생하는지** — 둘 다 시도해서 확인. 만약
   전체지점조회가 실제로 동작한다면 응답 크기가 매우 클 수 있으므로(3,838개
   지점), 툴 설계에서 이 옵션을 굳이 노출할지도 판단 필요(기본적으로는 특정
   areaNo 필수로 유지 권장).
2. **자외선지수 응답에 `h78` 필드가 실제로 포함되는지** (문서 표에는 h75까지만
   정의, 응답 예제 XML에는 h78도 등장 — 불일치).
3. **대기정체지수 값이 정확히 25/50/75/100인지, 중간값도 존재하는지.**
4. **에러 응답이 `dataType=JSON` 요청에도 XML로 오는지** (다른 공공데이터포털
   API에서 흔한 패턴 — 아래 XML 폴백 파서로 대응).

### 응답 파싱 (JSON 우선, XML 폴백 필수)

```python
def parse_response(response_text: str) -> dict:
    try:
        return json.loads(response_text)
    except ValueError:
        # XML 폴백: resultCode/resultMsg 패턴 추출
        import re
        code_match = re.search(r"<resultCode>(.*?)</resultCode>", response_text)
        msg_match = re.search(r"<resultMsg>(.*?)</resultMsg>", response_text)
        return {
            "resultCode": code_match.group(1) if code_match else "99",
            "resultMsg": msg_match.group(1) if msg_match else "UNKNOWN_ERROR",
        }
```

### 숫자 필드 안전 변환

지수값(h0~h78 등)은 문자열로 오므로, 안전하게 변환한다. 결측/비정상 값은
`None` 처리(대기질처럼 실측 0과 결측을 구분해야 하는 성격의 데이터이므로 0
대신 None 권장):

```python
def _safe_float(v):
    if v is None or v in ("", "-"):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
```

### IP 추출 / Rate limit / CORS preflight

프로젝트 지침 2-2절, 2-7절의 표준 코드를 그대로 적용한다:
- `Fly-Client-IP` 헤더 최우선 신뢰, `X-Forwarded-For`는 폴백
- OPTIONS 요청은 rate limit 카운팅에서 제외
- 분당 30회 / 1시간 20회 위반 시 24시간 차단 / 일일 1000회 상한
  (2026-08-25부터 개인 전용 사용 기준으로 완화. `MCP_ACCESS_KEY` 인증이 이미
  걸려 있어, rate limit은 실수로 반복 호출해도 안 막히는 수준이면 충분하다고
  판단해 3/5/30에서 완화함)

### 접근 인증 (2026-08-24 추가)

이 서버는 원래 인증 없이 URL만 알면 접근 가능했으나(코드 주석에도 "인증이
필요 없는 공개 서버"로 명시돼 있었음), 타인의 접속을 완전히 차단하기 위해
`AuthMiddleware`를 추가했다.

- `MCP_ACCESS_KEY`: 이 서버 자체 접근용 전용 비밀키. `KMA_LIVING_WEATHER_SERVICE_KEY`·
  `SAFEMAP_API_KEY`(둘 다 업스트림 API 호출용)와는 목적이 다른 별개 키다.
- 요청의 `?key=` 값을 `hmac.compare_digest`로 `MCP_ACCESS_KEY`와 비교, 불일치/누락
  시 401.
- `/mcp`뿐 아니라 `/api/dashboard`(PWA 대시보드용 REST 엔드포인트)도 인증 대상.
  대시보드만 무인증으로 열려 있으면 `/mcp` 인증이 무의미해지기 때문.
- `AuthMiddleware`는 `RateLimitMiddleware`보다 먼저 실행되도록 `middleware=[...]`
  리스트에서 앞에 위치시킨다(Starlette 미들웨어 리스트는 첫 항목이 가장 바깥쪽 =
  가장 먼저 실행). 인증 실패 요청이 rate limit 카운터를 소모하지 않게 하기 위함.
- `fly.toml`은 이 시점부터 `.gitignore` 처리 — 앱 이름(`app = '...'`)을 GitHub에
  커밋하지 않는다. 로컬 fly.toml에서 실제 앱 이름을 확인할 것.

### area_codes.json 재사용

기존 `safemap-uv-index-mcp` 프로젝트의 `area_codes.json`을 그대로 복사해서
쓴다. 별도로 엑셀을 다시 변환하지 않는다. 만약 로컬에 해당 파일 경로를 못
찾으면, DEVPLAN.md 2절에 있는 엑셀 원본 정보를 참고해 사용자에게 파일 위치를
질문한다(재변환은 최후 수단).

## 작업 순서

1. `requirements.txt` (`fastmcp`, `httpx`, `python-dotenv`)
2. `kma_living_weather_api.py` — API 호출 + 에러코드 매핑(JSON 우선, XML 폴백)
3. `server.py` — 3개 툴(`get_uv_forecast`, `get_air_diffusion_forecast`,
   `search_area_code`) 정의, docstring에 필드/단위/등급 명시,
   `stateless_http=True` 필수, rate limit 미들웨어 포함
4. `area_codes.json` 배치, `.env.example`, `.gitignore`
5. 로컬 테스트 (실제 키로 각 툴 호출, 위 "실측 필요 항목" 전부 확인)
6. FastMCP 스모크 테스트 (initialize 요청까지만)
7. `Dockerfile`, `fly.toml` (프로젝트 지침 6절 표준 템플릿 그대로 사용)
8. README/DEVLOG 갱신 (실측 결과를 실제 동작 기준으로 반영)
9. git add/commit/push
10. **여기서 정지** — 사용자에게 PowerShell 배포 절차 안내

## 하지 말 것

- 툴 개수를 3개보다 늘리지 않기 (DEVPLAN.md 3절 범위 고정)
- 인증키 하드코딩 금지
- `stateless_http=True` 누락 금지
- `fly launch` / `fly secrets set` / `flyctl deploy` / `fly logs` 자동 실행 금지
- rate limit 미들웨어 누락 금지
- `AuthMiddleware` 누락 금지, `/mcp`·`/api/dashboard` 중 하나라도 인증에서 빠뜨리지 않기
- `MCP_ACCESS_KEY` 값을 코드/문서/커밋에 하드코딩하지 않기 (환경변수로만 참조)
- 자외선지수(h0~h75)와 대기정체지수(h3~h78)의 시간 필드 범위를 혼동해서 같은
  파싱 로직을 억지로 공유하지 않기 — 별도 상수/딕셔너리로 명확히 구분
