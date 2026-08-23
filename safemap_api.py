"""
행정안전부 생활안전지도(safemap.go.kr) IF_0113 자외선지수 API 호출 모듈.

safemap-uv-index-mcp 프로젝트에서 이식. 기상청 생활기상지수(4.0)와는 별개의
API로, areaNo 코드 체계를 쓰지 않고 시도명/시군구명(문자열) 기반으로만
조회한다. 서버 자체에 지역 필터 파라미터가 없어(실측 확인됨) 전국 데이터를
가져온 뒤 클라이언트 사이드에서 필터링한다.
"""

import os
import re
import json
from typing import Any

import httpx

API_URL = "http://safemap.go.kr/openapi2/IF_0113"


class SafemapApiError(Exception):
    def __init__(self, result_code: Any, result_msg: str):
        self.result_code = result_code
        self.result_msg = result_msg
        super().__init__(f"safemap API error: resultCode={result_code}, resultMsg={result_msg}")


def _safe_int(v):
    if v is None or v == "":
        return 0
    return int(float(v))


def _parse_xml_fallback(text: str) -> dict:
    """JSON 파싱 실패 시 XML에서 resultCode/resultMsg 등을 정규식으로 추출."""
    def find(tag_options):
        for tag in tag_options:
            m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.IGNORECASE | re.DOTALL)
            if m:
                return m.group(1).strip()
        return None

    result_code = find(["resultCode", "CODE"])
    result_msg = find(["resultMsg", "MESSAGE"])
    total_count = find(["totalCount"])

    items = []
    for item_match in re.finditer(r"<item>(.*?)</item>", text, re.IGNORECASE | re.DOTALL):
        block = item_match.group(1)
        item = {}
        for field in ["lvlh_wether_se_id", "ctprvn_nm", "signgu_nm", "emd_nm", "ulvry_index", "occrrnc_dt"]:
            fm = re.search(rf"<{field}>(.*?)</{field}>", block, re.IGNORECASE | re.DOTALL)
            item[field] = fm.group(1).strip() if fm else None
        items.append(item)

    return {
        "resultCode": result_code,
        "resultMsg": result_msg,
        "totalCount": total_count,
        "items": items,
    }


def _normalize_json(data: dict) -> dict:
    """safemap JSON 응답 구조를 공통 dict 형태로 정규화한다."""
    if isinstance(data, dict) and "response" in data:
        data = data["response"]

    result_code = data.get("resultCode")
    result_msg = data.get("resultMsg")
    total_count = data.get("totalCount")

    items = data.get("items")
    if items is None:
        body = data.get("body", {})
        items = body.get("items", [])
        if total_count is None:
            total_count = body.get("totalCount")

    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    if items is None:
        items = []

    return {
        "resultCode": result_code,
        "resultMsg": result_msg,
        "totalCount": total_count,
        "items": items,
    }


async def fetch_uv_index(num_of_rows: int = 100, page_no: int = 1) -> dict:
    """safemap.go.kr IF_0113 자외선지수 API를 호출해 파싱된 결과를 반환한다."""
    api_key = os.environ.get("SAFEMAP_API_KEY", "")

    params = {
        "serviceKey": api_key,
        "numOfRows": num_of_rows,
        "pageNo": page_no,
        "returnType": "json",
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resp = await client.get(API_URL, params=params)
        resp.raise_for_status()
        text = resp.text

    try:
        data = json.loads(text)
        parsed = _normalize_json(data)
    except (json.JSONDecodeError, ValueError):
        parsed = _parse_xml_fallback(text)

    result_code = parsed.get("resultCode")
    if result_code is not None and str(result_code) not in ("0", "00", "success", "SUCCESS"):
        raise SafemapApiError(result_code, parsed.get("resultMsg", ""))

    return parsed
