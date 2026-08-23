# DEVPLAN.md — korea-living-weather-index-mcp

## 0. 프로젝트 개요

기상청이 공공데이터포털을 통해 제공하는 **생활기상지수 조회서비스(4.0)**
(`LivingWthrIdxServiceV5`)를 MCP로 구현한다. 이 API는 **자외선지수**와
**대기정체지수** 2종의 오퍼레이션을 제공하며, 전국 시군구/읍면동 단위(약
3,838개 지점)로 3시간 간격, 최대 75~78시간 후까지의 예측값을 제공한다.

기존에 운영 중인 `safemap-uv-index-mcp`(행정안전부 생활안전지도 IF_0113 +
기상청 getUVIdxV5 예보)와는 **별개의 신규 MCP**로 개발한다 (사용자 결정 —
이름을 "기상청 생활기상지수 MCP"로 명확히 하고, 대기정체지수까지 포함해
확장하기로 함). 기존 MCP는 그대로 유지된다.

## 1. API 스펙 요약

### 1-1. API 서비스 정보

| 항목 | 값 |
|---|---|
| API명(국문) | 생활기상지수 조회서비스 |
| API명(영문) | LivingWthrIdxServiceV5 |
| 제공기관 | 기상청 |
| 플랫폼 | 공공데이터포털(data.go.kr) |
| 서비스 URL | `http://apis.data.go.kr/1360000/LivingWthrIdxServiceV5` |
| 인터페이스 | REST(GET) |
| 응답 형식 | XML(기본) / JSON(`dataType=JSON` 지정) |
| 인증 방식 | `serviceKey` 쿼리 파라미터 (공공데이터포털 표준 방식) |
| 데이터 갱신주기 | 수시(일 8회) |
| 서비스 시작일 | 2021-08-03 |

**오퍼레이션 2종:**

| 번호 | 오퍼레이션명(영문) | 오퍼레이션명(국문) | Call Back URL |
|---|---|---|---|
| 1 | `getUVIdxV5` | 자외선지수조회 | `http://apis.data.go.kr/1360000/LivingWthrIdxServiceV5/getUVIdxV5` |
| 2 | `getAirDiffusionIdxV5` | 대기정체지수조회(=대기확산지수조회) | `http://apis.data.go.kr/1360000/LivingWthrIdxServiceV5/getAirDiffusionIdxV5` |

※ 문서 내 목차에는 "대기확산지수조회", 상세기능명에는 "대기정체지수조회"로
혼용 표기되어 있음. 기상청이 2023-02-20 이후 "대기확산지수"를
"대기정체지수"로 명칭 변경한 것과 일치하는 것으로 추정(웹검색 확인 — 기상청
날씨누리 공지). 이 MCP에서는 **"대기정체지수"로 통일**하되, 오퍼레이션 영문명
`getAirDiffusionIdxV5`는 API 스펙 그대로 유지한다.

### 1-2. 공통 요청 파라미터 (두 오퍼레이션 동일)

| 항목명(영문) | 설명 | 타입 | 필수여부 | 샘플 |
|---|---|---|---|---|
| serviceKey | 공공데이터포털 발급 인증키 | 문자열 | 필수(1) | - |
| areaNo | 지점코드. 공백이면 전체지점조회 | 문자열 | 필수(1) | 1100000000(서울) |
| time | 발표시간(YYYYMMDDHH, 3시간 단위: 00/03/06/09/12/15/18/21) | 문자열 | 필수(1) | 2021070618 |
| dataType | 응답 형식(XML/JSON), Default: XML | 문자열 | 선택(0) | JSON |
| numOfRows | 한 페이지 결과 수, Default: 10 | 문자열 | 선택(0) | 10 |
| pageNo | 페이지 번호 | 문자열 | 선택(0) | 1 |

**실측 필요 항목**: `areaNo`가 "필수(1)"로 명시되어 있으나 설명에는 "공백일때:
전체지점조회"라고 되어 있어 모순적이다. 빈 문자열(`areaNo=`)을 실제로
허용하는지, 아니면 파라미터 자체를 생략해야 하는지 실측이 필요하다 (1-1절
"부분 채움 금지" 패턴과 유사한 사례일 수 있음 — 프로젝트 지침 참고).

### 1-3. 자외선지수조회(getUVIdxV5) 응답 필드

**header**
| 필드 | 설명 |
|---|---|
| resultCode | 응답메시지 코드 |
| resultMsg | 응답메시지 내용 |

**body**
| 필드 | 설명 |
|---|---|
| dataType | 데이터 타입 |
| items.item | 결과 배열 |
| numOfRows | 한 페이지 결과 수 |
| pageNo | 페이지 번호 |
| totalCount | 데이터 총 개수 |

**item (자외선지수)**
| 필드 | 설명 | 단위 |
|---|---|---|
| areaNo | 지점코드 | - |
| date | 발표시간(YYYYMMDDHH) | - |
| code | 지수코드 | - |
| h0 | 0시간 후 예측값 | 지수(무단위) |
| h3, h6, h9, ... h72, h75 | 3시간 간격 예측값 (0~75시간, 총 26개 필드) | 지수(무단위) |

※ 응답 예제 XML에는 `h78` 필드도 등장하지만(응답 스키마 표에는 없음), 요청
메시지 명세 표에는 `h0`부터 `h75`까지만 정의되어 있다. 문서 내 표와 예제가
불일치하므로, Claude Code가 실측 단계에서 실제 응답에 h78이 포함되는지
확인하고 안전하게 처리(있으면 파싱, 없으면 무시)하도록 한다.

**자외선지수 단계 및 범위** (지수 값 → 등급 매핑, 툴 docstring에 반드시 포함)
| 단계 | 지수범위 |
|---|---|
| 위험 | 11 이상 |
| 매우높음 | 8~10 |
| 높음 | 6~7 |
| 보통 | 3~5 |
| 낮음 | 0~2 |

### 1-4. 대기정체지수조회(getAirDiffusionIdxV5) 응답 필드

header/body 구조는 자외선지수와 동일.

**item (대기정체지수)**
| 필드 | 설명 | 단위 |
|---|---|---|
| areaNo | 지점코드 | - |
| date | 발표시간(YYYYMMDDHH) | - |
| code | 지수코드 | - |
| h3, h6, h9, ... h75, h78 | 3시간 간격 예측값 (3~78시간, 총 26개 필드) | 지수(무단위, 0~100) |

※ 자외선지수는 `h0`부터 시작(0~75시간), 대기정체지수는 `h3`부터 시작(3~78시간)
— **두 오퍼레이션의 시간 필드 범위가 다르다.** 서버 코드에서 이 차이를
명확히 구분해서 처리해야 한다 (자외선: h0,h3...h75 / 대기정체: h3,h6...h78).

**대기정체지수 단계 및 범위** (지수값 → 등급 매핑, 툴 docstring에 반드시 포함)
| 지수단계 | 자료값 |
|---|---|
| 매우높음 | 100 |
| 높음 | 75 |
| 보통 | 50 |
| 낮음 | 25 |

※ 자외선지수는 "범위"로, 대기정체지수는 "단일값"으로 등급이 매핑되는 방식이
다르다. 실측 시 실제 응답값이 정확히 25/50/75/100 중 하나로만 오는지, 아니면
그 사이 값도 존재하는지 확인 필요(문서상으로는 "자료값"이라 정확히 4단계
값만 존재할 가능성이 높으나, 안전하게 근사 매핑 로직도 함께 고려).

### 1-5. 생산주기·예측기간

| 지수명 | 생산주기 | 생산시간 | 예측기간 | 제공기간 |
|---|---|---|---|---|
| 자외선지수 | 일 8회 | 00~21시(3시간 간격) | 오늘~글피(+3h~+78h) | 연중 |
| 대기정체지수 | 일 8회 | 00~21시(3시간 간격) | 오늘~글피(+3h~+78h) | 연중 |

### 1-6. 에러코드 (공공데이터포털 표준 2자리 숫자 코드)

| 코드 | 메시지 | 설명 |
|---|---|---|
| 00 | NORMAL_SERVICE | 정상 |
| 01 | APPLICATION_ERROR | 어플리케이션 에러 |
| 02 | DB_ERROR | 데이터베이스 에러 |
| 03 | NODATA_ERROR | 데이터없음 에러 |
| 04 | HTTP_ERROR | HTTP 에러 |
| 05 | SERVICETIME_OUT | 서비스 연결실패 에러 |
| 10 | INVALID_REQUEST_PARAMETER_ERROR | 잘못된 요청 파라메터 에러 |
| 11 | NO_MANDATORY_REQUEST_PARAMETERS_ERROR | 필수요청 파라메터가 없음 |
| 12 | NO_OPENAPI_SERVICE_ERROR | 해당 오픈API서비스가 없거나 폐기됨 |
| 20 | SERVICE_ACCESS_DENIED_ERROR | 서비스 접근거부 |
| 21 | TEMPORARILY_DISABLE_THE_SERVICEKEY_ERROR | 일시적으로 사용할 수 없는 서비스 키 |
| 22 | LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR | 서비스 요청제한횟수 초과에러 |
| 30 | SERVICE_KEY_IS_NOT_REGISTERED_ERROR | 등록되지 않은 서비스키 |
| 31 | DEADLINE_HAS_EXPIRED_ERROR | 기한만료된 서비스키 |
| 32 | UNREGISTERED_IP_ERROR | 등록되지 않은 IP |
| 33 | UNSIGNED_CALL_ERROR | 서명되지 않은 호출 |
| 99 | UNKNOWN_ERROR | 기타에러 |

(이 플랫폼은 공공데이터포털 표준 2자리 숫자 코드 체계이며, 서울시의
INFO-000/ERROR-3xx 문자열 코드 체계와는 다르다 — 프로젝트 지침 8절 참고)

## 2. 지점코드(areaNo) 매핑

기상청 생활기상지수 API의 `areaNo`는 행정구역코드 체계를 사용하며, 사용자가
제공한 엑셀(`dfs-zone-tree_excel_20260701.xlsx`, 시트 "최종 업데이트
파일_20260701", 3,838개 지점)이 기존 `safemap-uv-index-mcp`의
`area_codes.json`과 **동일 출처(기상청 생활기상지수 지점코드 매핑표,
2026-07-01 기준)** 로 확인되었다.

**재사용 방침**: 기존 `safemap-uv-index-mcp/area_codes.json` 파일을 그대로
복사해 재사용한다. 별도 신규 변환 작업 불필요. (컬럼 구조: 구분/행정구역코드/
1단계/2단계/3단계/격자XY/경위도 — MCP에서는 행정구역코드(`areaNo`)와
1~3단계 지역명만 사용하면 충분하다.)

**의정부시 관련 지점코드 예시** (엑셀 실측 확인):
| areaNo | 1단계 | 2단계 | 3단계 |
|---|---|---|---|
| 4115000000 | 경기도 | 의정부시 | (전체) |
| 4115062000 | 경기도 | 의정부시 | 녹양동 |
| 4115051000 | 경기도 | 의정부시 | 의정부1동 |

## 3. MCP 툴 설계

**툴 개수: 3개** (오퍼레이션 2개 + 지점코드 검색 1개 — 기존
`safemap-uv-index-mcp`와 동일한 패턴)

| 툴 이름 | 대응 오퍼레이션 | 설명 |
|---|---|---|
| `get_uv_forecast` | getUVIdxV5 | 지점코드+발표시간 기준 자외선지수 예보(0~75시간, 3시간 간격) |
| `get_air_diffusion_forecast` | getAirDiffusionIdxV5 | 지점코드+발표시간 기준 대기정체지수 예보(3~78시간, 3시간 간격) |
| `search_area_code` | (자체 기능, API 아님) | 지역명으로 areaNo 검색 (기존 MCP의 `search_uv_area_code`와 동일 로직) |

**설계 근거**: "API 1개 = MCP 1개, 최소 툴 개수" 조언(1-4절)에 따라, 같은
API(`LivingWthrIdxServiceV5`)의 두 오퍼레이션을 하나의 MCP로 묶었다.
제공기관·활용신청 단위가 동일(기상청, 공공데이터포털 단일 인증키)하고,
사용자가 "생활기상지수"라는 통합된 이름으로 MCP를 켜고 끄고 싶어하므로
분리보다 통합이 자연스럽다고 판단했다.

## 4. 기술 스택

- Python 3.11+, FastMCP
- httpx (API 호출)
- python-dotenv (.env 로드)
- `stateless_http=True` 필수 (fly.io 멀티머신 대응)
- Rate limit 미들웨어 포함 (API 키 없이 공개하는 서버이므로 2-7절 기준 적용)

## 5. 디렉토리 구조

```
korea-living-weather-index-mcp/
├── server.py              # MCP 툴 정의 (stateless_http=True)
├── kma_living_weather_api.py  # API 호출 + 에러코드 매핑 (JSON 우선, XML 폴백)
├── area_codes.json         # 지점코드 매핑 (safemap-uv-index-mcp에서 복사)
├── requirements.txt
├── .env.example
├── .gitignore
├── Dockerfile
├── fly.toml
├── CLAUDE.md
├── README.md
└── DEVLOG.md
```

## 6. 진행 순서

1. `requirements.txt`
2. `kma_living_weather_api.py` — API 호출 클라이언트, JSON 우선 파싱 + XML
   폴백, 에러코드 매핑(1-6절 표)
3. `server.py` — 3개 툴 정의, `stateless_http=True`, rate limit 미들웨어
4. `area_codes.json` — 기존 `safemap-uv-index-mcp`에서 복사
5. `.env.example`, `.gitignore`
6. 로컬 실측 테스트
   - `areaNo` 빈 문자열 허용 여부 확인 (1-2절 실측 필요 항목)
   - 응답의 `h78` 필드 존재 여부(자외선지수) 확인 (1-3절)
   - 대기정체지수 값이 정확히 25/50/75/100인지, 중간값도 있는지 확인 (1-4절)
   - `dataType=JSON` 요청 시 실제 JSON으로 오는지, 에러 응답은 여전히 XML로
     오는지 확인
7. FastMCP 스모크 테스트
8. `Dockerfile`, `fly.toml` (6절 표준 템플릿)
9. README/DEVLOG 갱신
10. git add/commit/push
11. 정지 — 사용자 안내 문구 출력

## 7. 사용자가 먼저 할 일

- 공공데이터포털(data.go.kr)에서 "기상청_생활기상지수 조회서비스(4.0)"
  활용신청 및 일반 인증키(Decoding) 발급
- 발급받은 키를 `C:\Users\hwang\Scripts\api-keys.env.example`에 기록

## 8. 환경변수

- ENV_KEY: KMA_LIVING_WEATHER_SERVICE_KEY

## 9. 저장소 설명(Description 제안)

> 기상청 생활기상지수(자외선지수·대기정체지수) 조회 MCP 서버 — 전국 3,838개 지점의 3시간 단위 예보(최대 78시간) 제공
