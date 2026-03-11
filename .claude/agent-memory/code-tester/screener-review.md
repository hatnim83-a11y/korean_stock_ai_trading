# screener.py 리뷰 노트

## 2026-03-10 폴백 로직 추가 검증

### 확인된 사항
- py_compile 통과 (syntax OK)
- runtime import: _THEME_TO_UPJONG_ALIAS, _search_naver_upjong, run_daily_screening 모두 정상
- search_naver_theme 반환에 'url' 키 존재 — import 경로 정상 (crawlers.py line 792)
- crawl_naver_theme_stocks: 업종 URL (type=upjong) 입력 시 종목 수집 가능 (실제 테스트 확인)

### 발견된 이슈

#### change_rate 오파싱 (주의)
업종 상세 페이지(type=upjong)의 컬럼 구조:
- cols[0]: 종목명 (테마와 동일)
- cols[1]: 현재가 "124,100" (테마와 동일 → 파싱 정상)
- cols[2]: "상승10,100" (등락폭+한글 혼합) — _safe_float → 0.0 반환
- cols[3]: "+8.86%" (실제 등락률, 파싱 안 됨)

테마 상세 페이지(type=theme)의 컬럼 구조:
- cols[1]: 테마 편입 사유 (텍스트)
- cols[2]: 현재가 "49,150"

즉, 같은 crawl_naver_theme_stocks 함수에 업종 URL을 넣으면:
- 종목 코드: 정상 수집
- price: cols[1] → "상승10,100".replace(",","") = "상승10100" → _safe_int("상승10100") = 0
- change_rate: cols[2] = 현재가 → _safe_float("49150") = 49150.0 (비정상값)

**실질 영향**: screener에서 change_rate와 price 미사용(KIS API에서 재조회) → 스크리닝 필터 영향 없음
→ 종목 코드 수집 자체는 정상, 부가 정보만 오염 (허용 가능 수준)

#### theme dict side-effect (주의)
```python
theme["url"] = theme_url  # line 528, 534
```
원본 dict 직접 수정. main.py에서 `self.today_themes`가 같은 객체를 참조하므로
run_daily_screening 호출 후 today_themes의 url이 업종 URL로 오염될 수 있음.

실질 영향: 이후 run_daily_screening을 같은 today_themes로 재호출 시 폴백 없이 업종 URL 직접 사용.
단, 동일 장중 재호출 시나리오가 현재 없으므로 실용적 위험은 낮음.
권장 수정: `theme.get("url") or theme_url` 조건으로 덮어쓰기 제한, 또는 별도 변수 사용.

#### [:20] 하드코딩 (주의)
```python
theme_stocks[theme_name] = stock_codes[:20]  # line 540
```
MAX_STOCKS_PER_THEME=10 상수가 있으나 크롤링 풀(20)에 별도 상수 없음.
스크리닝 풀 크기가 선정 수의 2배로 고정됨. config 상수화 권장.

#### partial match 거짓양성 (참고)
"반도체" → "반도체와반도체장비" 업종 매칭 (dict 순서에 의존)
dict 삽입 순서 보장(Python 3.7+)이지만, 네이버 페이지 순서가 바뀌면 매칭 결과 변경 가능.
현재 "AI반도체", "K-방산", "2차전지", "바이오" 등 주요 테마는 모두 no_match → 폴백 성공 없음.
폴백이 실제로 작동하는 케이스: "해운", "물류", "운송" (alias), "반도체" (partial) 정도.

### 기존 동작 영향 없음 확인
- URL 있는 테마: 1차 조건(`if not theme_url:`) 진입 안 함 → 완전히 기존 경로 유지
- 폴백 모두 실패(url 없음) 시: theme_stocks[theme_name] = [] → screen_all_themes에서 경고 후 skip
  (기존 동작과 동일)
- try/except에 모든 폴백 포함 → 네트워크 실패 시 graceful degradation 확인

### _THEME_TO_UPJONG_ALIAS 키 vs normalize_theme_name
- normalize_theme_name('해운') → '해운' (일치 OK)
- normalize_theme_name('물류') → '물류' (일치 OK)
- normalize_theme_name('운송') → '운송' (일치 OK)
- alias dict에 없는 테마는 2단계(partial match)로 진행

### requests vs httpx 혼용
- crawlers.py: httpx 사용
- _search_naver_upjong: requests 사용 (함수 내 lazy import)
- 두 라이브러리 모두 venv에 설치됨 (requests 2.32.5)
- 기능 문제 없으나 일관성 위해 httpx 통일 권장 (참고 수준)
