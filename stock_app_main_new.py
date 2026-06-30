"""
통합자산관리 시스템 v1.0
- 신한은행 IRP (TDF + ETF)
- 미래에셋증권 (국내주식 + ETF)
- Google Sheets 실시간 연동
- yfinance 실시간 시세
"""

import streamlit as st
import pandas as pd
import numpy as np
import gspread
import yfinance as yf
import plotly.graph_objects as go
import plotly.express as px
from google.oauth2.service_account import Credentials
from datetime import datetime, date
from zoneinfo import ZoneInfo
import logging

# ============================================================
# 기본 설정
# ============================================================
logging.basicConfig(level=logging.WARNING)
KST = ZoneInfo("Asia/Seoul")
APP_VERSION = "v1.0.0"

st.set_page_config(
    page_title=f"통합자산관리 시스템 {APP_VERSION}",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ============================================================
# 종목 마스터 데이터
# ============================================================
ASSET_MASTER = {
    "069500": {"name": "KODEX 200",         "ticker": "069500.KS", "type": "ETF",  "market": "KS"},
    "102110": {"name": "TIGER 200",          "ticker": "102110.KS", "type": "ETF",  "market": "KS"},
    "471990": {"name": "KODEX AI반도체핵심장비", "ticker": "471990.KS", "type": "ETF",  "market": "KS"},
    "487240": {"name": "KODEX AI전력핵심설비",  "ticker": "487240.KS", "type": "ETF",  "market": "KS"},
    "229200": {"name": "KODEX 코스닥150",     "ticker": "229200.KS", "type": "ETF",  "market": "KS"},
    "0148J0": {"name": "TIGER 코리아휴머노이드로봇산업", "ticker": "148J0.KS", "type": "ETF", "market": "KS"},
    "005930": {"name": "삼성전자",            "ticker": "005930.KS", "type": "주식", "market": "KS"},
    "000660": {"name": "SK하이닉스",          "ticker": "000660.KS", "type": "주식", "market": "KS"},
    "278470": {"name": "에이피알",            "ticker": "278470.KS", "type": "주식", "market": "KS"},
    "009150": {"name": "삼성전기",            "ticker": "009150.KS", "type": "주식", "market": "KS"},
    "005380": {"name": "현대차",             "ticker": "005380.KS", "type": "주식", "market": "KS"},
    "042660": {"name": "한화오션",            "ticker": "042660.KS", "type": "주식", "market": "KS"},
    "071970": {"name": "HD현대마린엔진",       "ticker": "071970.KS", "type": "주식", "market": "KS"},
}

# ============================================================
# Google Sheets 연결
# ============================================================
SHEET_NAMES = {
    "거래이력":        "거래이력",
    "비주식자산":      "비주식자산",
    "통합요약":        "통합요약",
    "자산변화로그":    "자산변화로그",
    "현금성자산":      "현금성자산",
    "원금변동원장":    "원금변동원장",
    "자산스냅샷":      "자산스냅샷",
    "월별자산스냅샷":  "월별자산스냅샷",
}

@st.cache_resource(ttl=60)
def get_gspread_client():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"],
        )
        return gspread.authorize(creds)
    except Exception as e:
        logging.warning("gspread 연결 실패: %s", e)
        return None

@st.cache_resource(ttl=60)
def get_spreadsheet():
    client = get_gspread_client()
    if client is None:
        return None
    try:
        # Secrets 구조: [google_sheets] spreadsheet_id = "..."
        sheet_id = st.secrets["google_sheets"]["spreadsheet_id"]
        return client.open_by_key(sheet_id)
    except Exception as e:
        logging.warning("스프레드시트 열기 실패: %s", e)
        return None

@st.cache_data(ttl=30)
def load_sheet(sheet_name: str) -> pd.DataFrame:
    try:
        spreadsheet = get_spreadsheet()
        if spreadsheet is None:
            return pd.DataFrame()
        ws = spreadsheet.worksheet(sheet_name)
        records = ws.get_all_records()
        df = pd.DataFrame(records)
        # gspread가 숫자 셀을 int로 반환 → 종목코드 앞자리 0 유실 방지
        if "종목코드" in df.columns:
            df["종목코드"] = df["종목코드"].apply(
                lambda x: str(int(x)).zfill(6) if str(x).strip().isdigit() else str(x).strip()
            )
        return df
    except Exception as e:
        logging.warning("시트 로드 실패 [%s]: %s", sheet_name, e)
        return pd.DataFrame()

def save_sheet_rows(sheet_name: str, rows: list[dict]) -> bool:
    try:
        spreadsheet = get_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet(sheet_name)
        for row in rows:
            ws.append_row(list(row.values()), value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        logging.warning("시트 저장 실패 [%s]: %s", sheet_name, e)
        return False

def update_sheet_cell(sheet_name: str, row: int, col: int, value) -> bool:
    try:
        spreadsheet = get_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet(sheet_name)
        ws.update_cell(row, col, value)
        return True
    except Exception as e:
        logging.warning("셀 업데이트 실패: %s", e)
        return False

# ============================================================
# 실시간 시세 조회
# ============================================================
@st.cache_data(ttl=300)
def get_prices(tickers: list[str]) -> dict[str, float]:
    """yfinance로 현재가 일괄 조회. 실패 시 빈 dict 반환."""
    if not tickers:
        return {}
    try:
        ticker_str = " ".join(tickers)
        data = yf.download(ticker_str, period="2d", progress=False, auto_adjust=True)
        prices = {}
        if "Close" in data.columns:
            latest = data["Close"].dropna().iloc[-1]
            if hasattr(latest, "items"):
                for t, p in latest.items():
                    prices[t] = float(p)
            else:
                prices[tickers[0]] = float(latest)
        return prices
    except Exception as e:
        logging.warning("시세 조회 실패: %s", e)
        return {}

def get_current_price(code: str, prices: dict) -> float | None:
    meta = ASSET_MASTER.get(code)
    if meta is None:
        return None
    ticker = meta["ticker"]
    return prices.get(ticker)

# ============================================================
# 보유 종목 계산
# ============================================================
def calc_holdings(trade_df: pd.DataFrame) -> pd.DataFrame:
    """거래이력으로 현재 보유 종목과 평균단가 계산."""
    if trade_df.empty:
        return pd.DataFrame()

    holdings = {}
    for _, row in trade_df.iterrows():
        code = str(row.get("종목코드", "")).strip()
        name = str(row.get("종목명", "")).strip()
        qty = int(row.get("거래수량", 0))
        price = float(row.get("거래단가", 0))
        account = str(row.get("운용사", "")).strip()
        구분 = str(row.get("거래구분", "")).strip()

        if code not in holdings:
            holdings[code] = {"종목코드": code, "종목명": name, "계좌": account,
                               "보유수량": 0, "매수금액합계": 0.0}
        if 구분 == "매수":
            holdings[code]["보유수량"] += qty
            holdings[code]["매수금액합계"] += qty * price
        elif 구분 == "매도":
            if holdings[code]["보유수량"] > 0:
                avg = holdings[code]["매수금액합계"] / holdings[code]["보유수량"]
                holdings[code]["보유수량"] = max(0, holdings[code]["보유수량"] - qty)
                holdings[code]["매수금액합계"] = avg * holdings[code]["보유수량"]

    rows = []
    for code, h in holdings.items():
        if h["보유수량"] > 0:
            avg = h["매수금액합계"] / h["보유수량"] if h["보유수량"] else 0
            rows.append({
                "종목코드": code,
                "종목명": h["종목명"],
                "계좌": h["계좌"],
                "보유수량": h["보유수량"],
                "평균단가": round(avg),
                "매입금액": round(avg * h["보유수량"]),
            })

    return pd.DataFrame(rows)

def enrich_with_prices(holdings_df: pd.DataFrame, prices: dict) -> pd.DataFrame:
    """보유 종목에 현재가·평가금액·손익 추가."""
    if holdings_df.empty:
        return holdings_df

    df = holdings_df.copy()
    df["현재가"] = df["종목코드"].apply(lambda c: get_current_price(c, prices))

    def _is_valid_price(v):
        if v is None:
            return False
        try:
            return not np.isnan(float(v))
        except Exception:
            return False

    def _calc_eval(r):
        if _is_valid_price(r["현재가"]):
            return round(float(r["현재가"]) * int(r["보유수량"]))
        return int(r["매입금액"])

    def _calc_pct(r):
        cost = int(r["매입금액"]) if r["매입금액"] else 0
        if cost == 0:
            return 0.0
        return round((int(r["평가금액"]) - cost) / cost * 100, 2)

    df["평가금액"] = df.apply(_calc_eval, axis=1)
    df["평가손익"] = df["평가금액"] - df["매입금액"].astype(int)
    df["수익률"]  = df.apply(_calc_pct, axis=1)
    df["시세반영"] = df["현재가"].apply(_is_valid_price)
    return df

# ============================================================
# 숫자 포맷 유틸
# ============================================================
def fmt_money(v, suffix="원") -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    v = int(v)
    if abs(v) >= 100_000_000:
        return f"{v/100_000_000:.2f}억{suffix}"
    if abs(v) >= 10_000:
        return f"{v:,}{suffix}"
    return f"{v:,}{suffix}"

def fmt_pct(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    return f"{float(v):+.2f}%"

def color_pnl(v) -> str:
    """한국 주식앱 기준: 상승=빨강(#e53935), 하락=파랑(#1976d2)"""
    if v is None:
        return "#9e9e9e"
    try:
        f = float(v)
    except Exception:
        return "#9e9e9e"
    return "#e53935" if f > 0 else "#1976d2" if f < 0 else "#9e9e9e"

def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")

# ============================================================
# CSS
# ============================================================
st.markdown("""
<style>

/* 한국 주식앱 색상 기준: 상승=파랑, 하락=빨강 */
:root {
    --color-up:   #1976d2;
    --color-down: #e53935;
    --color-flat: #9e9e9e;
}
.metric-card {
    background: var(--secondary-background-color);
    border-radius: 12px;
    padding: 1.1rem 1.3rem;
    margin-bottom: 0.6rem;
    border: 1px solid rgba(128,128,128,0.15);
}
.metric-label {
    font-size: 0.78rem;
    color: gray;
    margin-bottom: 0.2rem;
    font-weight: 500;
}
.metric-value {
    font-size: 1.45rem;
    font-weight: 700;
    line-height: 1.2;
}
.metric-sub {
    font-size: 0.82rem;
    margin-top: 0.2rem;
}
.account-card {
    background: var(--secondary-background-color);
    border-radius: 10px;
    padding: 0.9rem 1.1rem;
    border: 1px solid rgba(128,128,128,0.15);
    margin-bottom: 0.5rem;
}
.tag {
    display: inline-block;
    border-radius: 6px;
    padding: 0.15rem 0.55rem;
    font-size: 0.72rem;
    font-weight: 600;
    margin-right: 0.3rem;
}
.tag-irp  { background: rgba(33,150,243,0.15); color: #1976d2; }
.tag-mira { background: rgba(76,175,80,0.15);  color: #388e3c; }
.tag-etf  { background: rgba(156,39,176,0.12); color: #7b1fa2; }
.tag-stock{ background: rgba(255,152,0,0.12);  color: #f57c00; }
.section-title {
    font-size: 1.05rem;
    font-weight: 700;
    margin: 1.2rem 0 0.6rem 0;
    padding-bottom: 0.3rem;
    border-bottom: 2px solid rgba(128,128,128,0.2);
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# 데이터 로드
# ============================================================
@st.cache_data(ttl=30)
def load_all_data():
    trade_df     = load_sheet("거래이력")
    nonstock_df  = load_sheet("비주식자산")
    cash_df      = load_sheet("현금성자산")
    snapshot_df  = load_sheet("자산스냅샷")
    log_df       = load_sheet("자산변화로그")
    ledger_df    = load_sheet("원금변동원장")
    monthly_df   = load_sheet("월별자산스냅샷")
    return trade_df, nonstock_df, cash_df, snapshot_df, log_df, ledger_df, monthly_df

# ============================================================
# 메인 앱
# ============================================================
def main():
    # 헤더
    col_title, col_time = st.columns([4, 1])
    with col_title:
        st.markdown("## 📊 통합자산관리 시스템")
    with col_time:
        st.markdown(f"<div style='text-align:right;color:gray;font-size:0.8rem;padding-top:1rem'>{now_kst()} 기준</div>",
                    unsafe_allow_html=True)

    # 데이터 로드
    with st.spinner("데이터 불러오는 중..."):
        trade_df, nonstock_df, cash_df, snapshot_df, log_df, ledger_df, monthly_df = load_all_data()

    # 보유 종목 계산
    holdings_df = calc_holdings(trade_df)

    # 시세 조회
    tickers = []
    for code in holdings_df["종목코드"].tolist() if not holdings_df.empty else []:
        meta = ASSET_MASTER.get(code)
        if meta:
            tickers.append(meta["ticker"])

    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        if st.button("🔄 시세 새로고침", key="refresh_btn"):
            st.cache_data.clear()
            st.rerun()

    prices = get_prices(tickers) if tickers else {}
    holdings_df = enrich_with_prices(holdings_df, prices)

    # ──────────────────────────────────────────────
    # 탭 구성
    # ──────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs(["📈 통합 대시보드", "💼 보유 종목", "📋 거래이력", "⚙️ 데이터 관리"])

    # ══════════════════════════════════════════════
    # 탭1: 통합 대시보드
    # ══════════════════════════════════════════════
    with tab1:
        render_dashboard(holdings_df, nonstock_df, cash_df, snapshot_df, monthly_df, prices)

    # ══════════════════════════════════════════════
    # 탭2: 보유 종목 상세
    # ══════════════════════════════════════════════
    with tab2:
        render_holdings(holdings_df, prices)

    # ══════════════════════════════════════════════
    # 탭3: 거래이력
    # ══════════════════════════════════════════════
    with tab3:
        render_trades(trade_df)

    # ══════════════════════════════════════════════
    # 탭4: 데이터 관리
    # ══════════════════════════════════════════════
    with tab4:
        render_data_mgmt(nonstock_df, cash_df)


# ============================================================
# 탭1: 통합 대시보드
# ============================================================
def render_dashboard(holdings_df, nonstock_df, cash_df, snapshot_df, monthly_df, prices):

    # 자산 합산
    # 1) 주식/ETF 평가금액
    stock_eval  = int(holdings_df["평가금액"].sum()) if not holdings_df.empty else 0
    stock_cost  = int(holdings_df["매입금액"].sum()) if not holdings_df.empty else 0

    # 2) 비주식자산 (TDF 등) — 평가금액 합산
    tdf_eval = 0
    tdf_cost = 0
    if not nonstock_df.empty:
        for _, row in nonstock_df.iterrows():
            eva = float(row.get("평가금액", 0) or 0)
            pri = float(row.get("원금", 0) or 0)
            유형 = str(row.get("자산군", ""))
            if 유형 in ("TDF", "펀드", "채권"):
                tdf_eval += eva
                tdf_cost += pri

    # 3) 현금성자산 — 비주식자산 시트의 현금성자산 행 기준 (최신 데이터)
    cash_eval = 0
    if not nonstock_df.empty:
        cash_rows = nonstock_df[nonstock_df["자산군"] == "현금성자산"]
        cash_eval = int(cash_rows["평가금액"].apply(lambda x: float(x or 0)).sum())

    # 비주식 전체 (TDF + 현금)
    nonstock_eval = tdf_eval + cash_eval

    # 통합 합산
    total_eval = stock_eval + nonstock_eval
    total_cost = stock_cost + tdf_cost + cash_eval  # 현금은 원금=평가

    total_pnl  = total_eval - total_cost
    total_pct  = total_pnl / total_cost * 100 if total_cost else 0
    stock_pnl  = stock_eval - stock_cost
    stock_pct  = stock_pnl / stock_cost * 100 if stock_cost else 0

    # ── 상단 요약 카드 ──
    st.markdown('<div class="section-title">통합 자산 현황</div>', unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">통합 평가금액</div>
            <div class="metric-value">{fmt_money(total_eval)}</div>
            <div class="metric-sub" style="color:{color_pnl(total_pnl)}">
                {fmt_money(total_pnl)} ({fmt_pct(total_pct)})
            </div>
        </div>""", unsafe_allow_html=True)

    with c2:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">주식/ETF 평가</div>
            <div class="metric-value">{fmt_money(stock_eval)}</div>
            <div class="metric-sub" style="color:{color_pnl(stock_pnl)}">
                {fmt_money(stock_pnl)} ({fmt_pct(stock_pct)})
            </div>
        </div>""", unsafe_allow_html=True)

    with c3:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">TDF / 펀드</div>
            <div class="metric-value">{fmt_money(tdf_eval)}</div>
            <div class="metric-sub" style="color:gray">원금 {fmt_money(tdf_cost)}</div>
        </div>""", unsafe_allow_html=True)

    with c4:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-label">현금성 자산</div>
            <div class="metric-value">{fmt_money(cash_eval)}</div>
            <div class="metric-sub" style="color:gray">예수금 · 대기자금</div>
        </div>""", unsafe_allow_html=True)

    # ── 계좌별 현황 ──
    st.markdown('<div class="section-title">계좌별 현황</div>', unsafe_allow_html=True)

    irp_stocks = holdings_df[holdings_df["계좌"].str.contains("신한", na=False)] if not holdings_df.empty else pd.DataFrame()
    mira_stocks = holdings_df[holdings_df["계좌"].str.contains("미래에셋", na=False)] if not holdings_df.empty else pd.DataFrame()

    # IRP 계좌: ETF + TDF + 현금
    irp_stock_eval = int(irp_stocks["평가금액"].sum()) if not irp_stocks.empty else 0
    irp_stock_cost = int(irp_stocks["매입금액"].sum()) if not irp_stocks.empty else 0
    irp_tdf_eval   = tdf_eval  # 비주식자산 IRP 귀속
    irp_cash_eval  = 0
    if not nonstock_df.empty:
        irp_cash_rows = nonstock_df[
            (nonstock_df["자산군"] == "현금성자산") &
            (nonstock_df["계좌"].str.contains("신한", na=False))
        ]
        irp_cash_eval = int(irp_cash_rows["평가금액"].apply(lambda x: float(x or 0)).sum())
    irp_total = irp_stock_eval + irp_tdf_eval + irp_cash_eval
    irp_cost  = irp_stock_cost + tdf_cost + irp_cash_eval
    irp_pnl   = irp_total - irp_cost
    irp_pct   = irp_pnl / irp_cost * 100 if irp_cost else 0

    # 미래에셋: 주식 + 예수금
    mira_eval = int(mira_stocks["평가금액"].sum()) if not mira_stocks.empty else 0
    mira_cost = int(mira_stocks["매입금액"].sum()) if not mira_stocks.empty else 0
    mira_cash = 0
    if not nonstock_df.empty:
        mira_cash_rows = nonstock_df[
            (nonstock_df["자산군"] == "현금성자산") &
            (nonstock_df["계좌"].str.contains("미래에셋", na=False))
        ]
        mira_cash = int(mira_cash_rows["평가금액"].apply(lambda x: float(x or 0)).sum())
    mira_total = mira_eval + mira_cash
    mira_cost_total = mira_cost + mira_cash
    mira_pnl  = mira_total - mira_cost_total
    mira_pct  = mira_pnl / mira_cost_total * 100 if mira_cost_total else 0

    ca, cb = st.columns(2)
    with ca:
        st.markdown(f"""
        <div class="account-card">
            <div style="margin-bottom:0.5rem">
                <span class="tag tag-irp">신한은행 IRP</span>
                <span class="tag tag-etf">ETF</span>
                <span class="tag tag-etf">TDF</span>
            </div>
            <div style="font-size:1.3rem;font-weight:700">{fmt_money(irp_total)}</div>
            <div style="color:{color_pnl(irp_pnl)};font-size:0.88rem;margin-top:0.2rem">
                {fmt_money(irp_pnl)} ({fmt_pct(irp_pct)})
            </div>
            <div style="margin-top:0.6rem;font-size:0.8rem;color:gray">
                ETF {fmt_money(irp_stock_eval)} · TDF {fmt_money(irp_tdf_eval)} · 현금 {fmt_money(irp_cash_eval)}
            </div>
        </div>""", unsafe_allow_html=True)

    with cb:
        st.markdown(f"""
        <div class="account-card">
            <div style="margin-bottom:0.5rem">
                <span class="tag tag-mira">미래에셋증권</span>
                <span class="tag tag-stock">주식</span>
            </div>
            <div style="font-size:1.3rem;font-weight:700">{fmt_money(mira_total)}</div>
            <div style="color:{color_pnl(mira_pnl)};font-size:0.88rem;margin-top:0.2rem">
                {fmt_money(mira_pnl)} ({fmt_pct(mira_pct)})
            </div>
            <div style="margin-top:0.6rem;font-size:0.8rem;color:gray">
                주식 {fmt_money(mira_eval)} · 예수금 {fmt_money(mira_cash)}
            </div>
        </div>""", unsafe_allow_html=True)

    # ── 자산 구성 도넛 차트 ──
    st.markdown('<div class="section-title">자산 구성</div>', unsafe_allow_html=True)

    cc, cd = st.columns([1, 1])
    with cc:
        # 계좌별 비중
        labels_acc = ["신한은행 IRP", "미래에셋증권"]
        values_acc = [irp_total, mira_total]
        fig_acc = go.Figure(go.Pie(
            labels=labels_acc, values=values_acc,
            hole=0.55, textinfo="label+percent",
            marker_colors=["#1976d2", "#388e3c"],
        ))
        fig_acc.update_layout(
            title="계좌별 비중", height=300, margin=dict(t=40, b=10, l=10, r=10),
            showlegend=False,
        )
        st.plotly_chart(fig_acc, use_container_width=True)

    with cd:
        # 자산군별 비중
        labels_type = ["ETF (IRP)", "TDF", "국내주식", "현금성자산"]
        values_type = [irp_stock_eval, tdf_eval, mira_eval, cash_eval]
        values_type = [max(0, v) for v in values_type]
        fig_type = go.Figure(go.Pie(
            labels=labels_type, values=values_type,
            hole=0.55, textinfo="label+percent",
            marker_colors=["#7b1fa2", "#0288d1", "#f57c00", "#78909c"],
        ))
        fig_type.update_layout(
            title="자산군별 비중", height=300, margin=dict(t=40, b=10, l=10, r=10),
            showlegend=False,
        )
        st.plotly_chart(fig_type, use_container_width=True)

    # ── 월별 자산 추이 ──
    if not monthly_df.empty:
        st.markdown('<div class="section-title">월별 자산 추이</div>', unsafe_allow_html=True)
        try:
            mdf = monthly_df.copy()
            mdf["통합평가"] = pd.to_numeric(mdf["통합평가"], errors="coerce")
            mdf["통합원금"] = pd.to_numeric(mdf["통합원금"], errors="coerce")
            mdf = mdf.dropna(subset=["통합평가"])

            # 년월 컬럼을 문자열 "YYYY-MM" 형식으로 정규화
            def normalize_yearmonth(v):
                s = str(v).strip()
                # datetime 형태로 들어온 경우 (예: "2026-06-01 00:00:00")
                if len(s) > 7:
                    return s[:7]
                return s
            mdf["년월_표시"] = mdf["년월"].apply(normalize_yearmonth)

            fig_trend = go.Figure()
            fig_trend.add_trace(go.Bar(
                x=mdf["년월_표시"], y=mdf["통합평가"],
                name="평가금액", marker_color="#e53935", opacity=0.85,
            ))
            fig_trend.add_trace(go.Scatter(
                x=mdf["년월_표시"], y=mdf["통합원금"],
                name="원금", mode="lines+markers",
                line=dict(color="#1976d2", width=2),
                marker=dict(size=7),
            ))
            fig_trend.update_layout(
                height=280,
                margin=dict(t=10, b=30, l=10, r=10),
                legend=dict(orientation="h", y=1.08),
                yaxis=dict(tickformat=","),
                xaxis=dict(type="category", tickangle=0),
            )
            st.plotly_chart(fig_trend, use_container_width=True)
        except Exception as e:
            st.caption(f"추이 차트 오류: {e}")


# ============================================================
# 탭2: 보유 종목 상세
# ============================================================
def render_holdings(holdings_df, prices):
    st.markdown('<div class="section-title">보유 종목 상세</div>', unsafe_allow_html=True)

    if holdings_df.empty:
        st.info("보유 종목이 없습니다.")
        return

    시세반영수 = holdings_df["시세반영"].sum() if "시세반영" in holdings_df.columns else 0
    전체수 = len(holdings_df)
    st.caption(f"총 {전체수}종목 · 실시간 시세 반영 {시세반영수}종목")

    # 계좌별 필터
    계좌목록 = ["전체"] + sorted(holdings_df["계좌"].unique().tolist())
    선택계좌 = st.selectbox("계좌 필터", 계좌목록, key="holding_account_filter")
    if 선택계좌 != "전체":
        display_df = holdings_df[holdings_df["계좌"] == 선택계좌]
    else:
        display_df = holdings_df

    # 카드형 표시
    for _, row in display_df.iterrows():
        code = row["종목코드"]
        name = row["종목명"]
        계좌 = row["계좌"]
        qty  = row["보유수량"]
        avg  = row["평균단가"]
        cost = row["매입금액"]
        eval_amt = row["평가금액"]
        pnl  = row["평가손익"]
        pct  = row["수익률"]
        has_price = row.get("시세반영", False)
        current_price = row.get("현재가", None)

        tag_class = "tag-irp" if "신한" in 계좌 else "tag-mira"
        tag_label = "IRP" if "신한" in 계좌 else "미래에셋"
        asset_info = ASSET_MASTER.get(code, {})
        type_label = asset_info.get("type", "")
        type_class = "tag-etf" if type_label == "ETF" else "tag-stock"
        price_note = f"현재가 {fmt_money(current_price)}" if has_price else "⚠ 시세 미반영"

        st.markdown(f"""
        <div class="account-card">
            <div style="display:flex;justify-content:space-between;align-items:flex-start">
                <div>
                    <span class="tag {tag_class}">{tag_label}</span>
                    <span class="tag {type_class}">{type_label}</span>
                    <strong style="font-size:1rem">{name}</strong>
                    <span style="color:gray;font-size:0.78rem;margin-left:0.4rem">{code}</span>
                </div>
                <div style="text-align:right">
                    <div style="font-size:1.15rem;font-weight:700">{fmt_money(eval_amt)}</div>
                    <div style="color:{color_pnl(pnl)};font-size:0.85rem">{fmt_money(pnl)} ({fmt_pct(pct)})</div>
                </div>
            </div>
            <div style="margin-top:0.5rem;font-size:0.8rem;color:gray">
                {qty}주 · 평균단가 {fmt_money(avg)} · 매입 {fmt_money(cost)} · {price_note}
            </div>
        </div>""", unsafe_allow_html=True)

    # 종목별 수익률 바 차트
    if len(display_df) > 0 and "수익률" in display_df.columns:
        st.markdown('<div class="section-title">종목별 수익률</div>', unsafe_allow_html=True)
        chart_df = display_df.sort_values("수익률")
        colors = ["#e53935" if v >= 0 else "#1976d2" for v in chart_df["수익률"]]
        fig = go.Figure(go.Bar(
            x=chart_df["수익률"],
            y=chart_df["종목명"],
            orientation="h",
            marker_color=colors,
            text=[fmt_pct(v) for v in chart_df["수익률"]],
            textposition="outside",
        ))
        fig.update_layout(
            height=max(200, len(chart_df) * 45),
            margin=dict(t=10, b=10, l=10, r=80),
            xaxis_title="수익률(%)",
            xaxis=dict(zeroline=True),
        )
        st.plotly_chart(fig, use_container_width=True)


# ============================================================
# 탭3: 거래이력
# ============================================================
def render_trades(trade_df):
    st.markdown('<div class="section-title">거래이력</div>', unsafe_allow_html=True)

    if trade_df.empty:
        st.info("거래이력이 없습니다.")
        return

    df = trade_df.copy()

    # 필터
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        계좌필터 = st.selectbox("계좌", ["전체"] + sorted(df["운용사"].unique().tolist()), key="trade_account")
    with fc2:
        구분필터 = st.selectbox("거래구분", ["전체", "매수", "매도"], key="trade_type")
    with fc3:
        종목필터 = st.selectbox("종목", ["전체"] + sorted(df["종목명"].unique().tolist()), key="trade_stock")

    if 계좌필터 != "전체":
        df = df[df["운용사"] == 계좌필터]
    if 구분필터 != "전체":
        df = df[df["거래구분"] == 구분필터]
    if 종목필터 != "전체":
        df = df[df["종목명"] == 종목필터]

    df = df.sort_values("거래일자", ascending=False)
    df["거래금액"] = df["거래수량"] * df["거래단가"]

    # 통계
    s1, s2, s3 = st.columns(3)
    s1.metric("총 거래 건수", f"{len(df):,}건")
    s2.metric("매수 건수", f"{len(df[df['거래구분']=='매수']):,}건")
    s3.metric("매도 건수", f"{len(df[df['거래구분']=='매도']):,}건")

    # 테이블
    show_cols = ["거래일자", "운용사", "종목명", "거래구분", "거래수량", "거래단가", "거래금액", "비고"]
    show_cols = [c for c in show_cols if c in df.columns]
    st.dataframe(
        df[show_cols].rename(columns={"운용사": "계좌"}),
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# 탭4: 데이터 관리
# ============================================================
def render_data_mgmt(nonstock_df, cash_df):
    st.markdown('<div class="section-title">비주식자산 현황 (TDF · 현금성자산)</div>', unsafe_allow_html=True)
    st.caption("💡 대시보드의 현금성자산 금액은 이 시트 기준입니다. 잔액 변경 시 비주식자산 시트를 업데이트하세요.")

    if not nonstock_df.empty:
        tdf_rows  = nonstock_df[nonstock_df["자산군"] == "TDF"]
        cash_rows = nonstock_df[nonstock_df["자산군"] == "현금성자산"]
        if not tdf_rows.empty:
            st.markdown("**TDF / 펀드**")
            st.dataframe(tdf_rows, use_container_width=True, hide_index=True)
        if not cash_rows.empty:
            st.markdown("**현금성자산 (예수금 · 대기자금)**")
            st.dataframe(cash_rows, use_container_width=True, hide_index=True)
    else:
        st.info("비주식자산 데이터 없음")

    with st.expander("📁 현금성자산 시트 원본 (구버전 — 앱에서 미사용)", expanded=False):
        st.caption("⚠ 이 시트는 구버전으로 현재 대시보드 계산에 사용되지 않습니다.")
        if not cash_df.empty:
            st.dataframe(cash_df, use_container_width=True, hide_index=True)
        else:
            st.info("데이터 없음")

    st.markdown('<div class="section-title">캐시 초기화</div>', unsafe_allow_html=True)
    if st.button("전체 캐시 초기화 (데이터 새로고침)", key="clear_cache_btn"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("캐시가 초기화되었습니다. 페이지를 새로고침하세요.")
        st.rerun()


# ============================================================
# 실행
# ============================================================
if __name__ == "__main__" or True:
    main()
