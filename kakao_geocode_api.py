"""
카카오맵 좌표->행정동 변환(리버스 지오코딩) API 호출 모듈.

PWA 대시보드가 GPS 좌표를 얻은 뒤, 이 모듈을 통해 행정동 이름으로 변환하고
그 이름을 기존 MCP 도구(search_area_code, get_uv_forecast 등)에 그대로
넘겨 지점코드(areaNo)를 확정한다. 카카오 REST API 키는 서버에서만 사용하고
브라우저에는 노출하지 않는다 (서버 프록시 방식).
"""

import os

import httpx

KAKAO_COORD2ADDR_URL = "https://dapi.kakao.com/v2/local/geo/coord2address.json"


class KakaoGeocodeApiError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


async def reverse_geocode(lat: float, lon: float) -> dict:
    """
    위경도(lat, lon)를 카카오 좌표->행정동 API로 변환한다.

    Returns:
        dict: {"sido": str, "sigungu": str, "dong": str, "area_name": str}
        area_name은 기존 MCP 도구의 area_name 파라미터에 그대로 넘길 수 있는
        "시군구 동" 형태의 문자열이다 (예: "강남구 역삼동").
    """
    api_key = os.environ.get("KAKAO_REST_API_KEY", "")
    if not api_key:
        raise KakaoGeocodeApiError(
            "KAKAO_REST_API_KEY 환경변수가 설정되지 않았습니다. "
            "카카오 개발자 콘솔(developers.kakao.com)에서 REST API 키를 발급받아 등록하세요."
        )

    headers = {"Authorization": f"KakaoAK {api_key}"}
    params = {"x": lon, "y": lat, "input_coord": "WGS84"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(KAKAO_COORD2ADDR_URL, headers=headers, params=params)

    if resp.status_code != 200:
        raise KakaoGeocodeApiError(f"카카오 지오코딩 API 오류: HTTP {resp.status_code} {resp.text}")

    data = resp.json()
    documents = data.get("documents") or []
    if not documents:
        raise KakaoGeocodeApiError("해당 좌표에 대한 주소를 찾지 못했습니다.")

    addr = documents[0].get("address") or {}
    sido = addr.get("region_1depth_name", "")
    sigungu = addr.get("region_2depth_name", "")
    dong = addr.get("region_3depth_name", "")

    area_name = " ".join(filter(None, [sigungu, dong])) or sido

    return {
        "sido": sido,
        "sigungu": sigungu,
        "dong": dong,
        "area_name": area_name,
    }
