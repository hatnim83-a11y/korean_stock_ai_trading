"""closing_bet_system.collectors.kis_market_provider

단위 2-9d — KIS Open API ranking 4종을 활용해 universe v2의 출처 2~4 + 시총 보강을 제공.

배경: KRX 사이트 정책 변경으로 pykrx 시장 단위 bulk(`get_market_*_by_ticker`)이 빈 응답을
반환해 단위 2-9c에서 종목별 by_date 폴백 + 옵션 A 보수 탈락(시총 None → data_not_found)으로
임시 우회 중. 본 단위는 KIS Open API ranking 으로 본격 대체:

- 거래대금 상위: ``volume-rank`` (FID_BLNG_CLS_CODE=3)
- 등락률 상위: ``ranking/fluctuation``
- 외국인 순매수 상위: ``foreign-institution-total`` (FID_ETC_CLS_CODE=1, RANK_SORT=0)
- 시가총액 상위: ``ranking/market-cap`` — 옵션 B 자연 활성화 (시총 보강)

설계
----
- 기존 ``KISApi`` 인스턴스 위임 (toolen 공유 ``_shared_token``)
- 싱글톤 ``get_kis_market_provider()`` (race 방지 lock)
- ``_rate_limit()`` (settings.KIS_API_DELAY=0.11s) 자동 적용
- 응답 파싱: ``output[*]`` 6자리 정규식 검증 + ``_safe_int/_safe_float`` NaN 가드
- ``rt_cd != "0"`` warn + 빈 리스트/dict 폴백
- 모든 메서드 try/except 격리

Sources
-------
- https://github.com/koreainvestment/open-trading-api
- https://apiportal.koreainvestment.com/apiservice-apiservice
"""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from logger import logger
from modules.stock_screener.kis_api import KISApi, _safe_int, _safe_float
from closing_bet_system.infra.kis_client import get_kis_api


# ===== 모듈 상수 — KIS Open API ranking 매뉴얼 검증값 =====

# TR_ID (실전/모의 동일)
_TR_VOLUME_RANK = "FHPST01710000"           # 거래대금/거래량 순위
_TR_FLUCTUATION = "FHPST01700000"           # 등락률 순위
_TR_FOREIGN_TOTAL = "FHPTJ04400000"         # 외국인/기관 매매종목 가집계
_TR_MARKET_CAP = "FHPST01740000"            # 시가총액 순위

# URL path (base_url 은 KISApi 인스턴스에서 주입)
_PATH_VOLUME_RANK = "/uapi/domestic-stock/v1/quotations/volume-rank"
_PATH_FLUCTUATION = "/uapi/domestic-stock/v1/ranking/fluctuation"
_PATH_FOREIGN_TOTAL = "/uapi/domestic-stock/v1/quotations/foreign-institution-total"
_PATH_MARKET_CAP = "/uapi/domestic-stock/v1/ranking/market-cap"

# 응답 컬럼명 (volume_rank 검증, 다른 ranking 동일 패턴 추정)
_FIELD_TICKER = "mksc_shrn_iscd"            # 단축종목코드 (6자리)
_FIELD_NAME = "hts_kor_isnm"                # 종목명
_FIELD_PRICE = "stck_prpr"                  # 현재가
_FIELD_CHANGE_RATE = "prdy_ctrt"            # 등락률 (%)
_FIELD_VOLUME = "acml_vol"                  # 누적거래량
_FIELD_VALUE = "acml_tr_pbmn"               # 누적거래대금
_FIELD_MARKET_CAP = "stck_avls"             # 시가총액 (market-cap ranking)

# 시장 코드 (FID_INPUT_ISCD)
_MARKET_TO_ISCD = {
    "ALL": "0000",      # 전체
    "KOSPI": "0001",    # 거래소 (KOSPI200 아님 — 전 종목)
    "KOSDAQ": "1001",   # 코스닥
}

# 종목코드 6자리 정규식 (config.validate_stock_code 와 동일)
_TICKER_PATTERN = re.compile(r"^\d{6}$")

# Default 호출 개수
DEFAULT_TOP_N = 30
DEFAULT_MARKET_CAP_TOP_N = 200

# 단위 2-9e — KIS stck_avls 단위 = 억원 (사전 조사 5종목 × 1억 = inquire-price market_cap 정확 일치 확정)
# 005930: stck_avls=15,551,101 × 1억 = 1,555,110,100,000,000원 (= stck_prpr × lstn_stcn = inquire-price market_cap)
_STCK_AVLS_UNIT_TO_WON = 100_000_000


# ===== 헬퍼 =====


def _filter_valid_tickers(items: list[dict], top_n: int) -> list[str]:
    """output 리스트에서 6자리 종목코드만 추출 (중복 제거 + top_n 절단).

    KIS 응답이 비-6자리 코드를 포함할 수 있어 정규식으로 사전 필터링.
    """
    seen: set[str] = set()
    result: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get(_FIELD_TICKER, "")).strip()
        if _TICKER_PATTERN.match(code) and code not in seen:
            seen.add(code)
            result.append(code)
        if len(result) >= top_n:
            break
    return result


def _resolve_market_code(market: str) -> str:
    """`market` 인자를 KIS FID_INPUT_ISCD 코드로 변환. 미지원 시 ALL."""
    return _MARKET_TO_ISCD.get(market.upper(), _MARKET_TO_ISCD["ALL"])


# ===== KISMarketProvider 클래스 =====


class KISMarketProvider:
    """KIS Open API ranking 4종 래퍼.

    기존 ``KISApi`` 인스턴스를 위임받아 토큰/HTTP 클라이언트/rate_limit/headers 를
    재사용한다. 신규 토큰 발급하지 않음 (1분 발급 제한 회피).
    """

    def __init__(self, kis_api: Optional[KISApi] = None):
        self._kis = kis_api or get_kis_api()

    # ----- 출처 2: 거래대금 상위 -----

    def get_top_value_codes(
        self, top_n: int = DEFAULT_TOP_N, market: str = "ALL"
    ) -> list[str]:
        """거래대금 상위 N 종목코드.

        TR_ID FHPST01710000 / volume-rank with FID_BLNG_CLS_CODE=3 (거래금액순).
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",          # KRX
            "FID_COND_SCR_DIV_CODE": "20171",       # 거래량순위 화면
            "FID_INPUT_ISCD": _resolve_market_code(market),
            "FID_DIV_CLS_CODE": "0",                # 전체 (1=보통주, 2=우선주)
            "FID_BLNG_CLS_CODE": "3",               # 3=거래금액순
            "FID_TRGT_CLS_CODE": "111111111",       # 모든 증거금
            "FID_TRGT_EXLS_CLS_CODE": "0000000000", # 제외 없음
            "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": "",
            "FID_VOL_CNT": "",
            "FID_INPUT_DATE_1": "",
        }
        items = self._call_ranking(_PATH_VOLUME_RANK, _TR_VOLUME_RANK, params, source="top_value")
        return _filter_valid_tickers(items, top_n)

    # ----- 출처 3: 등락률 상위 -----

    def get_top_change_codes(
        self, top_n: int = DEFAULT_TOP_N, direction: str = "up"
    ) -> list[str]:
        """등락률 상위 N 종목코드.

        TR_ID FHPST01700000 / ranking/fluctuation. ``direction="up"`` 이면 상승률 상위,
        ``"down"`` 이면 하락률 상위.
        """
        # 코더 검토 심각 #2 — direction 인자를 실제로 반영
        rank_sort = "0" if direction == "up" else "1"
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20170",
            "fid_input_iscd": "0000",               # 전체 종목
            "fid_rank_sort_cls_code": rank_sort,    # 0=상승률 상위 (1=하락률 상위)
            "fid_input_cnt_1": str(max(top_n, 1)),
            "fid_prc_cls_code": "0",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": "",
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_div_cls_code": "0",
            "fid_rsfl_rate1": "",
            "fid_rsfl_rate2": "",
        }
        items = self._call_ranking(_PATH_FLUCTUATION, _TR_FLUCTUATION, params, source="top_change")
        return _filter_valid_tickers(items, top_n)

    # ----- 출처 4: 외국인 순매수 상위 -----

    def get_top_foreign_buy_codes(self, top_n: int = DEFAULT_TOP_N) -> list[str]:
        """외국인 순매수 상위 N 종목코드.

        TR_ID FHPTJ04400000 / foreign-institution-total
        with FID_ETC_CLS_CODE=1 (외국인) + FID_RANK_SORT_CLS_CODE=0 (순매수상위) + FID_DIV_CLS_CODE=1 (금액).
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "V",          # 매뉴얼 표기
            "FID_COND_SCR_DIV_CODE": "16449",
            "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "1",                # 1=금액 기준 (0=수량)
            "FID_RANK_SORT_CLS_CODE": "0",          # 0=순매수 상위 (1=순매도)
            "FID_ETC_CLS_CODE": "1",                # 1=외국인 (2=기관)
        }
        items = self._call_ranking(
            _PATH_FOREIGN_TOTAL, _TR_FOREIGN_TOTAL, params, source="top_foreign"
        )
        return _filter_valid_tickers(items, top_n)

    # ----- 시가총액 상위 (옵션 B 보강) -----

    def get_top_market_cap_data(
        self, top_n: int = DEFAULT_MARKET_CAP_TOP_N, market: str = "ALL"
    ) -> dict[str, dict]:
        """시가총액 상위 N 종목 → ``{ticker: {market_cap, close, change_rate}}``.

        TR_ID FHPST01740000 / ranking/market-cap. 옵션 B 시총 보강용.
        """
        params = {
            "fid_input_price_2": "",
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20174",
            "fid_div_cls_code": "0",
            "fid_input_iscd": _resolve_market_code(market),
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_vol_cnt": "",
        }
        items = self._call_ranking(
            _PATH_MARKET_CAP, _TR_MARKET_CAP, params, source="top_market_cap"
        )
        result: dict[str, dict] = {}
        if not items:
            return result
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            code = str(item.get(_FIELD_TICKER, "")).strip()
            if not _TICKER_PATTERN.match(code) or code in seen:
                continue
            seen.add(code)
            mcap = _safe_float(item.get(_FIELD_MARKET_CAP), default=0.0)
            close = _safe_float(item.get(_FIELD_PRICE), default=0.0)
            chg = _safe_float(item.get(_FIELD_CHANGE_RATE), default=0.0)
            entry: dict = {}
            # 단위 2-9e — stck_avls 단위 = 억원 확정 (사전 조사 5종목 검증):
            #   005930 stck_avls=15,551,101 × 100,000,000 = 1,555,110,100,000,000원
            #   ↔ stck_prpr × lstn_stcn = 1,554,909,948,728,000원
            #   ↔ inquire-price market_cap = 1,555,110,100,000,000원 (3값 정확 일치)
            # 비교 기준: universe_filters.min_market_cap = 50_000_000_000 (500억, settings.yaml:97)
            # int(round(...)) — _safe_float 가 float 반환 후 곱셈 시 IEEE 754 정밀도 한계로
            # 경계값(500억 근처) 절단 누락 위험 차단
            if mcap > 0:
                entry["market_cap"] = int(round(mcap * _STCK_AVLS_UNIT_TO_WON))
                # 단위 진단 로그 (첫 항목 1회) — 단위 2-9e 확정 표기
                if not result:
                    logger.info(
                        f"[kis_market_provider] stck_avls 단위 = 억원 (단위 2-9e 확정), × 1억 정규화 적용 — "
                        f"{code} raw={mcap:.0f} → market_cap={entry['market_cap']:,d}원 "
                        f"(min_market_cap=500억과 비교)"
                    )
            if close > 0:
                entry["close"] = close
            # 코더 검토 심각 #3 — _FIELD_CHANGE_RATE 상수 사용 (문자열 리터럴 제거)
            if chg != 0.0 or _FIELD_CHANGE_RATE in item:
                entry["change_rate"] = chg
            if entry:
                result[code] = entry
            if len(result) >= top_n:
                break
        return result

    # ----- 내부 호출 헬퍼 -----

    def _call_ranking(
        self, path: str, tr_id: str, params: dict, *, source: str
    ) -> list[dict]:
        """KIS ranking API 공통 호출. 실패 시 빈 리스트 + warning."""
        try:
            self._kis._rate_limit()
            url = f"{self._kis.base_url}{path}"
            headers = self._kis._get_headers(tr_id)
            response = self._kis.client.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("rt_cd") != "0":
                logger.warning(
                    f"[kis_market_provider] {source} 응답 rt_cd={data.get('rt_cd')} "
                    f"msg={data.get('msg1')} (tr_id={tr_id})"
                )
                return []
            output = data.get("output")
            # ranking API 일부는 output1/output2 분리 가능성 — 둘 다 시도
            if output is None:
                output = data.get("output1")
            if not isinstance(output, list):
                if isinstance(output, dict):
                    return [output]
                return []
            return output
        except Exception as e:
            logger.warning(f"[kis_market_provider] {source} 호출 실패: {type(e).__name__}: {e}")
            return []


# ===== 싱글톤 =====

_market_instance: Optional[KISMarketProvider] = None
_market_instance_lock = threading.Lock()


def get_kis_market_provider() -> KISMarketProvider:
    """싱글톤 인스턴스. ``KISApi`` 토큰 공유 (재발급 X)."""
    global _market_instance
    if _market_instance is None:
        with _market_instance_lock:
            if _market_instance is None:
                _market_instance = KISMarketProvider()
                logger.info("[kis_market_provider] 싱글톤 인스턴스 생성")
    return _market_instance


def reset_singleton() -> None:
    """테스트용: 싱글톤 초기화."""
    global _market_instance
    with _market_instance_lock:
        _market_instance = None
