# CLAUDE.md - 프로젝트 규칙

## 서비스 운영 규칙

### 반드시 systemd로만 구동
- 실행: `sudo systemctl start trading_system`
- 중지: `sudo systemctl stop trading_system`
- 재시작: `sudo systemctl restart trading_system`
- 상태: `sudo systemctl status trading_system`
- **절대 `nohup python main.py &` 또는 백그라운드 직접 실행 금지**
- 수동 테스트는 `python main.py --manual --test --real` (포그라운드, 1회성)만 허용

### 이중 실행 방지
- 서비스 시작/재시작 전 반드시 기존 프로세스 확인: `ps aux | grep main.py | grep -v grep`
- systemd 외 프로세스가 있으면 먼저 kill 후 서비스 시작
- PID 파일(`trading_system.pid`) 잔여 시 삭제 후 시작

## 계정 관리
- `.env` 파일에 활성/대기 계정 구분 (주석 처리로 전환)
- KIS API 토큰: 앱키당 1분에 1회 발급 제한 — `kis_api.py`와 `kis_order_api.py`가 `_shared_token`으로 공유

## 코드 규칙
- KIS API 응답 파싱 시 `_safe_int()`/`_safe_float()` 사용 (빈 문자열 방어)
- pandas 값 → float 변환 전 `pd.isna()` 체크 필수

## MCP 서버 활용 지침

프로젝트에 3개 MCP 서버가 `.mcp.json`에 등록되어 있다. 적극적으로 활용할 것.

### 1. SQLite (`mcp__sqlite__*`) — DB 조회/수정
- **DB 경로**: `data/trading.db` (래퍼: `scripts/mcp_sqlite.py`)
- **용도**: 포트폴리오, 거래내역, 테마, 성과 등 트레이딩 DB 직접 조회/수정
- **도구 목록**:
  - `list_tables` — 전체 테이블 목록
  - `describe_table` — 테이블 스키마 확인
  - `read_query` — SELECT 쿼리 (조회 전용)
  - `write_query` — INSERT/UPDATE/DELETE 쿼리
  - `create_table` — 테이블 생성
  - `append_insight` — 분석 인사이트 메모 저장
- **사용 시점**: 봇 상태 확인, 거래 분석, 데이터 검증, 스키마 확인 시 Python 코드 실행 대신 MCP로 직접 조회
- **주의**: write_query 실행 전 반드시 사용자 확인. 실서비스 DB이므로 함부로 수정 금지

### 2. Fetch (`mcp__fetch__fetch`) — 웹 페이지 조회
- **용도**: URL의 웹 페이지 내용을 마크다운으로 변환하여 조회
- **파라미터**: `url`(필수), `max_length`(기본 5000), `start_index`(이어읽기), `raw`(HTML 원본)
- **사용 시점**: API 문서 확인, 외부 참고자료 조회, 에러 관련 GitHub 이슈 확인 등
- **주의**: 사용자가 제공한 URL 또는 프로그래밍 관련 URL만 사용

### 3. Sequential Thinking (`mcp__sequential-thinking__sequentialthinking`) — 구조적 사고
- **용도**: 복잡한 문제를 단계별로 분석, 가설 생성 및 검증
- **사용 시점**: 복잡한 버그 원인 분석, 전략 설계, 아키텍처 결정 등 다단계 추론 필요 시
- **특징**: 중간에 이전 사고 수정, 분기, 추가 확장 가능

## 코드 변경 후 필수 프로세스
- **코드를 작성하거나 수정한 뒤 반드시 code-tester 에이전트로 검증**
- 에이전트 정의: `.claude/agents/code-tester.md`
- 수정된 파일을 대상으로 code-tester 에이전트 실행 → 심각/주의 이슈 발견 시 즉시 수정
- py_compile + 기존 테스트 통과 확인 후 서비스 재시작
