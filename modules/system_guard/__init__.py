"""system_guard — 시스템 가용성/복원력 보조 모듈 (2026-06-05 incident 대응).

- healthcheck: off-VM dead-man's-switch ping (봇/VM 다운을 외부에서 감지)
"""

from .healthcheck import send_healthcheck_ping

__all__ = ["send_healthcheck_ping"]
