# PLAN — CLAUDE.md 정리 및 계층 분배

## 목표
- 글로벌/프로젝트/시스템 3계층으로 CLAUDE.md를 재구성
- 각 CLAUDE.md 파일을 **200줄 이하**로 유지
- 오래되거나 실효성 없는 지시사항 제거

## 배경
- `~/.claude/CLAUDE.md`가 212줄로 상한 초과
- MCP 서버 상세 사용법이 프로젝트 CLAUDE.md에 혼재 → 필요 시에만 참조하는 구조로 분리
- "각 시스템 단" 규칙 (web 대시보드, 전략 모듈 등)이 프로젝트 CLAUDE.md에 흩어질 여지가 있어 사전 분리

## 구현 단계

### Step 1 — 글로벌 CLAUDE.md 슬림화 (`~/.claude/CLAUDE.md`)
- [ ] "Superpowers 참고" 출처 표기 제거
- [ ] "자동 매뉴얼 시스템 > Hooks 자동 트리거 원칙" 섹션 축약 (실제 hooks 구현 없음)
- [ ] "권장 프로젝트 구조" 섹션 제거 (프로젝트 INDEX.md가 전담)
- [ ] Plan-Driven "프로젝트에 템플릿이 없을 때" 축약
- [ ] 셀프 체크 리마인더 5→3개 핵심만
- [ ] 최종 200줄 이하 확인

### Step 2 — 프로젝트 CLAUDE.md 분리 (`korean_stock_ai_trading/CLAUDE.md`)
- [ ] MCP 서버 상세 사용법을 `docs/mcp-usage.md`로 추출
- [ ] 프로젝트 CLAUDE.md에는 "3개 MCP 있음, 상세는 docs/mcp-usage.md" 요약만 유지
- [ ] 서비스 운영/계정/코드 규칙 유지

### Step 3 — 시스템별 CLAUDE.md 신설
- [ ] `web/CLAUDE.md` 생성 — 대시보드 특화 규칙 (SSE, JWT, passlib 금지 등)
- [ ] `modules/CLAUDE.md` 생성 — 전략/스코어링 모듈 규칙 (NaN 방어, 점수 체계 참조 등)
- [ ] 각 50줄 이하로 간결하게

### Step 4 — INDEX 및 메타 업데이트
- [ ] `docs/INDEX.md`에 새 문서 추가 (mcp-usage.md, 시스템별 CLAUDE.md)
- [ ] `docs/INDEX.md` 완료 작업 목록에 이번 작업 추가 준비

### Step 5 — 검증 및 아카이브
- [ ] 모든 CLAUDE.md 파일 줄수 확인 (≤200)
- [ ] 글로벌 원칙과 프로젝트 규칙 간 충돌/중복 재검토
- [ ] active → completed 아카이브

## 변경 파일 목록
**수정**
- `~/.claude/CLAUDE.md` — 슬림화
- `/home/hatni/korean_stock_ai_trading/CLAUDE.md` — MCP 상세 분리
- `/home/hatni/korean_stock_ai_trading/docs/INDEX.md` — 새 문서 추가

**신규**
- `/home/hatni/korean_stock_ai_trading/docs/mcp-usage.md`
- `/home/hatni/korean_stock_ai_trading/web/CLAUDE.md`
- `/home/hatni/korean_stock_ai_trading/modules/CLAUDE.md`

## 접근 방식
- 문서 리팩터링이므로 런타임 영향 없음 → 순차 진행
- 각 단계 완료 시 파일 줄수 `wc -l`로 체크

## 롤백 계획
- Git으로 추적되는 파일만 수정하므로 `git diff` / `git checkout -- <file>`로 롤백
- 글로벌 CLAUDE.md는 git 미추적일 수 있음 → 수정 전 원본을 `~/.claude/CLAUDE.md.bak`로 백업

## 완료 기준
1. 모든 CLAUDE.md 파일 ≤200줄
2. 중복/오래된 지시사항 제거됨
3. 3계층(글로벌 / 프로젝트 / 시스템) 분배 완료
4. INDEX.md 업데이트 완료
5. `active/` → `completed/` 아카이브 완료
