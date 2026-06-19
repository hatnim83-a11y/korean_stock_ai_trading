# 제안서: 종가베팅 총자산 조회 재시도 (get_total_account_value resilience)

- **작성일**: 2026-06-16
- **유형**: 인프라 견고성 강화 (전략 파라미터 변경 아님)
- **대상 파일**: `closing_bet_system/infra/kis_client.py` (1파일)
- **상태**: 제안 (미구현)

---

## 1. 배경 (Incident)

2026-06-15 종가베팅 1차 진입(15:18 KST)에서 **하나금융지주(086790)** 가 매수 거부됨.

- DB 기록 사유: `fund_guard:총 자산 조회 실패 또는 0원 (보수적 차단)`
- 근본 원인: 진입 순간 KIS Open API `inquire-balance`(잔고) 엔드포인트가 **HTTP 500** 반환
  ```
  2026-06-15 06:18:01 UTC (= 15:18:01 KST) | ERROR | kis_order_api:get_balance
  잔고 조회 중 오류: Server error '500 Internal Server Error' (inquire-balance)
  ```
  거부 시각(15:18:03 KST)과 정확히 일치.
- `get_total_account_value()` → `get_balance()`가 500을 흡수하고 `total_value=0` 반환 → `fund_guard`가
  "총자산 확인 불가 → 과주문 방지 위해 차단"이라는 **설계된 안전동작(fail-safe)** 으로 거부.

당일 KIS 500 장애는 종일 산발(00:05 / 04:39 / 06:18 / 08:07 UTC …), 장 마감 후 회복된 **일시 장애**였다.
즉 **자금/한도 문제도, 설정 버그도 아니다.** 진입 순간의 단발성 API 블립이 차단으로 이어진 것.

## 2. 문제 정의

현재 `get_total_account_value()`는 **단 1회** 조회 후 실패 시 즉시 0을 반환한다.

```python
# closing_bet_system/infra/kis_client.py:76-97 (현재)
def get_total_account_value() -> int:
    order_api = get_order_api()
    try:
        balance = order_api.get_balance()
        total = balance.get("total_value", 0)
        if not total or total <= 0:
            logger.warning("[종가베팅] 총 평가금액 조회 실패 또는 0원")
            return 0
        return int(total)
    except Exception as e:
        logger.error(f"[종가베팅] 총 평가금액 조회 예외: {e}")
        return 0
```

진입 윈도우(15:18~15:19, **약 60초**) 안의 단발 500은 재시도 한 번이면 대부분 회복되는데,
현재는 그 기회를 주지 않고 즉시 포기 → 그날의 유일한 시도가 날아간다.

**중요**: `get_balance()`는 500을 내부에서 흡수해 `total_value=0`을 반환하므로(예외를 다시 던지지 않음),
재시도는 **예외뿐 아니라 `total<=0` 조건도 함께 재시도 트리거**로 다뤄야 한다.

## 3. 목표

진입 윈도우 내 **단발성 KIS 500/타임아웃**에 대한 내성을 확보한다. 단,

- 차단의 **안전성은 절대 약화하지 않는다** — 끝까지 실패하면 여전히 0 반환(현행 fail-safe 유지).
- 진입 윈도우(60초)를 넘기지 않도록 **백오프를 짧게** 유지(타이밍 초과 시 진입 자체가 무의미).

## 4. 방안

### 방안 A — 단기 백오프 재시도 (권장, 1차)

`get_total_account_value()` 내부를 짧은 재시도 루프로 감싼다. `total<=0`과 예외를 동일하게 "재시도 대상"으로 취급.

```python
import time  # 모듈 상단 추가

# 재시도 파라미터 (진입 윈도우 60초 보호 — 짧게 유지)
_TOTAL_VALUE_MAX_RETRIES = 3            # 총 3회 시도
_TOTAL_VALUE_BACKOFF_SEC = (0.5, 1.0)   # 시도 간 대기 (마지막 시도 후엔 대기 없음)

def get_total_account_value() -> int:
    """총 평가금액 조회 (단발 KIS 블립 내성, 2026-06-16).

    실패/0원을 재시도 대상으로 보고 최대 _TOTAL_VALUE_MAX_RETRIES 회 시도.
    끝까지 실패 시 0 반환 (현행 fail-safe 유지 — fund_guard 가 보수적으로 차단).
    """
    order_api = get_order_api()
    for attempt in range(1, _TOTAL_VALUE_MAX_RETRIES + 1):
        try:
            balance = order_api.get_balance()
            total = balance.get("total_value", 0)
            if total and total > 0:
                return int(total)
            logger.warning(
                f"[종가베팅] 총 평가금액 0원/실패 (시도 {attempt}/{_TOTAL_VALUE_MAX_RETRIES})"
            )
        except Exception as e:
            logger.error(
                f"[종가베팅] 총 평가금액 조회 예외 (시도 {attempt}/{_TOTAL_VALUE_MAX_RETRIES}): {e}"
            )
        # 마지막 시도면 대기 없이 종료
        if attempt < _TOTAL_VALUE_MAX_RETRIES:
            time.sleep(_TOTAL_VALUE_BACKOFF_SEC[attempt - 1])
    logger.error("[종가베팅] 총 평가금액 조회 최종 실패 → 0원 반환 (보수적 차단)")
    return 0
```

- 최악 대기: `0.5 + 1.0 = 1.5초` + 실제 요청 3회. 500은 `raise_for_status()`로 즉시 실패(빠른 fail),
  타임아웃은 `httpx timeout=10s`. 1종목당 worst-case 수 초 수준 → 60초 윈도우 안전.
- `get_balance()` 내부 `_rate_limit()`가 호출 간격을 관리하므로 rate-limit 위반 없음.

### 방안 B — 직전 성공값 단기 캐시 (보완, 선택)

같은 진입 사이클에서 fund_guard는 후보 수만큼(현재 max 2종목) `get_total_account_value()`를 반복 호출한다.
직전 **성공값을 짧은 TTL(예: 30~60초)로 캐시**하면 (1) API 호출 절감 (2) 직전 성공값으로 블립 보완.

- 장점: 윈도우 내 2번째 호출이 첫 호출 성공값을 즉시 재사용 → 첫 호출만 성공하면 둘째는 0 위험 0.
- 주의: `total_value`는 진입 체결 전 1분 내에는 거의 불변이라 staleness 위험 낮음. 단 캐시는 **성공값만** 저장하고,
  진입 사이클 종료 후(또는 TTL 만료)엔 반드시 신선 조회하도록 TTL을 짧게.
- 방안 A와 **독립적·상호 보완**. A를 먼저 적용하고, 운영 관찰 후 필요 시 B 추가 권장.

### 권장

**방안 A 단독 적용**을 1차로 권장. 변경 최소(1파일·1함수), fail-safe 유지, 기존 토큰 발급 재시도 패턴
(`kis_order_api.py:191` max_retries=3 + `2**attempt`)과 일관. 방안 B는 효과/복잡도 관찰 후 후속.

## 5. 영향 범위 / 리스크

| 항목 | 분석 |
|---|---|
| 안전성 | 끝까지 실패 시 0 반환 = 현행과 동일. **차단 안전성 약화 없음** |
| 타이밍 | worst-case 추가 ~1.5초/호출. 60초 진입 윈도우 대비 무시 가능 |
| 부작용 범위 | `get_total_account_value()` 만 변경. `get_balance()`(스윙 공유) **불변** → 스윙 봇 무영향 |
| rate limit | `get_balance` 내부 `_rate_limit()`로 관리. 재시도 3회는 안전 |
| 호출처 | 현재 `fund_guard._get_total_value()` 단일 경로. 반환 계약(int, 실패=0) 불변 |

## 6. 롤백

- 코드 1함수 원복(재시도 루프 제거)으로 즉시 롤백 가능.
- 토글이 필요하면 `_TOTAL_VALUE_MAX_RETRIES = 1` 로 설정 시 현행과 동일 동작(재시도 없음).
  → 설정 상수화로 무코드 롤백 옵션 제공 가능(선택).

## 7. 테스트 계획

`tests/` 신규 또는 기존 종가베팅 테스트에 추가 (provider mock 주입):

1. **1회차 성공**: `get_balance`가 정상 dict 반환 → 재시도 없이 즉시 정상값, sleep 0회.
2. **2회차 회복**: 1회차 `total_value=0`, 2회차 정상 → 정상값 반환, sleep 1회.
3. **전회 실패(예외)**: 모든 시도 예외 → 0 반환, 로그 3회 + 최종 error 1회.
4. **전회 0원**: 모든 시도 `total_value=0` → 0 반환 (fail-safe).
5. **타이밍**: `time.sleep` mock으로 누적 대기 ≤ 1.5초 검증.
6. **회귀**: `fund_guard.allow_order`가 정상 total_value에서 기존과 동일 capital_limit 산출.

→ 구현 후 **code-tester 에이전트** 검증 필수(CLAUDE.md 규칙).

## 8. 변경 파일 목록

- `closing_bet_system/infra/kis_client.py` — `get_total_account_value()` 재시도 루프 + `import time` + 상수 2개
- `tests/test_closing_bet_total_value_retry.py` (신규) — 위 6개 케이스
- `docs/improvements/change_log.md` — 1줄 추가 (배포 시)

## 9. 미해결/후속 검토

- **방안 B(단기 캐시)** 효과·복잡도 관찰 후 후속 결정.
- 동일 패턴이 `get_orderable_cash()` / `get_held_stock_codes()` 에도 유효 → 필요 시 공통 재시도 헬퍼로 추출 검토.
- 근본적으로 KIS 500은 거래소 서버 측 이슈라 재시도는 **완화책**이지 제거책 아님. 장기 다발 장애엔 무력
  (그땐 fail-safe 차단이 정답).
</content>
