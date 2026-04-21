# web/ — 대시보드 규칙

## 스택
- **Framework**: FastAPI + Jinja2 + Chart.js(CDN) + SSE
- **Entry**: `web_server.py` (uvicorn, 루트)
- **Port**: 8501, systemd: `trading_dashboard.service`
- **Auth**: SHA-256+HMAC password → JWT cookie (24h), rate limit 5/min

## 주요 파일
| 파일 | 역할 |
|------|------|
| `app.py` | FastAPI 앱 초기화, 라우트 등록 |
| `auth.py` | 비밀번호 해시/검증, JWT 발급/검증 |
| `api_routes.py` | REST API (포트폴리오/테마/거래 조회) |
| `sse_routes.py` | SSE 엔드포인트 (실시간 업데이트) |
| `dashboard_service.py` | DB 조회 + 매도 실행 함수 집합 |
| `templates/dashboard.html` | 메인 대시보드 |

## 필수 규칙

### passlib 금지 — hashlib 사용
- **passlib + bcrypt 5.x 비호환**으로 `hashlib.pbkdf2_hmac('sha256', ...)` + `hmac.compare_digest` 조합 사용
- 새로 비밀번호/토큰 검증 추가 시 passlib 재도입 금지

### 읽기 전용 정책 (2026-03-12)
- Actions 탭/매도 API는 대시보드에서 **제거됨**
- 매도 기능은 **텔레그램**으로 이전 (`/sell`, `/sellall`, `/confirm`, `/cancel` — 30초 TTL)
- `execute_sell(stock_code, quantity, reason)` / `execute_sell_all(reason)`는 `dashboard_service.py`에 유지 (텔레그램이 호출)
- **새 UI 매도 기능 추가 금지** — 텔레그램 경로만 사용

### KISApi 싱글톤
- `dashboard_service.py`에 `_get_kis_api()`, `_get_order_api()` 싱글톤 패턴 적용됨
- 직접 인스턴스화 금지 — 싱글톤 getter만 사용 (토큰 1분 발급 제한)

### 환경변수 (운영 시)
- `DASHBOARD_PASSWORD`, `DASHBOARD_SECRET_KEY`, `DASHBOARD_PORT`

### DB 컬럼명 주의
- `daily_snapshots`의 총자본 컬럼은 **`total_capital`** (❌ `total_value` 아님)

## 변경 후 검증
- 대시보드 수정 시 `systemctl restart trading_dashboard.service`
- 브라우저 무통신 확인 + SSE 로그 확인
