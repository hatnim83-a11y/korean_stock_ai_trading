# CHECKLIST: 대시보드 모바일 반응형 개선

## 구현 항목
- [x] login.html: 고정 너비 → 반응형 (max-width + width:90%)
- [x] login.html: 모바일 패딩 축소
- [x] dashboard.html: 480px breakpoint 추가
- [x] dashboard.html: 헤더 모바일 패딩/폰트 축소
- [x] dashboard.html: 탭 모바일 터치 영역 확보 (44px)
- [x] dashboard.html: 요약 카드 모바일 1열/폰트 축소
- [x] dashboard.html: 포트폴리오 테이블 → 모바일 카드뷰
- [x] dashboard.html: Trades 테이블 → 모바일 카드뷰
- [x] dashboard.html: Theme History 테이블 모바일 대응
- [x] dashboard.html: 테마 카드 모바일 축소
- [x] dashboard.html: 차트/뉴스 패딩 축소
- [x] dashboard.html: 필터 select/input 터치 친화

## 검증 항목
- [ ] 375px (iPhone SE) 뷰포트에서 가독성 확인
- [ ] 768px (태블릿) 뷰포트에서 레이아웃 확인
- [ ] 1400px (데스크톱) 기존 레이아웃 깨지지 않는지 확인
- [ ] SSE 실시간 업데이트 시 모바일 렌더링 정상 동작
- [ ] 로그인 페이지 모바일 정상 표시
- [x] py_compile 불필요 (HTML만 변경)

## 배포 항목
- [x] trading_dashboard 서비스 재시작
- [ ] `stock.jjjforever.com` 에서 모바일 접속 테스트

## 문서 업데이트 항목
- [ ] 메모리 MEMORY.md에 Cloudflare Tunnel 정보 추가
- [ ] 작업 완료 후 completed/ 아카이브
