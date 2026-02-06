# 트레이딩 봇 운영 프로토콜

## 봇 관리 명령어

### 기본 명령어
```bash
# 상태 확인
sudo systemctl status trading_system

# 시작
sudo systemctl start trading_system

# 중지
sudo systemctl stop trading_system

# 재시작
sudo systemctl restart trading_system

# 로그 실시간 확인
sudo journalctl -u trading_system -f

# 최근 로그 100줄
sudo journalctl -u trading_system -n 100
```

### 부팅 시 자동 시작
```bash
# 활성화 (기본 설정됨)
sudo systemctl enable trading_system

# 비활성화
sudo systemctl disable trading_system
```

---

## 이중 봇 방지 메커니즘

### 1. PID 락 파일
- 파일 위치: `trading_system.pid` (프로젝트 루트)
- `main.py` 시작 시 PID 락 파일 생성
- 이미 존재하면 해당 프로세스가 살아있는지 확인
- 살아있으면 시작 거부, 죽었으면 락 재획득
- 정상 종료 시 자동 삭제

### 2. run_now.py 자동 충돌 방지
- 실행 시 `systemctl is-active trading_system` 으로 서비스 상태 확인
- 서비스가 실행 중이면 자동으로 `sudo systemctl stop` 후 수동 실행
- 수동 실행 완료 후 서비스를 자동 재시작

### 3. 절대 하지 말 것
```bash
# nohup 사용 금지 (이중 실행 위험)
nohup python main.py &          # ❌ 절대 금지
nohup python main.py --real &   # ❌ 절대 금지

# 항상 systemd를 통해 실행
sudo systemctl start trading_system   # ✅ 올바른 방법
```

---

## 트러블슈팅

### 봇이 실행 안 됨
```bash
# 1. 서비스 상태 확인
sudo systemctl status trading_system

# 2. PID 파일이 남아있는지 확인
cat trading_system.pid

# 3. 해당 PID 프로세스 확인
ps -p $(cat trading_system.pid)

# 4. 프로세스가 없으면 PID 파일 삭제
rm trading_system.pid

# 5. 재시작
sudo systemctl restart trading_system
```

### 봇이 두 개 실행됨 (긴급)
```bash
# 1. 모든 관련 프로세스 확인
ps aux | grep "main.py"

# 2. systemd 서비스 중지
sudo systemctl stop trading_system

# 3. 남은 프로세스 강제 종료
pkill -f "python.*main.py"

# 4. PID 파일 삭제
rm -f trading_system.pid

# 5. 서비스 재시작
sudo systemctl start trading_system
```

### 수동 실행(run_now.py) 관련
```bash
# 수동 실행 (서비스가 켜져 있어도 자동 처리됨)
python run_now.py --real

# 수동 실행 후 서비스가 재시작 안 된 경우
sudo systemctl start trading_system
```

---

## 일일 점검 사항

1. **오전 8:00** - 봇 정상 작동 확인
   ```bash
   sudo systemctl status trading_system
   ```

2. **오전 9:30** - 매수 완료 확인 (텔레그램 알림)

3. **오후 3:30** - 장 마감 정리 확인 (텔레그램 알림)

4. **주 1회** - 로그 확인 및 디스크 용량 체크
   ```bash
   sudo journalctl -u trading_system --since "1 week ago" | tail -50
   df -h
   ```
