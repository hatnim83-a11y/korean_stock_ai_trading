# 웹 대시보드 접속 가이드

## 접속 정보

| 항목 | 값 |
|------|-----|
| URL | `http://34.64.210.247:8501` |
| 비밀번호 | `.env` 파일의 `DASHBOARD_PASSWORD` 참조 |
| 포트 | 8501 |

## 1단계: GCP 방화벽 설정 (최초 1회)

GCP 콘솔에서 방화벽 규칙을 추가해야 합니다.

1. [GCP 콘솔](https://console.cloud.google.com/) 접속
2. **VPC 네트워크 > 방화벽** 이동
3. **방화벽 규칙 만들기** 클릭
4. 아래 값 입력:

| 항목 | 값 |
|------|-----|
| 이름 | `allow-dashboard-8501` |
| 방향 | 수신 (Ingress) |
| 대상 | 네트워크의 모든 인스턴스 |
| 소스 IP 범위 | 접속할 IP (예: `1.2.3.4/32`, 여러 개는 쉼표 구분) |
| 프로토콜/포트 | TCP: `8501` |

> 소스 IP는 [내 IP 확인](https://ifconfig.me) 에서 확인 가능합니다.
> 보안을 위해 `0.0.0.0/0` (전체 허용)은 사용하지 마세요.

5. **만들기** 클릭

## 2단계: 접속

1. 브라우저에서 `http://34.64.210.247:8501` 접속
2. 비밀번호 입력 후 로그인
3. JWT 쿠키가 24시간 유지되므로 하루 1회 로그인

## 대시보드 기능

| 탭 | 설명 |
|----|------|
| Portfolio | 보유 종목, 현재가, 평가금액, 수익률, 트레일링 레벨 (SSE 실시간 갱신) |
| Trades | 매매 내역 (최근 30일) |
| Performance | 누적수익률 차트, 승률, MDD |
| Themes | 현재 선정 테마 + 히스토리 |
| News | 보유 종목 관련 뉴스 |
| System | 봇 상태, PID, 최근 에러 로그 |
| Actions | 수동 매도 (개별/전체) |

## 서비스 관리

```bash
# 대시보드 시작/중지/재시작
sudo systemctl start trading_dashboard
sudo systemctl stop trading_dashboard
sudo systemctl restart trading_dashboard

# 상태 확인
sudo systemctl status trading_dashboard

# 로그 확인
sudo journalctl -u trading_dashboard -f
```

## 트러블슈팅

### 접속이 안 될 때
1. 서비스 확인: `sudo systemctl is-active trading_dashboard`
2. 방화벽 확인: GCP 콘솔에서 소스 IP와 포트 8501 규칙 확인
3. 내 IP 변경: ISP가 IP를 변경했을 수 있음 → [ifconfig.me](https://ifconfig.me) 에서 현재 IP 확인 후 방화벽 규칙 업데이트

### 로그인이 안 될 때
- 비밀번호: `.env` 파일의 `DASHBOARD_PASSWORD` 확인
- 레이트 리밋: 5회 실패 시 60초 차단 → 잠시 대기 후 재시도

### 포트폴리오 데이터가 안 보일 때
- 트레이딩 봇 확인: `sudo systemctl is-active trading_system`
- DB 확인: `ls -la data/trading.db`
