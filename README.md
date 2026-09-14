# korea-living-weather-index-mcp

기상청이 공공데이터포털을 통해 제공하는 **생활기상지수 조회서비스(4.0)**
(`LivingWthrIdxServiceV5`)를 MCP로 구현한 서버입니다. **자외선지수**와
**대기정체지수** 예보를 전국 약 3,838개 지점(시군구~읍면동 단위) 기준으로
3시간 간격, 최대 75~78시간 후까지 조회할 수 있습니다. 추가로 행정안전부
생활안전지도(IF_0113)의 **실측/현재 자외선지수**도 함께 제공합니다.

기존에 운영 중이던 `safemap-uv-index-mcp`(행정안전부 생활안전지도 자외선지수
+ 기상청 예보)의 `get_uv_index` 툴을 이 프로젝트로 이식해 통합했습니다. 두
프로젝트가 겹치는 기능을 갖게 되어, `safemap-uv-index-mcp`는 더 이상
독립적으로 확장하지 않고 이 MCP로 기능을 일원화하는 방향입니다.

## 제공 툴

### `get_uv_forecast`
지점코드(areaNo)와 발표시간(time)을 기준으로 자외선지수 예보를 조회합니다.
0시간 후부터 75시간 후까지 3시간 간격 예측값을 반환합니다.

**자외선지수 단계**
| 단계 | 지수범위 |
|---|---|
| 위험 | 11 이상 |
| 매우높음 | 8~10 |
| 높음 | 6~7 |
| 보통 | 3~5 |
| 낮음 | 0~2 |

### `get_air_diffusion_forecast`
지점코드(areaNo)와 발표시간(time)을 기준으로 대기정체지수 예보를 조회합니다.
3시간 후부터 78시간 후까지 3시간 간격 예측값을 반환합니다.

**대기정체지수 단계**
| 단계 | 자료값 |
|---|---|
| 매우높음 | 100 |
| 높음 | 75 |
| 보통 | 50 |
| 낮음 | 25 |

### `search_area_code`
지역명(시/도, 시/군/구, 읍/면/동)으로 지점코드(areaNo)를 검색합니다.

### `get_uv_index`
행정안전부 생활안전지도(IF_0113, 기상청 제공) 실측/현재 자외선지수를
시/도·시/군/구 기준으로 조회합니다. `safemap-uv-index-mcp`에서 이식했습니다.

> **주의**: 이 API는 위 세 툴과 다른 API이며 **`areaNo` 코드 체계를 쓰지
> 않습니다**. 지역 필터 파라미터가 서버에 없어(실측 확인됨) 전국 데이터를
> 가져온 뒤 `sido`/`sigungu` 텍스트로 클라이언트 사이드 필터링을 수행합니다.
> 응답도 `ctprvn_nm`/`signgu_nm`(시도명/시군구명 문자열)로만 오며, `area_codes.json`의
> `areaNo`와는 매핑되지 않습니다.

## 설치 및 실행

로컬 전용(stdio) MCP 서버입니다. HTTP 배포 없이 `python server.py`로 바로
실행합니다.

```bash
pip install -r requirements.txt
cp .env.example .env  # KMA_LIVING_WEATHER_SERVICE_KEY, SAFEMAP_API_KEY 값 입력
python server.py
```

### 설치 방법

#### 1. Claude Code (CLI)

```bash
claude mcp add korea-living-weather-index-mcp -- python <프로젝트 경로>/server.py --scope user
```

`--scope user`로 등록하면 어느 폴더에서 작업하든 이 MCP 도구를 사용할 수
있습니다.

#### 2. Claude Desktop

`claude_desktop_config.json`의 `mcpServers`에 아래 항목을 추가합니다:

```json
{
  "mcpServers": {
    "korea-living-weather-index-mcp": {
      "command": "<python 절대경로>",
      "args": ["<프로젝트 경로>/server.py"]
    }
  }
}
```

**주의사항**

- `"command"`에 그냥 `"python"`만 쓰면 실패할 수 있습니다. PATH의 `python`과
  실제로 `fastmcp` 등 패키지가 설치된 python이 다른 경우가 있기 때문입니다
  (Windows에서 흔함).
- 아래 명령으로 실제 패키지가 설치된 python의 절대경로를 먼저 확인하세요:

  ```bash
  python -c "import sys; print(sys.executable)"
  ```

  이 절대경로를 `"command"`에 직접 지정하는 것을 권장합니다.
- 설정 변경 후 Claude Desktop을 완전히 종료(작업 관리자에서 프로세스 확인
  포함)한 뒤 재시작해야 반영됩니다.
- 연결이 안 되면 Claude Desktop 개발자 설정 → 로컬 MCP 서버 → 해당 서버 →
  "로그 보기"에서 `Using MCP server command: <경로>` 로그와 그 아래
  에러(Traceback)를 확인하면 원인이 바로 드러납니다.

#### 3. Claude.ai 웹 / Cowork

이 서버는 로컬 전용 stdio 방식이므로 claude.ai 웹이나 Cowork에서는 직접
연결할 수 없습니다. Claude Code 또는 Claude Desktop에서만 사용 가능합니다.

## 환경변수

| 변수명 | 설명 |
|---|---|
| `KMA_LIVING_WEATHER_SERVICE_KEY` | 공공데이터포털에서 발급받은 "기상청_생활기상지수 조회서비스(4.0)" 일반 인증키(Decoding) |
| `SAFEMAP_API_KEY` | 행정안전부 생활안전지도(IF_0113) 오픈API 인증키 |

## 데이터 출처

- **제공기관**: 기상청 / 행정안전부
- **플랫폼**: 공공데이터포털(data.go.kr) / 생활안전지도(safemap.go.kr)
- **API명**: 생활기상지수 조회서비스(4.0) (`LivingWthrIdxServiceV5`),
  생활안전지도 자외선지수(IF_0113)
- **라이선스**: 공공누리 (각 플랫폼 이용약관에 따름)

## 알려진 제약사항 (실측 완료, 2026-08-23 기준)

- `areaNo`를 빈 문자열로 보내면 **전체지점조회로 정상 동작**함(ERROR-10
  아님). 다만 응답이 매우 클 수 있어 이 옵션은 툴 파라미터로 노출하지
  않았습니다. `area_no` 또는 `area_name`으로 특정 지점을 지정해 조회하세요.
- 자외선지수 응답에는 **`h78` 필드가 포함되지 않음**을 확인했습니다(h0~h75만
  존재, 명세서 예제의 h78은 오기로 판단). 야간 시간대 값은 빈 문자열로 와서
  결측(None) 처리됩니다.
- 대기정체지수 값은 실측 결과 **정확히 25/50/75/100 중 하나로만 확인**되었으나,
  표본이 제한적이라 코드에는 안전하게 근사 매핑 로직(37.5/62.5/87.5 경계)을
  유지하고 있습니다.
- 에러 응답도 `dataType=JSON` 요청 시 **JSON으로 옴**을 확인했습니다(XML
  아님). XML 폴백 파서는 안전장치로 남겨두었습니다.

## 관련 프로젝트

- `safemap-uv-index-mcp` — 2026-08-23부로 **이 프로젝트에 흡수·통합됨**.
  해당 프로젝트가 제공하던 `get_uv_index`(행안부 생활안전지도 자외선지수)와
  `get_uv_forecast`, 지역코드 검색 기능이 모두 이 저장소로 이관되었으며,
  대기정체지수 기능도 새로 추가되었다. `safemap-uv-index-mcp` 저장소는
  참고용으로만 보관되고 fly.io 배포는 중단되었다. 앞으로 자외선지수·
  대기정체지수 관련 신규 기능은 모두 이 저장소(korea-living-weather-index-mcp)
  하나로 개발한다.
