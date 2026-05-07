# PLAN — 단위 2-9d 핫픽스 · KIS fluctuation 종목코드 필드 fallback

## 목표
KIS Open API `ranking/fluctuation` 응답의 종목코드 필드명이 다른 ranking과 달라(`stck_shrn_iscd` vs `mksc_shrn_iscd`) `_filter_valid_tickers`에서 30건 전부 탈락 → `top_change=0` 영손실 회귀를 수정.

목표 결과: `[universe_v2] 출처별 기여` 로그에서 `top_change >= 1` (장 흐름 따라 0 가능하지만 한번도 비-0이 없는 회귀를 차단).

## 배경
- **단위 2-9d** (2026-05-06) 도입 후 KIS Open API ranking 4종으로 출처 2~4 라우팅
- **단위 2-9d 통합 단발 결과**: theme=17 / **top_value=25** / **top_foreign=21** / 합계 63종목 — top_change 결과가 노트에 누락됨 (사후 단서)
- **5/6 자연 트리거**: theme=17 / top_value=0 / top_change=0 / top_foreign=0 — 모든 KIS ranking 영손실 (당시 다른 이유)
- **5/7 자연 트리거**: theme=19 / top_value=27 / top_change=**0** / top_foreign=24 — top_value/foreign 정상 작동 후에도 top_change만 회귀 일관
- **5/7 KST 19:15 단발 조사**: fluctuation 응답 30건 정상 수신 + 모든 item에 `mksc_shrn_iscd` 키 부재, 종목코드는 `stck_shrn_iscd` 필드에 존재 (예: `'002070'` 비비안, `'060900'` 에이전트AI, `'276730'` 한울앤제주)
- **회귀 시점**: 단위 2-9d 도입 이후 한번도 정상 작동한 적 없음. PRD 16-3 Layer 3 (등락률 모멘텀) 출처 영손실 상태로 운영 중이었음. 30건 운영 게이트는 통과(33/30)했지만 시그니처 다양성 부족

## 현재 코드 상태 (수정 대상)
- `closing_bet_system/collectors/kis_market_provider.py:61` — `_FIELD_TICKER = "mksc_shrn_iscd"` 단일 상수
- `closing_bet_system/collectors/kis_market_provider.py:91-107` — `_filter_valid_tickers(items, top_n)`: `code = str(item.get(_FIELD_TICKER, "")).strip()` 단일 필드 사용
- `closing_bet_system/collectors/kis_market_provider.py:235` — `get_top_market_cap_data` 내부 시총 보강 코드(동일 패턴)
- 5/7 단발 raw: fluctuation 응답에는 `stck_shrn_iscd` (다른 ranking은 `mksc_shrn_iscd`)

## 구현 단계
1. **사전 단발 검증** (foreign_total / market_cap 응답 필드 확인)
   - 토큰 1분 제한 회피: 단일 스크립트에서 4종 ranking 응답 첫 1건 필드 일괄 출력
   - 어느 ranking이 어느 필드를 쓰는지 표 작성 → 실제 fallback 우선순위 확정
2. **`_filter_valid_tickers` 다중 필드 fallback**:
   - 시그니처: `_filter_valid_tickers(items, top_n, *, ticker_keys=("mksc_shrn_iscd", "stck_shrn_iscd"))`
   - 본문: `for key in ticker_keys: code = str(item.get(key, "") or "").strip(); if _TICKER_PATTERN.match(code) and code not in seen: ...`
   - 빈 문자열/None/0 모두 탐색 후 fallback (코더 검토: `or ""`로 None safe)
3. **`get_top_market_cap_data` 시총 보강 코드 동일 패턴 적용** (라인 234-237 근처)
4. **단위 테스트**: `scripts/test_closing_bet_unit_2_9d_hotfix.py`
   - HF-1: items에 `mksc_shrn_iscd` 키만 있는 경우 (volume_rank 기존 동작)
   - HF-2: items에 `stck_shrn_iscd` 키만 있는 경우 (fluctuation 신규 정상화)
   - HF-3: 두 키 모두 있는 경우 (mksc 우선 — 다른 ranking 안전)
   - HF-4: `mksc_shrn_iscd=None` + `stck_shrn_iscd="123456"` (실제 fluctuation 응답 패턴)
   - HF-5: 두 키 모두 빈 문자열 → 정규식 탈락
   - HF-6: 비-6자리 코드 (예: 'A001') → 탈락
   - HF-7: 중복 코드 → 1회만 포함
   - HF-8: top_n 절단
   - HF-9: items=None / [] → []
   - HF-10: dict가 아닌 entry 섞임 → 무시
   - HF-11 (회귀): foreign_total 5/7 자연 트리거에서 24건 정상 → 동일 응답 mock으로 24건 유지
   - HF-12 (회귀): volume_rank 5/7 27건 → 동일 유지
   - HF-13 (시총 보강): `get_top_market_cap_data` 응답에서 mksc 키 정상 → market_cap 채워짐
   - HF-14 (시총 보강 핫픽스): 응답이 stck 키만 있어도 정상 동작 (예방)
5. **code-tester 에이전트 검증** (수정 파일 + 테스트 파일 대상)
6. **systemd 재시작** + journalctl 30분 무이상 모니터링
7. **5/8(금) 자연 트리거 관찰** — `top_change >= 1` 확인

## 변경 파일
- `closing_bet_system/collectors/kis_market_provider.py` (수정 — 핫픽스)
- `scripts/test_closing_bet_unit_2_9d_hotfix.py` (신규 — 단위 테스트 14건)
- `scripts/probe_kis_ranking_fields.py` (신규 — 4종 ranking 응답 필드 일괄 출력, 향후 회귀 재검증용 보존)
- `docs/improvements/change_log.md` (1줄 추가, 단위 2-9e 형식 일관)
- `memory/project_closing_bet_system.md` (단위 2-9d 핫픽스 단락 추가)
- `memory/MEMORY.md` (인덱스 description 갱신)

## 롤백 계획
1. **즉시 롤백**: `git revert <hotfix-commit>` + `sudo systemctl restart trading_system`
2. **부작용 시나리오**: foreign_total / market_cap 응답이 stck 키도 가지는데 우연히 다른 종목코드를 담을 가능성 — 사전 단발 검증으로 차단. mksc 우선 fallback이라 기존 동작 보존
3. **위험도**: 낮음 (메서드 1개 시그니처 변경 + 호출부 1곳)

## 완료 기준
- [ ] 4종 ranking 응답 필드 매핑표 확정 (단발 검증 보존)
- [ ] `_filter_valid_tickers` 다중 필드 fallback 구현
- [ ] 시총 보강 코드 동일 패턴 적용
- [ ] 단위 테스트 14건 PASS
- [ ] 회귀 누적 91건 (2-9e 기준) → 105건으로 확장
- [ ] code-tester 심각 0건
- [ ] systemd 재시작 + 30분 무이상
- [ ] 5/8(금) 자연 트리거에서 `top_change >= 1` (또는 0이어도 단위 테스트로 회귀 차단 검증됨)

## 후속 조치
- 단위 2-9f (별도) — ETF/우선주 차단 + lstn_stcn × stck_prpr 자체 계산 + pykrx bulk 게이트 변경
- 5/8 자연 트리거 결과에 따라 hotfix → completed 아카이브
