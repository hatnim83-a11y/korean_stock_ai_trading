# PLAN: 매수 필터 체인 개선 — 강세장 RSI 동적 + 테마 슬롯 보장 (Phase A)

> 최종 플랜 파일: `/home/hatni/.claude/plans/composed-tickling-yao.md` (승인 완료)
> 제안서: `docs/improvements/2026-04-23_buy_filter_proposal.md`

## 목표

강세장(KOSPI 전일 종가 등락률 ≥ +1%)에서는 RSI 상한을 75로 동적 조정하고, 테마당 최소 1개(≥25점) 후보를 AI 검증에 보장하여 매수 기회를 복원한다.

**정량 목표**: 1주 관찰 후 매수 실패일 ≤ 1/5 (20%) 달성 (최근 3일 66.7% 대비)

## 배경

- **최근 3영업일 매수 실패율 66.7%** (4/21 0건, 4/22 1건, 4/23 0건)
- filter 통과 수: 평시 42.8개 → 최근 14.0개 (-67%)
- **원인**: 강세장에서 RSI 70 고정컷이 KOSPI 주도주 전멸 — 4/23 RSI 80 이상 탈락 11건 (삼성SDI 93.9, SK하이닉스 87.9, LG이노텍 83.2)
- **2차 원인**: 테마 쏠림 (4/23 5개 테마 중 3개가 AI 검증 0건 진출)

## 구현 단계

### Phase A-0: 블로커 해제 (선행)
- [ ] KIS 일봉 지수 조회 TR ID 실기 확인 (`FHKUP03500100` 후보)

### Phase A-1: 스키마 선작업
- [ ] `config.py`: RSI 동적 6개 + 테마 슬롯 2개 Field 추가
- [ ] `database.py`: `_migrate()` v14 — `screening_log.rsi_at_screen`, `screening_log.theme_slot_protected` 컬럼 추가
- [ ] `save_screening_log()` 시그니처 확장

### Phase A-2: API 계층
- [ ] `kis_api.py`: `get_prev_index_change_rate(index_code="0001") -> Optional[float]` 신규 메서드

### Phase A-3: 필터/스크리너 전파
- [ ] `filters.py:40` 상수 → `settings.RSI_UPPER_NORMAL` 일원화
- [ ] `screen_stocks_in_theme / screen_all_themes / run_daily_screening` 시그니처에 `rsi_upper` 추가
- [ ] `run_daily_screening` 진입 시 `_get_market_regime_rsi()` 1회 호출 후 하위 전파
- [ ] `apply_all_filters(stock_info, rsi_upper=rsi_upper)` 전달 (screener.py:211)
- [ ] `main.py:954` (midweek 재평가) **수정 없음** (평시 기준 유지)

### Phase A-4: 테마 슬롯 보장 + 컷 순서 재설계
- [ ] `_apply_theme_min_slot()` 헬퍼 작성
- [ ] `screen_all_themes` 내부 `max_total` 컷 지연 (run_daily_screening으로 이동)
- [ ] `run_daily_screening` 라인 598 부근: ① 슬롯 보장 → ② min_score 컷 → ③ max_total 최종 컷
- [ ] 로깅 추가 (RSI regime, 슬롯 보장 종목 코드, safety_floor 미달 WARN)

### Phase A-5: 검증·배포
- [ ] py_compile 통과
- [ ] code-tester 에이전트 검증 (수정 6파일)
- [ ] 장 마감(15:30) 이후 `sudo systemctl restart trading_system`
- [ ] MCP SQLite로 screening_log 새 컬럼 populated 확인

### Phase A-6: 1주 관찰
- [ ] 매일 매수 결과 INFO 로그 + MCP SQLite 집계
- [ ] 롤백 트리거 체크 (제안서 9.1)
- [ ] 주간 총평 — Phase B 진행 여부 판단

## 변경 파일 목록

| 파일 | 변경 내용 |
|------|----------|
| `config.py` | 8개 Field 추가 (라인 389 근처) |
| `modules/stock_screener/filters.py` | 모듈 상수 `RSI_UPPER_LIMIT` 일원화 (라인 40) |
| `modules/stock_screener/screener.py` | 시그니처 전파 + 2개 헬퍼 + 컷 순서 |
| `modules/stock_screener/kis_api.py` | `get_prev_index_change_rate` 신규 |
| `database.py` | `_migrate()` v14, `save_screening_log` 확장 |
| `main.py` | **수정 없음** (midweek 평시 기준) |
| `modules/market_guard.py` | **수정 없음** (당일 기준과 독립 병행) |

## 롤백 계획

### 즉시 롤백 (런타임 스위치)
- `config.py` / `.env`: `RSI_DYNAMIC_ENABLED=False` + `THEME_MIN_SLOT_ENABLED=False`
- 재시작 없이 다음 스크리닝 사이클부터 기존 동작 복귀

### 롤백 트리거 (제안서 9.1)
- RSI 70~75 통과 종목 5일 평균 수익률 < -5% (5건 이상 축적 시)
- 전체 매도 건 평균 수익률이 직전 2주 대비 -5%p 하락
- 동일 테마 손절 2건 이상 동시 발생

## 완료 기준

- Phase A-1~A-5 구현·검증·배포 모두 `[x]`
- Phase A-6 1주 관찰 완료, 롤백 트리거 미발동 or 즉시 롤백 수행
- `docs/improvements/change_log.md` 1줄 추가
- 프로젝트 메모리 업데이트 (`project_strategy.md`, 신규 `project_buy_filter_phase_a.md`)
- `active/` → `completed/20260501_buy-filter-rsi-theme-slot/` 아카이브

## 의사결정 이력

- 2026-04-24: 사용자 확정
  - 배포 순서: #2+#3 동시 배포
  - 관찰 DB: v14 마이그레이션 동시 진행
  - midweek 재평가: 평시 RSI 기준 유지
- 2026-04-24: strategy-planner + strategy-coder 에이전트 리뷰 반영
  - Q3 구현 구멍(max_total × 슬롯 보장 순서) 해결
  - Q7-3 RSI 상수 일원화
  - Q4 TR ID 블로커 선행
