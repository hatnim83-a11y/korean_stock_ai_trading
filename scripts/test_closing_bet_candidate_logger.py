"""scripts/test_closing_bet_candidate_logger.py

Phase 1-6: ``CandidateLogger`` 단위 테스트 (메모리 SQLite + 임시 파일 DB).

라이프사이클: log_recommended → log_features → mark_entered → log_exit → log_labels.
조회 헬퍼 + 1-9 검증용 카운트.

실행:
    venv/bin/python scripts/test_closing_bet_candidate_logger.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from closing_bet_system.engines.cost_slippage_engine import CostSlippageEngine
from closing_bet_system.engines.signal_score_engine import SignalScoreEngine
from closing_bet_system.storage.candidate_logger import (
    ALLOWED_STATUSES,
    CANDIDATE_STATUS_ENTERED,
    CANDIDATE_STATUS_RECOMMENDED,
    CANDIDATE_STATUS_REJECTED_FILTER,
    CANDIDATE_STATUS_REJECTED_MANUAL,
    CandidateLogger,
    LAYER1_KEYS,
    LAYER2_KEYS,
    _direction_match,
    _safe_float,
    _to_date_str,
)
from closing_bet_system.storage.db import ClosingBetDatabase


# ===== Fixtures =====


def _make_logger() -> tuple[CandidateLogger, ClosingBetDatabase, Path]:
    """임시 SQLite DB + Logger 생성. 호출 측에서 db.close() + tmp 삭제 책임."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="closing_bet_test_"))
    db_path = tmp_dir / "test.db"
    db = ClosingBetDatabase(db_path)
    db.connect()
    db.init_tables()
    cost = CostSlippageEngine(settings={"cost": {
        "buy_commission": 0.00015, "sell_commission": 0.00015,
        "transaction_tax": 0.0018, "estimated_slippage": 0.001,
        "safety_margin": 0.005,
    }})
    return CandidateLogger(db=db, cost_engine=cost), db, tmp_dir


def _cleanup(db: ClosingBetDatabase, tmp_dir: Path):
    db.close()
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)


def _full_score() -> object:
    """SignalScoreEngine 만점 ScoreBreakdown 생성."""
    eng = SignalScoreEngine(layer1_weight=1.0)
    bd = eng.score(
        "005930",
        layer1={"inst_net_buy_estimated": 1e9, "foreign_net_buy_3d": 5e9,
                "program_net_buy_change": 0.05, "closing_flow_concentration": 0.6},
        layer2={"close_strength": 0.95, "volume_surprise": 3.0, "above_ma20": True,
                "closing_buy_sell_ratio": 1.5, "atr_overheat": 1.0},
        layer3={"near_52w_high": True, "theme_leadership_rank": 1,
                "has_positive_disclosure": True},
        market_ok=True,
    )
    return bd


# ===== 테스트 =====


def test_log_recommended_basic():
    """log_recommended → INSERT, candidate_id 정수 반환."""
    log, db, tmp = _make_logger()
    try:
        bd = _full_score()
        cid = log.log_recommended(date(2026, 5, 4), "005930", "삼성전자", score_breakdown=bd)
        assert isinstance(cid, int) and cid > 0
        row = log.get_candidate(cid)
        assert row["ticker"] == "005930"
        assert row["candidate_status"] == CANDIDATE_STATUS_RECOMMENDED
        assert row["layer1_score"] == 4
        assert row["layer2_score"] == 4
        assert row["layer3_score"] == 3
        assert row["total_score"] == 11
        print(f"  [PASS] log_recommended cid={cid}, scores 11/4/4/3")
    finally:
        _cleanup(db, tmp)


def test_log_recommended_with_dict_score():
    """ScoreBreakdown 대신 dict 도 허용 (duck-typing)."""
    log, db, tmp = _make_logger()
    try:
        cid = log.log_recommended(
            "2026-05-04", "000660", "SK하이닉스",
            score_breakdown={"layer1_subscore": 2, "layer2_subscore": 3,
                             "layer3_subscore": 1, "raw_total": 6},
        )
        row = log.get_candidate(cid)
        assert row["total_score"] == 6
        print(f"  [PASS] dict score breakdown 처리 (total=6)")
    finally:
        _cleanup(db, tmp)


def test_log_recommended_no_score():
    """score_breakdown=None → 점수 컬럼 모두 NULL."""
    log, db, tmp = _make_logger()
    try:
        cid = log.log_recommended(date(2026, 5, 4), "005930", "삼성전자")
        row = log.get_candidate(cid)
        assert row["layer1_score"] is None
        assert row["total_score"] is None
        print(f"  [PASS] score_breakdown=None → 점수 NULL")
    finally:
        _cleanup(db, tmp)


def test_log_features_full():
    """log_features 18컬럼 INSERT."""
    log, db, tmp = _make_logger()
    try:
        cid = log.log_recommended(date(2026, 5, 4), "005930", "삼성전자")
        log.log_features(
            cid,
            layer1={k: 1.0 for k in LAYER1_KEYS},
            layer2={k: 0.5 for k in LAYER2_KEYS},
            layer3={"days_from_52w_high": 3, "relative_strength_5d": 0.05,
                    "theme_leadership_rank": 2},
            market_regime={"kospi_above_200ma": True, "vkospi": 18.5,
                           "foreign_5d_cumulative": 1e10, "us_futures_change": -0.005,
                           "usd_krw_change": 0.002},
        )
        with db.get_cursor() as cur:
            cur.execute("SELECT * FROM candidate_features WHERE candidate_id=?", (cid,))
            row = dict(cur.fetchone())
        assert row["inst_net_buy_estimated"] == 1.0
        assert row["close_strength"] == 0.5
        assert row["days_from_52w_high"] == 3
        assert row["theme_leadership_rank"] == 2
        assert row["kospi_above_200ma"] == 1   # SQLite BOOLEAN = INTEGER
        assert abs(row["vkospi"] - 18.5) < 1e-9
        print(f"  [PASS] log_features 18컬럼 저장 + bool→int 변환")
    finally:
        _cleanup(db, tmp)


def test_log_features_partial():
    """일부 키 누락 → NULL 저장 (에러 X)."""
    log, db, tmp = _make_logger()
    try:
        cid = log.log_recommended(date(2026, 5, 4), "005930", "삼성전자")
        log.log_features(cid, layer1={"inst_net_buy_estimated": 1e9})  # 1키만
        with db.get_cursor() as cur:
            cur.execute("SELECT * FROM candidate_features WHERE candidate_id=?", (cid,))
            row = dict(cur.fetchone())
        assert row["inst_net_buy_estimated"] == 1e9
        assert row["foreign_net_buy_3d"] is None
        assert row["close_strength"] is None
        print(f"  [PASS] 부분 입력 → 누락 키 NULL")
    finally:
        _cleanup(db, tmp)


def test_log_features_int_coercion():
    """days_from_52w_high / theme_leadership_rank float → int 강제."""
    log, db, tmp = _make_logger()
    try:
        cid = log.log_recommended(date(2026, 5, 4), "005930", "삼성전자")
        log.log_features(cid, layer3={"days_from_52w_high": 3.7,   # float → int(3)
                                       "theme_leadership_rank": 2.0,
                                       "relative_strength_5d": 0.1})
        with db.get_cursor() as cur:
            cur.execute("SELECT days_from_52w_high, theme_leadership_rank FROM candidate_features WHERE candidate_id=?", (cid,))
            row = dict(cur.fetchone())
        assert row["days_from_52w_high"] == 3
        assert row["theme_leadership_rank"] == 2
        print(f"  [PASS] int 컬럼 float 강제 변환")
    finally:
        _cleanup(db, tmp)


def test_mark_rejected_by_filter():
    """rejected_filter 상태 전이."""
    log, db, tmp = _make_logger()
    try:
        cid = log.log_recommended(date(2026, 5, 4), "005930", "삼성전자")
        log.mark_rejected_by_filter(cid, "DART 즉시제외: 유상증자")
        row = log.get_candidate(cid)
        assert row["candidate_status"] == CANDIDATE_STATUS_REJECTED_FILTER
        assert "DART" in row["rejection_reason"]
        print(f"  [PASS] mark_rejected_by_filter → status={row['candidate_status']}")
    finally:
        _cleanup(db, tmp)


def test_mark_rejected_manual():
    """rejected_manual 상태 전이."""
    log, db, tmp = _make_logger()
    try:
        cid = log.log_recommended(date(2026, 5, 4), "005930", "삼성전자")
        log.mark_rejected_manual(cid, "사용자 거부 (텔레그램)")
        row = log.get_candidate(cid)
        assert row["candidate_status"] == CANDIDATE_STATUS_REJECTED_MANUAL
        print(f"  [PASS] mark_rejected_manual → status={row['candidate_status']}")
    finally:
        _cleanup(db, tmp)


def test_lifecycle_recommended_to_exit():
    """전체 라이프사이클: recommended → entered → exit + cost 분해."""
    log, db, tmp = _make_logger()
    try:
        cid = log.log_recommended(date(2026, 5, 4), "005930", "삼성전자",
                                   score_breakdown=_full_score())
        log.log_features(cid,
                         layer2={"close_strength": 0.9, "volume_surprise": 3.0,
                                 "atr_overheat": 1.0})

        # 진입
        entry_time = datetime(2026, 5, 4, 15, 18, tzinfo=None)
        log.mark_entered(cid, entry_price=70_000, entry_amount=70_000_000, entry_time=entry_time)
        row1 = log.get_candidate(cid)
        assert row1["candidate_status"] == CANDIDATE_STATUS_ENTERED
        assert row1["entry_price"] == 70_000

        # 매도 (익일 +1.5%)
        exit_time = datetime(2026, 5, 5, 9, 30, tzinfo=None)
        result = log.log_exit(cid, exit_price=71_050, exit_time=exit_time, shares=1000)
        row2 = log.get_candidate(cid)
        assert row2["exit_price"] == 71_050
        assert row2["net_pnl_pct"] is not None
        # 수익이라 net_pnl_pct > 0 (편도 슬리피지 0.1% 차감 후)
        assert row2["net_pnl_pct"] > 0
        # CostBreakdown 일관성
        assert result.cost_breakdown.shares == 1000
        assert result.cost_breakdown.net_pnl_pct == row2["net_pnl_pct"]
        print(f"  [PASS] 라이프사이클 entry→exit (net_pnl={result.cost_breakdown.net_pnl_pct*100:+.3f}%)")
    finally:
        _cleanup(db, tmp)


def test_log_exit_without_entry():
    """entry_price 없이 log_exit → LookupError."""
    log, db, tmp = _make_logger()
    try:
        cid = log.log_recommended(date(2026, 5, 4), "005930", "삼성전자")
        try:
            log.log_exit(cid, exit_price=70_000)
            raise AssertionError("entry 없이 exit 통과됨")
        except LookupError as e:
            assert "entry_price" in str(e)
        print(f"  [PASS] entry 없이 exit → LookupError")
    finally:
        _cleanup(db, tmp)


def test_log_exit_unknown_candidate():
    """존재하지 않는 candidate_id → LookupError."""
    log, db, tmp = _make_logger()
    try:
        try:
            log.log_exit(99999, exit_price=70_000)
            raise AssertionError("unknown cid 통과됨")
        except LookupError:
            pass
        print(f"  [PASS] unknown cid → LookupError")
    finally:
        _cleanup(db, tmp)


def test_log_labels_full():
    """T+1 라벨 4개 + 가격 변화율 3개 INSERT."""
    log, db, tmp = _make_logger()
    try:
        cid = log.log_recommended(date(2026, 5, 4), "005930", "삼성전자")
        log.log_labels(
            cid,
            next_open_pct=0.008, next_morning_high_pct=0.015, next_morning_low_pct=-0.003,
            label_gap_up=True, label_morning_exit=True, label_stop_risk=False,
            label_net_ev_positive=True,
        )
        with db.get_cursor() as cur:
            cur.execute("SELECT * FROM candidate_labels WHERE candidate_id=?", (cid,))
            row = dict(cur.fetchone())
        assert row["label_gap_up"] == 1
        assert row["label_morning_exit"] == 1
        assert row["label_stop_risk"] == 0
        assert abs(row["next_open_pct"] - 0.008) < 1e-9
        # INSERT OR REPLACE — 두 번 호출 OK
        log.log_labels(cid, label_gap_up=False, label_morning_exit=False)
        with db.get_cursor() as cur:
            cur.execute("SELECT label_gap_up FROM candidate_labels WHERE candidate_id=?", (cid,))
            row2 = dict(cur.fetchone())
        assert row2["label_gap_up"] == 0   # 갱신 확인
        print(f"  [PASS] log_labels 8필드 + REPLACE 갱신")
    finally:
        _cleanup(db, tmp)


def test_log_flow_reliability():
    """flow_data_reliability INSERT + 방향 일치 자동 계산."""
    log, db, tmp = _make_logger()
    try:
        # 부호 일치 (양/양) → True
        log.log_flow_reliability(date(2026, 5, 4), "005930",
                                  inst_estimated=1e9, inst_confirmed=1.2e9,
                                  foreign_estimated=-5e8, foreign_confirmed=2e8)  # 부호 다름
        with db.get_cursor() as cur:
            cur.execute("SELECT * FROM flow_data_reliability WHERE ticker='005930'")
            row = dict(cur.fetchone())
        assert row["inst_direction_match"] == 1
        assert row["foreign_direction_match"] == 0
        # 동일 (date, ticker) 재호출 → REPLACE
        log.log_flow_reliability(date(2026, 5, 4), "005930",
                                  inst_estimated=1e9, inst_confirmed=-1.2e9)   # 부호 다름
        with db.get_cursor() as cur:
            cur.execute("SELECT inst_direction_match FROM flow_data_reliability WHERE ticker='005930'")
            row2 = dict(cur.fetchone())
        assert row2["inst_direction_match"] == 0
        print(f"  [PASS] flow_reliability 방향 일치 + REPLACE")
    finally:
        _cleanup(db, tmp)


def test_query_get_candidates_in_period():
    """기간 + 상태별 조회."""
    log, db, tmp = _make_logger()
    try:
        for i in range(5):
            cid = log.log_recommended(date(2026, 5, 1) + timedelta(days=i),
                                       f"00593{i}", f"종목{i}")
            if i % 2 == 0:
                log.mark_rejected_by_filter(cid, "테스트")
        rows = log.get_candidates_in_period(date(2026, 5, 1), date(2026, 5, 10))
        assert len(rows) == 5
        rejected = log.get_candidates_in_period(date(2026, 5, 1), date(2026, 5, 10),
                                                  status=CANDIDATE_STATUS_REJECTED_FILTER)
        assert len(rejected) == 3
        recommended = log.get_candidates_in_period(date(2026, 5, 1), date(2026, 5, 10),
                                                     status=CANDIDATE_STATUS_RECOMMENDED)
        assert len(recommended) == 2
        print(f"  [PASS] 기간/상태 조회 (5건 / 거부 3건 / 추천 2건)")
    finally:
        _cleanup(db, tmp)


def test_get_distinct_stocks_count():
    """1-9 검증: 동일 종목 여러 status 도 1개로 카운트."""
    log, db, tmp = _make_logger()
    try:
        # 005930: 2회 (recommended + rejected)
        cid1 = log.log_recommended(date(2026, 5, 1), "005930", "삼성전자")
        log.mark_rejected_by_filter(cid1, "테스트")
        log.log_recommended(date(2026, 5, 2), "005930", "삼성전자")
        # 000660: 1회
        log.log_recommended(date(2026, 5, 1), "000660", "SK하이닉스")
        # 035420: 1회
        log.log_recommended(date(2026, 5, 1), "035420", "NAVER")

        c = log.get_distinct_stocks_count(date(2026, 5, 1), date(2026, 5, 10))
        assert c == 3, f"distinct={c}"
        c_rec = log.get_distinct_stocks_count(date(2026, 5, 1), date(2026, 5, 10),
                                                status=CANDIDATE_STATUS_RECOMMENDED)
        assert c_rec == 3
        print(f"  [PASS] distinct stocks={c} (recommended only={c_rec})")
    finally:
        _cleanup(db, tmp)


def test_count_by_status():
    """status 분포 dict."""
    log, db, tmp = _make_logger()
    try:
        for i in range(3):
            cid = log.log_recommended(date(2026, 5, 1), f"00593{i}", f"종목{i}")
            if i == 0:
                log.mark_rejected_by_filter(cid, "필터")
            elif i == 1:
                log.mark_rejected_manual(cid, "수동")
            else:
                log.mark_entered(cid, 70000, 70_000_000)

        counts = log.count_by_status(date(2026, 5, 1), date(2026, 5, 10))
        assert counts[CANDIDATE_STATUS_REJECTED_FILTER] == 1
        assert counts[CANDIDATE_STATUS_REJECTED_MANUAL] == 1
        assert counts[CANDIDATE_STATUS_ENTERED] == 1
        assert counts[CANDIDATE_STATUS_RECOMMENDED] == 0
        # 모든 status 키 존재 (없는 것도 0)
        assert set(counts.keys()) == set(ALLOWED_STATUSES)
        print(f"  [PASS] count_by_status {counts}")
    finally:
        _cleanup(db, tmp)


def test_invalid_inputs():
    """잘못된 ticker / candidate_id / status 등 → ValueError."""
    log, db, tmp = _make_logger()
    try:
        # ticker
        for bad in ["12345", "ABCDEF", 12345, ""]:
            try:
                log.log_recommended(date(2026, 5, 4), bad, "이름")
                raise AssertionError(f"bad ticker {bad!r} 통과됨")
            except ValueError:
                pass
        # name
        for bad_name in ["", "   ", None]:
            try:
                log.log_recommended(date(2026, 5, 4), "005930", bad_name)
                raise AssertionError(f"bad name {bad_name!r} 통과됨")
            except (ValueError, TypeError):
                pass
        # candidate_id
        for bad in [0, -1, "abc", True]:
            try:
                log.mark_rejected_by_filter(bad, "테스트")
                raise AssertionError(f"bad cid {bad!r} 통과됨")
            except ValueError:
                pass
        # status
        try:
            log.get_candidates_in_period(date(2026, 5, 1), date(2026, 5, 10),
                                          status="invalid_status")
            raise AssertionError("invalid status 통과됨")
        except ValueError:
            pass
        print(f"  [PASS] 잘못된 입력 14케이스 차단 (ticker 4 + name 3 + cid 4 + status 1)")
    finally:
        _cleanup(db, tmp)


def test_helpers():
    """_safe_float / _direction_match / _to_date_str."""
    # _safe_float
    assert _safe_float(1.5) == 1.5
    assert _safe_float(None) is None
    assert _safe_float(True) is None
    assert _safe_float(float("nan")) is None
    assert _safe_float("abc") is None

    # _direction_match
    assert _direction_match(1.0, 1.5) is True
    assert _direction_match(-1.0, -1.5) is True
    assert _direction_match(1.0, -1.5) is False
    assert _direction_match(0, 1.0) is None
    assert _direction_match(None, 1.0) is None
    assert _direction_match("abc", 1.0) is None

    # _to_date_str
    assert _to_date_str(date(2026, 5, 4)) == "2026-05-04"
    assert _to_date_str(datetime(2026, 5, 4, 10, 0)) == "2026-05-04"
    assert _to_date_str("2026-05-04") == "2026-05-04"
    assert _to_date_str("20260504") == "2026-05-04"
    assert _to_date_str(None).startswith("20")  # today
    try:
        _to_date_str("invalid")
        raise AssertionError("invalid date 통과됨")
    except ValueError:
        pass
    print(f"  [PASS] 헬퍼 _safe_float (5) + _direction_match (6) + _to_date_str (6)")


def test_get_recent_recommended():
    """최근 N건 recommended 후보 (최신순)."""
    log, db, tmp = _make_logger()
    try:
        for i in range(5):
            cid = log.log_recommended(date(2026, 5, 1) + timedelta(days=i),
                                       f"00593{i}", f"종목{i}")
            # 절반은 rejected (recent_recommended 에서 제외돼야 함)
            if i < 2:
                log.mark_rejected_by_filter(cid, "테스트")
        recent = log.get_recent_recommended(limit=10)
        assert len(recent) == 3   # 5 - 2 rejected
        # 최신순 (candidate_id DESC)
        cids = [r["candidate_id"] for r in recent]
        assert cids == sorted(cids, reverse=True)
        print(f"  [PASS] get_recent_recommended {len(recent)}건 (최신순)")
    finally:
        _cleanup(db, tmp)


def test_log_features_idempotent_check():
    """동일 candidate_id 두 번 log_features → IntegrityError (PK 충돌)."""
    log, db, tmp = _make_logger()
    try:
        import sqlite3 as sq
        cid = log.log_recommended(date(2026, 5, 4), "005930", "삼성전자")
        log.log_features(cid, layer2={"close_strength": 0.9})
        try:
            log.log_features(cid, layer2={"close_strength": 0.95})
            raise AssertionError("PK 충돌 없이 통과")
        except sq.IntegrityError:
            pass
        print(f"  [PASS] log_features PK 충돌 IntegrityError")
    finally:
        _cleanup(db, tmp)


def main():
    print("=== Phase 1-6 CandidateLogger 단위 테스트 ===\n")
    tests = [
        test_log_recommended_basic,
        test_log_recommended_with_dict_score,
        test_log_recommended_no_score,
        test_log_features_full,
        test_log_features_partial,
        test_log_features_int_coercion,
        test_mark_rejected_by_filter,
        test_mark_rejected_manual,
        test_lifecycle_recommended_to_exit,
        test_log_exit_without_entry,
        test_log_exit_unknown_candidate,
        test_log_labels_full,
        test_log_flow_reliability,
        test_query_get_candidates_in_period,
        test_get_distinct_stocks_count,
        test_count_by_status,
        test_invalid_inputs,
        test_helpers,
        test_get_recent_recommended,
        test_log_features_idempotent_check,
    ]
    for t in tests:
        print(f"▶ {t.__name__}")
        t()
        print()

    print(f"[ALL PASS] {len(tests)}개 테스트 통과")


if __name__ == "__main__":
    main()
