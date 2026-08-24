"""korea-living-weather-index-mcp: 기상청 생활기상지수(4.0) MCP 서버.

- get_uv_forecast: 자외선지수 예보(getUVIdxV5, h0~h75)
- get_air_diffusion_forecast: 대기정체지수 예보(getAirDiffusionIdxV5, h3~h78)
- search_area_code: 지역명으로 areaNo 검색
- get_uv_index: 행정안전부 생활안전지도(IF_0113) 실측/현재 자외선지수
  (safemap-uv-index-mcp에서 이식. areaNo 코드 체계를 쓰지 않는 별개 API임에 주의)
"""

import hmac
import os
import time
import json
import difflib
import threading
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from kma_living_weather_api import (
    fetch_uv_forecast,
    fetch_air_diffusion_forecast,
    LivingWeatherApiError,
)
from safemap_api import (
    fetch_uv_index,
    SafemapApiError,
    _safe_int,
)
from kakao_geocode_api import reverse_geocode, KakaoGeocodeApiError

load_dotenv()

mcp = FastMCP("korea-living-weather-index-mcp")

# ---------------------------------------------------------------------------
# Rate limit: 분당 3회 / 1시간 5회 위반 시 24시간 차단, 일일 30회 상한
# ---------------------------------------------------------------------------

MINUTE_LIMIT = 3
MINUTE_WINDOW = 60
HOUR_VIOLATION_LIMIT = 5
HOUR_WINDOW = 3600
BLOCK_DURATION = 24 * 3600
DAILY_LIMIT = 30
DAY_WINDOW = 24 * 3600

_rate_limit_lock = threading.Lock()
_minute_buckets: dict[str, list[float]] = {}
_violation_buckets: dict[str, list[float]] = {}
_daily_buckets: dict[str, list[float]] = {}
_blocked_until: dict[str, float] = {}


def _get_client_ip(request: Request) -> str:
    fly_client_ip = request.headers.get("fly-client-ip")
    if fly_client_ip:
        return fly_client_ip.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


OAUTH_DISCOVERY_PATH = "/.well-known/oauth-protected-resource"

# 서버 전용 접근 비밀키(MCP_ACCESS_KEY) 검사.
# KMA_LIVING_WEATHER_SERVICE_KEY(기상청 업스트림 API 호출용)와는 별개의 키다 —
# 이 키는 "이 MCP 서버 자체에 접근할 수 있는 사람인가"만 판별한다.
# RateLimitMiddleware보다 먼저 실행해 인증 실패 요청이 rate limit 카운터를
# 소모하지 않도록 한다(무단 접속 시도로 정상 사용자가 차단당하는 것을 방지).
# /mcp와 /api/dashboard(PWA 대시보드용 REST 엔드포인트) 둘 다 적용 대상이다 —
# 대시보드가 무인증으로 열려 있으면 /mcp 쪽을 막는 의미가 없어지기 때문.
# OAUTH_DISCOVERY_PATH는 인증 대상에서 제외한다 — discovery 메타데이터는
# 원래 공개적이어야 claude.ai 커넥터가 조회할 수 있다.
AUTH_PROTECTED_PATHS = ("/mcp", "/api/dashboard")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path == OAUTH_DISCOVERY_PATH:
            return await call_next(request)
        if not any(request.url.path.startswith(p) for p in AUTH_PROTECTED_PATHS):
            return await call_next(request)

        expected_key = os.environ.get("MCP_ACCESS_KEY")
        if not expected_key:
            return JSONResponse(
                {"error": "server_misconfigured", "message": "MCP_ACCESS_KEY가 설정되지 않았습니다."},
                status_code=500,
            )

        provided_key = request.query_params.get("key", "")
        if not provided_key or not hmac.compare_digest(provided_key, expected_key):
            return JSONResponse(
                {"error": "unauthorized", "message": "인증 실패: 올바른 ?key=가 필요합니다."},
                status_code=401,
            )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path == OAUTH_DISCOVERY_PATH:
            return await call_next(request)

        ip = _get_client_ip(request)
        now = time.time()

        with _rate_limit_lock:
            blocked_at = _blocked_until.get(ip)
            if blocked_at and now < blocked_at:
                return JSONResponse(
                    {"error": "blocked", "message": "Temporarily blocked due to rate limit violations."},
                    status_code=429,
                )

            daily = _daily_buckets.setdefault(ip, [])
            day_cutoff = now - DAY_WINDOW
            while daily and daily[0] < day_cutoff:
                daily.pop(0)
            if len(daily) >= DAILY_LIMIT:
                return JSONResponse(
                    {"error": "daily_limit_exceeded", "message": "Daily request limit exceeded."},
                    status_code=429,
                )

            minute = _minute_buckets.setdefault(ip, [])
            minute_cutoff = now - MINUTE_WINDOW
            while minute and minute[0] < minute_cutoff:
                minute.pop(0)

            if len(minute) >= MINUTE_LIMIT:
                violations = _violation_buckets.setdefault(ip, [])
                hour_cutoff = now - HOUR_WINDOW
                while violations and violations[0] < hour_cutoff:
                    violations.pop(0)
                violations.append(now)
                if len(violations) >= HOUR_VIOLATION_LIMIT:
                    _blocked_until[ip] = now + BLOCK_DURATION
                return JSONResponse(
                    {"error": "rate_limit_exceeded", "message": "Too many requests. Please try again later."},
                    status_code=429,
                )

            minute.append(now)
            daily.append(now)

        return await call_next(request)


# ---------------------------------------------------------------------------
# 지점코드 검색
# ---------------------------------------------------------------------------

AREA_CODES_PATH = Path(__file__).parent / "area_codes.json"
with open(AREA_CODES_PATH, encoding="utf-8") as f:
    AREA_CODES = json.load(f)


def _resolve_area_no(query: str) -> list[dict]:
    """지역명(시도/시군구/읍면동)으로 areaNo 후보를 찾는다. 부분일치 우선, 없으면 유사도 매칭."""
    query = query.strip()
    exact = [r for r in AREA_CODES if query in (r["sido"], r["sigungu"], r["dong"])]
    if exact:
        return exact[:5]

    partial = [
        r for r in AREA_CODES
        if query in r["sido"] or query in r["sigungu"] or query in r["dong"]
    ]
    if partial:
        return partial[:5]

    names = [f"{r['sido']} {r['sigungu']} {r['dong']}".strip() for r in AREA_CODES]
    close = difflib.get_close_matches(query, names, n=5, cutoff=0.5)
    result = []
    for c in close:
        idx = names.index(c)
        result.append(AREA_CODES[idx])
    return result


@mcp.tool()
async def search_area_code(query: str) -> dict:
    """
    지역명으로 기상청 생활기상지수 조회서비스(4.0)의 지점코드(areaNo)를 검색한다.
    get_uv_forecast / get_air_diffusion_forecast 호출 전 이 도구로 정확한
    areaNo를 먼저 확인하는 것을 권장한다.

    Args:
        query: 검색할 지역명 (예: "서울", "강남구", "청운효자동")
    """
    matches = _resolve_area_no(query)
    if not matches:
        return {
            "query": query,
            "matches": [],
            "note": "일치하는 지역을 찾지 못했습니다. 시/도, 시/군/구, 읍/면/동 단위로 다시 시도하세요.",
        }
    return {
        "query": query,
        "matches": [
            {
                "areaNo": m["code"],
                "지역명": " ".join(filter(None, [m["sido"], m["sigungu"], m["dong"]])),
            }
            for m in matches
        ],
        "source": "기상청 생활기상지수(4.0) 지점코드 매핑표 (2026-07-01 기준, 3,838건)",
    }


def _resolve_area(area_no: str | None, area_name: str | None):
    """area_no 우선, 없으면 area_name으로 자동 탐색. 반환: (area_no, note) 또는 에러 dict."""
    if area_no:
        return area_no, None
    if not area_name:
        return None, {"error": True, "message": "area_no 또는 area_name 중 하나는 반드시 입력해야 합니다."}
    matches = _resolve_area_no(area_name)
    if not matches:
        return None, {
            "error": True,
            "message": f"'{area_name}'에 해당하는 지점코드를 찾지 못했습니다. search_area_code로 먼저 검색해보세요.",
        }
    resolved_no = matches[0]["code"]
    resolved_name = " ".join(filter(None, [matches[0]["sido"], matches[0]["sigungu"], matches[0]["dong"]]))
    note = f"'{area_name}' -> areaNo={resolved_no} ({resolved_name})로 자동 매핑됨"
    return resolved_no, note


@mcp.tool()
async def get_uv_forecast(
    area_no: str | None = None,
    area_name: str | None = None,
    time: str | None = None,
) -> dict:
    """
    기상청 생활기상지수 조회서비스(4.0) - 자외선지수조회(getUVIdxV5)를 통해
    지점코드/발표시간 기준 3시간 간격, 0~75시간(h0~h75, 26개 필드) 후까지의
    자외선지수 예보값을 조회한다.

    등급 매핑(범위 기반): 위험(11↑) / 매우높음(8~10) / 높음(6~7) / 보통(3~5) / 낮음(0~2)

    Args:
        area_no: 기상청 지점코드 (10자리, 예: "1100000000"=서울). area_name 미입력 시 필수.
        area_name: 지역명으로 조회 (예: "서울", "강남구"). area_no가 없을 때 자동으로 코드를 탐색한다.
                   후보가 여러 개면 첫 번째 후보를 사용하고 그 사실을 응답에 표시한다.
        time: 발표시간 (YYYYMMDDHH 형식, 3시간 단위: 00,03,06,...,21시). 생략 시 가장 최근
              발표시각을 자동 계산해 사용한다 (이 API는 time이 실질적으로 필수임).

    Returns:
        지점별 h0~h75 자외선지수 예측값(지수, 등급)
    """
    if not os.environ.get("KMA_LIVING_WEATHER_SERVICE_KEY"):
        return {
            "error": True,
            "message": (
                "KMA_LIVING_WEATHER_SERVICE_KEY 환경변수가 설정되지 않았습니다. "
                "공공데이터포털(data.go.kr)에서 「기상청_생활기상지수 조회서비스(4.0)」 활용신청 후 "
                "발급받은 인증키를 등록하세요."
            ),
        }

    resolved_no, err_or_note = _resolve_area(area_no, area_name)
    if resolved_no is None:
        return err_or_note

    try:
        result = await fetch_uv_forecast(area_no=resolved_no, time=time)
    except LivingWeatherApiError as e:
        return {"error": True, "resultCode": e.result_code, "resultMsg": e.result_msg}

    out = {
        "items": result["items"],
        "source": (
            "기상청 기상융합서비스과, 공공데이터포털(data.go.kr) 제공 "
            "「기상청_생활기상지수 조회서비스(4.0)」 자외선지수조회(getUVIdxV5), "
            f"조회조건: areaNo={resolved_no}, time={result['time']}"
        ),
    }
    if area_no is None and area_name:
        out["area_resolution_note"] = err_or_note
    return out


@mcp.tool()
async def get_air_diffusion_forecast(
    area_no: str | None = None,
    area_name: str | None = None,
    time: str | None = None,
) -> dict:
    """
    기상청 생활기상지수 조회서비스(4.0) - 대기정체지수조회(getAirDiffusionIdxV5)를
    통해 지점코드/발표시간 기준 3시간 간격, 3~78시간(h3~h78, 26개 필드) 후까지의
    대기정체지수 예보값을 조회한다.

    등급 매핑(값 기반, 25/50/75/100 근사): 매우높음(100) / 높음(75) / 보통(50) / 낮음(25)

    Args:
        area_no: 기상청 지점코드 (10자리, 예: "1100000000"=서울). area_name 미입력 시 필수.
        area_name: 지역명으로 조회 (예: "서울", "강남구"). area_no가 없을 때 자동으로 코드를 탐색한다.
                   후보가 여러 개면 첫 번째 후보를 사용하고 그 사실을 응답에 표시한다.
        time: 발표시간 (YYYYMMDDHH 형식, 3시간 단위: 00,03,06,...,21시). 생략 시 가장 최근
              발표시각을 자동 계산해 사용한다 (이 API는 time이 실질적으로 필수임).

    Returns:
        지점별 h3~h78 대기정체지수 예측값(지수, 등급)
    """
    if not os.environ.get("KMA_LIVING_WEATHER_SERVICE_KEY"):
        return {
            "error": True,
            "message": (
                "KMA_LIVING_WEATHER_SERVICE_KEY 환경변수가 설정되지 않았습니다. "
                "공공데이터포털(data.go.kr)에서 「기상청_생활기상지수 조회서비스(4.0)」 활용신청 후 "
                "발급받은 인증키를 등록하세요."
            ),
        }

    resolved_no, err_or_note = _resolve_area(area_no, area_name)
    if resolved_no is None:
        return err_or_note

    try:
        result = await fetch_air_diffusion_forecast(area_no=resolved_no, time=time)
    except LivingWeatherApiError as e:
        return {"error": True, "resultCode": e.result_code, "resultMsg": e.result_msg}

    out = {
        "items": result["items"],
        "source": (
            "기상청 기상융합서비스과, 공공데이터포털(data.go.kr) 제공 "
            "「기상청_생활기상지수 조회서비스(4.0)」 대기정체지수조회(getAirDiffusionIdxV5), "
            f"조회조건: areaNo={resolved_no}, time={result['time']}"
        ),
    }
    if area_no is None and area_name:
        out["area_resolution_note"] = err_or_note
    return out


@mcp.tool()
async def get_uv_index(
    sido: str | None = None,
    sigungu: str | None = None,
    page_no: int = 1,
    num_of_rows: int = 300,
) -> dict:
    """행정안전부 생활안전지도(기상청 제공) 자외선지수를 시도/시군구 기준으로 조회한다.

    이 API는 기상청 생활기상지수(4.0, get_uv_forecast/get_air_diffusion_forecast)와는
    별개의 API로, areaNo 코드 체계를 쓰지 않는다(응답이 ctprvn_nm/signgu_nm
    문자열로만 옴). 서버 자체에 지역 필터 파라미터가 없어(실측 확인됨) 전국
    데이터를 조회한 뒤 sido/sigungu 값으로 클라이언트 사이드 필터링을 수행한다.

    Args:
        sido: 시도명으로 결과를 필터링한다 (예: "서울", "경기"). 부분 일치.
        sigungu: 시군구명으로 결과를 필터링한다 (예: "강남구"). 부분 일치.
        page_no: 지역 필터 없이 전체를 페이징 조회할 때 사용하는 페이지 번호 (기본 1).
        num_of_rows: 한 번에 가져올 결과 수 (기본 300, 전국 시군구 수는 약 269개).

    Returns:
        dict:
            - total_count: API가 보고한 전체 데이터 개수(페이지 기준, 필터 적용 전)
            - filtered_count: 필터 적용 후 반환된 항목 수
            - items: 각 항목은 다음 필드를 포함한다.
                - ctprvn_nm: 시도명
                - signgu_nm: 시군구명
                - emd_nm: 읍면동명 (실측 결과 항상 빈 문자열로 확인됨)
                - ulvry_index: 자외선지수 값 (정수, 실측 결과 0~9 등급 형태의 숫자로 확인됨)
                - occrrnc_dt: 발생일시 (실측 포맷: YYYYMMDDHH, 예: "2026072109")
    """
    try:
        result = await fetch_uv_index(num_of_rows=num_of_rows, page_no=page_no)
    except SafemapApiError as e:
        return {
            "error": True,
            "resultCode": e.result_code,
            "resultMsg": e.result_msg,
        }

    items = result.get("items") or []

    def matches(item):
        if sido and sido not in (item.get("ctprvn_nm") or ""):
            return False
        if sigungu and sigungu not in (item.get("signgu_nm") or ""):
            return False
        return True

    filtered = [item for item in items if matches(item)]

    normalized = [
        {
            "ctprvn_nm": item.get("ctprvn_nm"),
            "signgu_nm": item.get("signgu_nm"),
            "emd_nm": item.get("emd_nm"),
            "ulvry_index": _safe_int(item.get("ulvry_index")),
            "occrrnc_dt": item.get("occrrnc_dt"),
        }
        for item in filtered
    ]

    return {
        "total_count": _safe_int(result.get("totalCount")),
        "filtered_count": len(normalized),
        "items": normalized,
    }


# ---------------------------------------------------------------------------
# PWA 대시보드 전용 REST 엔드포인트 (MCP 도구가 아닌 일반 HTTP API)
#
# GPS 좌표 -> 카카오 리버스 지오코딩으로 행정동 이름 획득 -> area_codes.json
# 매칭으로 areaNo 확정 -> 자외선지수/대기정체지수/실측 자외선지수를 한 번에
# 모아서 반환한다. 카카오 API 키는 서버에만 있고 브라우저에는 노출되지 않는다.
# ---------------------------------------------------------------------------

async def dashboard_endpoint(request: Request) -> JSONResponse:
    try:
        lat = float(request.query_params.get("lat", ""))
        lon = float(request.query_params.get("lon", ""))
    except (TypeError, ValueError):
        return JSONResponse({"error": True, "message": "lat, lon 쿼리 파라미터가 필요합니다."}, status_code=400)

    try:
        geo = await reverse_geocode(lat, lon)
    except KakaoGeocodeApiError as e:
        return JSONResponse({"error": True, "message": e.message}, status_code=502)

    area_no, resolution_note = _resolve_area(None, geo["area_name"])
    if area_no is None:
        return JSONResponse(
            {"error": True, "message": resolution_note.get("message"), "geo": geo},
            status_code=404,
        )

    uv_result = None
    uv_error = None
    try:
        uv_result = await fetch_uv_forecast(area_no=area_no)
    except LivingWeatherApiError as e:
        uv_error = {"resultCode": e.result_code, "resultMsg": e.result_msg}

    air_result = None
    air_error = None
    try:
        air_result = await fetch_air_diffusion_forecast(area_no=area_no)
    except LivingWeatherApiError as e:
        air_error = {"resultCode": e.result_code, "resultMsg": e.result_msg}

    uv_now_result = None
    uv_now_error = None
    try:
        safemap = await fetch_uv_index(sido=geo["sido"], sigungu=geo["sigungu"], num_of_rows=300)
        items = safemap.get("items") or []
        uv_now_result = items[0] if items else None
    except SafemapApiError as e:
        uv_now_error = {"resultCode": e.result_code, "resultMsg": e.result_msg}

    return JSONResponse({
        "area": {
            "areaNo": area_no,
            "areaName": geo["area_name"],
            "sido": geo["sido"],
            "sigungu": geo["sigungu"],
            "dong": geo["dong"],
        },
        "uv_forecast": uv_result,
        "uv_forecast_error": uv_error,
        "air_diffusion_forecast": air_result,
        "air_diffusion_forecast_error": air_error,
        "uv_now": uv_now_result,
        "uv_now_error": uv_now_error,
        "generated_at": time.time(),
    })


extra_routes = [Route("/api/dashboard", dashboard_endpoint, methods=["GET"])]

app = mcp.http_app(
    stateless_http=True,
    middleware=[
        Middleware(AuthMiddleware),
        Middleware(RateLimitMiddleware),
        Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"]),
    ],
)
app.router.routes.extend(extra_routes)


async def oauth_protected_resource(request: Request) -> JSONResponse:
    """RFC 9728 OAuth Protected Resource Metadata 스텁.

    이 서버는 OAuth가 아니라 ?key= 쿼리 파라미터(MCP_ACCESS_KEY, AuthMiddleware
    참고)로 접근을 제한한다. authorization_servers를 빈 배열로 반환해 "이
    리소스에는 OAuth 인가 서버가 없다"는 것만 명시적으로 알려서, claude.ai
    커넥터 등록 시 discovery 요청이 404로 실패해 재시도를 반복하며 rate
    limit을 소진하는 것을 막는다. 이 엔드포인트 자체는 discovery 메타데이터라
    AUTH_PROTECTED_PATHS에 포함하지 않고 공개로 둔다.
    """
    return JSONResponse(
        {
            "resource": str(request.base_url).rstrip("/"),
            "authorization_servers": [],
        }
    )


app.add_route(OAUTH_DISCOVERY_PATH, oauth_protected_resource, methods=["GET"])


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
