# CHECKLIST: KIND 시장경보 네이버 프로바이더

## 구현 항목

### 단위 2-2b-1: KindNaverProvider 함수 구현 ✅ (2026-05-04 완료)
- [x] `closing_bet_system/collectors/kind_naver_provider.py` 신규 (~190줄)
  - [x] `fetch_kind_alerts() -> dict[str, str]` 메인 함수 (5 페이지 fetch + 통합)
  - [x] **5 데이터 출처 확정** (사전 조사 결과 — investment_alert 는 type 파라미터로 단계별 분리됨)
    - [x] `manage` (관리종목, 110건)
    - [x] `halt` (매매거래정지, 200건)
    - [x] `caution` (투자주의, ?type=caution, 18건)
    - [x] `warning` (투자경고, ?type=warning, 49건)
    - [x] `risk` (투자위험, ?type=risk, 0건 — 현재 없음 정상)
  - [x] `_pick_strongest_level()` — LEVEL_PRIORITY (관리5/정지4/위험3/경고2/주의1)
  - [x] urllib + EUC-KR → UTF-8 디코딩
  - [x] User-Agent (Mozilla/5.0)
  - [x] timeout=5 (각 fetch)
  - [x] 6자리 정규식 (`code=(\d{6})\"\s+class="tltle"`)
  - [x] 모든 fetch 실패 시 빈 dict 폴백
  - [x] 각 fetch try/except 격리 + URLError/HTTPError/TimeoutError 모두 흡수
- [x] `kind_alert_collector.py` 수정
  - [x] `ALERT_LEVEL_TO_SEVERITY`에 `"관리종목": 3, "관리": 3` 매핑 추가 (PRD 4-1 정합)
- [x] py_compile 통과 (kind_naver_provider, kind_alert_collector, test 파일)
- [x] **단발 실행 검증** — 5 페이지 fetch 1초 내, 총 315종목 (110 관리 + 142 정지 + 0 위험 + 45 경고 + 18 주의)
- [x] `scripts/test_closing_bet_unit_2_2b_1.py` 신규 (15 시나리오 모두 PASS)
  - [x] KN-1: 5 페이지 모두 정상 → 통합 dict
  - [x] KN-2: 1개 페이지 URLError → 나머지로 진행
  - [x] KN-3: 모두 실패 → 빈 dict
  - [x] KN-4: EUC-KR 디코딩 (한글 종목명)
  - [x] KN-5: 다중 단계 → 관리종목 우선 (LEVEL_PRIORITY)
  - [x] KN-6: 6자리 정규식 (영문/5자리/7자리 제외)
  - [x] KN-7: 5단계 매핑 + LEVEL_PRIORITY 정확성
  - [x] KN-8: 중복 종목코드 자연 제거
  - [x] KN-9: 빈/None HTML
  - [x] KN-10: timeout 5초 적용
  - [x] KN-11: User-Agent 헤더 설정
  - [x] KN-12: HTTPError graceful 폴백
  - [x] KN-13: ALERT_LEVEL_TO_SEVERITY 관리종목=3 매핑 (PRD 4-1)
  - [x] KN-14: _pick_strongest_level (빈/단일/다중)
  - [x] KN-15: KindAlertCollector(provider=fetch_kind_alerts) 통합
- [ ] code-tester 검증 (단위 2-2b-2 완료 후 일괄)

### 단위 2-2b-2: 통합 (main_orchestrator + universe_filters + universe_provider_v2) ✅ (2026-05-04 완료)
- [x] `closing_bet_system/main_orchestrator.py` 수정
  - [x] KIND collect → universe_provider 호출 순서 변경 (universe 산출 전에 fetch)
  - [x] universe_provider 호출은 lambda wrap (TypeError 발생 시 무인자 호출 폴백 — v1 시그니처 호환)
  - [x] `kind_collector` property에 `KindNaverProvider` 주입 (import 실패 시 graceful 폴백)
  - [x] severity_map None/빈 dict 모두 회귀 안전
  - [x] 기존 KIND collect 호출 (Phase 2-2 위치) 제거 — 이중 호출 방지
- [x] `closing_bet_system/collectors/universe_filters.py` 수정
  - [x] `apply_attribute_filters`에 `severity_map: Optional[dict] = None` 인자 추가
  - [x] `apply_all_filters`에 `severity_map` 인자 추가 (전달용)
  - [x] severity ≥ 3 사전 제외 로직 (속성 필터 첫 단계, first-rejection-only 정합)
  - [x] `SEVERITY_EXCLUDE_THRESHOLD = 3` 모듈 상수 (kind_alert_collector 와 동기)
  - [x] 모듈 docstring 후속 단위 항목 갱신 (KIND 항목을 본 단위로 이동)
- [x] `closing_bet_system/collectors/universe_provider_v2.py` 수정
  - [x] `get_universe_v2_filtered`에 `severity_map: Optional[dict] = None` 인자 추가
  - [x] `apply_all_filters` 호출 시 severity_map 전달
- [x] `scheduler.py` 검토 — 변경 불필요 (main_orchestrator의 lambda wrap으로 처리)
- [x] py_compile 통과 (4 파일)
- [x] **단발 통합 검증** — `run_daily_pipeline()` 호출 시 KindNaverProvider가 실제 fetch (315종목 severity_map 수집) + universe_provider에 정확히 전달 확인
- [x] `scripts/test_closing_bet_unit_2_2b_2.py` 신규 (10 시나리오 모두 PASS)
  - [x] KI-1: severity 3 사전 제외 + severity < 3 통과
  - [x] KI-2: severity_map=None → 기존 동작 (회귀)
  - [x] KI-3: severity_map={} → 사전 제외 0건
  - [x] KI-4: severity 1, 2 → 통과 (임계값 3 미만)
  - [x] KI-5: severity 3 + 시총 미달 → kind 사유 우선 (first-rejection-only)
  - [x] KI-6: apply_all_filters severity_map 전달 정상
  - [x] KI-7: apply_all_filters severity_map 미지정 → 기존 동작 (회귀)
  - [x] KI-8: get_universe_v2_filtered severity_map 인자 통과 + 사전 제외
  - [x] KI-9: SEVERITY_EXCLUDE_THRESHOLD = 3 (universe_filters / kind_alert_collector 동기)
  - [x] KI-10: 모든 종목 KIND 사전 제외 → passed=[], rejected 정합
- [x] **회귀 검증**: 단위 2-2 (16) / 2-9a (18) / 2-9b (15) 모두 PASS — 누적 74건
- [x] code-tester 검증 — **심각 0건 / 주의 3건** (모두 즉시 반영)
  - [x] 주의 1: `apply_attribute_filters` 로그 카운트 명확화 (KIND 사전 제외 vs 속성 탈락 분리 표시)
  - [x] 주의 2: `_call_universe_provider` TypeError 폴백 시 warning 로그 추가 (KIND 무력화 명시적 알림)
  - [x] 주의 3: `SEVERITY_EXCLUDE_THRESHOLD` 중복 제거 → `kind_alert_collector` 단방향 import (순환 위험 없음)
  - [x] 수정 후 회귀: 단위 2-2 (16) / 2-2b-1 (15) / 2-2b-2 (10) / 2-9b (15) 모두 PASS
  - [x] **종합 판정: 배포 가능**

## 검증 항목

### 단위 검증
- [ ] py_compile 5 파일 통과
- [ ] 단위 테스트 20+ 시나리오 PASS (12 + 8)
- [ ] code-tester 심각 0건

### 통합 검증
- [ ] 단발 트리거 — KIND fetch + severity 사전 제외 정상
- [ ] universe 종목 수 변화 (5~10건 감소 예상)
- [ ] 회귀: KindHttpProvider 미주입 시 기존 동작 유지

### 실전 검증 (배포 후 1일)
- [ ] 5/6 (수) 15:10 자연 트리거 — KIND severity 매칭 로그 확인
- [ ] KIND fetch 시간 < 5초 모니터링
- [ ] candidates 테이블 — `kind_severity_3` 사유 분포 확인
- [ ] 네이버 차단 미발생 (1회/일 호출)

## 배포 항목
- [ ] systemd 재시작 전 선행 체크 (단일 PID)
- [ ] 장 마감 후 또는 장 시작 전 권장
- [ ] `sudo systemctl restart trading_system`
- [ ] active(running) 확인
- [ ] KIND provider 활성 로그 확인

## 문서 업데이트 항목
- [ ] `docs/improvements/change_log.md` 1줄 추가
- [ ] `memory/project_closing_bet_system.md` — KIND 네이버 프로바이더 도입 1단락 추가
- [ ] 3문서 active → completed/YYYYMMDD_closing-bet-kind-naver-provider/ 이동

## 완료 게이트 (선언 전 체크)
- [ ] 구현 항목 전부 `[x]` (단위 2-2b-1, 2-2b-2)
- [ ] 검증 항목 전부 `[x]`
- [ ] 배포 항목 전부 `[x]`
- [ ] 문서 업데이트 항목 전부 `[x]`
