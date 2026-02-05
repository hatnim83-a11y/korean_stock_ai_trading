#!/usr/bin/env python3
"""
get_telegram_chat_id.py - 텔레그램 Chat ID 자동 가져오기

사용법:
    python scripts/get_telegram_chat_id.py
"""

import os
import sys
import httpx
from pathlib import Path

# 프로젝트 루트 경로
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# .env 파일 로드
from dotenv import load_dotenv
load_dotenv(project_root / '.env')

def get_chat_id():
    """텔레그램 봇의 Chat ID 가져오기"""
    
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not bot_token or bot_token == '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        print("❌ 봇 토큰이 설정되지 않았습니다!")
        print("   .env 파일에서 TELEGRAM_BOT_TOKEN을 설정해주세요.")
        return None
    
    print("=" * 70)
    print("📱 텔레그램 Chat ID 가져오기")
    print("=" * 70)
    print(f"\n봇 토큰: {bot_token[:20]}... ✅\n")
    
    # getUpdates API 호출
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    
    try:
        print("📡 텔레그램 API 호출 중...")
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        if not data.get('ok'):
            print(f"❌ API 호출 실패: {data.get('description', 'Unknown error')}")
            return None
        
        updates = data.get('result', [])
        
        if not updates:
            print("\n⚠️  메시지가 없습니다!")
            print("\n다음 단계를 진행해주세요:")
            print("1. 텔레그램에서 내 봇을 검색")
            print("2. /start 명령어 입력")
            print("3. 아무 메시지나 보내기 (예: '안녕')")
            print("4. 이 스크립트 다시 실행\n")
            return None
        
        # 최신 메시지에서 chat_id 추출
        latest_update = updates[-1]
        chat = latest_update.get('message', {}).get('chat', {})
        chat_id = chat.get('id')
        chat_type = chat.get('type')
        
        if chat_id:
            print(f"\n✅ Chat ID 발견: {chat_id}")
            print(f"   타입: {chat_type}")
            
            # 사용자 정보
            user = latest_update.get('message', {}).get('from', {})
            if user:
                username = user.get('username', 'N/A')
                first_name = user.get('first_name', 'N/A')
                print(f"   사용자: {first_name} (@{username})")
            
            return chat_id
        else:
            print("❌ Chat ID를 찾을 수 없습니다.")
            return None
            
    except httpx.HTTPStatusError as e:
        print(f"❌ HTTP 오류: {e.response.status_code}")
        if e.response.status_code == 401:
            print("   → 봇 토큰이 올바르지 않습니다!")
        return None
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        return None


def update_env_file(chat_id):
    """Chat ID를 .env 파일에 업데이트"""
    
    env_path = project_root / '.env'
    
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        updated = False
        for i, line in enumerate(lines):
            if line.startswith('TELEGRAM_CHAT_ID='):
                lines[i] = f'TELEGRAM_CHAT_ID={chat_id}\n'
                updated = True
                break
        
        if updated:
            with open(env_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            
            print(f"\n✅ .env 파일 업데이트 완료!")
            print(f"   TELEGRAM_CHAT_ID={chat_id}")
            return True
        else:
            print("\n⚠️  .env 파일에 TELEGRAM_CHAT_ID 항목이 없습니다.")
            return False
            
    except Exception as e:
        print(f"\n❌ .env 파일 업데이트 실패: {e}")
        return False


def test_notification(chat_id, bot_token):
    """테스트 메시지 전송"""
    
    print("\n📤 테스트 메시지 전송 중...")
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    
    try:
        response = httpx.post(
            url,
            json={
                "chat_id": chat_id,
                "text": "🎉 한국 주식 AI 트레이딩 시스템\n\n텔레그램 알림이 성공적으로 연결되었습니다!",
                "parse_mode": "Markdown"
            },
            timeout=10
        )
        response.raise_for_status()
        
        print("✅ 테스트 메시지 전송 완료!")
        print("   텔레그램 앱에서 확인해보세요.\n")
        return True
        
    except Exception as e:
        print(f"❌ 메시지 전송 실패: {e}\n")
        return False


if __name__ == "__main__":
    # Chat ID 가져오기
    chat_id = get_chat_id()
    
    if chat_id:
        # .env 파일 업데이트
        if update_env_file(chat_id):
            # 테스트 메시지 전송
            bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
            test_notification(chat_id, bot_token)
            
            print("=" * 70)
            print("🎉 텔레그램 설정 완료!")
            print("=" * 70)
    else:
        print("\n" + "=" * 70)
        print("❌ Chat ID를 가져오지 못했습니다.")
        print("=" * 70)
        sys.exit(1)
