# -*- coding: utf-8 -*-
"""
investor_trend.py

투자자별(외국인/기관/개인) 순매수 동향을 조회하고,
"며칠 연속 순매수/순매도 중인지" 추세를 계산하는 모듈입니다.

pykrx의 get_market_trading_value_by_date()로 특정 종목의
일별 투자자별 순매수 거래대금을 가져옵니다.

※ 체결강도는 실시간 틱 데이터가 필요해 무료 라이브러리로는 제공되지 않아
   이 모듈에는 포함하지 않았습니다. (증권사 정식 API 연동 시 추가 가능)

사용 예:
    from investor_trend import get_investor_trend

    result = get_investor_trend("005930")
    print(result.summary_dict())
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

try:
    import streamlit as st
    _cache_data = st.cache_data
except ImportError:
    def _cache_data(ttl=None):
        def decorator(func):
            return func
        return decorator


LOOKBACK_DAYS = 15          # 연속 추세 계산을 위해 넉넉히 조회
CACHE_TTL_SECONDS = 6 * 60 * 60

# pykrx가 반환하는 컬럼명은 버전에 따라 조금씩 다를 수 있어,
# 정확히 일치하는 이름이 없으면 이 키워드가 포함된 컬럼을 찾습니다.
INVESTOR_COLUMN_HINTS = {
    "외국인": ["외국인합계", "외국인"],
    "기관": ["기관합계", "기관"],
    "개인": ["개인"],
}


@dataclass
class InvestorTrendResult:
    """투자자별 순매수 동향 결과를 담는 데이터 클래스"""
    ticker: str
    latest_date: str
    # 투자자 유형별: {"latest_net": 오늘 순매수 거래대금, "streak_days": 연속일수, "streak_direction": "순매수"/"순매도"/None}
    trends: dict = field(default_factory=dict)
    data_available: bool = True

    def summary_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "latest_date": self.latest_date,
            "trends": self.trends,
            "data_available": self.data_available,
        }


def _find_column(df: pd.DataFrame, hints: list) -> Optional[str]:
    """컬럼명 후보들 중 데이터프레임에 실제 존재하는 첫 번째 컬럼을 찾습니다."""
    for hint in hints:
        if hint in df.columns:
            return hint
    # 정확히 일치하는 게 없으면 포함 관계로 재탐색
    for hint in hints:
        for col in df.columns:
            if hint in col:
                return col
    return None


def _calc_streak(series: pd.Series):
    """
    최근 값부터 거꾸로 훑으면서, 같은 방향(순매수/순매도)이 며칠 이어졌는지 계산합니다.

    Returns:
        (direction, days): direction은 "순매수"/"순매도"/None, days는 연속 일수
    """
    if series.empty:
        return None, 0

    values = series.tolist()
    values.reverse()  # 최신 -> 과거 순서로

    latest = values[0]
    if latest == 0:
        return None, 0

    direction = "순매수" if latest > 0 else "순매도"
    days = 0
    for v in values:
        if direction == "순매수" and v > 0:
            days += 1
        elif direction == "순매도" and v < 0:
            days += 1
        else:
            break

    return direction, days


@_cache_data(ttl=CACHE_TTL_SECONDS)
def fetch_investor_trading_value(ticker: str, lookback_days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """
    pykrx로 종목의 일별 투자자별 순매수 거래대금을 가져옵니다.

    Returns:
        pd.DataFrame: 날짜 인덱스, 투자자 유형별 순매수 거래대금 컬럼.
                       조회 실패 시 빈 데이터프레임 반환.
    """
    from pykrx import stock

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")

    try:
        df = stock.get_market_trading_value_by_date(start_date, end_date, ticker)
    except Exception:
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    return df


def get_investor_trend(ticker: str) -> InvestorTrendResult:
    """
    종목코드를 받아 외국인/기관/개인의 순매수 동향(오늘 순매수액 + 연속 추세)을 계산합니다.

    Args:
        ticker: 종목코드 6자리 (예: "005930")

    Returns:
        InvestorTrendResult: 투자자 유형별 동향을 담은 결과 객체
    """
    df = fetch_investor_trading_value(ticker)

    if df.empty:
        return InvestorTrendResult(
            ticker=ticker,
            latest_date="",
            trends={},
            data_available=False,
        )

    trends = {}
    for investor_type, hints in INVESTOR_COLUMN_HINTS.items():
        col = _find_column(df, hints)
        if col is None:
            continue

        series = df[col]
        latest_net = int(series.iloc[-1])
        direction, streak_days = _calc_streak(series)

        trends[investor_type] = {
            "latest_net": latest_net,
            "streak_direction": direction,
            "streak_days": streak_days,
        }

    return InvestorTrendResult(
        ticker=ticker,
        latest_date=str(df.index[-1].date()),
        trends=trends,
        data_available=True,
    )


# ---------------------------------------------------------
# 간단한 동작 확인용 (직접 실행 시에만 동작)
# ---------------------------------------------------------
if __name__ == "__main__":
    import pandas as pd

    # pykrx 실제 통신 없이, 연속 추세 계산 로직만 가상 데이터로 검증
    print("=== _calc_streak 로직 테스트 ===")

    # 최근 3일 연속 순매수 (양수)
    s1 = pd.Series([100, -50, 200, 300, 150])
    print("케이스1 (최근 3일 연속 순매수):", _calc_streak(s1))

    # 최근 2일 연속 순매도 (음수)
    s2 = pd.Series([200, 100, -50, -30])
    print("케이스2 (최근 2일 연속 순매도):", _calc_streak(s2))

    # 오늘 순매수가 0인 경우
    s3 = pd.Series([100, 200, 0])
    print("케이스3 (오늘 0):", _calc_streak(s3))
