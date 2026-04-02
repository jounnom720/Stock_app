import io
import os
import math
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

# =========================
# 기본 설정
# =========================
st.set_page_config(
    page_title="개인 투자 분석 앱",
    page_icon="📈",
    layout="wide"
)

# =========================
# 스타일 (UI 개선 핵심)
# =========================
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.3rem;
        padding-bottom: 2rem;
        max-width: 1450px;
    }
    h1 {
        font-size: 1.9rem !important;
        margin-bottom: 0.2rem !important;
    }
    h2 {
        font-size: 1.35rem !important;
        margin-top: 0.6rem !important;
        margin-bottom: 0.5rem !important;
    }
    h3 {
        font-size: 1.05rem !important;
        margin-top: 0.4rem !important;
        margin-bottom: 0.35rem !important;
    }
    .subtle-text {
        color: #6b7280;
        font-size: 0.93rem;
        margin-bottom: 0.8rem;
    }
    .developer-box {
        font-size: 0.93rem;
        padding: 0.45rem 0 0.15rem 0;
    }
    .metric-card {
        background: #f8fafc;
        border: 1px solid #e5e7eb;
        border-radius: 14px;
        padding: 0.8rem 0.9rem;
        min-height: 112px;
    }
    .metric-label {
        font-size: 0.88rem;
        color: #6b7280;
        margin-bottom: 0.15rem;
    }
    .metric-value {
        font-size: 1.22rem;
        font-weight: 600;
        margin-bottom: 0.15rem;
        line-height: 1.2;
    }
    .metric-change {
        font-size: 0.9rem;
        font-weight: 500;
    }
    .metric-status {
        margin-top: 0.35rem;
        font-size: 0.8rem;
        color: #6b7280;
    }
    .section-gap {
        margin-top: 0.65rem;
        margin-bottom: 0.25rem;
    }
    .small-note {
        color: #6b7280;
        font-size: 0.84rem;
    }
    hr {
        margin-top: 0.7rem;
        margin-bottom: 1rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =========================
# 유틸 함수
# =========================
def fmt_int(x):
    if pd.isna(x):
        return "-"
    try:
        return f"{int(round(float(x))):,}"
    except Exception:
        return "-"


def fmt_float(x, digits=2):
    if pd.isna(x):
        return "-"
    try:
        return f"{float(x):,.{digits}f}"
    except Exception:
        return "-"


def fmt_pct(x, digits=2):
    if pd.isna(x):
        return "-"
    try:
        return f"{float(x):,.{digits}f}%"
    except Exception:
        return "-"


def fmt_krw(x):
    if pd.isna(x):
        return "-"
    try:
        return f"{int(round(float(x))):,} 원"
    except Exception:
        return "-"


def safe_numeric(series):
    return pd.to_numeric(series, errors="coerce")


def ensure_ks_ticker(ticker):
    if pd.isna(ticker):
        return None
    t = str(ticker).strip().upper()
    if not t:
        return None

    # 이미 접미사가 있으면 그대로 사용
    if t.endswith((".KS", ".KQ", "=X", "F", "-USD", "^KS11", "^KQ11")):
        return t

    # 숫자 6자리 한국 종목코드 처리
    if t.isdigit() and len(t) == 6:
        return f"{t}.KS"

    return t


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out


def get_required_cols_map(df):
    normalized = {str(c).strip().lower(): c for c in df.columns}

    candidates = {
        "date": ["date", "날짜", "거래일", "매매일", "체결일"],
        "ticker": ["ticker", "종목코드", "코드", "symbol", "티커"],
        "name": ["name", "종목명", "종목", "이름"],
        "side": ["side", "구분", "매수매도", "거래구분", "매매구분"],
        "qty": ["qty", "수량", "보유수량", "체결수량"],
        "price": ["price", "단가", "체결가", "매입가", "매매가", "가격"],
        "amount": ["amount", "금액", "체결금액", "매수금액", "매도금액"],
        "fee": ["fee", "수수료"],
        "tax": ["tax", "세금", "거래세"],
    }

    col_map = {}
    for key, names in candidates.items():
        for n in names:
            if n in normalized:
                col_map[key] = normalized[n]
                break
    return col_map


def clean_trade_history(df_raw):
    df = df_raw.copy()
    col_map = get_required_cols_map(df)

    essential = ["date", "ticker", "side"]
    missing = [k for k in essential if k not in col_map]
    if missing:
        raise ValueError(f"필수 컬럼이 부족합니다: {', '.join(missing)}")

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df[col_map["date"]], errors="coerce")
    out["ticker"] = df[col_map["ticker"]].apply(ensure_ks_ticker)
    out["name"] = df[col_map.get("name", col_map["ticker"])] if "name" in col_map else df[col_map["ticker"]]
    out["side"] = df[col_map["side"]].astype(str).str.strip()
    out["qty"] = safe_numeric(df[col_map["qty"]]) if "qty" in col_map else np.nan
    out["price"] = safe_numeric(df[col_map["price"]]) if "price" in col_map else np.nan
    out["amount"] = safe_numeric(df[col_map["amount"]]) if "amount" in col_map else np.nan
    out["fee"] = safe_numeric(df[col_map["fee"]]) if "fee" in col_map else 0
    out["tax"] = safe_numeric(df[col_map["tax"]]) if "tax" in col_map else 0

    # 금액 없고 수량/단가 있으면 계산
    mask_amount_missing = out["amount"].isna() & out["qty"].notna() & out["price"].notna()
    out.loc[mask_amount_missing, "amount"] = out.loc[mask_amount_missing, "qty"] * out.loc[mask_amount_missing, "price"]

    # 중복 제거 (날짜/티커/구분/수량/단가/금액 기준)
    before = len(out)
    out = out.drop_duplicates(subset=["date", "ticker", "side", "qty", "price", "amount"], keep="first")
    removed_dup = before - len(out)

    # 날짜/티커/구분 정상값만 유지
    invalid_date = out["date"].isna().sum()
    invalid_ticker = out["ticker"].isna().sum()
    out = out.dropna(subset=["date", "ticker", "side"])

    # 수량 없는 행은 제외
    missing_qty = out["qty"].isna().sum()
    out = out[out["qty"].notna() & (out["qty"] != 0)]

    # 매수/매도 정규화
    out["side"] = (
        out["side"].replace(
            {
                "매수": "매수",
                "BUY": "매수",
                "buy": "매수",
                "B": "매수",
                "매도": "매도",
                "SELL": "매도",
                "sell": "매도",
                "S": "매도",
            }
        )
    )

    out = out.sort_values(["date", "ticker"]).reset_index(drop=True)

    info = {
        "removed_dup": removed_dup,
        "invalid_date": int(invalid_date),
        "invalid_ticker": int(invalid_ticker),
        "missing_qty": int(missing_qty),
        "rows_after": len(out),
    }
    return out, info


def build_portfolio_from_trades(trades):
    if trades.empty:
        return pd.DataFrame()

    records = []

    for ticker, g in trades.groupby("ticker"):
        g = g.sort_values("date")
        qty = 0.0
        cost = 0.0
        name = g["name"].dropna().astype(str).iloc[-1] if not g["name"].dropna().empty else ticker

        for _, row in g.iterrows():
            side = row["side"]
            q = float(row["qty"] or 0)
            amt = float(row["amount"] or 0)
            fee_tax = float(row.get("fee", 0) or 0) + float(row.get("tax", 0) or 0)

            if side == "매수":
                qty += q
                cost += amt + fee_tax
            elif side == "매도":
                if qty > 0:
                    avg_cost = cost / qty if qty != 0 else 0
                    sell_qty = min(q, qty)
                    cost_reduction = avg_cost * sell_qty
                    qty -= sell_qty
                    cost -= cost_reduction
                    if qty <= 0:
                        qty = 0
                        cost = 0

        if qty > 0:
            avg_buy = cost / qty if qty != 0 else np.nan
            records.append(
                {
                    "ticker": ticker,
                    "name": name,
                    "qty": qty,
                    "avg_buy": avg_buy,
                    "book_cost": cost,
                }
            )

    return pd.DataFrame(records)


@st.cache_data(ttl=300, show_spinner=False)
def fetch_price_data(ticker, period="1y", interval="1d"):
    t = yf.Ticker(ticker)
    hist = t.history(period=period, interval=interval, auto_adjust=False)
    if hist is None or hist.empty:
        return pd.DataFrame()
    hist = hist.reset_index()
    if "Date" in hist.columns:
        hist["Date"] = pd.to_datetime(hist["Date"]).dt.tz_localize(None)
    elif "Datetime" in hist.columns:
        hist["Date"] = pd.to_datetime(hist["Datetime"]).dt.tz_localize(None)
    hist = hist[[c for c in ["Date", "Open", "High", "Low", "Close", "Volume"] if c in hist.columns]]
    return hist


@st.cache_data(ttl=180, show_spinner=False)
def fetch_market_snapshot():
    assets = {
        "KOSPI": {"ticker": "^KS11", "label": "코스피"},
        "KOSDAQ": {"ticker": "^KQ11", "label": "코스닥"},
        "USDKRW": {"ticker": "KRW=X", "label": "원/달러"},
        "GOLD": {"ticker": "GC=F", "label": "국제금"},
        "WTI": {"ticker": "CL=F", "label": "WTI"},
        "BRENT": {"ticker": "BZ=F", "label": "브렌트유"},
        # 두바이유 직접 대체가 어려워 브렌트 기반 대체값 구조
        "DUBAI": {"ticker": "BZ=F", "label": "두바이유", "proxy": True},
        "S&P500": {"ticker": "^GSPC", "label": "S&P500"},
        "NASDAQ": {"ticker": "^IXIC", "label": "나스닥"},
    }

    rows = []
    for key, info in assets.items():
        ticker = info["ticker"]
        status = "실시간"
        try:
            hist = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=False)
            if hist is None or hist.empty or len(hist) < 2:
                rows.append(
                    {
                        "key": key,
                        "label": info["label"],
                        "price": np.nan,
                        "change_pct": np.nan,
                        "status": "실패",
                    }
                )
                continue

            close = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2])
            chg_pct = ((close - prev) / prev) * 100 if prev else np.nan

            if info.get("proxy"):
                status = "대체값"

            rows.append(
                {
                    "key": key,
                    "label": info["label"],
                    "price": close,
                    "change_pct": chg_pct,
                    "status": status,
                }
            )
        except Exception:
            rows.append(
                {
                    "key": key,
                    "label": info["label"],
                    "price": np.nan,
                    "change_pct": np.nan,
                    "status": "실패",
                }
            )

    return pd.DataFrame(rows)


def compute_portfolio_market_values(portfolio_df):
    if portfolio_df.empty:
        return portfolio_df

    df = portfolio_df.copy()
    prices = []
    changes = []
    for ticker in df["ticker"]:
        try:
            hist = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=False)
            if hist is None or hist.empty:
                prices.append(np.nan)
                changes.append(np.nan)
            else:
                close = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else np.nan
                chg = ((close - prev) / prev) * 100 if pd.notna(prev) and prev != 0 else np.nan
                prices.append(close)
                changes.append(chg)
        except Exception:
            prices.append(np.nan)
            changes.append(np.nan)

    df["current_price"] = prices
    df["change_pct"] = changes
    df["market_value"] = df["qty"] * df["current_price"]
    df["profit"] = df["market_value"] - df["book_cost"]
    df["profit_pct"] = np.where(df["book_cost"] != 0, df["profit"] / df["book_cost"] * 100, np.nan)
    total_mv = df["market_value"].sum(skipna=True)
    df["weight_pct"] = np.where(total_mv != 0, df["market_value"] / total_mv * 100, np.nan)
    return df


def render_market_cards(snapshot_df):
    cols = st.columns(3)
    for idx, (_, row) in enumerate(snapshot_df.iterrows()):
        with cols[idx % 3]:
            value = fmt_float(row["price"], 2)
            chg = row["change_pct"]
            chg_text = "-" if pd.isna(chg) else f"{chg:+.2f}%"
            color = "#dc2626" if pd.notna(chg) and chg > 0 else ("#2563eb" if pd.notna(chg) and chg < 0 else "#6b7280")
            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-label">{row['label']}</div>
                    <div class="metric-value">{value}</div>
                    <div class="metric-change" style="color:{color};">{chg_text}</div>
                    <div class="metric-status">상태: {row['status']}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def style_numeric_df(df, pct_cols=None, int_cols=None, float_cols=None):
    pct_cols = pct_cols or []
    int_cols = int_cols or []
    float_cols = float_cols or []

    format_dict = {}
    for c in pct_cols:
        if c in df.columns:
            format_dict[c] = "{:+,.2f}%"
    for c in int_cols:
        if c in df.columns:
            format_dict[c] = "{:,.0f}"
    for c in float_cols:
        if c in df.columns:
            format_dict[c] = "{:,.2f}"

    num_cols = [c for c in pct_cols + int_cols + float_cols if c in df.columns]

    styler = df.style.format(format_dict, na_rep="-")
    if num_cols:
        styler = styler.set_properties(subset=num_cols, **{"text-align": "right"})
    styler = styler.set_table_styles(
        [
            {"selector": "th", "props": [("font-size", "13px"), ("text-align", "center")]},
            {"selector": "td", "props": [("font-size", "13px")]},
        ]
    )
    return styler


def make_price_chart(hist, ma_list, show_candle=True, show_volume=True):
    if hist.empty:
        return None

    df = hist.copy()
    for ma in ma_list:
        df[f"MA{ma}"] = df["Close"].rolling(ma).mean()
    df["RSI14"] = rsi(df["Close"], 14)

    rows = 2 if show_volume else 1
    heights = [0.75, 0.25] if show_volume else [1.0]
    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=heights,
    )

    if show_candle:
        fig.add_trace(
            go.Candlestick(
                x=df["Date"],
                open=df["Open"],
                high=df["High"],
                low=df["Low"],
                close=df["Close"],
                name="가격",
            ),
            row=1,
            col=1,
        )
    else:
        fig.add_trace(
            go.Scatter(x=df["Date"], y=df["Close"], mode="lines", name="종가"),
            row=1,
            col=1,
        )

    for ma in ma_list:
        fig.add_trace(
            go.Scatter(x=df["Date"], y=df[f"MA{ma}"], mode="lines", name=f"MA{ma}"),
            row=1,
            col=1,
        )

    if show_volume:
        fig.add_trace(
            go.Bar(x=df["Date"], y=df["Volume"], name="거래량"),
            row=2,
            col=1,
        )

    fig.update_layout(
        height=700 if show_volume else 540,
        margin=dict(l=20, r=20, t=35, b=20),
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    fig.update_yaxes(tickformat=",", row=1, col=1)
    if show_volume:
        fig.update_yaxes(tickformat=",", row=2, col=1)
    return fig, df


# =========================
# 세션 상태
# =========================
if "trades" not in st.session_state:
    st.session_state.trades = pd.DataFrame()

# =========================
# 헤더
# =========================
st.title("개인 투자 분석 앱")
st.markdown('<div class="subtle-text">실시간 시장 요약 · 거래이력 분석 · 포트폴리오 평가 · 차트 확인</div>', unsafe_allow_html=True)
st.markdown("<hr>", unsafe_allow_html=True)

# =========================
# 1. 대시보드
# =========================
st.markdown("## 대시보드", unsafe_allow_html=True)
try:
    snapshot = fetch_market_snapshot()
    render_market_cards(snapshot)
except Exception as e:
    st.warning(f"대시보드 데이터를 불러오는 중 오류가 발생했습니다: {e}")

st.markdown("<div class='section-gap'></div>", unsafe_allow_html=True)
st.markdown("<hr>", unsafe_allow_html=True)

# =========================
# 2. 거래이력 업로드
# =========================
st.markdown("## 거래이력", unsafe_allow_html=True)
left, right = st.columns([1.2, 1])

with left:
    uploaded = st.file_uploader("거래이력 엑셀 파일 업로드", type=["xlsx", "xls", "csv"])

with right:
    st.markdown(
        """
        <div class='small-note'>
        권장 컬럼 예시: 날짜, 종목코드, 종목명, 매수매도, 수량, 단가, 체결금액, 수수료, 세금
        </div>
        """,
        unsafe_allow_html=True,
    )

if uploaded is not None:
    try:
        if uploaded.name.lower().endswith(".csv"):
            raw_df = pd.read_csv(uploaded)
        else:
            raw_df = pd.read_excel(uploaded)

        trades, info = clean_trade_history(raw_df)
        st.session_state.trades = trades

        st.success(f"거래이력 반영 완료: {info['rows_after']:,}건")

        msg_parts = []
        if info["removed_dup"] > 0:
            msg_parts.append(f"중복 제거 {info['removed_dup']:,}건")
        if info["invalid_date"] > 0:
            msg_parts.append(f"날짜 오류 {info['invalid_date']:,}건")
        if info["invalid_ticker"] > 0:
            msg_parts.append(f"티커 오류 {info['invalid_ticker']:,}건")
        if info["missing_qty"] > 0:
            msg_parts.append(f"수량 누락 제외 {info['missing_qty']:,}건")

        if msg_parts:
            st.info(" / ".join(msg_parts))

    except Exception as e:
        st.error(f"엑셀 파일 처리 중 오류가 발생했습니다: {e}")

trades_df = st.session_state.trades.copy()

if not trades_df.empty:
    show_trades = trades_df.copy()
    show_trades["date"] = pd.to_datetime(show_trades["date"]).dt.strftime("%Y-%m-%d")
    show_trades = show_trades.rename(
        columns={
            "date": "날짜",
            "ticker": "티커",
            "name": "종목명",
            "side": "구분",
            "qty": "수량",
            "price": "단가",
            "amount": "금액",
            "fee": "수수료",
            "tax": "세금",
        }
    )
    st.dataframe(
        style_numeric_df(
            show_trades,
            int_cols=["수량", "단가", "금액", "수수료", "세금"],
        ),
        use_container_width=True,
        height=300,
    )
else:
    st.info("거래이력 파일을 업로드하면 이곳에 표시됩니다.")

st.markdown("<hr>", unsafe_allow_html=True)

# =========================
# 3. 포트폴리오
# =========================
st.markdown("## 포트폴리오", unsafe_allow_html=True)
portfolio = build_portfolio_from_trades(trades_df)
portfolio_eval = compute_portfolio_market_values(portfolio) if not portfolio.empty else pd.DataFrame()

if not portfolio_eval.empty:
    total_cost = portfolio_eval["book_cost"].sum(skipna=True)
    total_mv = portfolio_eval["market_value"].sum(skipna=True)
    total_profit = portfolio_eval["profit"].sum(skipna=True)
    total_profit_pct = (total_profit / total_cost * 100) if total_cost else np.nan

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 매입금액", fmt_int(total_cost))
    c2.metric("현재 평가금액", fmt_int(total_mv))
    c3.metric("평가손익", fmt_int(total_profit))
    c4.metric("수익률", fmt_pct(total_profit_pct))

    display_pf = portfolio_eval.rename(
        columns={
            "ticker": "티커",
            "name": "종목명",
            "qty": "보유수량",
            "avg_buy": "평균매입가",
            "book_cost": "매입금액",
            "current_price": "현재가",
            "change_pct": "등락률",
            "market_value": "평가금액",
            "profit": "평가손익",
            "profit_pct": "수익률",
            "weight_pct": "비중",
        }
    )

    st.dataframe(
        style_numeric_df(
            display_pf,
            pct_cols=["등락률", "수익률", "비중"],
            int_cols=["보유수량", "평균매입가", "매입금액", "현재가", "평가금액", "평가손익"],
        ),
        use_container_width=True,
        height=340,
    )
else:
    st.info("거래이력을 반영하면 현재 포트폴리오가 자동 계산됩니다.")

st.markdown("<hr>", unsafe_allow_html=True)

# =========================
# 4. 차트 분석
# =========================
st.markdown("## 차트 분석", unsafe_allow_html=True)

chart_col1, chart_col2, chart_col3, chart_col4 = st.columns([1.2, 1, 1, 1])

candidate_tickers = []
if not portfolio_eval.empty:
    candidate_tickers = portfolio_eval["ticker"].tolist()
else:
    candidate_tickers = ["005930.KS", "000660.KS", "069500.KS"]

with chart_col1:
    selected_ticker = st.selectbox("종목 선택", candidate_tickers, index=0)
with chart_col2:
    period = st.selectbox("조회기간", ["3mo", "6mo", "1y", "2y", "5y"], index=2)
with chart_col3:
    chart_type = st.selectbox("차트 유형", ["캔들", "라인"], index=0)
with chart_col4:
    ma_choice = st.multiselect("이동평균선", [20, 60, 120], default=[20, 60, 120])

# 조회기간과 무관하게 지표 계산용 여유 데이터 확보
buffer_period_map = {
    "3mo": "1y",
    "6mo": "1y",
    "1y": "2y",
    "2y": "5y",
    "5y": "10y",
}
fetch_period = buffer_period_map.get(period, "2y")

hist_full = fetch_price_data(selected_ticker, period=fetch_period, interval="1d")

if not hist_full.empty:
    end_date = hist_full["Date"].max()
    if period == "3mo":
        start_cut = end_date - pd.DateOffset(months=3)
    elif period == "6mo":
        start_cut = end_date - pd.DateOffset(months=6)
    elif period == "1y":
        start_cut = end_date - pd.DateOffset(years=1)
    elif period == "2y":
        start_cut = end_date - pd.DateOffset(years=2)
    else:
        start_cut = end_date - pd.DateOffset(years=5)

    # 계산은 hist_full 전체로, 표시만 잘라냄
    fig, enriched = make_price_chart(hist_full, ma_choice, show_candle=(chart_type == "캔들"), show_volume=True)
    view_df = enriched[enriched["Date"] >= start_cut].copy()

    if not view_df.empty:
        fig2, view_df2 = make_price_chart(view_df, ma_choice, show_candle=(chart_type == "캔들"), show_volume=True)
        st.plotly_chart(fig2, use_container_width=True)

        latest = view_df2.iloc[-1]
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("종가", fmt_int(latest["Close"]))
        r2.metric("RSI(14)", fmt_float(latest["RSI14"], 2))
        if 20 in ma_choice:
            r3.metric("MA20", fmt_int(latest.get("MA20", np.nan)))
        else:
            r3.metric("MA20", "-")
        if 60 in ma_choice:
            r4.metric("MA60", fmt_int(latest.get("MA60", np.nan)))
        else:
            r4.metric("MA60", "-")

        if 120 in ma_choice:
            st.caption(f"MA120: {fmt_int(latest.get('MA120', np.nan))}")
    else:
        st.warning("선택한 기간에 표시할 데이터가 없습니다.")
else:
    st.warning("차트 데이터를 불러오지 못했습니다.")

st.markdown("<hr>", unsafe_allow_html=True)

# =========================
# 5. 데이터 다운로드
# =========================
st.markdown("## 데이터 내보내기", unsafe_allow_html=True)
d1, d2 = st.columns(2)

with d1:
    if not trades_df.empty:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            trades_df.to_excel(writer, index=False, sheet_name="trades")
        st.download_button(
            label="거래이력 다운로드",
            data=buf.getvalue(),
            file_name="trade_history_cleaned.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

with d2:
    if not portfolio_eval.empty:
        buf2 = io.BytesIO()
        with pd.ExcelWriter(buf2, engine="openpyxl") as writer:
            portfolio_eval.to_excel(writer, index=False, sheet_name="portfolio")
        st.download_button(
            label="포트폴리오 다운로드",
            data=buf2.getvalue(),
            file_name="portfolio_evaluation.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# =========================
# 하단 개발자 표시
# =========================
st.markdown("<div class='developer-box'>개발자 : <a href='mailto:hwcho@me.com'>조현웅 hwcho@me.com</a></div>", unsafe_allow_html=True)
