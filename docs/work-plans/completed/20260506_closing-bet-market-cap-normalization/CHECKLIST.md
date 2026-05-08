# CHECKLIST — 단위 2-9e · 시총 보강 정규화 (옵션 A)

## 구현 항목

### Step 1 — `kis_market_provider.py` 단위 정규화
- [x] 모듈 상수 추가: `_STCK_AVLS_UNIT_TO_WON = 100_000_000` (억원 → 원)
- [x] `get_top_market_cap_data` 라인 246: `entry["market_cap"] = int(round(mcap * _STCK_AVLS_UNIT_TO_WON))` 변경 (round 적용으로 부동소수점 절단 보호)
- [x] mcap > 0 가드 유지 (음수/0/NaN 종목 entry["market_cap"] 미생성)
- [x] 인라인 주석 갱신 (라인 239-244): "단위 미확정" → "단위 = 억원 (단위 2-9e 확정), × 1억 정규화 적용"
- [x] 진단 로그 갱신 (라인 247-253): "원 단위 가정. min_market_cap=5,000억" 오기 → "단위 2-9e 확정: 억원 × 1억 정규화. min_market_cap=500억과 비교"
- [x] py_compile 통과

### Step 2 — `universe_filters.py` DEFAULT 값 정합 (안전망 보강)
- [x] 라인 86: `DEFAULT_FALLBACK_INCLUDE_MARKET_CAP = True` → `False`
- [x] 변경 사유 주석 추가 (settings 로드 실패 시 안전망 정합)
- [x] py_compile 통과

### Step 3 — `settings.yaml` 토글 활성화
- [x] `data_source.fallback_include_market_cap: false` → `true` 변경
- [x] 주석에 단위 2-9e 활성 배경 + 사전 조사 5종목 단위 확정 결과 추가
- [x] yaml 문법 검증

### Step 4 — 단위 테스트 (`scripts/test_closing_bet_unit_2_9e.py`) — 11건
- [x] MC-1: stck_avls=15551101 → market_cap=1,555,110,100,000,000원 (× 1억 정규화)
- [x] MC-1b: `isinstance(entry["market_cap"], int)` 타입 검증
- [x] MC-2: stck_avls=0 → entry["market_cap"] 키 미생성 (mcap > 0 가드)
- [x] MC-3: 비-6자리 코드 혼입 → 정규식 필터링 정상
- [x] MC-4: `fallback_include_market_cap=true` → universe_filters 시총 보강 적용 (mock)
- [x] MC-5: 시총 보강 후 옵션 A 보수 탈락 종목 ≥ 1건 (상위 200 외) — `data_not_found` 유지
- [x] MC-6a: 정규화 적용 → market_cap=60,000,000,000 (600억) → `min_market_cap=50_000_000_000` (500억) 임계 통과 PASS
- [x] MC-6b (회귀): 정규화 누락 시뮬 → market_cap=600 → 500억 미달 FAIL (회귀 검출)
- [x] MC-7 (회귀): `fallback_include_market_cap=false` → 옵션 A 보수 그대로 (단위 2-9d 동작)
- [x] MC-7b (안전망): `_load_filter_config` 예외 → `DEFAULT_FALLBACK_INCLUDE_MARKET_CAP=False` 분기 (Step 2 변경 검증)
- [x] MC-8: NaN/inf/빈 문자열 시총 응답 → `_safe_float` None 반환 → entry["market_cap"] 키 미생성
- [x] 11건 모두 PASS

### Step 5 — code-tester 검증
- [x] code-tester 에이전트 호출 (수정 파일 3개 + 테스트 1개 대상)
- [x] 단위 2-9d 발견 패턴 (cfg NameError, direction 인자, _FIELD_CHANGE_RATE 상수, 단위 진단 로그) 재발 차단 확인
- [x] 심각 이슈 0건 또는 발견 시 즉시 수정
- [x] 회귀 재실행 PASS

## 검증 항목

### 단위 검증
- [x] py_compile 통과 (kis_market_provider.py + universe_filters.py + scripts/test_closing_bet_unit_2_9e.py)
- [x] `venv/bin/python scripts/test_closing_bet_unit_2_9e.py` 11건 PASS
- [x] 회귀 91건: 2-9a 18 + 2-9b 15 + 2-9c 10 + 2-9d 12 + 2-9e 11 + 2-2b-1 15 + 2-2b-2 10 = **91건 PASS**

### 통합 검증 (단발)
- [x] `get_universe_v2_filtered()` 단발 호출
- [x] KIS market-cap 호출 로그 1건 + 보강 종목 수 ≥ 30건 (상위 200 ∩ universe 63 추정)
- [x] `[kis_market_provider] stck_avls 단위 = 억원 (단위 2-9e 확정), × 1억 정규화 적용` 로그 1회
- [x] universe ≥ 1건 (단발 검증, 5건은 자연 트리거 의존)
- [x] KIS API 호출 시간 ≤ 6초 (4 ranking + 1 market-cap)

### 실전 검증 (5/7 또는 다음 영업일 자연 트리거)
- [x] 15:10 KST 자동 트리거 발화
- [x] `[universe_v2] 출처별 기여` 모두 비-0
- [x] `[universe_filters] 시총 보강` 로그 — 매치 종목 수 ≥ 30 + 미매치 탈락 카운트
- [x] universe ≥ 5건 (목표 달성)
- [x] candidates INSERT ≥ 1건
- [x] 일일 요약 텔레그램 알림 (15:35)
- [x] **신규**: pykrx bulk 정상 복구 여부 점검 (시총 보강 로그 미발생 시 bulk 복구 의심)
- [x] **신규**: universe 에 ETF(069500 등)/우선주(005935 등) 0건 확인 (단위 2-9f 트리거 점검)

## 배포 항목
- [x] systemd 재시작 전 단일 PID 확인 (`pgrep -f main.py | wc -l`)
- [x] 변경 파일 git stage (`git status`로 의도와 일치 확인)
- [x] `sudo systemctl restart trading_system`
- [x] active(running) 확인
- [x] 종가베팅 잡 3건 등록 + Phase 1 알림형 로그 확인
- [x] 배포 후 30분 `journalctl -u trading_system -f` 무이상 모니터링

## 문서 업데이트 항목
- [x] `docs/improvements/change_log.md` 1줄 추가 (단위 2-9e 항목, 단위 2-9d 형식 일관)
- [x] `memory/project_closing_bet_system.md` 단위 2-9e 1단락 추가 (시총 보강 정규화)
- [x] `memory/MEMORY.md` 인덱스 description 갱신 (closing_bet_system 항목)
- [x] git commit + push
- [x] 임시 스크립트 `scripts/probe_kis_unit_2_9e.py` **보존** (사용자 결정 확정 — 재검증용)

## 사전 조사 (이번 세션 — 2026-05-06 KST 14:57)
- [x] `scripts/probe_kis_unit_2_9e.py` 신규 생성 + 단발 실행
- [x] KIS ranking/market-cap 응답 11개 키 확정
- [x] stck_avls 단위 = 억원 검증 (5종목 × 1억 = inquire-price market_cap 정확 일치)
- [x] volume-rank 응답 시총 컬럼 부재 확인 (lstn_stcn만 존재)
- [x] 옵션 (A) vs (C) 비교 + (A) 채택 결정
- [x] 3문서(PLAN/CONTEXT/CHECKLIST) 작성
- [x] Plan + strategy-coder 에이전트 검토 완료 (심각 3 + 주의 6 발견)
- [x] 사용자 결정: ETF/우선주 단위 2-9f 미루기, probe 스크립트 보존
- [x] 3문서 사전 정정 9건 반영 (이 단계)

## 완료 게이트 (선언 전 체크)
- [x] 구현 항목 전부 `[x]`
- [x] 검증 항목 단위/통합 전부 `[x]` (실전 검증은 자연 트리거 후)
- [x] 배포 항목 전부 `[x]`
- [x] 문서 업데이트 항목 (아카이브 제외) 전부 `[x]`
- [x] 자연 트리거 검증 후 → active → completed 아카이브 (단위 2-9c/2-9d 와 함께)

## 후속 단위 (별도 작업, 1주 관찰 후 결정)
- **단위 2-9f — 옵션 C `lstn_stcn × stck_prpr` 자체 계산 + ETF/우선주 차단 + pykrx 게이트 변경**:
  - ranking 4종 응답에서 발행주식수 × 현재가 = 시총 산출 (상위 200 한도 없음)
  - 우선주(코드 끝 5/7/9) / ETF(prefix 069/091/114 등) 차단 필터 추가
  - pykrx bulk 정상 복구 시에도 시총 보강 유지 분기 추가
  - **트리거**: 단위 2-9e 활성 후 1주 관찰 — universe < 5건 빈발 OR ETF/우선주 universe 진입 사례 발견 시
