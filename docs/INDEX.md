# 프로젝트 문서 목차 (INDEX)

> 작업 시작 시 이 파일을 먼저 확인하고, 해당 작업에 필요한 챕터만 읽는다.

## 프로젝트 규칙
| 문서 | 설명 | 언제 보는가 |
|------|------|------------|
| `CLAUDE.md` (프로젝트 루트) | 프로젝트별 규칙 (서비스 운영, 코드 규칙, 테스트) | 항상 (자동 로드됨) |
| `~/.claude/CLAUDE.md` | 글로벌 규칙 (워크플로우, 품질검사, 단축명령어) | 항상 (자동 로드됨) |

## 아키텍처 & 운영
| 문서 | 설명 | 언제 보는가 |
|------|------|------------|
| `docs/ARCHITECTURE.md` | 전체 시스템 아키텍처 | 시스템 구조 파악, 새 기능 추가 시 |
| `docs/OPERATIONS.md` | 운영 가이드 (배포, 모니터링) | 서비스 운영/배포 관련 작업 시 |
| `docs/dashboard-guide.md` | 웹 대시보드 가이드 | 대시보드 수정/확인 시 |

## 전략 & 백테스트
| 문서 | 설명 | 언제 보는가 |
|------|------|------------|
| `docs/BACKTEST_RESULTS.md` | 전략 백테스트 결과 종합 (테마·52주·파라미터) | 전략 변경/최적화 시 |
| `docs/phase2-4-roadmap.md` | 테마 점수 시스템 고도화 로드맵 (Phase 1~5) | 전략 개선 계획 수립 시 |
| `docs/backtest_turtle_results.md` | 한국 주식 터틀 전략 백테스트 | 터틀 전략 비교 참고 |
| `docs/backtest_us_turtle_results.md` | 미국 주식 터틀 전략 백테스트 | 해외 전략 비교 참고 |

## 버그 & 이슈
| 문서 | 설명 | 언제 보는가 |
|------|------|------------|
| `docs/BUG_REPORT_2026-02-06.md` | UTC→KST 타임존 버그 보고서 | 타임존 관련 이슈 발생 시 |

## 작업 계획 (Work Plans)
| 경로 | 설명 |
|------|------|
| `docs/work-plans/active/` | 진행 중인 작업의 3문서 (PLAN/CONTEXT/CHECKLIST) |
| `docs/work-plans/completed/` | 완료된 작업 아카이브 |

### 진행 중 작업
- (없음)

### 완료된 작업
- `20260315_dashboard-mobile-responsive/` — 모바일 반응형 + Cloudflare Tunnel
- `20260314_dashboard-theme-supply-fix/` — 테마 수급비율 표시 버그 수정
- `20260313_api-fallback-bugfix/` — API 폴백 버그 수정
- `20260313_theme-analysis-pipeline/` — 테마 분석 파이프라인 강화
- `20260312_dashboard-readonly-telegram-sell/` — 읽기전용 대시보드 + 텔레그램 매도
- `20260309_theme-category/` — 테마 카테고리 자동분류
- `20260309_theme-overheat-penalty/` — 테마 과열 감점 시스템
- `20260309_theme-retention/` — 테마 연장 로직
- `20260306_turtle-comparison-backtest/` — 한국 터틀 전략 백테스트
- `20260306_us-turtle-backtest/` — 미국 터틀 전략 백테스트
- `20260303_theme-selection-improvement/` — 테마 선정 정규화·매칭 개선
- `20260303_post-trade-analyzer/` — 매매 사후 분석기
- `20260303_fix-5-issues/` — 5개 버그 일괄 수정
- `20260303_datetime-holiday-fix/` — datetime·공휴일 처리
- `20260226_db-migration-v8/` — DB 스키마 v8 마이그레이션
