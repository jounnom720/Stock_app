import streamlit as st
import pandas as pd
from pykrx import stock
from datetime import datetime, timedelta
import plotly.graph_objects as go

st.set_page_config(page_title="Jone 주식 분석기", layout="wide")

st.title("📈 Jone 포트폴리오 분석기")

# 보유 종목
WATCHLIST = {
    "069500": "KODEX 200",
    "005930": "삼성전자",
    "000660": "SK하이닉스"
}

# 기본 포트폴리오 (수량/매입가는 수정하세요)
portfolio = pd.DataFrame([
    {"ticker": "069500", "name": "KODEX 200", "quantity": 94, "buy_price": 84026},
    {"ticker": "005930", "name": "삼성전자", "quantity": 27, "buy_price": 187700},
    {"ticker": "000660", "name": "SK하이닉스", "quantity": 1, "buy_price": 941000}
])


def get_price(ticker):
    today = datetime.today().strftime("%Y%m%d")
    df = stock.get_market_ohlcv_by_date(today, today, ticker)
    if df.empty:
        return None
    return df.iloc[-1]["종가"]


def get_history(ticker):

    end = datetime.today()
    start = end - timedelta(days=180)

    df = stock.get_market_ohlcv_by_date(
        start.strftime("%Y%m%d"),
        end.strftime("%Y%m%d"),
        ticker
    )

    df = df.rename(columns={"종가": "Close"})
    df["MA20"] = df["Close"].rolling(20).mean()

    return df


# 현재 가격 요약
st.subheader("현재 주가")

cols = st.columns(3)

prices = {}

for i, ticker in enumerate(WATCHLIST):

    name = WATCHLIST[ticker]
    price = get_price(ticker)

    prices[ticker] = price

    with cols[i]:
        if price:
            st.metric(name, f"{price:,.0f}원")
        else:
            st.metric(name, "데이터 없음")


# 포트폴리오 계산

st.subheader("포트폴리오 현황")

portfolio["current_price"] = portfolio["ticker"].apply(lambda x: prices.get(x))
portfolio["eval_amount"] = portfolio["current_price"] * portfolio["quantity"]
portfolio["invest_amount"] = portfolio["buy_price"] * portfolio["quantity"]

portfolio["profit"] = portfolio["eval_amount"] - portfolio["invest_amount"]
portfolio["return"] = portfolio["profit"] / portfolio["invest_amount"] * 100

total_eval = portfolio["eval_amount"].sum()

portfolio["weight"] = portfolio["eval_amount"] / total_eval * 100

st.dataframe(portfolio)

# 그래프

st.subheader("주가 그래프")

select = st.selectbox(
    "종목 선택",
    portfolio["name"]
)

ticker = portfolio[portfolio["name"] == select]["ticker"].values[0]

df = get_history(ticker)

fig = go.Figure()

fig.add_trace(go.Scatter(
    x=df.index,
    y=df["Close"],
    name="가격"
))

fig.add_trace(go.Scatter(
    x=df.index,
    y=df["MA20"],
    name="20일 평균"
))

st.plotly_chart(fig, use_container_width=True)


# 리밸런싱

st.subheader("리밸런싱 계산")

col1, col2, col3 = st.columns(3)

with col1:
    w1 = st.number_input("KODEX200 목표 비중", value=50)

with col2:
    w2 = st.number_input("삼성전자 목표 비중", value=25)

with col3:
    w3 = st.number_input("SK하이닉스 목표 비중", value=25)

target = {
    "069500": w1,
    "005930": w2,
    "000660": w3
}

portfolio["target_weight"] = portfolio["ticker"].map(target)

portfolio["target_amount"] = total_eval * portfolio["target_weight"] / 100

portfolio["rebalance_amount"] = portfolio["target_amount"] - portfolio["eval_amount"]

portfolio["rebalance_shares"] = portfolio["rebalance_amount"] / portfolio["current_price"]

portfolio["rebalance_shares"] = portfolio["rebalance_shares"].round()

st.subheader("리밸런싱 결과")

result = portfolio[[
    "name",
    "weight",
    "target_weight",
    "rebalance_amount",
    "rebalance_shares"
]]

st.dataframe(result)

st.write("양수 = 추가 매수 / 음수 = 비중 축소")