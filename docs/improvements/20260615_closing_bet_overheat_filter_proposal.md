---
analysis_period: 2026-05-04 ~ 2026-06-12
mode: focus:closing_bet_overheat
sample_size: 33 (atr_overheat 거부·라벨완료) / 361 (recommended·라벨완료)
generated_at: 2026-06-15 09:50 KST
---

# 종가베팅 atr_overheat 과열필터(1.8 하드필터) 재검토 제안서

> **요지**: `atr_overheat > 1.8` 하드필터가 거부한 후보들이 추천 후보보다 익일 성과가 압도적으로 좋다(아침고가 +9.85% vs +3.84%, net_ev+ 88% vs 63%). 다만 거부 그룹 내부는 **비단조(non-monotonic)** — 단순 임계 상향은 최악 구간(1.8~2.2)을 먼저 푸는 역효과를 낸다. 임계 조정이 아니라 **과열도 밴드별 차등 처리(2.2+ 예외 허용 / 1.8~2.2 유지)** 를 제안한다. 표본 33건·Low 신뢰도 — 본 제안은 즉시 운영 적용이 아니라 **드라이런 관찰 + 후속 검증** 게이트를 전제로 한다.

---

## 1. 현황

### 1-1. 필터 위치와 임계값
- **코드**: `closing_bet_system/engines/signal_score_engine.py:336-357` (`score()` 내부 하드필터)
  ```
  elif atr_oh_f > self.atr_overheat_max:
      excluded = True
      excl_reason = f"atr_overheat={atr_oh_f:.3f} > {self.atr_overheat_max} (PRD 하드 필터)"
  ```
- **기본 임계값**: `DEFAULT_ATR_OVERHEAT_MAX = 1.8` (signal_score_engine.py:79)
- **설정 경로**: `score_settings.get("atr_overheat_max", DEFAULT_ATR_OVERHEAT_MAX)` (line 246). 현재 `settings.yaml`의 `score:` 섹션에 `atr_overheat_max` 키가 **없으므로 코드 default 1.8 사용**. 즉 settings.yaml 1줄 추가만으로 임계 조정 가능(코드 수정 불요).
- **None 처리**: `atr_overheat`가 None이면 보수적 차단(EXCLUDED). 본 제안 범위 밖(33건 중 atr 측정불가 2건은 별도).
- **PRD 근거**: `종가베팅_트레이딩_시스템_PRD_v2.0.md:170`
  > "ATR 과열도 = 당일 상승폭 / ATR, **1.8 초과 시 제외 (구 +5% 룰 대체)**"
  → 단일 임계 하드컷. 밴드/수급 예외 개념 없음.

### 1-2. 필터의 의도
- 당일 급등으로 ATR 대비 과열된 종목은 익일 차익실현 매물에 갭하락 위험이 크다는 가정 → 진입 차단.
- "구 +5% 룰 대체"는, 단순 절대등락률(+5%) 대신 변동성 정규화(상승폭/ATR)로 종목별 변동성 차이를 반영한 개선이었다.

---

## 2. 근거 데이터 (DB 재확인)

DB: `data/closing_bet.db`. 라벨 단위는 분수(0.0306 = +3.06%). MCP SQLite 서버는 메인 trading.db만 보므로 Python venv 폴백으로 조회.

### 2-1. 그룹 비교 (라벨 완료분)

```sql
SELECT COUNT(*) n,
  AVG(l.next_open_pct), AVG(l.next_morning_high_pct), AVG(l.next_morning_low_pct),
  AVG(l.label_gap_up), AVG(l.label_net_ev_positive), AVG(l.label_stop_risk)
FROM candidates c JOIN candidate_labels l ON c.candidate_id = l.candidate_id
WHERE <group>;
```

| 그룹 | n | 익일시가갭 | 아침고가 | 아침저가 | gap_up | net_ev+ | stop위험 |
|---|---|---|---|---|---|---|---|
| recommended | 361 | +0.81% | +3.84% | −2.61% | 50% | 63% | 62% |
| **atr_overheat 거부** | **33** | **+3.06%** | **+9.85%** | **−2.17%** | **73%** | **88%** | **45%** |
| entered (실거래) | 5 | +0.90% | +3.02% | −5.24% | 40% | 60% | 80% |

→ 거부 그룹이 **모든 핵심 지표에서 우월**. 평균 total_score도 2.88 vs 1.84로 더 높음(L1 1.00/L2 1.85). 즉 점수 자체는 강한데 과열 하나로 컷.

### 2-2. 🔴 비단조 발견 — 단순 임계 상향이 위험한 이유

거부 33건을 atr_overheat 밴드별로 분해:

```sql
SELECT COUNT(*) n, AVG(next_open_pct), AVG(next_morning_high_pct),
       AVG(next_morning_low_pct), AVG(label_net_ev_positive), AVG(label_stop_risk)
FROM candidates c JOIN candidate_labels l ON c.candidate_id=l.candidate_id
JOIN candidate_features f ON c.candidate_id=f.candidate_id
WHERE c.rejection_reason LIKE '%atr_overheat%'
  AND f.atr_overheat > <lo> AND f.atr_overheat <= <hi>;
```

| atr_overheat 밴드 | n | 익일시가갭 | 아침고가 | 아침저가 | net_ev+ | stop위험 |
|---|---|---|---|---|---|---|
| 1.8 ~ 2.0 | 7 | +0.25% | +5.12% | **−5.37%** | 71% | **86%** |
| 2.0 ~ 2.2 | 6 | +1.23% | +6.51% | **−5.23%** | 83% | 67% |
| **2.2 ~ 2.5** | 13 | +5.41% | +11.13% | **−0.39%** | 92% | **31%** |
| **2.5+** | 5 | +4.02% | **+18.60%** | **+2.52%** | 100% | **0%** |

**핵심**: 위험이 과열도와 단조 증가하지 않는다. **중간 과열(1.8~2.2)이 가장 위험**(stop 67~86%, 아침저가 −5%대), **극과열(2.2+)이 가장 안전하고 상방도 크다**(stop 0~31%, 아침고가 +11~18%).

→ 해석: 2.2+는 강한 재료/수급으로 추세가 다음날까지 이어지는 "진짜 모멘텀"이고, 1.8~2.2는 ATR 대비 애매하게 부푼 "변동성만 큰" 종목일 가능성. **따라서 "임계를 2.2로 올린다"는 순진한 조정은, 정작 좋은 2.2+는 여전히 거르면서 최악의 1.8~2.2만 풀어주는 역효과**를 낸다.

### 2-3. 수급/강도 예외 후보 검증

```sql
... WHERE c.rejection_reason LIKE '%atr_overheat%' AND <cond>;
```

| 조건 (거부 33건 중) | n | 익일시가갭 | 아침고가 | 아침저가 | net_ev+ | stop위험 |
|---|---|---|---|---|---|---|
| atr_overheat ≥ 2.2 (밴드) | 18 | +5.03% | +13.21% | +0.42% | 94% | **22%** |
| close_strength ≥ 0.7 | 20 | +3.95% | +10.80% | −0.50% | 85% | 45% |
| atr_overheat ≥ 2.2 AND close_strength ≥ 0.7 | 11 | +5.20% | +12.88% | +0.42% | 91% | 27% |

→ 가장 깨끗한 단일 판별자는 **`atr_overheat ≥ 2.2`** 자체(n=18, stop 22%). close_strength 단독은 변별력이 약함. `foreign_net_buy_3d`는 스케일/부호 노이즈가 커(억원 미정규화 음수 다수) 본 제안의 예외 조건에서는 제외 권고.

### 2-4. 표본 다양성
- 거부 33건은 **17개 거래일**에 분산(전체 라벨 23거래일 중). 특정 1~2일 쏠림 아님 → 단일 이벤트 왜곡 가능성은 낮음.

---

## 3. 제안 (3안, 상호 배타 아님 — 권고 우선순위순)

> 모든 안의 정량 추정은 **익일 라벨(고점매도 가정)** 기반이며 실현 보장이 아니다(§5 리스크 참조). 또한 종가베팅의 더 큰 손실 원인은 **청산 로직(저점 시장가 투매)** 임이 별도 분석에서 확인됨 — 본 필터 완화는 청산 개선과 **병행**되어야 효과가 실현된다.

### 제안 A (1순위·권고): 밴드 차등 — `2.2 이상은 예외 허용`, `1.8~2.2는 유지`
- **현재**: `atr_overheat > 1.8` → 무조건 EXCLUDED.
- **제안**: 2단 구간으로 분리.
  - `atr_overheat ≤ 1.8`: 통과(기존과 동일)
  - `1.8 < atr_overheat < 2.2`: **EXCLUDED 유지**(데이터상 최악 구간)
  - `atr_overheat ≥ 2.2`: **하드컷 해제, 통과 후보로 진입**(점수/임계는 기존 게이트 적용)
- **before/after**: 1.8 단일컷(2개 구간) → 1.8/2.2 이중 경계(3개 구간, 가운데만 차단).
- **33건 적용 시 기대효과**: 새로 허용되는 후보 = 2.2+ 18건. 이 18건 익일 아침고가 평균 **+13.21%**, net_ev+ **94%**, stop위험 **22%**(저가 평균 +0.42%로 갭하락 거의 없음). 여전히 차단되는 1.8~2.2 13건은 stop위험 67~86%·저가 −5%대로 차단 정당.
- **신뢰도**: **Low** (표본 18건, 통계 유의성 미검증, 낙관 라벨 기반).

### 제안 B (2순위·대안): 하드필터 → 점수 감점(soft penalty) 전환
- **현재**: 하드 EXCLUDED.
- **제안**: 과열도를 EXCLUDED 대신 `total_score` 감점으로 전환하여 진입 게이트(`entry_executor.score_threshold`)가 최종 판단하게 함. 예: `1.8 < oh ≤ 2.2 → −1점`, `oh > 2.2 → 0점(감점 없음)`. 감점 후 임계(현 NORMAL 2 / CRISIS 3) 미달이면 자연 탈락.
- **근거**: §2-2 비단조성을 점수에 그대로 반영. 중간 과열에 페널티를 주되 극과열은 면제하여, 임계 게이트가 다른 강한 신호와 합산해 최종 결정.
- **before/after**: `EXCLUDED(이진)` → `score 감점(연속)`. 게이트 일원화로 "과열이지만 L2/L3 만점" 같은 강후보를 살릴 수 있음.
- **33건 적용 시 기대효과**: 2.2+ 18건 중 잔여 점수가 임계를 넘는 후보만 진입(현 total_score 평균 2.88 → 임계 2~3 근방, 일부만 통과). 제안 A보다 **보수적**(자동 18건 전부 허용 아님). 1.8~2.2는 −1점으로 대부분 탈락.
- **신뢰도**: **Low**. 단, 하드컷보다 **롤백·튜닝이 쉬움**(감점 폭만 조정).

### 제안 C (보조): 임계 단순 상향 (1.8 → 2.2)
- **현재**: 1.8.
- **제안**: `atr_overheat_max: 2.2` (settings.yaml 1줄).
- **⚠️ 비권고 이유**: §2-2에 따라 이 안은 **2.2+ 좋은 18건을 여전히 거르면서**, 새로 푸는 것은 1.8~2.2 13건(stop위험 67~86%, 저가 −5%대 = 최악 구간)뿐이다. **방향이 정반대**. 만약 굳이 단일 임계만 쓴다면 `2.2 → ∞`(사실상 상단 컷 제거) 쪽이 데이터와 일치하나, 그 경우 극단 과열(oh>3.0, 6건)의 꼬리 위험을 별도 관리해야 함.
- **결론**: 단일 임계 조정으로는 데이터를 표현 불가. **A 또는 B 채택 권고, C는 기각 후보.**

---

## 4. 권고 결정
- **1순위 제안 A**(밴드 차등 예외)를 **드라이런 관찰 대상**으로. 구현 시 `1.8 < oh < 2.2` 차단 + `oh ≥ 2.2` 통과.
- **B(soft penalty)** 는 게이트 일원화·튜닝 용이성 측면에서 더 견고하므로, 청산 로직 개선 이후 **본 채택 후보**로 병행 검토.
- **C 기각**(데이터 방향 불일치).
- **전제 조건**: 본 완화는 청산 로직 개선(저점 시장가 투매 차단) 제안서와 **세트**로만 운영 적용. 청산이 그대로면 좋은 진입을 늘려도 §3 실거래 5건처럼 저점에 던져 엣지가 파괴된다.

---

## 5. 리스크 (필수 명시)

1. **낙관 라벨 한계**: `net_ev_positive`/`next_morning_high_pct`는 "익일 오전고가가 비용선·목표에 **도달**"이라는 고점매도 가정 라벨이다. **실현 보장 아님.** §3 실거래 5건은 라벨이 좋아도(아침고가 +3~7%) 청산이 저점 시장가라 −3~4% 실현했다. 본 제안의 +13% 류 수치는 **상한(ceiling)** 으로 읽어야 한다.
2. **표본 절대 부족**: 거부 33건, 2.2+ 18건. **Low 신뢰도.** 통계적 유의성 미검증, 25거래일·강세장 편중(별도 분석 §4: 관찰기간 거의 내내 KOSPI 강세). 하락장에서 과열 종목은 갭하락 위험이 급증할 수 있다 — **현 데이터로는 하락장 거동을 알 수 없다.**
3. **과열 = 변동성**: stop위험이 2.2+에서 22%로 낮긴 하나 0이 아니다. oh>3.0(6건)의 극단 꼬리는 갭하락 시 손실폭이 크다. 제안 A 채택 시에도 상단 무제한 허용보다 **상한 가드(예 oh ≤ 3.5)** 병행 검토 권장.
4. **생존 편향 우려**: 거부된 후보는 실제 진입·청산을 거치지 않았다. "거부했더니 좋았다"는 사후 라벨일 뿐, 실제 진입 시 슬리피지(현 vwap+2% 지정가)·체결 실패·청산 타이밍이 라벨을 갉아먹는다.
5. **수급 예외 부적합**: `foreign_net_buy_3d`는 억원 미정규화로 스케일/부호 노이즈가 커 예외 조건으로 부적합(§2-3). close_strength 단독도 변별력 약함 → 예외는 **atr_overheat 밴드 자체**로만 설계 권고.

---

## 6. 롤백 계획
- **제안 A**: signal_score_engine 하드필터 분기를 2단 구간으로 변경 시, `atr_overheat_max` 기존 1.8 단일 동작으로 즉시 원복 가능(분기 조건 1줄). settings.yaml에 `atr_overheat_max: 1.8` 명시 + `atr_overheat_band_exception: false` 류 토글 설계 권장 → 토글 off + systemctl restart로 NO-OP.
- **제안 B**: 감점 로직은 `total_score`에만 영향 → 감점 폭 0 또는 토글 off로 기존 하드컷 복귀.
- **제안 C**(채택 시): settings.yaml `atr_overheat_max` 1줄 원복.
- 어느 안이든 **드라이런(`entry_executor.dry_run=true`) 상태에서 먼저 검증** — 실발주 전 후보 통과 흐름만 로그로 확인.

---

## 7. 검증 계획
1. **드라이런 1~2주**: 제안 A/B 적용 후 `candidate_status`에 2.2+ 후보가 recommended/entered로 전환되는지, rejection_reason 분포 변화 확인.
2. **반사실 추적**: 새로 통과한 2.2+ 후보의 익일 라벨(`candidate_labels`)을 계속 수집 → 표본 18 → 30+ 누적 시 신뢰도 재평가(operational_review 게이트 30건).
3. **밴드 안정성 재검증**: 표본 증가분으로 1.8~2.0 / 2.0~2.2 / 2.2~2.5 / 2.5+ stop위험·아침저가 분포가 §2-2 패턴을 유지하는지 확인. 비단조성이 표본 노이즈였는지 구조인지 판별.
4. **청산 연동 확인**: 청산 개선 제안서 배포 후, 동일 후보의 **실현 손익**(net_pnl_pct)이 라벨에 수렴하는지 측정. 라벨↔실현 갭이 크면 본 완화 효과는 무효.
5. **하락장 가드**: MarketGuard DANGER/CRISIS 발동일에 2.2+ 후보가 어떻게 거동하는지 별도 추적(현 데이터엔 하락일 거의 없음).

---

## 8. 다음 사이클 관찰 항목
- [ ] 2.2+ 통과 후보 익일 라벨 누적 18 → 30건 도달 여부
- [ ] 1.8~2.2 차단 구간이 계속 최악인지(stop위험 60%+) 재확인
- [ ] foreign_net_buy_3d 단위 정규화(억원) 후 수급 예외 변별력 재평가
- [ ] 청산 로직 개선 제안서와 세트 배포 여부(병행 필수)
- [ ] 하락장(MarketGuard DANGER+) 발동일 과열 후보 거동 추적

---

## 9. 메타 정보
- **Claude API 추가 호출**: 아니오. 정량 비교는 전부 SQL 직접 집계.
- **기존 분석 흡수**: `docs/improvements/20260615_closing_bet_candidate_full_analysis.md` §2의 "과열필터가 최고 종목을 거른다" 발견을 입력으로 사용, 본 제안서는 그 위에 **밴드별 비단조 분해(§2-2)** 와 **예외 설계(§3)** 를 추가. 원 분석의 "완화 또는 하드필터→점수 감점 전환" 권고를 정량 검증·구체화.
- **데이터 출처**: `data/closing_bet.db` (메인 repo 절대경로). MCP SQLite 서버 범위 밖이라 Python venv 폴백 조회.
- **라벨 단위**: 분수(0.0306 = +3.06%) — 본문 표는 ×100 표기.
- **민감 데이터**: 계좌 잔고 실수치·주문 ID·앱키 미포함(종목코드/종목명/통계만).
- **JSON 파싱 실패**: 해당 없음(candidate_labels는 정규 컬럼).
- **신뢰도 총평**: 전 제안 **Low**(표본 33/18, 강세장 편중, 낙관 라벨). 즉시 운영 적용 아님 — 드라이런 + 후속 누적 검증 전제.

---

### 부록: 재현 쿼리 (핵심)
```sql
-- 그룹 비교
SELECT c.candidate_status, c.rejection_reason, COUNT(*) n,
  AVG(l.next_open_pct), AVG(l.next_morning_high_pct),
  AVG(l.label_net_ev_positive), AVG(l.label_stop_risk)
FROM candidates c JOIN candidate_labels l ON c.candidate_id=l.candidate_id
GROUP BY c.candidate_status;

-- 밴드별 비단조 (핵심)
SELECT
  CASE WHEN f.atr_overheat<=2.0 THEN '1.8-2.0'
       WHEN f.atr_overheat<=2.2 THEN '2.0-2.2'
       WHEN f.atr_overheat<=2.5 THEN '2.2-2.5'
       ELSE '2.5+' END band,
  COUNT(*) n, AVG(l.next_morning_low_pct), AVG(l.label_stop_risk)
FROM candidates c JOIN candidate_labels l ON c.candidate_id=l.candidate_id
JOIN candidate_features f ON c.candidate_id=f.candidate_id
WHERE c.rejection_reason LIKE '%atr_overheat%'
GROUP BY band;
```
