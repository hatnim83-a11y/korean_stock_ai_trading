# 런북 — GCP 콘솔로 VM/프로세스 다운 외부 감지 (코드 무변경)

근거: docs/incidents/20260605_vm_freeze_host_fault.md (P0). 2026-06-05 호스트 장애 때
봇 네트워크가 끊겨 텔레그램 알림이 못 갔다. **VM 밖(GCP 인프라)에서** 다운을 감지하는
이중 안전망을 코드 변경 없이 콘솔 설정으로 구성한다. (코드 기반 healthcheck ping과 병행)

> 이 문서는 사용자가 GCP 콘솔에서 직접 수행하는 절차다. Claude는 콘솔 접근 권한이 없다.

---

## 권장 구성 2종 (둘 다 또는 택1)

### 1. Uptime Check + Alert (대시보드 HTTPS 감시) — 가장 쉬움
봇 대시보드(`stock.jjjforever.com`, 포트 8501 / Cloudflare Tunnel)가 응답하는지 외부에서 주기 점검.

1. GCP 콘솔 → **Monitoring → Uptime checks → Create uptime check**
2. Target:
   - Protocol: HTTPS
   - Hostname: `stock.jjjforever.com`
   - Path: 로그인 불필요한 헬스 경로(없으면 `/` — 401이어도 "응답함"은 확인됨. 단 2xx 기대 시 별도 공개 엔드포인트 권장)
   - Check frequency: 1분
3. **Response timeout** 10초, 최소 2개 리전 실패 시 alert
4. **Create alerting policy** 연결 → Notification channel에 **이메일**(필요 시 SMS/Slack) 등록
5. 저장 후 의도적 `systemctl stop trading_dashboard`로 알림 수신 테스트

주의: 이 방식은 "대시보드 프로세스"만 본다. trading_system 본체가 죽고 dashboard만 살아있으면 못 잡는다 → 아래 2번 병행 권장.

### 2. Ops Agent + Process/Metric Alert (프로세스 직접 감시) — 가장 정확
이미 VM에 **google-cloud-ops-agent**가 설치되어 동작 중이다(인시던트 로그에서 확인). 이를 활용.

방법 A — 호스트 가용성(인스턴스 reset/down) 알림:
1. Monitoring → **Alerting → Create policy**
2. Metric: `Uptime`(`compute.googleapis.com/instance/uptime`) 또는 VM `up` 신호
3. 조건: 일정 시간(예: 5분) 데이터 없음(absent) → alert
4. Notification: 이메일. 이러면 **VM 자체가 멈추거나 reset되면** 콘솔이 직접 감지(2026-06-05 같은 호스트 이벤트 포착).

방법 B — trading_system 프로세스 감시(고급):
1. Ops Agent 설정(`/etc/google-cloud-ops-agent/config.yaml`)에 process 또는 systemd 상태를 메트릭/로그로 수집하도록 receiver 추가
2. 또는 간단히 **로그 기반 알림**: 앱이 주기적으로 찍는 INFO 로그(예: 모니터링 30초 dump)나 시작 로그가 일정 시간 없으면 alert
   - Logging → Logs-based metric 생성 → "지난 N분간 trading_system 로그 0건" → Alerting policy

---

## 권장 조합
- **healthcheck ping(코드, opt-in)** + **GCP Uptime check 또는 Uptime metric absent 알림(콘솔)**
- 서로 다른 실패모드를 커버: ping은 "프로세스 생존+네트워크", uptime metric absent는 "VM/호스트 생존".
- 단일 외부 서비스(healthchecks.io) 장애 시에도 GCP 쪽이 백업.

## 검증 체크
- [ ] 알림 채널(이메일) 등록 + 테스트 발송 성공
- [ ] 의도적 stop으로 실제 알림 수신 확인
- [ ] grace/주기를 장중 대응 가능한 수준(≤15분)으로 설정
