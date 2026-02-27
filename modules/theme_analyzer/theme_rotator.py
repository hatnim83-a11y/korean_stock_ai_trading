"""
theme_rotator.py - 테마 로테이션 관리 모듈

이 파일은 1주 단위 테마 로테이션 전략을 구현합니다.
(백테스트 결과: 7일이 14일 대비 +75% 수익 개선)

주요 기능:
- 1주마다 메인 테마 재평가
- 테마 점수 20% 이상 하락 시 즉시 변경
- 급등 테마(+15% 이상) 감지 및 즉시 진입
- 테마 히스토리 관리

사용법:
    from modules.theme_analyzer.theme_rotator import ThemeRotator
    
    rotator = ThemeRotator()
    should_change = rotator.check_rotation_needed(current_themes)
    new_theme = rotator.select_new_main_theme(current_themes)
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict
from dataclasses import dataclass, field
import json

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from logger import logger
from config import settings, now_kst
from database import Database


@dataclass
class ThemeSnapshot:
    """테마 스냅샷"""
    theme_name: str
    score: float
    timestamp: datetime
    stock_count: int
    avg_change_rate: float
    
    def score_change_rate(self, new_score: float) -> float:
        """점수 변화율"""
        if self.score > 0:
            return (new_score - self.score) / self.score
        return 0


@dataclass
class MainTheme:
    """메인 테마 정보"""
    theme_name: str
    start_date: datetime
    initial_score: float
    current_score: float
    days_held: int = 0
    
    @property
    def score_change_rate(self) -> float:
        """점수 변화율"""
        if self.initial_score > 0:
            return (self.current_score - self.initial_score) / self.initial_score
        return 0
    
    @property
    def should_review(self) -> bool:
        """재평가 필요 여부 (매주 월요일)"""
        return now_kst().weekday() == 0  # 0 = Monday


class ThemeRotator:
    """
    테마 로테이션 관리자

    1주 단위로 메인 테마를 재평가하고,
    조건 충족 시 테마를 변경합니다.
    
    Attributes:
        current_main_theme: 현재 메인 테마
        theme_history: 테마 히스토리
        
    Example:
        >>> rotator = ThemeRotator()
        >>> rotator.set_main_theme("AI반도체", 85.0)
        >>> should_change, reason = rotator.check_rotation_needed(new_themes)
        >>> if should_change:
        ...     new_theme = rotator.select_new_main_theme(new_themes)
    """
    
    def __init__(self):
        """로테이터 초기화"""
        self.current_main_theme: Optional[MainTheme] = None
        self.theme_history: List[Dict] = []
        self.last_check_date: Optional[datetime] = None
        
        # 설정 로드
        self.review_days = settings.THEME_REVIEW_DAYS
        self.change_threshold = settings.THEME_CHANGE_THRESHOLD
        self.surge_threshold = settings.THEME_SURGE_THRESHOLD
        
        # DB에서 히스토리 로드
        self._load_history()
        
        logger.info(f"테마 로테이터 초기화")
        logger.info(f"  재평가 주기: {self.review_days}일")
        logger.info(f"  변경 임계값: {self.change_threshold:.0%}")
        logger.info(f"  급등 임계값: {self.surge_threshold:.0%}")
    
    # ===== 메인 테마 관리 =====
    
    def set_main_theme(
        self,
        theme_name: str,
        initial_score: float,
        start_date: Optional[datetime] = None
    ) -> None:
        """
        메인 테마 설정
        
        Args:
            theme_name: 테마명
            initial_score: 초기 점수
            start_date: 시작일
        """
        if start_date is None:
            start_date = now_kst()
        
        self.current_main_theme = MainTheme(
            theme_name=theme_name,
            start_date=start_date,
            initial_score=initial_score,
            current_score=initial_score,
            days_held=0
        )
        
        # 히스토리에 추가
        self._add_to_history(
            theme_name=theme_name,
            score=initial_score,
            action="set",
            reason="메인 테마 설정"
        )
        
        logger.info(f"메인 테마 설정: {theme_name} (점수: {initial_score:.1f})")
    
    def update_main_theme_score(self, new_score: float) -> None:
        """
        메인 테마 점수 업데이트
        
        Args:
            new_score: 새로운 점수
        """
        if self.current_main_theme is None:
            logger.warning("메인 테마가 설정되지 않았습니다")
            return
        
        old_score = self.current_main_theme.current_score
        self.current_main_theme.current_score = new_score
        
        # 보유 일수 계산
        days_held = (now_kst() - self.current_main_theme.start_date).days
        self.current_main_theme.days_held = days_held
        
        score_change = new_score - old_score
        score_change_rate = self.current_main_theme.score_change_rate
        
        logger.debug(
            f"메인 테마 점수 업데이트: {self.current_main_theme.theme_name} "
            f"{old_score:.1f} → {new_score:.1f} ({score_change_rate:+.1%}) "
            f"[보유 {days_held}일]"
        )
    
    def get_main_theme_info(self) -> Optional[Dict]:
        """
        메인 테마 정보 조회
        
        Returns:
            메인 테마 정보 딕셔너리
        """
        if self.current_main_theme is None:
            return None
        
        theme = self.current_main_theme
        return {
            "theme_name": theme.theme_name,
            "start_date": theme.start_date,
            "days_held": theme.days_held,
            "initial_score": theme.initial_score,
            "current_score": theme.current_score,
            "score_change_rate": theme.score_change_rate,
            "should_review": theme.should_review
        }
    
    # ===== 로테이션 체크 =====
    
    def check_rotation_needed(
        self,
        current_themes: List[Dict]
    ) -> tuple[bool, str]:
        """
        로테이션 필요 여부 체크
        
        Args:
            current_themes: 현재 테마 리스트
                [{'theme': '테마명', 'score': 85.0, ...}, ...]
        
        Returns:
            (로테이션 필요 여부, 이유)
        """
        if self.current_main_theme is None:
            return True, "메인 테마 미설정"
        
        # 현재 메인 테마 정보 찾기
        main_theme_data = None
        for theme in current_themes:
            if theme['theme'] == self.current_main_theme.theme_name:
                main_theme_data = theme
                break
        
        if main_theme_data is None:
            logger.warning(f"현재 메인 테마 '{self.current_main_theme.theme_name}'를 찾을 수 없습니다")
            return True, "메인 테마 데이터 없음"
        
        # 점수 업데이트
        self.update_main_theme_score(main_theme_data['score'])
        
        # 1. 2주 경과 체크
        if self.current_main_theme.should_review:
            logger.info(
                f"메인 테마 재평가 시점 도달: {self.current_main_theme.theme_name} "
                f"(보유 {self.current_main_theme.days_held}일)"
            )
            return True, "월요일 주간 로테이션"
        
        # 2. 점수 급락 체크 (-20% 이상)
        score_change_rate = self.current_main_theme.score_change_rate
        if score_change_rate <= self.change_threshold:
            logger.warning(
                f"메인 테마 점수 급락: {self.current_main_theme.theme_name} "
                f"({score_change_rate:.1%})"
            )
            return True, f"점수 급락 {score_change_rate:.1%}"
        
        # 3. 급등 테마 감지 (+15% 이상 && 현재 메인 테마보다 높음)
        surge_theme = self._detect_surge_theme(current_themes)
        if surge_theme:
            logger.info(
                f"급등 테마 감지: {surge_theme['theme']} "
                f"(점수: {surge_theme['score']:.1f}, "
                f"변화율: {surge_theme.get('change_rate', 0):.1%})"
            )
            return True, f"급등 테마 감지: {surge_theme['theme']}"
        
        # 로테이션 불필요
        return False, "유지"
    
    def _detect_surge_theme(
        self,
        current_themes: List[Dict]
    ) -> Optional[Dict]:
        """
        급등 테마 감지
        
        Args:
            current_themes: 현재 테마 리스트
        
        Returns:
            급등 테마 (없으면 None)
        """
        if self.current_main_theme is None:
            return None
        
        main_score = self.current_main_theme.current_score
        
        for theme in current_themes:
            # 메인 테마 제외
            if theme['theme'] == self.current_main_theme.theme_name:
                continue
            
            # 점수가 메인 테마보다 높고
            if theme['score'] > main_score:
                # 급등 조건 충족 (변화율 +15% 이상)
                # 여기서는 간단히 avg_change_rate로 체크
                change_rate = theme.get('avg_change_rate', 0) / 100  # % -> 비율
                if change_rate >= self.surge_threshold:
                    return theme
        
        return None
    
    def select_new_main_theme(
        self,
        current_themes: List[Dict]
    ) -> Optional[Dict]:
        """
        새로운 메인 테마 선정
        
        Args:
            current_themes: 현재 테마 리스트
        
        Returns:
            새로운 메인 테마
        """
        if not current_themes:
            logger.warning("선정할 테마가 없습니다")
            return None
        
        # 점수 순으로 정렬
        sorted_themes = sorted(
            current_themes,
            key=lambda x: x['score'],
            reverse=True
        )
        
        # 상위 테마 선정
        new_theme = sorted_themes[0]
        
        # 메인 테마 변경
        old_theme = self.current_main_theme.theme_name if self.current_main_theme else None
        
        self.set_main_theme(
            theme_name=new_theme['theme'],
            initial_score=new_theme['score']
        )
        
        # 히스토리에 추가
        self._add_to_history(
            theme_name=new_theme['theme'],
            score=new_theme['score'],
            action="change",
            reason=f"이전 테마: {old_theme}"
        )
        
        logger.info(f"메인 테마 변경: {old_theme} → {new_theme['theme']}")
        logger.info(f"  새 테마 점수: {new_theme['score']:.1f}")
        
        return new_theme
    
    # ===== 히스토리 관리 =====
    
    def _add_to_history(
        self,
        theme_name: str,
        score: float,
        action: str,
        reason: str
    ) -> None:
        """
        히스토리에 추가
        
        Args:
            theme_name: 테마명
            score: 점수
            action: 액션 (set, change)
            reason: 이유
        """
        entry = {
            "timestamp": now_kst().isoformat(),
            "theme_name": theme_name,
            "score": score,
            "action": action,
            "reason": reason
        }
        
        self.theme_history.append(entry)
        
        # DB에 저장
        self._save_history_to_db(entry)
    
    def _load_history(self) -> None:
        """DB에서 히스토리 로드"""
        try:
            db = Database()
            db.connect()
            
            # 최근 30일 히스토리 조회
            # TODO: DB 스키마에 theme_rotation_history 테이블 추가 필요
            # 현재는 메모리에만 저장
            
            db.close()
            
            logger.info(f"테마 히스토리 로드: {len(self.theme_history)}개")
            
        except Exception as e:
            logger.error(f"히스토리 로드 실패: {e}")
    
    def _save_history_to_db(self, entry: Dict) -> None:
        """히스토리를 DB에 저장"""
        try:
            db = Database()
            db.connect()
            
            # TODO: DB 스키마에 theme_rotation_history 테이블 추가 필요
            # INSERT INTO theme_rotation_history ...
            
            db.close()
            
        except Exception as e:
            logger.error(f"히스토리 저장 실패: {e}")
    
    def get_history(self, days: int = 30) -> List[Dict]:
        """
        히스토리 조회
        
        Args:
            days: 조회 기간 (일)
        
        Returns:
            히스토리 리스트
        """
        cutoff = now_kst() - timedelta(days=days)
        
        recent_history = [
            entry for entry in self.theme_history
            if datetime.fromisoformat(entry['timestamp']) >= cutoff
        ]
        
        return recent_history
    
    def display_history(self, days: int = 30) -> None:
        """히스토리 출력"""
        history = self.get_history(days)
        
        print("\n" + "=" * 70)
        print(f"📊 테마 로테이션 히스토리 (최근 {days}일)")
        print("=" * 70)
        
        if not history:
            print("  히스토리가 없습니다")
        else:
            for entry in history:
                timestamp = datetime.fromisoformat(entry['timestamp'])
                print(f"{timestamp.strftime('%Y-%m-%d %H:%M')} | "
                      f"{entry['action']:6} | "
                      f"{entry['theme_name']:15} | "
                      f"점수: {entry['score']:5.1f} | "
                      f"{entry['reason']}")
        
        print("=" * 70)
    
    # ===== 상태 출력 =====
    
    def display_status(self) -> None:
        """현재 상태 출력"""
        print("\n" + "=" * 70)
        print("📊 테마 로테이션 현황")
        print("=" * 70)
        
        if self.current_main_theme is None:
            print("  메인 테마: 미설정")
        else:
            theme = self.current_main_theme
            print(f"  메인 테마: {theme.theme_name}")
            print(f"  시작일: {theme.start_date.strftime('%Y-%m-%d')}")
            print(f"  보유 일수: {theme.days_held}일 / {self.review_days}일")
            print(f"  초기 점수: {theme.initial_score:.1f}")
            print(f"  현재 점수: {theme.current_score:.1f}")
            print(f"  점수 변화: {theme.score_change_rate:+.1%}")
            
            if theme.should_review:
                print(f"  상태: ⚠️ 재평가 필요 ({self.review_days}일 경과)")
            elif theme.score_change_rate <= self.change_threshold:
                print(f"  상태: ⚠️ 점수 급락 ({theme.score_change_rate:.1%})")
            else:
                print(f"  상태: ✅ 정상")
        
        print("=" * 70)


# ===== 직접 실행 시 테스트 =====
if __name__ == "__main__":
    print("=" * 70)
    print("📊 테마 로테이터 테스트")
    print("=" * 70)
    
    # 로테이터 생성
    rotator = ThemeRotator()
    
    # 초기 메인 테마 설정
    print("\n[초기 설정]")
    rotator.set_main_theme("AI반도체", 85.0)
    rotator.display_status()
    
    # 테스트 테마 데이터
    test_themes = [
        {'theme': 'AI반도체', 'score': 87.0, 'avg_change_rate': 2.0},
        {'theme': '2차전지', 'score': 82.0, 'avg_change_rate': 5.0},
        {'theme': '바이오', 'score': 78.0, 'avg_change_rate': 3.0}
    ]
    
    # 로테이션 체크 (1일 후)
    print("\n[1일 후 체크]")
    rotator.current_main_theme.days_held = 1
    should_rotate, reason = rotator.check_rotation_needed(test_themes)
    print(f"로테이션 필요: {should_rotate} (이유: {reason})")
    rotator.display_status()
    
    # 로테이션 체크 (14일 후 - 재평가 시점)
    print("\n[14일 후 체크 - 재평가 시점]")
    rotator.current_main_theme.days_held = 14
    should_rotate, reason = rotator.check_rotation_needed(test_themes)
    print(f"로테이션 필요: {should_rotate} (이유: {reason})")
    
    if should_rotate:
        new_theme = rotator.select_new_main_theme(test_themes)
        print(f"새로운 메인 테마: {new_theme['theme']}")
    
    rotator.display_status()
    
    # 점수 급락 시나리오
    print("\n[점수 급락 시나리오]")
    test_themes_drop = [
        {'theme': 'AI반도체', 'score': 65.0, 'avg_change_rate': -20.0},  # 급락!
        {'theme': '2차전지', 'score': 90.0, 'avg_change_rate': 15.0},
        {'theme': '바이오', 'score': 78.0, 'avg_change_rate': 3.0}
    ]
    
    rotator.current_main_theme.days_held = 5  # 5일만 경과
    should_rotate, reason = rotator.check_rotation_needed(test_themes_drop)
    print(f"로테이션 필요: {should_rotate} (이유: {reason})")
    
    if should_rotate:
        new_theme = rotator.select_new_main_theme(test_themes_drop)
        print(f"새로운 메인 테마: {new_theme['theme']}")
    
    rotator.display_status()
    
    # 히스토리 출력
    rotator.display_history()
    
    print("\n" + "=" * 70)
    print("✅ 테마 로테이터 테스트 완료!")
    print("=" * 70)
