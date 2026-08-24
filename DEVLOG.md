# DEVLOG.md — korea-living-weather-index-mcp

## 2026-08-23 — 프로젝트 시작

- 기상청 생활기상지수 조회서비스(4.0) API 명세서(docx) + 지점코드 엑셀
  (dfs-zone-tree_excel_20260701.xlsx) 업로드받아 분석 완료.
- 오퍼레이션 2종 확인: `getUVIdxV5`(자외선지수), `getAirDiffusionIdxV5`(대기정체지수)
- 지점코드 엑셀이 기존 `safemap-uv-index-mcp`의 `area_codes.json`과 동일
  출처(기상청 생활기상지수 지점코드 매핑표, 2026-07-01 기준)로 확인됨 —
  재변환 없이 기존 파일 재사용하기로 결정.
- 사용자 결정: 기존 `safemap-uv-index-mcp`(행안부+기상청 병행)는 그대로
  유지하고, 이번 건은 **신규 MCP** `korea-living-weather-index-mcp`로 개발
  (자외선지수+대기정체지수 둘 다 포함).
- DEVPLAN.md 작성 완료 직후 자체검증 수행:
  - `grep -n "저장소 설명" DEVPLAN.md` → 9절에서 확인됨 (정상)
  - `grep -n "ENV_KEY:" DEVPLAN.md` → 8절에서 확인됨 (정상)
  - 둘 다 최초 작성 시점에 포함되어 누락 없이 통과.
- 명세서 내 목차와 본문 표기 불일치 발견: 목차는 "대기확산지수조회", 상세
  기능명은 "대기정체지수조회"로 혼용. 기상청이 과거 "대기확산지수"를
  "대기정체지수"로 개칭한 이력과 일치하는 것으로 추정 — README/DEVPLAN에는
  "대기정체지수"로 통일해 기재함.
- 명세서 내 자체 불일치 발견(실측 필요 항목으로 등록):
  1. `areaNo` 필수(1) 표기 vs "공백이면 전체지점조회" 설명 공존
  2. 자외선지수 요청/응답 스키마 표는 h0~h75까지만 정의하나, 응답 예제 XML에는
     h78도 등장
  3. 대기정체지수 "자료값" 표는 25/50/75/100 단일값만 제시 — 중간값 존재
     여부 불명
- 다음 단계: Claude Code에게 CLAUDE.md 전달 후 구현 착수 예정.

## 2026-08-23 — 구현 및 실측 완료

- `requirements.txt`, `kma_living_weather_api.py`, `server.py`, `area_codes.json`
  (safemap-uv-index-mcp에서 복사), `.env.example` 작성 완료.
- 실측 결과 (실제 서비스키로 호출):
  1. **`areaNo=` 빈 문자열 → 전체지점조회로 정상 동작 확인** (ERROR-10 아님).
     resultCode=00 정상 응답, 전체 3,838개 지점 데이터가 반환되는 것으로
     보임(numOfRows 기본값 10에 의해 첫 페이지만 확인). 응답 크기가 매우 클
     수 있으므로 툴 설계에서는 area_no/area_name을 통한 특정 지점 조회를
     기본으로 유지하고, 빈 문자열 전체조회 옵션은 툴 파라미터로 노출하지
     않음(DEVPLAN 권장안 그대로 따름).
  2. **자외선지수 응답에 `h78` 필드 없음 확인.** 실제 응답은 h0~h75까지만
     포함(h60~h75는 야간이라 값이 빈 문자열로 옴 — 결측 처리로 None 변환됨).
     문서 예제의 h78은 오기로 판단. 코드에는 안전하게 h78 폴백 파싱 로직은
     남겨둠(있어도 없어도 동작).
  3. **대기정체지수 값은 실측 결과 정확히 25/50/75/100 중 하나로만 확인됨**
     (중간값 없음). 다만 표본이 1개 지점 1개 발표시각에 한정되므로, 안전하게
     근사 매핑 로직(37.5/62.5/87.5 경계)은 그대로 유지하기로 함 — 정확한
     값 매핑으로 교체하지 않음(리스크 없음, 안전한 선택).
  4. **에러 응답도 `dataType=JSON` 요청 시 JSON으로 옴** (XML로 안 옴).
     잘못된 areaNo로 호출 시 `{"response":{"header":{"resultCode":"99",...}}}`
     형태로 정상적으로 JSON 파싱 가능함을 확인. XML 폴백 로직은 안전장치로
     유지하되, 실제로는 거의 타지 않을 것으로 예상.
- FastMCP 앱 임포트 및 3개 툴 등록 스모크 테스트 통과.
- 정지 시점 도달: 코드 구현 + 로컬 실측 테스트 완료. fly.io 배포는 사용자가
  직접 진행 필요 (CLAUDE.md 절대 규칙에 따름).

## 2026-08-23 — get_uv_index 이식, 2개 MCP를 하나로 통합

- 사용자 요청으로 `safemap-uv-index-mcp`의 `get_uv_index`(행정안전부
  생활안전지도 IF_0113, 실측/현재 자외선지수) 로직을 이 프로젝트로 그대로
  이식함. 4번째 툴로 추가.
- 구현: `safemap_api.py` 신규 파일로 IF_0113 호출/파싱 로직을 분리 이식
  (`fetch_uv_index`, `SafemapApiError`, XML 폴백 파서 등 원본 그대로).
  `server.py`에 `get_uv_index` 툴 등록.
- **areaNo 코드 체계 확인 결과: 두 API가 다른 체계를 씀.**
  `getUVIdxV5`/`getAirDiffusionIdxV5`(기상청 생활기상지수 4.0)는 `areaNo`
  (지점코드)를 쓰지만, IF_0113(행안부 생활안전지도)은 `areaNo`를 전혀 쓰지
  않고 `ctprvn_nm`/`signgu_nm`(시도명/시군구명 문자열) 기반으로만 조회한다.
  서버 자체에 지역 필터 파라미터가 없어(원본 프로젝트에서 이미 실측 확인됨)
  전국 데이터를 가져온 뒤 클라이언트 사이드에서 문자열 매칭한다. 따라서
  `area_codes.json`/`search_area_code`의 `areaNo`를 `get_uv_index`에 재사용할
  수 없음 — 별도 파라미터(`sido`, `sigungu`)로 필터링하도록 설계함.
- 인증키도 별도(`SAFEMAP_API_KEY`, `.env`/`.env.example`에 추가)이며 기존
  `KMA_LIVING_WEATHER_SERVICE_KEY`와 혼동하지 않도록 문서화함.
- 로컬 실측 테스트: `get_uv_index(sido="서울", num_of_rows=300)` 호출 결과
  total_count=269, filtered_count=26으로 정상 필터링 확인. 한글 데이터는
  UTF-8로 정상 반환됨(터미널 출력에서만 코드페이지 문제로 깨져 보였던 것을
  파일 저장으로 재확인).
- DEVPLAN.md §3(툴 설계 4개로 갱신, areaNo 불일치 주의사항 명시), §8(환경변수
  SAFEMAP_API_KEY 추가), README.md(4번째 툴 설명, 환경변수, 배포 안내,
  관련 프로젝트 섹션) 갱신.
- 두 MCP(`safemap-uv-index-mcp`, `korea-living-weather-index-mcp`)의 기능이
  겹치게 되어, 향후 신규 기능은 이 MCP로 일원화하는 방향으로 정리함(원본
  프로젝트는 코드 유지, 신규 확장 없음).
- 정지 시점 도달: 코드 구현 + 로컬 실측 테스트 완료. fly.io 배포는 사용자가
  직접 진행 필요 (CLAUDE.md 절대 규칙에 따름).

## 2026-08-24 — OAuth discovery 404로 인한 claude.ai 커넥터 등록 실패 대응

- 증상: claude.ai에 이 MCP를 커넥터로 등록 시 실패. `curl`로
  `/.well-known/oauth-protected-resource`를 직접 호출해보니 404 확인.
- 원인 추정: 이 서버는 API 키 인증이 아니라 자체 인증이 없는 공개 서버라
  OAuth 자체가 필요 없지만, claude.ai 커넥터 등록 과정이 관례적으로
  OAuth Protected Resource discovery(RFC 9728)를 먼저 시도한다. 이 경로가
  404를 반환하자 등록 로직이 반복 재시도하며 rate limit(분당 3회)까지
  소진시켜 등록이 최종 실패하는 것으로 추정.
- 조치 (`server.py`):
  1. `/.well-known/oauth-protected-resource` GET 라우트를 `app.add_route`로
     추가. RFC 9728 최소 형태로 `resource`(필수)와 `authorization_servers: []`
     (인가 서버 없음 = 인증 불필요를 명시)를 담은 200 JSON 응답을 반환.
  2. `RateLimitMiddleware.dispatch`에서 이 경로를 OPTIONS와 동일하게
     카운팅 대상에서 제외(`OAUTH_DISCOVERY_PATH` 상수로 분리) — discovery
     재시도 자체가 실제 요청의 rate limit을 갉아먹지 않도록 함.
- 로컬 검증: `python server.py` 기동 후
  `curl -i .../.well-known/oauth-protected-resource` → 200 확인
  (기존 404 대비). 동일 경로 4회 연속 호출해도 전부 200 — 분당 3회
  제한에 걸리지 않음을 확인(rate limit 제외 정상 동작).
- 정지 시점 도달: 코드 수정 + 로컬 검증 완료. fly.io 재배포(`flyctl deploy`)는
  사용자가 PowerShell에서 직접 진행 필요.
