# CLAUDE.md — korea-living-weather-index-mcp

## 절대 규칙

- DEVPLAN.md 하나만 먼저 읽고 시작한다. 다른 문서 재탐색 금지.
- 웹서치 금지 (API 스펙은 DEVPLAN.md에 이미 있음).
- 불확실하면 추측성 재설계 대신 기본값 1개로 구현 후 DEVLOG.md에 "확인 필요"로
  기록.
- 동일 오류 최대 3회까지만 재시도. 3회 실패 시 기록하고 사용자에게 보고.
- 이 프로젝트는 **로컬 전용(stdio) MCP 서버**다. HTTP 배포, fly.io 관련 명령
  (`fly launch`, `fly secrets set`, `flyctl deploy`, `fly logs` 등)은 대상이
  아니며 실행하지 않는다.

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

### 트랜스포트 (2026-09-14 로컬 전용 전환)

로컬 전용 stdio 서버로 운영한다 — `mcp.run()`이 기본 stdio 트랜스포트를
사용한다. HTTP 트랜스포트, 인증 미들웨어(`MCP_ACCESS_KEY`), rate limit
미들웨어, `/api/dashboard` REST 엔드포인트는 모두 제거되었다(로컬 프로세스로만
접근하므로 불필요). 관련 배경은 DEVLOG.md 참고.

### area_codes.json 재사용

기존 `safemap-uv-index-mcp` 프로젝트의 `area_codes.json`을 그대로 복사해서
쓴다. 별도로 엑셀을 다시 변환하지 않는다. 만약 로컬에 해당 파일 경로를 못
찾으면, DEVPLAN.md 2절에 있는 엑셀 원본 정보를 참고해 사용자에게 파일 위치를
질문한다(재변환은 최후 수단).

## 작업 순서

1. `requirements.txt` (`fastmcp`, `httpx`, `python-dotenv`)
2. `kma_living_weather_api.py` — API 호출 + 에러코드 매핑(JSON 우선, XML 폴백)
3. `server.py` — 툴(`get_uv_forecast`, `get_air_diffusion_forecast`,
   `search_area_code`, `get_uv_index`) 정의, docstring에 필드/단위/등급 명시,
   `mcp.run()`(stdio) 사용
4. `area_codes.json` 배치, `.env.example`, `.gitignore`
5. 로컬 테스트 (실제 키로 각 툴 호출, 위 "실측 필요 항목" 전부 확인)
6. README/DEVLOG 갱신 (실측 결과를 실제 동작 기준으로 반영)
7. git add/commit/push

## 하지 말 것

- 인증키 하드코딩 금지
- 자외선지수(h0~h75)와 대기정체지수(h3~h78)의 시간 필드 범위를 혼동해서 같은
  파싱 로직을 억지로 공유하지 않기 — 별도 상수/딕셔너리로 명확히 구분
