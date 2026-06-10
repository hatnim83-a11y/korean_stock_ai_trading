# PLAN — 매수↔모니터 재시작 레이스 근본 수정

## 목표
자동 매수로 체결된 종목이 모니터링에서 누락되는 레이스 컨디션을 제거한다.
누락 종목은 손절·트레일링·분할익절·불타기(2차 진입)가 전부 미작동하므로 **무방비 노출**이 된다.

## 절대 제약 (불변식)
- **매수 타이밍은 절대 변경하지 않는다.** `buy_minute`(early_buy 09:05 / legacy 09:25)는 그대로.
- 변경 대상은 오직 **"모니터 재시작이 언제/어떻게 발화하는가"** 뿐. 매수 잡의 시작 시각·발주 로직·체결 로직은 한 줄도 건드리지 않는다.
- 즉 매수는 지금과 똑같이 09:05에 시작하고, "매수가 끝난 뒤 모니터가 그 결과를 확실히 반영하도록"만 고친다.

## 배경 (2026-06-10 실제 사건)
- 09:05:00 `execute_buy` 잡 시작 (공격적 지정가 + 재시도 루프, 총 ~82초 소요)
- **09:06:00** `monitoring_start`(재시작, "신규 매수분 포함") 발화 → 이 시점 DB엔 신규 체결분 미저장 → `포지션 로드: 1개`(기존 롯데쇼핑만)
- 09:06:19 피에스케이홀딩스(031980) 체결 완료 + DB 저장 → **재시작보다 19초 늦어 영구 누락**
- 이후 추가 재시작 잡 없음 → 15:30 stop까지 무방비. 당일 수동 `systemctl restart`로 임시 복구

### 근본 원인
`scheduler.py:191-193` early_buy 모드: 매수 `buy_minute=5`(09:05) / 모니터 재시작 `monitoring_minute=6`(09:06).
**두 잡 간격이 1분**인데 매수 루프(후보 수 × 지정가 재시도)가 1분을 초과하면, 재시작이 매수 완료 전에 발화 → 그 이후 체결 종목 누락.
두 잡은 독립 APScheduler 잡이라 시간상 겹쳐도 서로를 기다리지 않는다.

## 구현 단계

### Step 1 — (1차/즉시 완화) 고정 간격 확대 + 가드
- `monitoring_minute`를 매수 완료 여유를 둔 값으로 후행 (예: early_buy 시 6→10).
- 한계: 매수 루프 길이가 가변이라 근본 해결 아님(슬롯 5개 × 재시도 누적 시 여전히 위험). **임시 안전마진**으로만.

### Step 2 — (근본) 매수 완료 이벤트에 모니터 재시작을 체이닝
- 삽입 위치: `main.py:1581` `execute_buy_orders()` `return` **직전**(Phase 8 요약 발송 후, 모든 체결분이 `save_holding_position`으로 DB 저장 완료된 시점).
- `try: await self.start_monitoring() except: 로그만` (매수 잡 오염 차단) 한 블록 추가.
- `start_monitoring()`은 이미 멱등(`main.py:1789` `_running`이면 stop+start)이고 `load_positions_from_db()`(`:1803`)로 holding 전량을 적재 → 신규 체결분 자동 포함.
- **매수 타이밍 불변**: 이 호출은 매수 루프가 끝난 *뒤*에 실행되므로 매수 발주 시점·순서에 영향 없음.
- 고정 시각 cron 재시작은 **idempotent 안전망**으로 잔존(매수 잡이 도중 예외로 죽어 체이닝이 안 도는 경우 대비). 단 Step 1으로 cron을 매수 완료 이후로 후행시켜 이중 race 방지.
- 효과: "DB 저장 완료 → 재시작" 순서를 코드로 보장 → 레이스 구조적 불가능.

### Step 3 — (방어 심층) 누락 종목 자동 편입 스윕
- 모니터 루프 또는 주기 잡에서 `portfolio.status='holding'` ∖ `self.positions` 차집합 탐지 → `add_position()`로 지각 편입.
- 어떤 경로로든(수동 매수, 지각 체결 등) 누락이 생겨도 다음 사이클에 자동 복구.
- 텔레그램 1회 경보로 가시성 확보(무음 복구 금지).

### Step 4 — start_monitoring 멱등성 보강
- Step 2에서 매수완료 트리거 + cron 트리거가 이중 발화해도 무해하도록 stop+start 재진입 안전성 점검.

## 변경 파일 (예상)
- `scheduler.py` — 잡 시각/콜백 배선 (Step 1, 필요 시 Step 2 콜백 등록)
- `main.py` — `execute_buy_orders()` 말미 모니터 재시작 체이닝 (Step 2), 누락 스윕 (Step 3)
- `modules/trading_engine/portfolio_monitor_v2.py` — 누락 종목 차집합 편입 헬퍼 (Step 3), 멱등성 (Step 4)
- `config.py` / `.env` — 간격/토글 상수 (Step 1, Step 3 토글)

## 완료 기준
- 매수 루프가 1분 넘게 걸려도 모든 체결 종목이 당일 모니터에 편입됨 (로그 `포지션 로드` 수 = holding 수)
- 인위적 지연(매수 루프 mock 90초) 테스트에서 누락 0건
- 이중 트리거(매수완료 + cron) 시 예외/중복 구독 없음
- `docs/improvements/change_log.md` 1줄 기록 (해당 시)

## 롤백
- Step 1: `monitoring_minute` 원복 + restart
- Step 2/3: 신규 토글 false + restart → 기존 고정 cron 재시작 경로로 NO-OP 복귀

## 리스크
- 매수완료 체이닝이 매수 잡 내부에서 모니터 stop+start를 호출 → 매수 잡 실행시간 증가 및 WebSocket 재구독 부하. start_monitoring 내부 예외가 매수 잡을 오염시키지 않도록 try/except 격리 필수.
- 누락 스윕이 매도 직후 잔재 포지션을 오편입하지 않도록 `status='holding'` 엄격 가드.
