# CHECKLIST — 단위 2-9d 핫픽스 · KIS fluctuation 종목코드 필드 fallback

## 구현 항목

### Step 1 — 사전 단발 검증 (4종 ranking 응답 필드 매핑)
- [ ] `scripts/probe_kis_ranking_fields.py` 신규 작성 — 단일 스크립트에서 4종 ranking 응답 첫 1건 필드 일괄 출력
- [ ] 단발 실행 + 결과를 CONTEXT.md "5/7 KST 19:15 단발 raw 응답" 섹션에 추가
- [ ] 4종 ranking 필드 매핑표 확정 (volume_rank/fluctuation/foreign_total/market_cap × mksc/stck)
- [ ] 토큰 1분 제한 회피 (단일 호출 + 호출 간 sleep 1초)

### Step 2 — `kis_market_provider.py` 다중 필드 fallback
- [ ] 모듈 상수 추가: `_TICKER_KEYS_DEFAULT = ("mksc_shrn_iscd", "stck_shrn_iscd")`
- [ ] `_filter_valid_tickers` 시그니처 변경: `(items, top_n, *, ticker_keys=_TICKER_KEYS_DEFAULT)`
- [ ] 본문 변경: ticker_keys 순회 + None safe (`raw or ""`) + 첫 비-빈 값 사용
- [ ] `get_top_market_cap_data` 시총 보강 코드 라인 230~235 동일 패턴 적용 (helper 함수 추출 또는 inline 동일 처리)
- [ ] py_compile 통과
- [ ] mksc 우선 폴백 검증: 두 키 모두 있는 경우 mksc 값 사용

### Step 3 — 단위 테스트 (`scripts/test_closing_bet_unit_2_9d_hotfix.py`) — 14건
- [ ] HF-1: items에 mksc 키만 → 정상 (volume_rank 기존 동작)
- [ ] HF-2: items에 stck 키만 → 정상 (fluctuation 신규 정상화)
- [ ] HF-3: 두 키 모두 있는 경우 → mksc 우선 (다른 ranking 안전)
- [ ] HF-4: `mksc=None` + `stck="123456"` → stck 사용 (fluctuation 실제 패턴)
- [ ] HF-5: 두 키 모두 빈 문자열 → 정규식 탈락
- [ ] HF-6: 비-6자리 코드 ('A001') → 탈락
- [ ] HF-7: 중복 코드 → 1회만 포함
- [ ] HF-8: top_n 절단 (5건 요청 → 5건 반환)
- [ ] HF-9: items=None / [] → []
- [ ] HF-10: dict가 아닌 entry 섞임 → 무시
- [ ] HF-11 (회귀): foreign_total mock 24건 → 24건 유지
- [ ] HF-12 (회귀): volume_rank mock 27건 → 27건 유지
- [ ] HF-13 (시총 보강): market_cap 응답 mksc 키 정상 → market_cap 채워짐
- [ ] HF-14 (시총 보강 핫픽스): stck 키만 있는 응답 → 정상 동작 (예방)
- [ ] 14건 모두 PASS

### Step 4 — code-tester 검증
- [ ] code-tester 에이전트 호출 (수정 1개 + 테스트 1개 + probe 1개 대상)
- [ ] 단위 2-9d 발견 패턴(direction 인자, _FIELD_CHANGE_RATE 상수, cfg NameError, 단위 진단 로그) 재발 차단 확인
- [ ] 심각 이슈 0건 또는 발견 시 즉시 수정
- [ ] 회귀 누적 91건 (단위 2-9e 기준) → 105건 (HF 14건 추가) 재실행 PASS

## 검증 항목

### 단위 검증
- [ ] py_compile 통과 (`kis_market_provider.py`)
- [ ] `venv/bin/python scripts/test_closing_bet_unit_2_9d_hotfix.py` 14건 PASS
- [ ] 회귀: 2-9a 18 + 2-9b 15 + 2-9c 10 + 2-9d 12 + 2-9e 11 + 2-9d-hotfix 14 + 2-2b-1 15 + 2-2b-2 10 = **105건 PASS**

### 통합 검증 (단발)
- [ ] `get_top_change_codes(top_n=30, direction='up')` 단발 호출 → ≥ 1건 반환
- [ ] `get_top_value_codes()` / `get_top_foreign_buy_codes()` / `get_top_market_cap_data()` 회귀 — 5/7 자연 트리거 수치 (27/24/18 매치) 또는 그 시점 데이터에 부합

### 실전 검증 (5/8 또는 다음 영업일 자연 트리거)
- [ ] 15:10 KST 자동 트리거 발화
- [ ] `[universe_v2] 출처별 기여` 로그에서 `top_change >= 1` (또는 0이어도 단위 테스트로 회귀 차단 검증됨)
- [ ] `[universe_v2] KIS top_change 실패 → pykrx 폴백` warning 로그 부재
- [ ] universe ≥ 5건 유지 (회귀 없음)
- [ ] candidates INSERT ≥ 1건

## 배포 항목
- [ ] systemd 재시작 전 단일 PID 확인 (`pgrep -f main.py | wc -l`)
- [ ] 변경 파일 git stage (`git status`로 의도와 일치 확인)
- [ ] `sudo systemctl restart trading_system`
- [ ] active(running) 확인 + 신규 PID 기록
- [ ] 종가베팅 잡 3건 등록 + Phase 1 알림형 로그 확인
- [ ] 배포 후 30분 `journalctl -u trading_system -f` 무이상 모니터링

## 문서 업데이트 항목
- [ ] `docs/improvements/change_log.md` 1줄 추가 (단위 2-9d 핫픽스 항목, 단위 2-9e 형식 일관)
- [ ] `memory/project_closing_bet_system.md` 단위 2-9d 핫픽스 단락 추가
- [ ] `memory/MEMORY.md` 인덱스 description 갱신 (closing_bet_system 항목)
- [ ] git commit + push
- [ ] 임시 스크립트 `scripts/probe_kis_ranking_fields.py` 보존 (재검증용)

## 완료 게이트 (선언 전 체크)
- [ ] 구현 항목 전부 `[x]`
- [ ] 검증 항목 단위/통합 전부 `[x]` (실전 검증은 자연 트리거 후)
- [ ] 배포 항목 전부 `[x]`
- [ ] 문서 업데이트 항목 (아카이브 제외) 전부 `[x]`
- [ ] 5/8 자연 트리거 검증 후 → active → completed 아카이브 (단위 2-9c/2-9d/2-9e 와 함께)

## 후속 단위 (별도)
- **단위 2-9f** (이번 세션 플랜만 작성, 코딩 별도 세션):
  - ETF prefix 차단 (069/091/114 등)
  - 우선주 코드 끝자리 5/7/9 차단
  - lstn_stcn × stck_prpr 자체 계산 (KIS top 200 한도 제거)
  - pykrx bulk 정상 복구 시에도 시총 보강 유지 분기
