# CHECKLIST — 종가베팅 실발주 활성화

## Phase 1: 사전 점검 (2026-05-23 토 ~ 2026-05-24 일 저녁)

### 환경/인프라
- [ ] `trading_system.service` active 상태 확인
- [ ] `data/closing_bet.db` 존재 + 접근 가능
- [ ] `closing_bet_system/config/settings.yaml` 백업 (sed -i 전에 cp 권장)
- [ ] KIS 계좌 잔액 확인 (실 매수 자금 확보, 종가베팅 사이즈 = 총자산 × 1.75% × 1~2종목/일)
- [ ] KIS Open API 토큰 정상 (`_shared_token` 발급 로그 확인)

### 텔레그램
- [ ] `.env`에 `CLOSING_BET_TELEGRAM_BOT_TOKEN` / `CLOSING_BET_TELEGRAM_CHAT_ID` 설정 확인
- [ ] 종가베팅 봇 활성: 5/22 15:35 daily_summary 메시지 정상 수신 확인
- [ ] 텔레그램 `/sell <ticker> <qty>` / `/sellall` 수동 매도 SOP 숙지

### 데이터 정합성
- [ ] dry-run 라벨 누락 5건 확인:
  - [ ] cid=47 (319400 현대무벡스, 5/8)
  - [ ] cid=157 (028260 삼성물산, 5/15)
  - [ ] cid=174 (034020 두산에너빌리티, 5/18)
  - [ ] cid=214 (005935 삼성전자우, 5/21)
  - [ ] cid=221 (034730 SK, 5/21)
- [ ] (선택) 5/24 일요일 라벨 백필 작업 (label_provider.get_label 직접 호출)

### fund_guard 동작 검증
- [ ] `closing_bet_system/infra/fund_guard.py` `_fetch_db_state` 단일 connection 작동
- [ ] capital_ratio=0.10 한도 초과 시 거부 로그 확인 (단위 테스트 또는 실측)
- [ ] weekly_loss_limit=-0.05 적용 검증 (단위 E 테스트 통과 확인)

### 사전 시뮬
- [ ] 5/22 (금) 15:18 dry-run 발화 결과 확인 (entry_pipeline 정상 트리거)
- [ ] 5/22 15:35 daily_summary 누적 게이트 (198~200/30) 확인
- [ ] 5/23 (토) 라벨링 잡 미발화 (영업일 보정) 확인

### 비상 대응 SOP 숙지
- [ ] 긴급 중지 1단: `entry_executor.enabled: false` + `morning_exit.enabled: false` + restart
- [ ] 긴급 중지 2단: `dry_run: true` 복귀 + restart
- [ ] 긴급 중지 3단: `sudo systemctl stop trading_system` (스윙 동시 중단)
- [ ] 강제 청산: 텔레그램 `/sell <ticker> <qty>` 또는 `/sellall`

## Phase 2: 토글 전환 + 재시작 (2026-05-24 일 23:00~23:30 KST)

### 설정 변경
- [ ] `settings.yaml` 백업: `cp closing_bet_system/config/settings.yaml{,.bak.20260524}`
- [ ] `entry_executor.dry_run: true → false` (라인 161)
- [ ] `morning_exit.dry_run: true → false` (라인 182)
- [ ] diff 확인 (2줄만 변경됐는지)

### 문법 검증
- [ ] yaml syntax: `venv/bin/python -c "import yaml; yaml.safe_load(open('closing_bet_system/config/settings.yaml'))"`
- [ ] settings 로드 테스트: `venv/bin/python -c "from closing_bet_system.config import load_settings; s=load_settings(); print(s.entry_executor.dry_run, s.morning_exit.dry_run)"` → 둘 다 False 확인

### 서비스 재시작
- [ ] 기존 프로세스 확인: `ps aux | grep main.py | grep -v grep` (단일 프로세스)
- [ ] `sudo systemctl restart trading_system`
- [ ] 재시작 후 active 확인: `sudo systemctl is-active trading_system`
- [ ] 종가베팅 잡 8개 등록 로그 확인:
  ```
  [orchestrator] 종가베팅 잡 8건 등록 — pipeline 15:10 / summary 15:35 / label 10:00 / flow_reliability 19:27 / entry_pipeline 15:18 / emergency_stop 09:01 / morning_exit 09:30 / morning_force_close 10:30
  ```

### 활성화 알림
- [ ] 텔레그램 종가베팅 봇에 "실발주 활성화 시작 (2026-05-25)" 수동 알림 발송
- [ ] 사용자 본인 텔레그램 알림 수신 확인

## Phase 3: 실전 운영 1주 (2026-05-25 월 ~ 2026-05-29 금)

### 5/25 월 — 첫 실 발주
- [ ] 09:01 emergency_stop 잡 발화 (5/24는 비영업일이라 대상 0건 정상)
- [ ] 09:30 morning_exit 잡 발화 (대상 0건 정상)
- [ ] 10:00 label_yesterday 잡 발화 (5/22 후보 13건 라벨링)
- [ ] 10:30 morning_force_close 잡 발화 (대상 0건 정상)
- [ ] 15:10 daily_pipeline → 후보 수집 (universe 100건 → 필터 후 ~19건)
- [ ] **15:18 entry_pipeline phase1 → 실 발주 (텔레그램 알림 수신)**
  - [ ] 텔레그램 메시지에 `order_id` 포함
  - [ ] DB `candidates.entry_phase1_order_id` NOT NULL 확인
  - [ ] KIS 계좌에서 매수 체결 확인
- [ ] **15:25 entry_pipeline phase2 → 실 발주 (체결 시)**
  - [ ] phase1 체결 종목 대상 50% 추가 매수
  - [ ] DB `candidates.entry_phase2_order_id` 박제
- [ ] 15:35 daily_summary `entered=N` (N >= 1) 확인

### 5/26 화 ~ 5/29 금 — 매일 모니터링
- [ ] 09:01 emergency_stop: T-1 entered 종목 중 갭다운 -1% 이하 즉시 매도
- [ ] 09:30 morning_exit: 시초가 50% 매도
- [ ] 10:30 morning_force_close: 잔여 50% 강제 청산
- [ ] 텔레그램 매도 알림 수신 + 체결가 확인
- [ ] DB `candidates.exit_price` / `net_pnl_pct` 박제 확인
- [ ] 매일 daily_loss 누적 확인 (-3% 도달 시 추가 진입 자동 차단)
- [ ] 매주 weekly_loss 누적 확인 (-5% 도달 시 매매 전체 중지)

### 일별 점검 항목
- [ ] 매일 22:00 KST 경: 당일 trade_reviews 저장 확인
- [ ] 매일 daily_summary 누적 게이트 표시 확인
- [ ] 슬리피지 측정값 vs 예상 0.1% 비교 (sell_slippage_tracking 모듈)
- [ ] flow_reliability 19:27 잡 정상 매칭 확인

## Phase 4: 1주 평가 (2026-05-29 금 마감 후)

### 정량 평가
- [ ] 1주 누적 진입 종목 수 / 체결률
- [ ] 누적 net realistic PnL (옵션 C 기준)
- [ ] 누적 EV+ 승률 (dry-run 100% vs 실전 비교)
- [ ] 슬리피지 실측 평균 vs 예상 0.1%
- [ ] daily_loss / weekly_loss 발동 여부

### 정성 평가
- [ ] 종목 다양성 (010170 외 신규 종목 비율)
- [ ] phase1 → phase2 진행률
- [ ] emergency_stop / morning_exit / force_close 분기별 발화 비율
- [ ] 텔레그램 알림 누락 여부
- [ ] KIS API 장애/지연 발생 빈도

### 결정 분기
- [ ] **net realistic ≥ +1.2% (세전 목표)** 달성 시:
  - [ ] 계속 운영
  - [ ] 다음 주 capital_ratio 증액 검토 (0.10 → 0.12~0.15)
  - [ ] dry-run 벤치마크와 차이 분석 (시뮬 정합성)
- [ ] **net realistic 0 ~ +1.2% (양수지만 목표 미달)** 시:
  - [ ] 계속 운영, 패턴 더 관찰 (n=10~15까지)
  - [ ] 슬리피지/지정가 미스 원인 조사
- [ ] **net realistic < 0** 시:
  - [ ] dry_run=true 복귀
  - [ ] 손실 종목 사후 분석
  - [ ] dry-run 검증 한계점 정리
- [ ] **daily/weekly 손실 한도 발동** 시:
  - [ ] 즉시 매매 중지 (자동)
  - [ ] 원인 사후 분석
  - [ ] 사용자 결정 받기 전 재개 금지

## 문서 업데이트 (Phase 4 완료 후)

- [ ] `memory/project_closing_bet_followups.md` 갱신
  - [ ] 단위 2-4f / 2-5f 활성화 결과 1단락 추가
  - [ ] dry-run vs 실전 정합성 비교
- [ ] `memory/MEMORY.md` 인덱스 한 줄 보강 (활성화 시점/결과)
- [ ] `docs/improvements/change_log.md` 1줄 추가 (before/after 추적)
- [ ] CLAUDE.md (새 규칙/교훈 발견 시)

## 아카이브
- [ ] `docs/work-plans/active/closing-bet-live-activation/` → `docs/work-plans/completed/20260529_closing-bet-live-activation/`
- [ ] 모든 [ ] 항목 [x] 체크 완료 후 아카이브
