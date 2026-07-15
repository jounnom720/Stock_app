# -*- coding: utf-8 -*-
"""
test_indicators_app.py

레이어 2(종목별 지표 계산)를 실행 화면으로 눈으로 확인하기 위한
독립 테스트용 Streamlit 앱입니다.

본 서비스(stock_app_main.py)와는 완전히 별개로 동작하므로,
여기서 무슨 일이 생겨도 지인들이 쓰는 본 앱에는 영향이 없습니다.

이 파일 하나만 있으면 실행되도록, stock_indicators.py의 로직을
그대로 이 파일 안에 포함시켰습니다 (별도 import 불필요).
"""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

st.set_page_config(page_title="종목 지표 테스트", layout="centered")


# ---------------------------------------------------------
# 지표 계산 로직 (stock_indicators.py와 동일한 내용)
# ---------------------------------------------------------
MA_WINDOW = 20
RSI_PERIOD = 14
VOLUME_WINDOW = 20
LOOKBACK_DAYS = 60


@st.cache_data(ttl=6 * 60 * 60)
def fetch_ohlcv(ticker: str, lookback_days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    from pykrx import stock

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")

    try:
        df = stock.get_market_ohlcv_by_date(start_date, end_date, ticker)
    except Exception as e:
        st.error(f"pykrx 조회 중 오류 발생: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    return df


def calc_ma_deviation(close_series: pd.Series, window: int = MA_WINDOW):
    if len(close_series) < window:
        return None
    moving_avg = close_series.rolling(window=window).mean().iloc[-1]
    latest_close = close_series.iloc[-1]
    if moving_avg == 0 or pd.isna(moving_avg):
        return None
    return round(float((latest_close - moving_avg) / moving_avg * 100), 2)


def calc_rsi(close_series: pd.Series, period: int = RSI_PERIOD):
    if len(close_series) < period + 1:
        return None
    delta = close_series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean().iloc[-1]
    avg_loss = loss.rolling(window=period).mean().iloc[-1]
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(float(100 - (100 / (1 + rs))), 2)


def calc_volume_ratio(volume_series: pd.Series, window: int = VOLUME_WINDOW):
    if len(volume_series) < window + 1:
        return None
    avg_volume = volume_series.iloc[-(window + 1):-1].mean()
    latest_volume = volume_series.iloc[-1]
    if avg_volume == 0 or pd.isna(avg_volume):
        return None
    return round(float(latest_volume / avg_volume), 2)


# ---------------------------------------------------------
# 화면 UI
# ---------------------------------------------------------
st.title("종목 지표 테스트")
st.caption("레이어 2(이동평균 이격도 / RSI / 거래량 배율) 실행 결과를 눈으로 확인하는 화면입니다.")

col1, col2 = st.columns([3, 1])
with col1:
    ticker = st.text_input("종목코드 입력 (예: 삼성전자 005930, KODEX200 069500)", value="005930")
with col2:
    st.write("")
    st.write("")
    run = st.button("조회하기", use_container_width=True)

if run:
    with st.spinner("pykrx에서 데이터를 가져오는 중..."):
        df = fetch_ohlcv(ticker)

    if df.empty:
        st.warning("데이터를 가져오지 못했습니다. 종목코드를 다시 확인해주세요.")
    else:
        close_series = df["종가"]
        volume_series = df["거래량"]

        ma_dev = calc_ma_deviation(close_series)
        rsi = calc_rsi(close_series)
        vol_ratio = calc_volume_ratio(volume_series)

        st.success(f"조회 완료 — 최근 {len(df)}일치 데이터 확보 (기준일: {df.index[-1].date()})")

        m1, m2, m3 = st.columns(3)
        m1.metric("이동평균 이격도", f"{ma_dev}%" if ma_dev is not None else "데이터 부족")
        m2.metric("RSI", f"{rsi}" if rsi is not None else "데이터 부족")
        m3.metric("거래량 배율", f"{vol_ratio}배" if vol_ratio is not None else "데이터 부족")

        st.divider()
        st.subheader("원본 데이터 (최근 10일)")
        st.dataframe(df.tail(10))
