#!/usr/bin/env python3
"""
setup_dart_api.py - DART API 설정 및 테스트

사용법:
    python scripts/setup_dart_api.py <API_KEY>
"""

import os
import sys
import httpx
from pathlib import Path

# 프로젝트 루트 경로
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def update_env_file(api_key):
    """DART API Key를 .env 파일에 업데이트"""
    
    env_path = project_root / '.env'
    
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        updated = False
        for i, line in enumerate(lines):
            if line.startswith('DART_API_KEY='):
                lines[i] = f'DART_API_KEY={api_key}\n'
                updated = True
                break
        
        if updated:
            with open(env_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
            
            print(f"✅ .env 파일 업데이트 완료!")
            print(f"   DART_API_KEY={api_key[:20]}...")
            return True
        else:
            print("⚠️  .env 파일에 DART_API_KEY 항목이 없습니다.")
            return False
            
    except Exception as e:
        print(f"❌ .env 파일 업데이트 실패: {e}")
        return False


def test_dart_api(api_key):
    """DART API 테스트"""
    
    print("\n📡 DART API 테스트 중...")
    
    # 최근 3개월 공시 조회 (DART API 제약)
    from datetime import datetime, timedelta
    today = datetime.now()
    three_months_ago = today - timedelta(days=90)
    
    url = "https://opendart.fss.or.kr/api/list.json"
    
    params = {
        "crtfc_key": api_key,
        "corp_code": "",
        "bgn_de": three_months_ago.strftime("%Y%m%d"),
        "end_de": today.strftime("%Y%m%d"),
        "pblntf_ty": "A",  # 정기공시
        "page_no": "1",
        "page_count": "5"
    }
    
    try:
        response = httpx.get(url, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        status = data.get('status')
        message = data.get('message')
        
        if status == '000':
            print("✅ DART API 연결 성공!")
            
            # 최근 공시 샘플 출력
            items = data.get('list', [])
            if items:
                print(f"\n📋 최근 공시 {len(items)}건 확인:")
                for i, item in enumerate(items[:3], 1):
                    corp_name = item.get('corp_name', 'N/A')
                    report_nm = item.get('report_nm', 'N/A')
                    rcept_dt = item.get('rcept_dt', 'N/A')
                    print(f"   {i}. {corp_name} - {report_nm} ({rcept_dt})")
            
            return True
            
        elif status == '010':
            print(f"❌ API Key가 올바르지 않습니다.")
            print(f"   메시지: {message}")
            return False
            
        elif status == '020':
            print(f"❌ 일일 호출 한도 초과")
            print(f"   메시지: {message}")
            return False
            
        else:
            print(f"⚠️  알 수 없는 응답: {status} - {message}")
            return False
            
    except httpx.HTTPStatusError as e:
        print(f"❌ HTTP 오류: {e.response.status_code}")
        return False
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        return False


def main():
    print("=" * 70)
    print("📄 DART API 설정")
    print("=" * 70)
    
    # API Key 입력 확인
    if len(sys.argv) < 2:
        print("\n사용법: python scripts/setup_dart_api.py <API_KEY>")
        print("\nAPI Key 발급 방법:")
        print("1. https://opendart.fss.or.kr 접속")
        print("2. 회원가입/로그인")
        print("3. [인증키 신청/관리] 메뉴")
        print("4. 인증키 발급")
        print("\n발급받은 API Key를 인자로 전달하세요.")
        sys.exit(1)
    
    api_key = sys.argv[1].strip()
    
    print(f"\nAPI Key: {api_key[:20]}...")
    
    # API 테스트
    if test_dart_api(api_key):
        # .env 파일 업데이트
        if update_env_file(api_key):
            print("\n" + "=" * 70)
            print("🎉 DART API 설정 완료!")
            print("=" * 70)
            print("\n공시 정보를 활용한 AI 분석이 강화됩니다!")
            return 0
    
    print("\n" + "=" * 70)
    print("❌ DART API 설정 실패")
    print("=" * 70)
    return 1


if __name__ == "__main__":
    sys.exit(main())
