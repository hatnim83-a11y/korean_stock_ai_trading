# CONTEXT — 테마 점수 박제 버그 핫픽스

## 변경 이유
`themes` 테이블의 `selected=1` 5건이 5/4 이후 매일 `momentum=0, ai_sentiment=0, news_count=0`으로 박제되는 회귀 발견(2026-05-07 검증). 원인은 두 곳 — (1) DB 복원 시 점수 키 누락 (2) "기존 테마 유지" 분기의 키명 불일치 — 그리고 잠재 위험 한 곳 — (3) midweek 교체 시 점수 키 누락.

## 현재 코드 상태

### main.py:184-200 (DB 복원 정규화 — 버그 #1)
```python
last_date = self.db.get_last_theme_analysis_date()
if last_date:
    self._last_theme_rotation_date = last_date
    themes_from_db = self.db.get_top_themes(last_date, count=settings.TOP_THEME_COUNT)
    if themes_from_db:
        normalized = [
            {
                "name": t["theme_name"],
                "theme": t["theme_name"],
                "score": t["score"],
                "total_score": t["score"],
                "url": t.get("url", ""),
                "category": t.get("category", "기타"),
                # ❌ momentum/supply_ratio/news_count/ai_sentiment 누락
            }
            for t in themes_from_db
        ]
        self.today_themes = normalized
```

### main.py:432-447 (기존 테마 유지 저장 — 버그 #2)
```python
themes_to_save = [
    {
        "theme": t.get("theme", t.get("name", "")),
        "score": t.get("score", 0),
        "momentum": t.get("momentum", 0),  # ❌ momentum_score를 읽어야 함
        "supply_ratio": t.get("supply_ratio", 0),
        "news_count": t.get("news_count", 0),
        "ai_sentiment": t.get("ai_sentiment", 0),
        "category": t.get("category", "기타"),
        "url": t.get("url", ""),
    }
    for t in self.today_themes
]
self.db.save_theme_scores(themes_to_save, today, selected=True)
```

### main.py:1946-1957 (midweek 교체 갱신 — 버그 #3)
```python
self.today_themes = [t for t in self.today_themes if t.get("theme") != dropped_name]
self.today_themes.append({
    "name": replacement["theme_name"],
    "theme": replacement["theme_name"],
    "score": replacement["score"],
    "total_score": replacement["score"],
    "category": replacement.get("category", "기타"),
    "url": replacement.get("url", ""),
    # ❌ momentum/supply_ratio/news_count/ai_sentiment 누락
})
```

### main.py:631-644 (정규 재선정 저장 — 정상)
```python
themes_to_save = [
    {
        "theme": t.get("theme", t.get("name", "")),
        "score": t.get("score", 0),
        "momentum": t.get("momentum_score", 0),  # ✅ momentum_score 읽음
        ...
    }
    for t in themes
]
self.db.save_theme_scores(themes_to_save, now_kst().date(), selected=True)
```

### scorer.py:652-673 (소스 — 양쪽 키 출력)
```python
scored_theme = {
    **theme,
    "theme": theme_name,
    "avg_change_rate": round(avg_return, 2),
    "total_score": round(total, 2),
    "score": round(total, 2),
    "momentum": round(momentum_score, 2),         # ← 둘 다 같은 값
    "momentum_score": round(momentum_score, 2),   # ← 둘 다 같은 값
    "supply_score": 0,
    "news_score": round(news_score, 2),
    "news_count": news_count,
    "news": news_text,
    "ai_score": round(ai_score, 2),
    "ai_sentiment": round(ai_sentiment, 2) if ai_sentiment else 0,
    ...
}
```

### database.py:613-660 (save_theme_scores — 정상 동작)
- `selected=True`: 같은 날 selected=1 모두 0 초기화 후 INSERT OR REPLACE
- `selected=False`: 일별 수집은 selected=1 행 절대 수정 안 함 (가드)

### database.py:662-693 (get_top_themes — SELECT *)
- 모든 컬럼 반환, `[dict(row) for row in rows]`로 dict 변환
- NULL 컬럼은 `None`으로 들어옴 → 정규화 시 `is None` 체크 필요

## 핵심 회귀 시나리오
1. 4/29 (화) 정규 재선정: today_themes에 momentum=22.08 정상 (scorer 결과)
2. 4/30, 5/1: "기존 테마 유지" 저장 → DB momentum=22.08 정상 (메모리 유지)
3. 5/2~5/3 사이 systemd 재시작 (4월 closing-bet 단위 작업 + 5/6 도입 작업으로 다수 발생)
4. 5/4 (월) 08:30 KST: DB 복원본 today_themes (momentum 키 없음) → "기존 테마 유지" 저장 → `t.get("momentum", 0)` → **0 박제 시작**
5. 매일 재기록 → 5/12 정규 재선정까지 지속

## 영향 범위
- 대시보드 트렌드 차트 (5/4~5/7 dip)
- midweek 교체 판단: score만 비교라 결정 영향 작음
- trade-improvement-analyst 분석 정확도 저하

## 과거 관련 버그
- 2026-05-01: `trade_reviews` 누락 fix (보유기간/midweek 매도 3경로) — 동일하게 시스템 무결성 fix 분류
- `da6276b` (2026-02-27) "DB 복원 키 불일치 버그 수정" — 당시 minimal dict 정규화 도입 → 이번 버그의 원인
- `8b7df38` (2026-03-06) DB 복원 강화 — 점수 키 미포함 결정 (현재 회귀 원인)
- `477eb99` (2026-04-10) "주중 교체 후 서비스 재시작 시 테마 유실 방지" — "기존 테마 유지" 분기 추가, 키명 불일치 발생

## 의존 관계 (향후 회귀 방지)
- `scorer.py:658-659`가 `momentum`과 `momentum_score` 양쪽 키 동시 출력해야 → main.py:438 폴백 체인 정상 동작
- 한쪽 키만 남기는 변경 시 main.py 동시 수정 필수 (scorer.py 가드 코멘트로 명시)
- DB `themes.momentum` 컬럼 리네임 시 main.py:438 + weekly_aggregator.py:123 동시 수정 필요

## 5/12 (화) 정규 재선정 의존
- 핫픽스 후 다음 정규 재선정으로 자연 회복 검증 가능
- 5/12 영업일 사전 확인: `python -c "from config import is_trading_day; from datetime import date; assert is_trading_day(date(2026,5,12))"`

## 사용자 결정
- 소급 복구: **옵션 2 (NULL 마킹)** — 박제 컬럼 NULL UPDATE, 차트 dash 표시
- 단발 검증 스크립트: **작성**
