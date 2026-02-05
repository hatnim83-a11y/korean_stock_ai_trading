#!/bin/bash
# install_service.sh - systemd 서비스 설치 스크립트

set -e

echo "====================================="
echo "트레이딩 시스템 서비스 설치"
echo "====================================="

# 프로젝트 디렉토리
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
SERVICE_FILE="$PROJECT_DIR/trading_system.service"

echo "프로젝트 디렉토리: $PROJECT_DIR"

# 1. 서비스 파일 확인
if [ ! -f "$SERVICE_FILE" ]; then
    echo "❌ 서비스 파일을 찾을 수 없습니다: $SERVICE_FILE"
    exit 1
fi

# 2. 서비스 파일 복사
echo "서비스 파일 복사 중..."
sudo cp "$SERVICE_FILE" /etc/systemd/system/trading_system.service

# 3. systemd 리로드
echo "systemd 리로드 중..."
sudo systemctl daemon-reload

# 4. 서비스 활성화
echo "서비스 활성화 중..."
sudo systemctl enable trading_system

echo ""
echo "✅ 설치 완료!"
echo ""
echo "사용 가능한 명령어:"
echo "  시작:   sudo systemctl start trading_system"
echo "  중지:   sudo systemctl stop trading_system"
echo "  재시작: sudo systemctl restart trading_system"
echo "  상태:   sudo systemctl status trading_system"
echo "  로그:   sudo journalctl -u trading_system -f"
echo ""
