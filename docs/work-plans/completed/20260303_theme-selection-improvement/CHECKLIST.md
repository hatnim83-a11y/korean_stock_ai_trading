# Checklist: 테마 선정 개선

## 구현 항목

### Step 0: 테마 정의 정비
- [x] crawlers.py: normalize_theme_name() 함수 추가 (predefined aliases에서 자동 역매핑)
- [x] crawlers.py: _match_theme_name() 정규화 후 정확 매칭으로 개선
- [x] crawlers.py: crawl_all_themes() 중복 제거 전 테마명 정규화 적용
- [x] database.py: _migrate_v10() — themes 테이블에 category 컬럼 추가
- [x] database.py: save_theme_scores() — category 저장 추가

### Step 1: 일별 테마 수집 + 점수 체계 변경
- [x] scorer.py: 상수 변경 (MAX_MOMENTUM=40, BASE_SCORE=15, MAX_AI=15)
- [x] scorer.py: score_themes()에서 뉴스/AI 점수 호출 활성화
- [x] ai_analyzer.py: 모델명 claude-sonnet-4-6 업데이트

### Step 2: 화요일 가중 집계
- [x] weekly_aggregator.py 신규 생성 (6일 가중 평균 함수)
- [x] __init__.py: weekly_aggregator export 추가

### Step 3: 로테이션 화요일 변경
- [x] theme_rotator.py: should_review weekday()==0 → weekday()==1
- [x] main.py: 다음 재평가일 계산 월→화 변경

### Step 4: 스케줄러 통합
- [x] scheduler.py: on_daily_theme_collection 콜백 + 17:05 스케줄 추가
- [x] main.py: 17:05 일별 수집 메서드 + 콜백 연결
- [x] main.py: 08:30 on_theme_analysis 화요일 분기 로직

### Step 5: 텔레그램 알림 개선
- [x] main.py: 화요일 선정 시 메시지 (월→화 변경 완료)

## 검증 항목
- [x] py_compile 전체 수정 파일 통과 (9/9)
- [x] normalize_theme_name() alias→표준명 변환 확인 (13/13 테스트 OK)
- [x] _match_theme_name() 거짓양성 제거 확인
- [x] code-tester 에이전트 전체 검증 (심각 1건 수정 완료)
- [x] DB v10 마이그레이션: category 컬럼 존재 확인 (로그: "마이그레이션 v10 적용")
- [x] systemd 재시작 후 스케줄 정상 확인 (17:05 일별 테마 수집 등록 확인)

## 배포 항목
- [x] systemd 서비스 재시작
- [x] 스케줄 로그에서 17:05 일별 수집 + 08:30 테마 분석 확인

## 문서 업데이트 항목
- [x] memory/MEMORY.md: DB Schema v10 + 테마 정규화 기록
- [x] CONTEXT.md: 작업 결과 반영
