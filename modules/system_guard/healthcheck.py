"""Off-VM dead-man's-switch — 외부 헬스체크 ping.

2026-06-05 incident 대응(P0-A): GCP 호스트 장애로 VM이 freeze/네트워크 단절되면
봇은 텔레그램조차 못 보내 운영자가 무감지였다. 봇이 외부 서비스(healthchecks.io 등)에
주기적으로 ping을 보내고, **무신호 시 외부에서** 이메일/푸시로 경보하게 한다.

설계 원칙(리뷰 반영):
- **트레이딩 무간섭**: 어떤 예외도 raise하지 않는다. 실패는 bool False로만 보고.
- **이벤트루프 비블로킹**: AsyncIOScheduler가 async 잡을 await하므로 httpx.AsyncClient(async) 사용.
  (동기 httpx.post는 09:05 매수 잡과 동시 발화 시 루프를 막아 매수 지연을 유발할 수 있음)
"""

import logging

import httpx

logger = logging.getLogger(__name__)


async def send_healthcheck_ping(url: str, timeout: int = 5) -> bool:
    """외부 헬스체크 URL에 GET ping을 보낸다.

    Args:
        url: ping 대상 URL (예: https://hc-ping.com/<uuid>). 공백이면 즉시 False.
        timeout: HTTP 타임아웃(초).

    Returns:
        2xx 응답 수신 시 True, 그 외(네트워크 실패/타임아웃/비정상 응답/잘못된 URL)면 False.
        **절대 예외를 전파하지 않는다.**
    """
    if not url or not url.strip():
        return False

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url.strip())
        if resp.status_code // 100 == 2:
            return True
        logger.warning(f"healthcheck ping 비정상 응답: HTTP {resp.status_code}")
        return False
    except Exception as e:
        # 네트워크 단절/타임아웃/InvalidURL 등 — 트레이딩에 영향 주지 않도록 전부 흡수
        logger.warning(f"healthcheck ping 실패: {e}")
        return False
