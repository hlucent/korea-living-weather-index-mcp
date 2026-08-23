"""
기상청 생활기상지수 조회서비스(4.0) LivingWthrIdxServiceV5 API 호출 모듈.

- fetch_uv_forecast: getUVIdxV5 (자외선지수, h0~h75, 26개 필드)
- fetch_air_diffusion_forecast: getAirDiffusionIdxV5 (대기정체지수, h3~h78, 26개 필드)

행정안전부 생활안전지도(IF_0113) 실측 자외선지수 API는 별도 모듈
`safemap_api.py`에 있다 (areaNo 코드 체계를 쓰지 않는 별개 API이므로 분리).
"""

import os
import re
import json
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

BASE_URL = "http://apis.data.go.kr/1360000/LivingWthrIdxServiceV5"

# 자외선지수: 0~75시간, 3시간 간격 (26개 필드)
UV_FORECAST_HOURS = [f"h{h}" for h in range(0, 76, 3)]
# 대기정체지수: 3~78시간, 3시간 간격 (26개 필드)
AIR_DIFFUSION_FORECAST_HOURS = [f"h{h}" for h in range(3, 79, 3)]

# 응답 예제 XML에 h78이 등장하는 사례가 있어(자외선지수 표에는 미정의), 실제로
# 오면 안전하게 파싱하도록 폴백 필드로 포함해둔다.
UV_FORECAST_HOURS_WITH_FALLBACK = UV_FORECAST_HOURS + ["h78"]

ERROR_CODES = {
    "00": "NORMAL_SERVICE",
    "01": "APPLICATION_ERROR",
    "02": "DB_ERROR",
    "03": "NODATA_ERROR",
    "04": "HTTP_ERROR",
    "05": "SERVICETIME_OUT",
    "10": "INVALID_REQUEST_PARAMETER_ERROR",
    "11": "NO_MANDATORY_REQUEST_PARAMETERS_ERROR",
    "12": "NO_OPENAPI_SERVICE_ERROR",
    "20": "SERVICE_ACCESS_DENIED_ERROR",
    "21": "TEMPORARILY_DISABLE_THE_SERVICEKEY_ERROR",
    "22": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
    "30": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
    "31": "DEADLINE_HAS_EXPIRED_ERROR",
    "32": "UNREGISTERED_IP_ERROR",
    "33": "UNSIGNED_CALL_ERROR",
    "99": "UNKNOWN_ERROR",
}


class LivingWeatherApiError(Exception):
    def __init__(self, result_code: Any, result_msg: str):
        self.result_code = result_code
        self.result_msg = result_msg
        super().__init__(f"LivingWthrIdxServiceV5 API error: resultCode={result_code}, resultMsg={result_msg}")


def uv_grade(value: float) -> str:
    """자외선지수 등급 (범위 기반)."""
    if value >= 11:
        return "위험"
    if value >= 8:
        return "매우높음"
    if value >= 6:
        return "높음"
    if value >= 3:
        return "보통"
    return "낮음"  # 0~2


def air_diffusion_grade(value: float) -> str:
    """대기정체지수 등급 (값 기반, 25/50/75/100 근사 매핑).

    실측 결과 정확히 25/50/75/100 중 하나로만 오는지 확인이 필요하며,
    확인 전까지는 근사 매핑 로직을 유지한다 (DEVLOG.md 참고).
    """
    if value >= 87.5:
        return "매우높음"  # 100 근사
    if value >= 62.5:
        return "높음"  # 75 근사
    if value >= 37.5:
        return "보통"  # 50 근사
    return "낮음"  # 25 근사


def _safe_float(v):
    if v is None or v in ("", "-"):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _parse_xml_fallback(text: str) -> dict:
    """JSON 파싱 실패 시(에러 응답이 XML로 오는 경우 등) resultCode/resultMsg를 정규식으로 추출."""
    def find(tag):
        m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else None

    return {
        "resultCode": find("resultCode") or "99",
        "resultMsg": find("resultMsg") or "UNKNOWN_ERROR",
    }


def _latest_forecast_time() -> str:
    """
    가장 최근에 발표됐을 3시간 단위 발표시각(YYYYMMDDHH, KST)을 계산한다.
    이 API는 time 파라미터가 필수이며(생략 시 ERROR-11), 발표 후 데이터 반영
    지연을 감안해 안전하게 최근 발표시각보다 한 슬롯 전을 사용한다.
    """
    now = datetime.now(timezone(timedelta(hours=9)))  # KST
    slot_hour = (now.hour // 3) * 3
    latest = now.replace(hour=slot_hour, minute=0, second=0, microsecond=0)
    safe = latest - timedelta(hours=3)
    return safe.strftime("%Y%m%d%H")


async def _call_api(operation: str, area_no: str, time: str | None, num_of_rows: int, page_no: int) -> dict:
    if not time:
        time = _latest_forecast_time()

    service_key = os.environ.get("KMA_LIVING_WEATHER_SERVICE_KEY", "")

    params = {
        "serviceKey": service_key,
        "areaNo": area_no,
        "time": time,
        "dataType": "JSON",
        "numOfRows": num_of_rows,
        "pageNo": page_no,
    }

    url = f"{BASE_URL}/{operation}"

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        text = resp.text

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        parsed = _parse_xml_fallback(text)
        raise LivingWeatherApiError(parsed["resultCode"], parsed["resultMsg"])

    try:
        header = data["response"]["header"]
    except (KeyError, TypeError):
        raise LivingWeatherApiError(None, f"응답 파싱 실패: {data}")

    result_code = header.get("resultCode")
    if result_code is not None and str(result_code) not in ("0", "00"):
        raise LivingWeatherApiError(result_code, header.get("resultMsg", ""))

    try:
        items = data["response"]["body"]["items"]["item"]
    except (KeyError, TypeError):
        items = []
    if isinstance(items, dict):
        items = [items]

    return {
        "resultCode": result_code,
        "resultMsg": header.get("resultMsg"),
        "items": items,
        "time": time,
    }


async def fetch_uv_forecast(area_no: str, time: str | None = None, num_of_rows: int = 10, page_no: int = 1) -> dict:
    """getUVIdxV5 호출: 자외선지수 예보(h0~h75, 3시간 간격)."""
    result = await _call_api("getUVIdxV5", area_no, time, num_of_rows, page_no)

    normalized_items = []
    for item in result["items"]:
        forecast = {}
        for h in UV_FORECAST_HOURS_WITH_FALLBACK:
            if h not in item:
                continue
            v = item.get(h)
            fv = _safe_float(v)
            if fv is None:
                continue
            forecast[h] = {"지수": fv, "등급": uv_grade(fv)}
        normalized_items.append({
            "areaNo": item.get("areaNo"),
            "발표시간": item.get("date"),
            "지수코드": item.get("code"),
            "예측값": forecast,
        })

    return {
        "resultCode": result["resultCode"],
        "resultMsg": result["resultMsg"],
        "time": result["time"],
        "items": normalized_items,
    }


async def fetch_air_diffusion_forecast(area_no: str, time: str | None = None, num_of_rows: int = 10, page_no: int = 1) -> dict:
    """getAirDiffusionIdxV5 호출: 대기정체지수 예보(h3~h78, 3시간 간격)."""
    result = await _call_api("getAirDiffusionIdxV5", area_no, time, num_of_rows, page_no)

    normalized_items = []
    for item in result["items"]:
        forecast = {}
        for h in AIR_DIFFUSION_FORECAST_HOURS:
            if h not in item:
                continue
            v = item.get(h)
            fv = _safe_float(v)
            if fv is None:
                continue
            forecast[h] = {"지수": fv, "등급": air_diffusion_grade(fv)}
        normalized_items.append({
            "areaNo": item.get("areaNo"),
            "발표시간": item.get("date"),
            "지수코드": item.get("code"),
            "예측값": forecast,
        })

    return {
        "resultCode": result["resultCode"],
        "resultMsg": result["resultMsg"],
        "time": result["time"],
        "items": normalized_items,
    }
