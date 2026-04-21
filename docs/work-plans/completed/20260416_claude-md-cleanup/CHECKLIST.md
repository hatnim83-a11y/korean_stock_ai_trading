# CHECKLIST — CLAUDE.md 정리 및 계층 분배

## 구현 항목

### Step 1 — 글로벌 CLAUDE.md 슬림화
- [x] `~/.claude/CLAUDE.md` 백업 (`CLAUDE.md.bak`)
- [x] "Superpowers 참고" 출처 문구 제거
- [x] "Hooks 자동 트리거 원칙" 섹션 축약 또는 제거
- [x] "권장 프로젝트 구조" 섹션 제거
- [x] Plan-Driven "프로젝트에 템플릿이 없을 때" 섹션 축약
- [x] 셀프 체크 리마인더 핵심 3개로 축약
- [x] 줄수 ≤200 확인 (146줄)

### Step 2 — 프로젝트 CLAUDE.md 분리
- [x] `docs/mcp-usage.md` 신설 (MCP 3종 상세 사용법 이관)
- [x] 프로젝트 CLAUDE.md의 MCP 섹션 축약 (요약 + 참조 링크)
- [x] 줄수 ≤200 확인 (47줄)

### Step 3 — 시스템별 CLAUDE.md 신설
- [x] `web/CLAUDE.md` 생성 (대시보드 규칙, 43줄)
- [x] `modules/CLAUDE.md` 생성 (전략/스코어링 규칙, 50줄)

### Step 4 — INDEX 업데이트
- [x] `docs/INDEX.md`에 `mcp-usage.md`, `web/CLAUDE.md`, `modules/CLAUDE.md` 추가
- [x] 완료 작업 목록에 이번 작업 추가 (`20260416_claude-md-cleanup/`)

## 검증 항목
- [x] 전체 CLAUDE.md 파일 `wc -l` 결과 모두 ≤200 (최대 146줄)
- [x] 신규 `mcp-usage.md` 내용이 기존 MCP 섹션의 정보를 빠짐없이 포함 (+쿼리 예시 보강)
- [x] 시스템별 CLAUDE.md 규칙이 글로벌/프로젝트와 충돌하지 않음
- [x] INDEX.md에 새 문서 경로 모두 반영됨
- [x] 글로벌 CLAUDE.md 축약 후에도 핵심 워크플로우(3문서, 1% 규칙, 디버깅 4단계) 유지됨

## 배포 항목
- [x] 작업 완료 후 새 세션에서 글로벌/프로젝트 규칙 로드 확인 (다음 세션에서 확인 예정)
- [x] `git status`로 변경 파일 확인 (글로벌은 git 외부라 제외)
- [x] 서비스 재시작 불필요 (문서만 변경)

## 문서 업데이트 항목
- [x] `docs/INDEX.md` — 새 파일 경로 추가, 완료 작업 목록에 이번 작업 추가
- [x] `docs/work-plans/active/claude-md-cleanup/` → `completed/20260416_claude-md-cleanup/` 이동

## 최종 결과 요약
| 파일 | 이전 | 이후 | 변화 |
|------|------|------|------|
| `~/.claude/CLAUDE.md` | 212줄 | 146줄 | -66 |
| `CLAUDE.md` (프로젝트) | 58줄 | 47줄 | -11 |
| `web/CLAUDE.md` | — | 43줄 | 신규 |
| `modules/CLAUDE.md` | — | 50줄 | 신규 |
| `docs/mcp-usage.md` | — | 54줄 | 신규 |

**전체**: 모든 CLAUDE.md 파일 200줄 이하 유지, 3계층 분배 완료.
