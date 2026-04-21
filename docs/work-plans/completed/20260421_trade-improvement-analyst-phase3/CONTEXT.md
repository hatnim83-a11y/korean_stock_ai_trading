# CONTEXT: Phase 3 운영 정비 + 리마인더 자동화

## 변경 이유
1. `change_log.md`가 비어 있으면 에이전트의 "섹션 2 Before/After 추적"이 의미 없어짐 — 3건 소급 기록으로 기준점 확보
2. 사용자가 주간/월간 실행 시점을 놓치지 않도록 "수동 호출 전 알림" 체계 도입
3. Claude Code `CronCreate` 세션 전용 제약으로 원안 폐기, 서버 APScheduler로 전환

## 현재 코드 상태 (파일:라인)

### scheduler.py 기존 구조
- `scheduler.py:46` — `_KST_TZ = "Asia/Seoul"` 상수
- `scheduler.py:50-58` — `_skip_on_holiday` 데코레이터 (휴장일 스킵)
- `scheduler.py:228-235` — 기존 `_run_post_trade_analysis` 잡 등록 패턴
  ```python
  self.scheduler.add_job(
      self._run_post_trade_analysis,
      CronTrigger(hour=17, minute=0, day_of_week='mon-fri', timezone=_KST_TZ),
      id='post_trade_analysis',
      name='매매 사후 분석',
  )
  ```
- `scheduler.py:248-253` — 주간 매매 복기 `_run_weekly_trade_review` 금 17:30 (중복 시점 주의)
- `scheduler.py:397` — `_run_post_trade_analysis()` 핸들러 구현 예시
- `scheduler.py:473` — `_run_weekly_trade_review()` 핸들러 구현 예시

### telegram_notifier.py 기존 구조
- `modules/reporter/telegram_notifier.py:37-79` — `TelegramNotifier` 클래스
- `send_message()` 공용 메시지 전송 메서드 기존 보유 (추가 함수는 이를 래핑)

### 에이전트 정의 관련 섹션
- `.claude/agents/trade-improvement-analyst.md:183-193` — 실패/엣지 케이스 테이블
- `.claude/agents/trade-improvement-analyst.md:24` — 표본 임계값 규칙

## 핵심 스니펫

### CronTrigger 패턴 (KST 강제)
```python
# scheduler.py:230
CronTrigger(hour=17, minute=0, day_of_week='mon-fri', timezone=_KST_TZ)
```
**Phase 3에서 추가할 것**:
```python
# 주간 리마인더 (금 17:45)
CronTrigger(hour=17, minute=45, day_of_week='fri', timezone=_KST_TZ)

# 월간 리마인더 (매월 1일 09:00)
CronTrigger(hour=9, minute=0, day='1', timezone=_KST_TZ)
```

### 공휴일 가드
기존 잡들은 `@_skip_on_holiday` 데코레이터로 장 휴장일 스킵. 리마인더는 **스킵 대상 아님** (공휴일이어도 사용자 행동 유도는 필요). 의도적으로 데코레이터 미적용.

## 영향 범위

### 직접 영향
- `scheduler.py`: 2개 add_job + 2개 async 핸들러 추가 (~35줄)
- `telegram_notifier.py`: 1개 헬퍼 메서드 추가 (~12줄)
- `change_log.md`: 3행 추가
- `.claude/agents/trade-improvement-analyst.md`: 1줄 추가

### 간접 영향
- 텔레그램 채팅에 추가로 주간 1건/월간 1건 메시지 수신 (사용자 체감 증가)
- APScheduler 잡 수 +2 (기존 약 15개 → 17개). 리소스 영향 무시 가능.

### 비영향
- 매매 로직 (trading_engine, optimizer, calculators, portfolio_monitor): 수정 없음
- 기존 스케줄 잡의 시각: 수정 없음 (충돌 점검만)

## 시각 충돌 점검

| 시간 | 기존 잡 | Phase 3 추가 | 충돌 |
|-----|--------|-------------|-----|
| 금 17:30 | 주간 매매 복기 | — | — |
| 금 17:45 | — | **주간 개선 리마인더** | 없음 (15분 간격) |
| 매월 1일 08:30 | 테마 분석 (화요일만 실행) | — | — |
| 매월 1일 09:00 | — | **월간 개선 리마인더** | 없음 (월간은 요일 무관) |
| 매월 1일 09:05 | 종목 스크리닝 | — | 근접하지만 다른 잡 |

## 과거 버그/주의점
1. **UTC/KST 혼동**: 서버가 UTC라 `CronTrigger`에 `timezone=_KST_TZ` 명시 누락 시 9시간 어긋남 (MEMORY.md 기록)
2. **`day_of_week`와 `day` 구분**: 주간은 `day_of_week='fri'`, 월간은 `day='1'` (날짜) — 혼동 금지
3. **주간 매매 복기(17:30) 종료 시간 불확정**: 복기 처리가 15분 이상 걸릴 수 있으므로 17:45 리마인더가 겹칠 수 있음. 메시지 큐는 독립적이므로 기능적 충돌은 없음.
4. **텔레그램 `send_message()` 기존 시그니처**: `telegram_notifier.py:37` 에 이미 공용 메서드 존재. 새 헬퍼는 단순 포맷팅 래퍼로 충분.

## 리마인더가 "스케줄 잡이 아닌 단순 알림"인 이유
- 에이전트 호출은 Claude Code 세션 내에서만 가능 → 서버 프로세스(봇)가 직접 `/improve` 실행 불가
- 대안 경로:
  - ❌ **CronCreate**: 세션 활성 시에만, 7일 만료
  - ❌ **Claude CLI headless**: 존재/안정성 미검증, API 비용 리스크
  - ✅ **텔레그램 리마인더 + 사용자 수동 실행**: 안전, 검증된 인프라, 판단 게이트 유지
- 완전 자동화는 Phase 4로 이관.
