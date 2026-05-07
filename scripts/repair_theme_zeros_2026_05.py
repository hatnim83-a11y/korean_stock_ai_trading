"""
repair_theme_zeros_2026_05.py - 5/4~5/7 박제 데이터 NULL 마킹 (옵션 2)

테마 점수 박제 회귀(2026-05-04 ~ 2026-05-07)로 themes 테이블의 selected=1 row가
momentum=0/ai_sentiment=0/news_count=0으로 저장된 케이스를 NULL로 UPDATE.

목적:
- 차트 트렌드 시각적 정직성 (NULL은 dash로 표시 가능)
- trade-improvement-analyst 분석에서 결측으로 인식 (0값 학습 방지)
- 변경 1의 폴백 로직(get_score_fallback_for_theme)이 NULL row를 박제로 감지하여 자동 복원

흐름:
1. data/trading.db 백업 (data/trading.db.backup_YYYYMMDD_HHMMSS)
2. dry-run: 영향받는 row 출력 (theme/score/momentum/ai/news 표시)
3. confirm 입력 (y) → UPDATE 실행
4. 결과 검증

실행:
    python scripts/repair_theme_zeros_2026_05.py
"""

import sys
import shutil
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import Database
from config import settings, now_kst


REPAIR_START = date(2026, 5, 4)
REPAIR_END = date(2026, 5, 7)


def main():
    db_path = Path(settings.DATABASE_PATH)
    if not db_path.exists():
        print(f"❌ DB 파일 없음: {db_path}")
        sys.exit(1)

    # 0. 서비스 중지 사전 확인 (WAL 모드 — 구동 중 백업은 변경분 누락 위험)
    print("⚠️  실행 전 반드시 서비스 중지 필요")
    print("   WAL 모드 DB — 서비스 구동 중 백업은 WAL 내 변경사항 미포함")
    print("   중지 명령: sudo systemctl stop trading_system")
    confirm = input("서비스 중지 완료? (y/N): ").strip().lower()
    if confirm != "y":
        print("❌ 취소됨")
        sys.exit(0)

    # 1. 백업 (.db + WAL/SHM 동반 복사로 안전성 확보)
    # KST 타임스탬프 (서버는 UTC라 datetime.now() 사용 시 9시간 차이 발생)
    timestamp = now_kst().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.parent / f"trading.db.backup_{timestamp}"
    print(f"\n📦 백업 생성: {backup_path}")
    shutil.copy2(db_path, backup_path)
    # WAL/SHM 동반 복사 (서비스 중지 후이지만 잔여 파일 보존)
    for suffix in ("-wal", "-shm"):
        src = db_path.parent / f"{db_path.name}{suffix}"
        if src.exists():
            shutil.copy2(src, db_path.parent / f"{backup_path.name}{suffix}")
    print(f"   ✓ 백업 완료 ({backup_path.stat().st_size / 1024:.1f} KB)\n")

    db = Database()
    db.connect()

    try:
        # 2. dry-run: 박제 row 조회
        with db.get_cursor() as cur:
            cur.execute("""
                SELECT date, theme_name, score, momentum, supply_ratio,
                       news_count, ai_sentiment, category
                FROM themes
                WHERE date BETWEEN ? AND ?
                  AND selected = 1
                  AND (momentum = 0 OR momentum IS NULL)
                  AND (ai_sentiment = 0 OR ai_sentiment IS NULL)
                  AND (news_count = 0 OR news_count IS NULL)
                ORDER BY date, theme_name
            """, (REPAIR_START, REPAIR_END))
            rows = [dict(r) for r in cur.fetchall()]

        if not rows:
            print(f"✅ 박제 데이터 없음 (5/4~5/7 selected=1 모두 정상)")
            sys.exit(0)

        print(f"🔍 영향받는 row {len(rows)}건 (selected=1 + 박제 의심):")
        print(f"{'date':<12} {'theme':<25} {'score':>7} {'mom':>6} {'ai':>6} {'news':>5}")
        print("-" * 70)
        for r in rows:
            mom = r["momentum"]
            ai = r["ai_sentiment"]
            nc = r["news_count"]
            print(
                f"{str(r['date']):<12} "
                f"{r['theme_name']:<25} "
                f"{r['score']:>7.2f} "
                f"{('NULL' if mom is None else f'{mom:.2f}'):>6} "
                f"{('NULL' if ai is None else f'{ai:.2f}'):>6} "
                f"{('NULL' if nc is None else str(nc)):>5}"
            )

        print(f"\n📝 변경 사항: {len(rows)}건 row의 momentum/ai_sentiment/news_count/supply_ratio → NULL")
        print(f"   (score/category/url/selected 컬럼은 변경 안 함)")
        print()

        # 3. confirm
        answer = input("실행하시겠습니까? (y/N): ").strip().lower()
        if answer != "y":
            print("❌ 취소됨")
            sys.exit(0)

        # 4. UPDATE 실행
        with db.get_cursor() as cur:
            cur.execute("""
                UPDATE themes
                SET momentum = NULL,
                    supply_ratio = NULL,
                    news_count = NULL,
                    ai_sentiment = NULL
                WHERE date BETWEEN ? AND ?
                  AND selected = 1
                  AND (momentum = 0 OR momentum IS NULL)
                  AND (ai_sentiment = 0 OR ai_sentiment IS NULL)
                  AND (news_count = 0 OR news_count IS NULL)
            """, (REPAIR_START, REPAIR_END))
            updated = cur.rowcount

        print(f"\n✅ {updated}개 row NULL 마킹 완료")

        # 5. 결과 검증
        with db.get_cursor() as cur:
            cur.execute("""
                SELECT date, theme_name, score, momentum, ai_sentiment, news_count
                FROM themes
                WHERE date BETWEEN ? AND ?
                  AND selected = 1
                ORDER BY date, theme_name
            """, (REPAIR_START, REPAIR_END))
            verify_rows = [dict(r) for r in cur.fetchall()]

        print(f"\n📋 결과 검증 (5/4~5/7 selected=1 {len(verify_rows)}건):")
        for r in verify_rows:
            mom = r["momentum"]
            ai = r["ai_sentiment"]
            nc = r["news_count"]
            print(
                f"   {r['date']} {r['theme_name']:<20} "
                f"momentum={'NULL' if mom is None else f'{mom:.2f}'}, "
                f"ai={'NULL' if ai is None else f'{ai:.2f}'}, "
                f"news={'NULL' if nc is None else nc}"
            )

        print(f"\n💡 다음 단계:")
        print(f"   1. sudo systemctl start trading_system (재시작 시 자동 폴백 발동)")
        print(f"   2. python scripts/verify_theme_restore_keys.py (폴백 확인)")
        print(f"   3. 다음 영업일(5/8) 08:30 KST 후 themes 테이블 selected=1 정상값 저장 확인")
        print(
            f"\n🔄 롤백: sudo systemctl stop trading_system "
            f"&& cp {backup_path} {db_path} "
            f"&& sudo systemctl start trading_system"
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()
