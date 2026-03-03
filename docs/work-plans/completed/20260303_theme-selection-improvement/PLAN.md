# 테마 선정 개선: 정의 정비 + 일별 누적 + 뉴스/AI 감성 + 화요일 선정

## 목표
- 테마명 정규화로 일관성 확보
- 매일 17:00 테마 데이터 수집 (모멘텀 + 뉴스 + AI 감성) → DB 누적
- 화요일 08:30에 6영업일 가중 평균으로 테마 선정
- 화~다음주 월 = 5영업일 매매

## 배경
현재 시스템은 월요일 08:30에 1회 크롤링하여 모멘텀만으로 테마를 선정.
방산/AI 등 "지금 핫한" 테마를 감지하지 못하는 구조적 한계.
뉴스/AI 감성 코드가 존재하지만 비활성 상태.
테마명 부분 매칭으로 인한 거짓양성과 DB에 category 미저장 문제.

## 구현 단계

### Step 0: 테마 정의 정비
- crawlers.py: normalize_theme_name() 추가, _match_theme_name() 개선
- database.py: v10 마이그레이션 (themes.category), save_theme_scores() 수정

### Step 1: 일별 테마 수집 + 점수 체계 변경
- scorer.py: 배점 변경 (모멘텀40 + 뉴스20 + AI15 + 종목수10 + 기본15)
- ai_analyzer.py: 모델명 업데이트
- score_themes()에서 뉴스/AI 점수 호출 활성화

### Step 2: 화요일 가중 집계
- weekly_aggregator.py (신규): 6일 가중 평균 로직

### Step 3: 로테이션 화요일 변경
- theme_rotator.py: weekday()==0 → weekday()==1
- main.py: 재평가일 계산 수정

### Step 4: 스케줄러 통합
- scheduler.py: 17:00 on_daily_theme_collection 추가
- main.py: 콜백 연결 + 화요일 분기

### Step 5: 텔레그램 알림 개선
- 화요일 선정 시 6일 누적 기반 사유 표시

## 변경 파일 목록
- modules/theme_analyzer/crawlers.py
- modules/theme_analyzer/scorer.py
- modules/theme_analyzer/ai_analyzer.py
- modules/theme_analyzer/weekly_aggregator.py (신규)
- modules/theme_analyzer/theme_rotator.py
- modules/theme_analyzer/__init__.py
- database.py
- main.py
- scheduler.py

## 롤백 계획
- DB: v10 마이그레이션은 ALTER TABLE ADD COLUMN (비파괴적)
- 코드: git revert로 이전 상태 복원 가능
- 스케줄: 17:00 일별 수집 비활성화만으로 기존 동작 복원

## 완료 기준
- py_compile 전체 통과
- normalize_theme_name() 정상 동작 확인
- 17:00 일별 수집 → themes 테이블 20+개 저장 (category 포함)
- 화요일 가중 집계 → 상위 5개 선정
- code-tester 에이전트 통과
- systemd 재시작 후 스케줄 정상
