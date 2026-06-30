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
@st.cache_data(ttl=180)
def get_prices(tickers: list[str]) -> dict[str, float]:
    """yfinance로 현재가 조회. 일괄 조회 실패 시 종목별 개별 재시도."""
    if not tickers:
        return {}
    prices = {}
    # 1차: 일괄 조회
    try:
        ticker_str = " ".join(tickers)
        data = yf.download(ticker_str, period="5d", progress=False, auto_adjust=True, threads=True)
        if "Close" in data.columns:
            close = data["Close"].dropna(how="all")
            if not close.empty:
                latest = close.iloc[-1]
                if hasattr(latest, "items"):
                    for t, p in latest.items():
                        if pd.notna(p):
                            prices[t] = float(p)
                elif len(tickers) == 1 and pd.notna(latest):
                    prices[tickers[0]] = float(latest)
    except Exception as e:
        logging.warning("일괄 시세 조회 실패: %s", e)

    # 2차: 누락된 종목 개별 재시도
    missing = [t for t in tickers if t not in prices]
    for t in missing:
        try:
            hist = yf.Ticker(t).history(period="5d")
            if not hist.empty:
                prices[t] = float(hist["Close"].dropna().iloc[-1])
        except Exception as e:
            logging.warning("개별 시세 조회 실패 [%s]: %s", t, e)

    return prices

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
    """한국 주식앱 기준: 상승=빨강(#ef5350), 하락=파랑(#42a5f5)"""
    if v is None:
        return "#9e9e9e"
    try:
        f = float(v)
    except Exception:
        return "#9e9e9e"
    return "#ef5350" if f > 0 else "#42a5f5" if f < 0 else "#9e9e9e"

def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")

def build_number_column_config(df: pd.DataFrame, money_cols: list[str] = None, pct_cols: list[str] = None) -> dict:
    """st.dataframe에 천 단위 콤마(,) 포맷을 적용하는 column_config 생성."""
    money_cols = money_cols or []
    pct_cols = pct_cols or []
    config = {}
    for col in df.columns:
        if col in money_cols:
            config[col] = st.column_config.NumberColumn(col, format="localized")
        elif col in pct_cols:
            config[col] = st.column_config.NumberColumn(col, format="%.2f%%")
    return config

# ============================================================
# CSS
# ============================================================
st.markdown("""
<style>

/* 한국 주식앱 색상 기준: 상승=빨강, 하락=파랑 */
:root {
    --color-up:    #ef5350;
    --color-down:  #42a5f5;
    --color-flat:  #9e9e9e;
    --card-bg:     rgba(255,255,255,0.035);
    --card-border: rgba(255,255,255,0.08);
    --text-dim:    rgba(255,255,255,0.55);
    --text-dim2:   rgba(255,255,255,0.4);
}

/* ── 히어로 카드: 총 원금→평가금액 한눈에 ── */
.hero-card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 16px;
    padding: 1.4rem 1.6rem;
    margin-bottom: 1rem;
}
.hero-label {
    font-size: 0.82rem;
    color: var(--text-dim);
    margin-bottom: 0.3rem;
}
.hero-row {
    display: flex;
    align-items: baseline;
    gap: 0.6rem;
    flex-wrap: wrap;
}
.hero-value {
    font-size: 2.1rem;
    font-weight: 700;
    line-height: 1.1;
}
.hero-pnl {
    font-size: 1.05rem;
    font-weight: 600;
}
.hero-bar {
    margin-top: 0.9rem;
    height: 9px;
    border-radius: 5px;
    background: rgba(255,255,255,0.06);
    overflow: hidden;
    display: flex;
}
.hero-legend {
    display: flex;
    gap: 1.1rem;
    margin-top: 0.55rem;
    font-size: 0.76rem;
    color: var(--text-dim);
    flex-wrap: wrap;
}
.hero-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 4px;
}

/* ── 계좌별 카드 (2개 큰 카드) ── */
.acct-card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 14px;
    padding: 1.1rem 1.3rem;
    margin-bottom: 0.5rem;
}
.acct-badge {
    display: inline-block;
    border-radius: 6px;
    padding: 0.18rem 0.6rem;
    font-size: 0.74rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
}
.badge-irp  { background: rgba(83,74,183,0.22);  color: #AFA9EC; }
.badge-mira { background: rgba(29,158,117,0.22); color: #5DCAA5; }
.acct-value { font-size: 1.5rem; font-weight: 700; line-height: 1.2; }
.acct-cost  { font-size: 0.8rem; color: var(--text-dim); margin: 0.15rem 0; }
.acct-pnl   { font-size: 0.85rem; font-weight: 600; }
.acct-detail{ font-size: 0.76rem; color: var(--text-dim2); margin-top: 0.5rem; }

/* ── 보유종목 1줄 리스트 ── */
.holding-row {
    display: flex;
    align-items: center;
    padding: 0.85rem 0.2rem;
    border-bottom: 1px solid rgba(255,255,255,0.06);
}
.holding-row:last-child { border-bottom: none; }
.holding-icon {
    width: 36px; height: 36px;
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.68rem; font-weight: 700;
    margin-right: 0.85rem;
    flex-shrink: 0;
}
.icon-etf   { background: rgba(29,158,117,0.22); color: #5DCAA5; }
.icon-stock { background: rgba(83,74,183,0.22);  color: #AFA9EC; }
.holding-name { font-weight: 600; font-size: 0.92rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.holding-sub  { font-size: 0.78rem; color: var(--text-dim); margin-top: 0.1rem; }
.holding-right { text-align: right; flex-shrink: 0; margin-left: 0.7rem; }
.holding-amt  { font-weight: 700; font-size: 0.95rem; }
.holding-pnl  { font-size: 0.78rem; font-weight: 600; margin-top: 0.1rem; }

.section-title {
    font-size: 1.05rem;
    font-weight: 700;
    margin: 1.4rem 0 0.7rem 0;
    padding-bottom: 0.35rem;
    border-bottom: 2px solid rgba(255,255,255,0.1);
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

    tdf_pnl = tdf_eval - tdf_cost
    tdf_pct = tdf_pnl / tdf_cost * 100 if tdf_cost else 0
    cash_pct_of_total = cash_eval / total_eval * 100 if total_eval else 0

    # ── 히어로 카드: 원금 → 평가금액 → 손익 한눈에 + 비중 바 ──
    st.markdown('<div class="section-title">통합 자산 현황</div>', unsafe_allow_html=True)

    stock_pct_w = stock_eval / total_eval * 100 if total_eval else 0
    tdf_pct_w   = tdf_eval / total_eval * 100 if total_eval else 0
    cash_pct_w  = cash_eval / total_eval * 100 if total_eval else 0

    st.markdown(f"""
    <div class="hero-card">
        <div class="hero-label">총 투자원금 {fmt_money(total_cost)} → 통합 평가금액</div>
        <div class="hero-row">
            <div class="hero-value">{fmt_money(total_eval)}</div>
            <div class="hero-pnl" style="color:{color_pnl(total_pnl)}">{fmt_money(total_pnl)} ({fmt_pct(total_pct)})</div>
        </div>
        <div class="hero-bar">
            <div style="width:{stock_pct_w:.1f}%;background:#534AB7"></div>
            <div style="width:{tdf_pct_w:.1f}%;background:#1D9E75"></div>
            <div style="width:{cash_pct_w:.1f}%;background:#5F5E5A"></div>
        </div>
        <div class="hero-legend">
            <span><span class="hero-dot" style="background:#534AB7"></span>주식/ETF {fmt_money(stock_eval)} ({stock_pct_w:.0f}%) · {fmt_pct(stock_pct)}</span>
            <span><span class="hero-dot" style="background:#1D9E75"></span>TDF/펀드 {fmt_money(tdf_eval)} ({tdf_pct_w:.0f}%) · {fmt_pct(tdf_pct)}</span>
            <span><span class="hero-dot" style="background:#5F5E5A"></span>현금성자산 {fmt_money(cash_eval)} ({cash_pct_w:.0f}%)</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

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
        <div class="acct-card">
            <span class="acct-badge badge-irp">신한은행 IRP · ETF·TDF</span>
            <div class="acct-value">{fmt_money(irp_total)}</div>
            <div class="acct-cost">원금 {fmt_money(irp_cost)}</div>
            <div class="acct-pnl" style="color:{color_pnl(irp_pnl)}">{fmt_money(irp_pnl)} ({fmt_pct(irp_pct)})</div>
            <div class="acct-detail">
                ETF {fmt_money(irp_stock_eval)} · TDF {fmt_money(irp_tdf_eval)} · 현금 {fmt_money(irp_cash_eval)}
            </div>
        </div>""", unsafe_allow_html=True)

    with cb:
        st.markdown(f"""
        <div class="acct-card">
            <span class="acct-badge badge-mira">미래에셋증권 · 주식</span>
            <div class="acct-value">{fmt_money(mira_total)}</div>
            <div class="acct-cost">원금 {fmt_money(mira_cost_total)}</div>
            <div class="acct-pnl" style="color:{color_pnl(mira_pnl)}">{fmt_money(mira_pnl)} ({fmt_pct(mira_pct)})</div>
            <div class="acct-detail">
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
        st.caption("📌 빨간 막대 = 그 달 말 기준 통합 평가금액 · 파란 선·점 = 그 달 말 기준 통합 투자원금. 두 값의 차이가 누적 손익입니다.")
        if len(monthly_df) < 2:
            st.info("월별 데이터가 1개월치만 있어 추이(변화)를 비교할 수 없습니다. 매월 스냅샷이 쌓이면 추이선이 나타납니다.")
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
                name="평가금액", marker_color="#ef5350", opacity=0.85,
            ))
            fig_trend.add_trace(go.Scatter(
                x=mdf["년월_표시"], y=mdf["통합원금"],
                name="원금", mode="lines+markers",
                line=dict(color="#42a5f5", width=2),
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

    # 투자금(평가금액) 큰 순서로 정렬
    display_df = display_df.sort_values("평가금액", ascending=False)

    # 1줄형 압축 리스트
    rows_html = []
    for _, row in display_df.iterrows():
        code = row["종목코드"]
        name = row["종목명"]
        계좌 = row["계좌"]
        qty  = row["보유수량"]
        avg  = row["평균단가"]
        eval_amt = row["평가금액"]
        pnl  = row["평가손익"]
        pct  = row["수익률"]
        has_price = row.get("시세반영", False)
        current_price = row.get("현재가", None)

        acct_short = "IRP" if "신한" in 계좌 else "미래에셋"
        asset_info = ASSET_MASTER.get(code, {})
        type_label = asset_info.get("type", "")
        icon_class = "icon-etf" if type_label == "ETF" else "icon-stock"
        cur_str = fmt_money(current_price) if has_price else "매입가 적용"

        rows_html.append(f"""
        <div class="holding-row">
            <div class="holding-icon {icon_class}">{type_label}</div>
            <div style="flex:1;min-width:0">
                <div class="holding-name">{name} <span style="color:var(--text-dim2);font-weight:400;font-size:0.74rem">{acct_short}</span></div>
                <div class="holding-sub">{qty}주 · 평단 {fmt_money(avg)} · 현재 {cur_str}</div>
            </div>
            <div class="holding-right">
                <div class="holding-amt">{fmt_money(eval_amt)}</div>
                <div class="holding-pnl" style="color:{color_pnl(pnl)}">{fmt_money(pnl)} ({fmt_pct(pct)})</div>
            </div>
        </div>""")

    list_html = f'<div class="acct-card">{"".join(rows_html)}</div>'
    st.markdown(list_html, unsafe_allow_html=True)

    미반영수 = (~display_df["시세반영"]).sum() if "시세반영" in display_df.columns else 0
    if 미반영수 > 0:
        st.caption(f"⚠ {미반영수}종목은 실시간 시세 조회에 실패해 매입가로 표시 중입니다. '시세 새로고침'을 눌러 다시 시도하세요.")

    # 종목별 수익률 바 차트
    if len(display_df) > 0 and "수익률" in display_df.columns:
        st.markdown('<div class="section-title">종목별 수익률</div>', unsafe_allow_html=True)
        chart_df = display_df.sort_values("수익률")
        colors = ["#ef5350" if v >= 0 else "#42a5f5" for v in chart_df["수익률"]]
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
    display_df = df[show_cols].rename(columns={"운용사": "계좌"})
    col_config = build_number_column_config(
        display_df, money_cols=["거래수량", "거래단가", "거래금액"]
    )
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config=col_config,
    )


# ============================================================
# 탭4: 데이터 관리
# ============================================================
def render_data_mgmt(nonstock_df, cash_df):
    st.markdown('<div class="section-title">비주식자산 현황 (TDF · 현금성자산)</div>', unsafe_allow_html=True)
    st.caption("💡 대시보드의 현금성자산 금액은 이 시트 기준입니다. 잔액 변경 시 비주식자산 시트를 업데이트하세요.")

    money_cols_nonstock = ["원금", "평가금액"]
    pct_cols_nonstock = ["예상연수익률"]

    if not nonstock_df.empty:
        tdf_rows  = nonstock_df[nonstock_df["자산군"] == "TDF"]
        cash_rows = nonstock_df[nonstock_df["자산군"] == "현금성자산"]
        if not tdf_rows.empty:
            st.markdown("**TDF / 펀드**")
            st.dataframe(
                tdf_rows, use_container_width=True, hide_index=True,
                column_config=build_number_column_config(tdf_rows, money_cols_nonstock, pct_cols_nonstock),
            )
        if not cash_rows.empty:
            st.markdown("**현금성자산 (예수금 · 대기자금)**")
            st.dataframe(
                cash_rows, use_container_width=True, hide_index=True,
                column_config=build_number_column_config(cash_rows, money_cols_nonstock, pct_cols_nonstock),
            )
    else:
        st.info("비주식자산 데이터 없음")

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
