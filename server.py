"""korea-living-weather-index-mcp: 기상청 생활기상지수(4.0) MCP 서버.

- get_uv_forecast: 자외선지수 예보(getUVIdxV5, h0~h75)
- get_air_diffusion_forecast: 대기정체지수 예보(getAirDiffusionIdxV5, h3~h78)
- search_area_code: 지역명으로 areaNo 검색
"""

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
from starlette.requests import Request
from starlette.responses import JSONResponse

from kma_living_weather_api import (
    fetch_uv_forecast,
    fetch_air_diffusion_forecast,
    LivingWeatherApiError,
)

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


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
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


app = mcp.http_app(stateless_http=True, middleware=[Middleware(RateLimitMiddleware)])


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
