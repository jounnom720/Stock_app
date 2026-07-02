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
# 시장 지표 마스터 (대시보드 상단 카드용, 성격별 4그룹)
# ============================================================
MARKET_INDICES = [
    {"group": "국내 증시",      "name": "코스피",     "ticker": "^KS11"},
    {"group": "국내 증시",      "name": "코스닥",     "ticker": "^KQ11"},
    {"group": "환율·원자재",    "name": "USD/KRW",    "ticker": "KRW=X"},
    {"group": "환율·원자재",    "name": "WTI 유가",   "ticker": "CL=F"},
    {"group": "미국 증시",      "name": "S&P500",     "ticker": "^GSPC"},
    {"group": "미국 증시",      "name": "나스닥",     "ticker": "^IXIC"},
    {"group": "위험심리·금리",  "name": "VIX",        "ticker": "^VIX"},
    {"group": "위험심리·금리",  "name": "달러인덱스", "ticker": "DX-Y.NYB"},
    {"group": "위험심리·금리",  "name": "美 10년물",  "ticker": "^TNX"},
]

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

# ============================================================
# 실시간 시세 조회
# ============================================================
@st.cache_data(ttl=60)
def get_prices(tickers: tuple) -> dict[str, float]:
    """yfinance로 현재가 조회. 일괄 조회 실패 시 종목별 개별 재시도.
    st.cache_data는 list를 해시할 수 없으므로 tuple로 받음.
    """
    if not tickers:
        return {}
    prices = {}
    ticker_list = list(tickers)
    # 1차: 일괄 조회
    try:
        ticker_str = " ".join(ticker_list)
        data = yf.download(ticker_str, period="5d", progress=False, auto_adjust=True, threads=False)
        if "Close" in data.columns:
            close = data["Close"].dropna(how="all")
            if not close.empty:
                latest = close.iloc[-1]
                if hasattr(latest, "items"):
                    for t, p in latest.items():
                        if pd.notna(p):
                            prices[t] = float(p)
                elif len(ticker_list) == 1 and pd.notna(latest):
                    prices[ticker_list[0]] = float(latest)
    except Exception as e:
        logging.warning("일괄 시세 조회 실패: %s", e)

    # 2차: 누락된 종목 개별 재시도
    missing = [t for t in ticker_list if t not in prices]
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

@st.cache_data(ttl=300)
def get_market_index_data() -> dict[str, dict]:
    """시장 지표(코스피·환율·VIX 등)의 현재가와 전일 대비 등락률을 조회."""
    result = {}
    tickers = [m["ticker"] for m in MARKET_INDICES]
    try:
        ticker_str = " ".join(tickers)
        data = yf.download(ticker_str, period="5d", progress=False, auto_adjust=True, threads=True)
        if "Close" in data.columns:
            close = data["Close"].dropna(how="all")
            if len(close) >= 2:
                latest_row = close.iloc[-1]
                prev_row = close.iloc[-2]
                for t in tickers:
                    try:
                        cur = float(latest_row[t]) if hasattr(latest_row, "__getitem__") else float(latest_row)
                        prev = float(prev_row[t]) if hasattr(prev_row, "__getitem__") else float(prev_row)
                        if pd.notna(cur) and pd.notna(prev) and prev != 0:
                            result[t] = {"current": cur, "change_pct": (cur - prev) / prev * 100}
                    except Exception:
                        continue
    except Exception as e:
        logging.warning("시장지표 일괄 조회 실패: %s", e)

    # 누락된 지표 개별 재시도
    missing = [t for t in tickers if t not in result]
    for t in missing:
        try:
            hist = yf.Ticker(t).history(period="5d")
            closes = hist["Close"].dropna()
            if len(closes) >= 2:
                cur, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
                if prev != 0:
                    result[t] = {"current": cur, "change_pct": (cur - prev) / prev * 100}
        except Exception as e:
            logging.warning("시장지표 개별 조회 실패 [%s]: %s", t, e)

    return result

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

# ============================================================
# 실현손익 계산 (단일 함수 — 모든 화면이 이것 하나만 참조)
# ============================================================
def calc_realized_pnl(trade_df: pd.DataFrame) -> pd.DataFrame:
    """매도 건별 실현손익을 평균매입가법으로 계산하는 단일 함수.
    주식/ETF 전체가 이 함수 하나만 거쳐가므로 화면마다 다른 숫자가 나올 수 없다.
    """
    if trade_df.empty:
        return pd.DataFrame()

    df = trade_df.copy()
    df["거래일자"] = pd.to_datetime(df["거래일자"], errors="coerce")
    df = df.sort_values("거래일자").reset_index(drop=True)

    avg_cost = {}
    qty_held = {}
    realized_rows = []

    for _, row in df.iterrows():
        code = str(row["종목코드"]).strip()
        name = row["종목명"]
        qty = int(row["거래수량"])
        price = float(row["거래단가"])
        account = row["운용사"]
        date = row["거래일자"]

        if row["거래구분"] == "매수":
            prev_qty = qty_held.get(code, 0)
            prev_avg = avg_cost.get(code, 0.0)
            new_qty = prev_qty + qty
            new_avg = (prev_avg * prev_qty + price * qty) / new_qty if new_qty else price
            qty_held[code] = new_qty
            avg_cost[code] = new_avg
        elif row["거래구분"] == "매도":
            prev_avg = avg_cost.get(code, price)
            매도금액 = qty * price
            매입금액 = qty * prev_avg
            실현손익 = 매도금액 - 매입금액
            realized_rows.append({
                "거래일자": date, "계좌": account, "종목코드": code, "종목명": name,
                "매도수량": qty, "매도단가": price, "평균매입단가": round(prev_avg),
                "매도금액": round(매도금액), "매입금액": round(매입금액),
                "실현손익": round(실현손익),
            })
            qty_held[code] = max(0, qty_held.get(code, 0) - qty)

    return pd.DataFrame(realized_rows)

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

def fmt_money_full(v, suffix="원") -> str:
    """억 단위 축약 없이 전체 자릿수를 콤마와 함께 표시 (메인 금액 강조용)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    return f"{int(v):,}{suffix}"

def fmt_pct(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    return f"{float(v):+.2f}%"

def color_pnl(v) -> str:
    """한국 주식앱 기준: 상승=빨강, 하락=파랑 (카드 배경과 어울리는 톤)"""
    if v is None:
        return "#8a8d96"
    try:
        f = float(v)
    except Exception:
        return "#8a8d96"
    return "#e0635e" if f > 0 else "#5b9bd8" if f < 0 else "#8a8d96"

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
    --color-up:    #e0635e;
    --color-down:  #5b9bd8;
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
    padding: 1.7rem 1.9rem;
    margin-bottom: 1rem;
}
.hero-label {
    font-size: 0.95rem;
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
    font-size: 2.5rem;
    font-weight: 700;
    line-height: 1.1;
}
.hero-pnl {
    font-size: 1.3rem;
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
    margin-top: 0.6rem;
    font-size: 0.92rem;
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
    padding: 1.4rem 1.6rem;
    margin-bottom: 0.5rem;
}
.acct-badge {
    display: inline-block;
    border-radius: 6px;
    padding: 0.22rem 0.7rem;
    font-size: 0.88rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
}
.badge-irp  { background: rgba(83,74,183,0.22);  color: #AFA9EC; }
.badge-mira { background: rgba(29,158,117,0.22); color: #5DCAA5; }
.acct-value { font-size: 1.9rem; font-weight: 700; line-height: 1.2; }
.acct-pnl   { font-size: 1.1rem; font-weight: 600; }

/* ── 계좌 카드: 메인 수치 행 ── */
.acct-main-row {
    display: flex;
    align-items: baseline;
    gap: 0.6rem;
    margin-top: 0.3rem;
    flex-wrap: wrap;
}

/* ── 계좌 카드: 구분선 ── */
.acct-divider {
    margin: 0.75rem 0 0.55rem 0;
    border-top: 1px solid var(--card-border);
}
.acct-divider-light {
    margin: 0.3rem 0;
    border-top: 1px dashed rgba(255,255,255,0.06);
}

/* ── 계좌 카드: 항목 행 (라벨 + 값 좌우 정렬) ── */
.acct-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 0.22rem 0;
    font-size: 0.95rem;
}
.acct-row-label { color: var(--text-dim); }
.acct-row-sub   { color: var(--text-dim2); font-size: 0.88rem; }
.acct-row-val   { font-weight: 600; }

/* ── 매도 이벤트 카드 (자금흐름 타임라인) ── */
.sell-event-card { max-width: 560px; }
.sell-event-header {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    flex-wrap: wrap;
}
.sell-event-name { font-weight: 600; font-size: 1.05rem; }
.sell-event-date { color: var(--text-dim); font-size: 0.9rem; }
.sell-event-spacer { flex: 1; min-width: 0.5rem; }
.sell-event-amount { font-weight: 700; font-size: 1.05rem; white-space: nowrap; }
.sell-event-pnl { font-size: 0.92rem; font-weight: 600; white-space: nowrap; }
.sell-follow-item, .sell-follow-empty {
    margin-top: 0.4rem;
    padding-left: 0.8rem;
    border-left: 2px solid var(--card-border);
    font-size: 0.95rem;
    color: var(--text-dim);
}

/* ── 시장 지표 카드 (9개 전체를 한 그리드에 가로로 펼침) ── */
.mkt-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 0.6rem;
}
.mkt-card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 10px;
    padding: 0.9rem 1.05rem;
}
.mkt-group-tag { font-size: 0.74rem; color: var(--text-dim2); margin-bottom: 0.2rem; }
.mkt-name { font-size: 0.92rem; color: var(--text-dim); font-weight: 600; }
.mkt-value { font-size: 1.35rem; font-weight: 700; margin-top: 0.2rem; }
.mkt-change { font-size: 0.92rem; font-weight: 600; margin-top: 0.2rem; }

.section-title {
    font-size: 1.25rem;
    font-weight: 700;
    margin: 1.4rem 0 0.7rem 0;
    padding-bottom: 0.35rem;
    border-bottom: 2px solid rgba(255,255,255,0.1);
}

/* ── 통계/지표 메트릭 카드 (거래이력·자금흐름 탭) ── */
.metric-card {
    border-radius: 12px;
    padding: 1.1rem 1.3rem;
    margin-bottom: 0.5rem;
}
.metric-label {
    font-size: 0.95rem;
    color: var(--text-dim);
    margin-bottom: 0.35rem;
}
.metric-value {
    font-size: 1.65rem;
    font-weight: 700;
    line-height: 1.2;
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

    prices = get_prices(tuple(tickers)) if tickers else {}
    holdings_df = enrich_with_prices(holdings_df, prices)

    # 시세 반영 현황 표시 (조회 실패 시 경고)
    if tickers and not prices:
        st.warning("⚠ 실시간 시세 조회에 실패했습니다. 잠시 후 '시세 새로고침'을 눌러주세요. (yfinance 서버 응답 없음)")

    # ──────────────────────────────────────────────
    # 탭 구성
    # ──────────────────────────────────────────────
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📈 통합 대시보드", "💼 보유 종목", "📋 거래이력", "💵 현금흐름", "⚙️ 데이터 관리"])

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
    # 탭4: 현금흐름 (거래이력 기반 자동 계산)
    # ══════════════════════════════════════════════
    with tab4:
        render_cashflow(trade_df)

    # ══════════════════════════════════════════════
    # 탭5: 데이터 관리
    # ══════════════════════════════════════════════
    with tab5:
        render_data_mgmt(nonstock_df, cash_df)


# ============================================================
# 탭1: 통합 대시보드
# ============================================================
def render_market_indices():
    """대시보드 상단 시장 지표 카드 — 9개 지표를 한 그리드에 가로로 펼쳐 배치."""
    st.markdown('<div class="section-title">시장 지표</div>', unsafe_allow_html=True)

    data = get_market_index_data()

    cards = []
    for item in MARKET_INDICES:
        info = data.get(item["ticker"])
        if info is None:
            value_str = "-"
            change_str = "조회 실패"
            color = "#8a8d96"
        else:
            cur = info["current"]
            chg = info["change_pct"]
            if item["ticker"] == "^TNX":
                value_str = f"{cur:,.2f}%"
            else:
                value_str = f"{cur:,.2f}" if abs(cur) < 1000 else f"{cur:,.0f}"
            change_str = f"{chg:+.2f}%"
            color = color_pnl(chg)
        cards.append(f"""
        <div class="mkt-card">
            <div class="mkt-group-tag">{item['group']}</div>
            <div class="mkt-name">{item['name']}</div>
            <div class="mkt-value">{value_str}</div>
            <div class="mkt-change" style="color:{color}">{change_str}</div>
        </div>""")
    st.markdown(f'<div class="mkt-row">{"".join(cards)}</div>', unsafe_allow_html=True)


def render_dashboard(holdings_df, nonstock_df, cash_df, snapshot_df, monthly_df, prices):

    render_market_indices()

    # 자산 합산
    # 1) 주식/ETF 평가금액
    stock_eval  = int(holdings_df["평가금액"].sum()) if not holdings_df.empty else 0
    stock_cost  = int(holdings_df["매입금액"].sum()) if not holdings_df.empty else 0

    # 2) 비주식자산 (TDF 등) — 평가금액 합산
    tdf_eval = 0
    tdf_cost = 0
    if not nonstock_df.empty:
        for _, row in nonstock_df.iterrows():
            def _to_float(val):
                try:
                    v = str(val).strip().replace(",", "")
                    return float(v) if v and v not in ("-", "") else 0.0
                except (ValueError, TypeError):
                    return 0.0
            eva = _to_float(row.get("평가금액", 0))
            pri = _to_float(row.get("원금", 0))
            유형 = str(row.get("자산군", ""))
            if 유형 in ("TDF", "펀드", "채권"):
                tdf_eval += eva
                tdf_cost += pri

    # 3) 현금성자산 — 비주식자산 시트의 현금성자산 행 기준 (최신 데이터)
    cash_eval = 0
    if not nonstock_df.empty:
        cash_rows = nonstock_df[nonstock_df["자산군"] == "현금성자산"]
        cash_eval = int(cash_rows["평가금액"].apply(lambda x: float(str(x).strip().replace(',','')) if str(x).strip() not in ('', '-') else 0.0).sum())

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
        <div class="hero-label">총 투자원금 {fmt_money_full(total_cost)} → 통합 평가금액</div>
        <div class="hero-row">
            <div class="hero-value">{fmt_money_full(total_eval)}</div>
            <div class="hero-pnl" style="color:{color_pnl(total_pnl)}">{fmt_money_full(total_pnl)} ({fmt_pct(total_pct)})</div>
        </div>
        <div class="hero-bar">
            <div style="width:{stock_pct_w:.1f}%;background:#534AB7"></div>
            <div style="width:{tdf_pct_w:.1f}%;background:#1D9E75"></div>
            <div style="width:{cash_pct_w:.1f}%;background:#5F5E5A"></div>
        </div>
        <div class="hero-legend">
            <span><span class="hero-dot" style="background:#534AB7"></span>주식/ETF {fmt_money_full(stock_eval)} ({stock_pct_w:.0f}%) · {fmt_pct(stock_pct)}</span>
            <span><span class="hero-dot" style="background:#1D9E75"></span>TDF/펀드 {fmt_money_full(tdf_eval)} ({tdf_pct_w:.0f}%) · {fmt_pct(tdf_pct)}</span>
            <span><span class="hero-dot" style="background:#5F5E5A"></span>현금성자산 {fmt_money_full(cash_eval)} ({cash_pct_w:.0f}%)</span>
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
        irp_cash_eval = int(irp_cash_rows["평가금액"].apply(lambda x: float(str(x).strip().replace(',','')) if str(x).strip() not in ('', '-') else 0.0).sum())
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
        mira_cash = int(mira_cash_rows["평가금액"].apply(lambda x: float(str(x).strip().replace(',','')) if str(x).strip() not in ('', '-') else 0.0).sum())
    mira_total = mira_eval + mira_cash
    mira_cost_total = mira_cost + mira_cash
    mira_pnl  = mira_total - mira_cost_total
    mira_pct  = mira_pnl / mira_cost_total * 100 if mira_cost_total else 0

    ca, cb = st.columns(2)
    with ca:
        st.markdown(f"""
        <div class="acct-card">
            <span class="acct-badge badge-irp">신한은행 IRP · ETF·TDF</span>
            <div class="acct-main-row">
                <div class="acct-value">{fmt_money_full(irp_total)}</div>
                <div class="acct-pnl" style="color:{color_pnl(irp_pnl)}">{fmt_money_full(irp_pnl)} ({fmt_pct(irp_pct)})</div>
            </div>
            <div class="acct-divider"></div>
            <div class="acct-row">
                <span class="acct-row-label">투자원금</span>
                <span class="acct-row-val">{fmt_money_full(irp_cost)}</span>
            </div>
            <div class="acct-divider-light"></div>
            <div class="acct-row">
                <span class="acct-row-label acct-row-sub">├ ETF 평가</span>
                <span class="acct-row-val">{fmt_money_full(irp_stock_eval)}</span>
            </div>
            <div class="acct-row">
                <span class="acct-row-label acct-row-sub">├ TDF 평가</span>
                <span class="acct-row-val">{fmt_money_full(irp_tdf_eval)}</span>
            </div>
            <div class="acct-row">
                <span class="acct-row-label acct-row-sub">└ 현금</span>
                <span class="acct-row-val">{fmt_money_full(irp_cash_eval)}</span>
            </div>
        </div>""", unsafe_allow_html=True)

    with cb:
        st.markdown(f"""
        <div class="acct-card">
            <span class="acct-badge badge-mira">미래에셋증권 · 주식</span>
            <div class="acct-main-row">
                <div class="acct-value">{fmt_money_full(mira_total)}</div>
                <div class="acct-pnl" style="color:{color_pnl(mira_pnl)}">{fmt_money_full(mira_pnl)} ({fmt_pct(mira_pct)})</div>
            </div>
            <div class="acct-divider"></div>
            <div class="acct-row">
                <span class="acct-row-label">투자원금</span>
                <span class="acct-row-val">{fmt_money_full(mira_cost_total)}</span>
            </div>
            <div class="acct-divider-light"></div>
            <div class="acct-row">
                <span class="acct-row-label acct-row-sub">├ 주식 평가</span>
                <span class="acct-row-val">{fmt_money_full(mira_eval)}</span>
            </div>
            <div class="acct-row">
                <span class="acct-row-label acct-row-sub">└ 예수금</span>
                <span class="acct-row-val">{fmt_money_full(mira_cash)}</span>
            </div>
        </div>""", unsafe_allow_html=True)

    # ── 자산 구성 (도넛 + 표 병행) ──
    st.markdown('<div class="section-title">자산 구성</div>', unsafe_allow_html=True)

    _colors = ["#7b1fa2", "#0288d1", "#f57c00", "#78909c"]
    _labels = ["ETF (IRP)", "TDF", "국내주식", "현금성자산"]
    _values = [max(0, v) for v in [irp_stock_eval, tdf_eval, mira_eval, cash_eval]]
    _total_for_pct = sum(_values) or 1

    col_donut, col_table = st.columns([1, 1])
    with col_donut:
        fig_type = go.Figure(go.Pie(
            labels=_labels, values=_values,
            hole=0.55, textinfo="label+percent",
            marker_colors=_colors,
        ))
        fig_type.update_layout(
            title="자산군별 비중", height=320,
            margin=dict(t=40, b=10, l=10, r=10),
            showlegend=False,
            font=dict(size=14),
        )
        st.plotly_chart(fig_type, use_container_width=True)

    with col_table:
        st.markdown("<div style='height:40px'></div>", unsafe_allow_html=True)
        # f-string 중첩 따옴표 충돌 방지: 행을 개별 문자열로 조립
        p_cell = "padding:0.55rem 0.8rem;"
        p_right = "padding:0.55rem 0.8rem;text-align:right;"
        rows_html = ""
        for label, value, color in zip(_labels, _values, _colors):
            pct = value / _total_for_pct * 100
            dot = (
                '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;'
                'background:' + color + ';margin-right:6px;vertical-align:middle;"></span>'
            )
            amt = fmt_money_full(value)
            rows_html += (
                "<tr>"
                + "<td style='" + p_cell + "'>" + dot + label + "</td>"
                + "<td style='" + p_right + "font-weight:600;'>" + amt + "</td>"
                + "<td style='" + p_right + "color:var(--text-dim);'>" + f"{pct:.1f}%" + "</td>"
                + "</tr>"
            )
        total_amt = fmt_money_full(sum(_values))
        table_html = (
            "<table style='width:100%;border-collapse:collapse;font-size:0.97rem;'>"
            "<thead><tr style='border-bottom:1px solid var(--card-border);color:var(--text-dim);font-size:0.85rem;'>"
            "<th style='padding:0.4rem 0.8rem;text-align:left;font-weight:400;'>자산군</th>"
            "<th style='padding:0.4rem 0.8rem;text-align:right;font-weight:400;'>평가금액</th>"
            "<th style='padding:0.4rem 0.8rem;text-align:right;font-weight:400;'>비중</th>"
            "</tr></thead>"
            "<tbody style='border-bottom:1px solid var(--card-border);'>"
            + rows_html +
            "</tbody>"
            "<tfoot><tr style='border-top:1px solid var(--card-border);font-weight:700;'>"
            "<td style='" + p_cell + "'>합계</td>"
            "<td style='" + p_right + "'>" + total_amt + "</td>"
            "<td style='" + p_right + "'>100%</td>"
            "</tr></tfoot>"
            "</table>"
        )
        st.markdown(table_html, unsafe_allow_html=True)

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
                name="평가금액", marker_color="#e0635e", opacity=0.85,
            ))
            fig_trend.add_trace(go.Scatter(
                x=mdf["년월_표시"], y=mdf["통합원금"],
                name="원금", mode="lines+markers",
                line=dict(color="#5b9bd8", width=2),
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

    # ETF 먼저, 그 다음 주식 — 각 그룹 내에서는 투자원금(매입금액) 큰 순서
    def _type_rank(code):
        asset_info = ASSET_MASTER.get(code, {})
        return 0 if asset_info.get("type") == "ETF" else 1

    display_df = display_df.copy()
    display_df["_type_rank"] = display_df["종목코드"].apply(_type_rank)
    display_df = display_df.sort_values(
        ["_type_rank", "매입금액"], ascending=[True, False]
    )

    # 표시용 데이터프레임 구성
    table_rows = []
    for _, row in display_df.iterrows():
        code = row["종목코드"]
        계좌 = row["계좌"]
        has_price = row.get("시세반영", False)
        current_price = row.get("현재가", None)
        acct_short = "IRP" if "신한" in 계좌 else "미래에셋"
        asset_info = ASSET_MASTER.get(code, {})
        type_label = asset_info.get("type", "")
        cur_val = current_price if has_price else row["평균단가"]

        table_rows.append({
            "구분": type_label,
            "종목명": row["종목명"],
            "계좌": acct_short,
            "수량": int(row["보유수량"]),
            "평단": int(row["평균단가"]),
            "현재가": int(cur_val) if pd.notna(cur_val) else None,
            "투자원금": int(row["매입금액"]),
            "평가금액": int(row["평가금액"]),
            "손익": int(row["평가손익"]),
            "수익률": float(row["수익률"]),
            "시세반영": bool(has_price),
        })

    table_df = pd.DataFrame(table_rows)

    def _style_holding_pnl(v):
        try:
            f = float(v)
        except Exception:
            return ""
        color = "#e0635e" if f > 0 else "#5b9bd8" if f < 0 else "inherit"
        return f"color: {color}; font-weight: 600"

    show_cols = ["구분", "종목명", "계좌", "수량", "평단", "현재가", "투자원금", "평가금액", "손익", "수익률"]
    styled_table = table_df[show_cols].style.map(_style_holding_pnl, subset=["손익", "수익률"])

    col_config = {
        "수량": st.column_config.NumberColumn("수량", format="localized"),
        "평단": st.column_config.NumberColumn("평단", format="localized"),
        "현재가": st.column_config.NumberColumn("현재가", format="localized"),
        "투자원금": st.column_config.NumberColumn("투자원금", format="localized"),
        "평가금액": st.column_config.NumberColumn("평가금액", format="localized"),
        "손익": st.column_config.NumberColumn("손익", format="localized"),
        "수익률": st.column_config.NumberColumn("수익률", format="%.2f%%"),
    }

    st.dataframe(
        styled_table,
        use_container_width=True,
        hide_index=True,
        column_config=col_config,
        height=min(560, 50 + 45 * len(table_df)),
    )

    미반영수 = (~display_df["시세반영"]).sum() if "시세반영" in display_df.columns else 0
    if 미반영수 > 0:
        st.caption(f"⚠ {미반영수}종목은 실시간 시세 조회에 실패해 매입가로 표시 중입니다. '시세 새로고침'을 눌러 다시 시도하세요.")

    # ── 종목별 보유 비중 도넛 + 수익률 바 차트 ──
    if len(display_df) > 0:
        ch1, ch2 = st.columns([1, 1])

        with ch1:
            st.markdown('<div class="section-title">종목별 보유 비중</div>', unsafe_allow_html=True)
            donut_labels = display_df["종목명"].tolist()
            donut_values = display_df["평가금액"].tolist()
            # 종목 유형별 색상 팔레트
            palette = [
                "#534AB7", "#7b5ea7", "#1976d2", "#0288d1",
                "#f57c00", "#e65100", "#388e3c", "#1b5e20",
                "#c62828", "#ad1457",
            ]
            donut_colors = [palette[i % len(palette)] for i in range(len(donut_labels))]
            fig_donut = go.Figure(go.Pie(
                labels=donut_labels,
                values=donut_values,
                hole=0.52,
                textinfo="label+percent",
                marker_colors=donut_colors,
                textfont=dict(size=13),
            ))
            fig_donut.update_layout(
                height=360,
                margin=dict(t=10, b=10, l=10, r=10),
                showlegend=False,
                font=dict(size=13),
            )
            st.plotly_chart(fig_donut, use_container_width=True)

        with ch2:
            st.markdown('<div class="section-title">종목별 수익률</div>', unsafe_allow_html=True)
            chart_df = display_df.sort_values("수익률")
            colors = ["#e0635e" if v >= 0 else "#5b9bd8" for v in chart_df["수익률"]]
            fig = go.Figure(go.Bar(
                x=chart_df["수익률"],
                y=chart_df["종목명"],
                orientation="h",
                marker_color=colors,
                text=[fmt_pct(v) for v in chart_df["수익률"]],
                textposition="outside",
            ))
            fig.update_layout(
                height=360,
                margin=dict(t=10, b=10, l=10, r=90),
                xaxis_title="수익률(%)",
                xaxis=dict(zeroline=True, tickfont=dict(size=13)),
                yaxis=dict(tickfont=dict(size=13)),
                font=dict(size=13),
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
    s1.markdown(f"""
    <div class="metric-card" style="background:var(--card-bg);border:1px solid var(--card-border)">
        <div class="metric-label">총 거래 건수</div>
        <div class="metric-value">{len(df):,}건</div>
    </div>""", unsafe_allow_html=True)
    s2.markdown(f"""
    <div class="metric-card" style="background:var(--card-bg);border:1px solid var(--card-border)">
        <div class="metric-label">매수 건수</div>
        <div class="metric-value" style="color:#e0635e">{len(df[df['거래구분']=='매수']):,}건</div>
    </div>""", unsafe_allow_html=True)
    s3.markdown(f"""
    <div class="metric-card" style="background:var(--card-bg);border:1px solid var(--card-border)">
        <div class="metric-label">매도 건수</div>
        <div class="metric-value" style="color:#5b9bd8">{len(df[df['거래구분']=='매도']):,}건</div>
    </div>""", unsafe_allow_html=True)

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
        height=min(600, 50 + 45 * len(display_df)),
    )


# ============================================================
# 탭4: 현금흐름 (거래이력 기반 자동 계산 — 별도 시트 없음)
# ============================================================
def render_cashflow(trade_df):
    st.markdown('<div class="section-title">자금흐름 추적</div>', unsafe_allow_html=True)
    st.caption("💡 거래이력 시트만으로 자동 계산됩니다. 매도 한 건이 발생하면 그 이후 같은 계좌에서 일어난 매수 내역을 시간순으로 보여줍니다.")
    st.caption("⚠ 참고용입니다. 매도금이 정확히 어느 매수에 쓰였는지는 계좌 잔액이 섞이기 때문에 100% 단정할 수 없고, 시간 순서로 정황만 보여줍니다.")

    if trade_df.empty:
        st.info("거래이력이 없습니다.")
        return

    realized_df = calc_realized_pnl(trade_df)

    # ── 실현손익 요약 ──
    st.markdown('<div class="section-title">실현손익 (매도 건 기준)</div>', unsafe_allow_html=True)
    st.caption("📌 평균매입가법으로 계산하며, 이 화면과 다른 모든 화면이 동일한 계산 함수 하나를 공유합니다.")

    if realized_df.empty:
        st.info("매도 거래가 없어 실현손익이 없습니다.")
        return

    total_realized = int(realized_df["실현손익"].sum())
    win_count = int((realized_df["실현손익"] > 0).sum())
    lose_count = int((realized_df["실현손익"] < 0).sum())

    r1, r2, r3 = st.columns(3)
    with r1:
        st.markdown(f"""
        <div class="metric-card" style="background:var(--card-bg);border:1px solid var(--card-border)">
            <div class="metric-label">총 실현손익</div>
            <div class="metric-value" style="color:{color_pnl(total_realized)}">{fmt_money_full(total_realized)}</div>
        </div>""", unsafe_allow_html=True)
    with r2:
        st.markdown(f"""
        <div class="metric-card" style="background:var(--card-bg);border:1px solid var(--card-border)">
            <div class="metric-label">수익 매도</div>
            <div class="metric-value" style="color:#e0635e">{win_count}건</div>
        </div>""", unsafe_allow_html=True)
    with r3:
        st.markdown(f"""
        <div class="metric-card" style="background:var(--card-bg);border:1px solid var(--card-border)">
            <div class="metric-label">손실 매도</div>
            <div class="metric-value" style="color:#5b9bd8">{lose_count}건</div>
        </div>""", unsafe_allow_html=True)

    # 실현손익 표 — 손익 색상 적용 (Styler 사용)
    display_realized = realized_df.sort_values("거래일자", ascending=False).copy()
    display_realized["거래일자"] = display_realized["거래일자"].dt.strftime("%Y-%m-%d")

    def _style_pnl(v):
        try:
            f = float(v)
        except Exception:
            return ""
        color = "#e0635e" if f > 0 else "#5b9bd8" if f < 0 else "inherit"
        return f"color: {color}; font-weight: 600"

    styled = display_realized.style
    if hasattr(styled, "map"):
        styled = styled.map(_style_pnl, subset=["실현손익"])
    else:
        styled = styled.applymap(_style_pnl, subset=["실현손익"])
    col_config = build_number_column_config(
        display_realized,
        money_cols=["매도단가", "평균매입단가", "매도금액", "매입금액", "실현손익"],
    )
    st.dataframe(
        styled, use_container_width=True, hide_index=True,
        column_config=col_config,
        height=min(560, 50 + 45 * len(display_realized)),
    )

    # ── 통합 자금흐름 타임라인 (주식 매도 + TDF 환매 통합) ──
    st.markdown('<div class="section-title">매도 이후 자금 사용 타임라인</div>', unsafe_allow_html=True)
    st.caption("📌 매도 건 아래에 그 이후 같은 계좌에서 발생한 매수 내역을 표시합니다. 매도금이 그대로 쓰였다는 뜻이 아니라 시간 순서상 정황입니다.")

    trade_sorted = trade_df.copy()
    trade_sorted["거래일자_dt"] = pd.to_datetime(trade_sorted["거래일자"], errors="coerce")
    trade_sorted = trade_sorted.sort_values("거래일자_dt")

    # ── 이벤트 목록 구성: 주식/ETF 매도만 ──
    events = []
    for _, row in realized_df.iterrows():
        events.append({
            "날짜": row["거래일자"],
            "계좌": row["계좌"],
            "제목": f"{row['종목명']} 매도",
            "금액": row["매도금액"],
            "손익": row["실현손익"],
        })

    # 날짜 내림차순 정렬
    events = sorted(events, key=lambda x: x["날짜"], reverse=True)

    def _render_event_block(ev):
        ev_date = ev["날짜"]
        ev_account = ev["계좌"]
        ev_amount = ev["금액"]
        pnl = ev["손익"]

        # 매도일 이후 같은 계좌의 매수 내역
        후속매수 = trade_sorted[
            (trade_sorted["거래일자_dt"] >= ev_date) &
            (trade_sorted["운용사"] == ev_account) &
            (trade_sorted["거래구분"] == "매수")
        ].head(5)

        if 후속매수.empty:
            buy_html = '<div class="sell-follow-empty">↳ 이후 같은 계좌에서 매수 내역 없음 (예수금으로 남아있을 가능성)</div>'
        else:
            items = []
            for _, buy in 후속매수.iterrows():
                buy_amt = int(buy["거래수량"]) * float(buy["거래단가"])
                items.append(
                    f'<div class="sell-follow-item">↳ {buy["거래일자_dt"].strftime("%Y-%m-%d")} · '
                    f'{buy["종목명"]} 매수 {int(buy["거래수량"])}주 · {fmt_money(buy_amt)}</div>'
                )
            buy_html = "".join(items)

        badge_class = "badge-irp" if "신한" in ev_account else "badge-mira"
        pnl_html = f'<span class="sell-event-pnl" style="color:{color_pnl(pnl)}">실현손익 {fmt_money(pnl)}</span>'

        st.markdown(
            '<div class="acct-card sell-event-card">'
            '<div class="sell-event-header">'
            f'<span class="acct-badge {badge_class}">{ev_account}</span>'
            f'<span class="sell-event-name">{ev["제목"]}</span>'
            f'<span class="sell-event-date">{ev_date.strftime("%Y-%m-%d")}</span>'
            '<span class="sell-event-spacer"></span>'
            f'<span class="sell-event-amount">{fmt_money(ev_amount)} 회수</span>'
            f'{pnl_html}'
            '</div>'
            f'{buy_html}'
            '</div>',
            unsafe_allow_html=True
        )

    if not events:
        st.info("매도 내역이 없습니다.")
    else:
        최근표시건수 = 3
        for i in range(min(최근표시건수, len(events))):
            _render_event_block(events[i])

        if len(events) > 최근표시건수:
            with st.expander(f"이전 매도 건 {len(events) - 최근표시건수}건 더 보기", expanded=False):
                for i in range(최근표시건수, len(events)):
                    _render_event_block(events[i])


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
