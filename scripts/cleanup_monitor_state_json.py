"""
cleanup_monitor_state_json.py - data/monitor_state.json 잔재 키 1회용 정화

2026-05-12 한화오션(042660) BE 손절 오발동 사건 대응.
이전 매도 사이클의 highest_price/max_profit_rate 가 JSON 에 잔존해
재매수 시 BE 손절가가 즉시 활성화되는 버그를 차단한다.

동작:
1. trading_system 서비스가 실행 중이면 즉시 중단 (충돌 방지)
2. 현재 monitor_state.json 백업 (KST 타임스탬프)
3. DB portfolio 테이블에서 status='holding' 종목 코드 셋 로드
4. JSON 에서 보유 외 키(잔재) 제거 후 덮어쓰기
5. 전후 키 개수 + 제거된 키 목록 콘솔 출력

실행 순서 (배포 시):
    sudo systemctl stop trading_system
    source venv/bin/activate
    python scripts/cleanup_monitor_state_json.py
    # 코드 패치 반영 후
    sudo systemctl start trading_system
"""

import json
import shutil
import subprocess
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings, now_kst


def _service_is_active(service_name: str = "trading_system") -> bool:
    """systemd 서비스 활성 여부 확인."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service_name],
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        # systemctl 미설치 환경 (개발 PC 등) — 가드 우회
        return False


def _load_holding_codes(db_path: Path) -> set:
    """portfolio.status='holding' 종목 코드 셋 로드."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT stock_code FROM portfolio WHERE status='holding'")
        return {row["stock_code"] for row in cursor.fetchall()}
    finally:
        conn.close()


def main() -> int:
    # 1) 서비스 가동 가드
    if _service_is_active("trading_system"):
        print(
            "❌ trading_system 서비스가 실행 중입니다.\n"
            "   먼저 `sudo systemctl stop trading_system` 후 재실행하세요.",
            file=sys.stderr,
        )
        return 1

    json_path = Path(settings.DATABASE_PATH).parent / "monitor_state.json"
    db_path = Path(settings.DATABASE_PATH)

    if not json_path.exists():
        print(f"✓ {json_path} 파일이 없습니다. 정화할 잔재 없음.")
        return 0

    # 2) 백업 (KST 타임스탬프)
    timestamp = now_kst().strftime("%Y%m%d_%H%M%S")
    backup_path = json_path.with_suffix(f".json.bak_{timestamp}")
    shutil.copy2(str(json_path), str(backup_path))
    print(f"📦 백업 생성: {backup_path}")

    # 3) 현재 JSON 로드
    with open(json_path) as f:
        state = json.load(f)
    before_keys = set(state.keys())
    print(f"📂 정화 전 키 개수: {len(before_keys)}개")
    print(f"   키 목록: {sorted(before_keys)}")

    # 4) holding 종목 셋 로드
    holding_codes = _load_holding_codes(db_path)
    print(f"💼 현재 holding 종목: {len(holding_codes)}개 → {sorted(holding_codes)}")

    # 5) 잔재 키 제거
    cleaned = {code: data for code, data in state.items() if code in holding_codes}
    removed_keys = before_keys - set(cleaned.keys())

    if not removed_keys:
        print("✓ 잔재 키 없음. JSON 변경 불필요.")
        # 백업은 유지 (안전)
        return 0

    with open(json_path, "w") as f:
        json.dump(cleaned, f, ensure_ascii=False)

    print(f"🚮 제거된 잔재 키: {len(removed_keys)}개 → {sorted(removed_keys)}")
    print(f"✓ 정화 완료: {len(before_keys)}개 → {len(cleaned)}개")
    print(f"   롤백 필요 시: cp {backup_path} {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
