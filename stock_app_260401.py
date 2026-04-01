import json
import os
from datetime import datetime
from io import BytesIO

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

# ============================================================
# 기본 설정
# ============================================================
st.set_page_config(page_title="개인 투자 포트폴리오", layout="wide")

AUTO_SAVE_PATH = "transaction_history_autosave.json"

STD_COLUMNS = {
    "거래일자": ["거래일자", "일자", "날짜", "체결일", "매매일자", "date"],
    "거래구분": ["거래구분", "구분", "매수매도", "매매구분", "type"],
    "종목코드": ["종목코드", "코드", "ticker", "symbol"],
    "종목명": ["종목명", "종목", "name"],
    "거래수량": ["거래수량", "수량", "주수", "qty", "quantity"],
    "거래단가": ["거래단가", "단가", "체결단가", "price"],
    "운용사": ["운용사", "증권사", "계좌", "broker"],
}

BUY_WORDS = ["매수", "buy", "b"]
SELL_WORDS = ["매도", "sell", "s"]

ETF_NAME_KEYWORDS = [
    "etf", "kodex", "tiger", "ace", "arirang", "kbstar", "hanaro", "koact",
    "timefolio", "sol", "plus", "rise", "foocus", "truston", "마이티"
]

MANUAL_CLASSIFICATION = {
    # 필요 시 직접 보정
    # "069500": {"asset_class": "ETF", "market_type": "ETF"},
    # "005930": {"asset_class": "종목", "market_type": "KOSPI"},
}

NUMERIC_COLUMNS = [
    "거래수량", "거래단가", "보유수량", "평균매입가", "현재가", "평가금액", "미실현손익", "실현손익",
    "총매수금액", "총매도금액", "수익률(%)", "비중(%)", "RSI", "MA20", "MA60", "MA120",
    "거래량배수", "20일위치(%)", "판정점수", "현재비중(%)", "목표비중(%)", "목표평가금액",
    "현재평가금액", "차이금액", "제안수량"
]

# ============================================================
# 스타일
# ============================================================
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
    .top-nav {
        display:flex; flex-wrap:wrap; gap:10px; margin: 0.3rem 0 1rem 0;
    }
    .top-nav a {
        text-decoration:none; padding: 6px 12px; border:1px solid rgba(250,250,250,0.18);
        border-radius:999px; font-size:0.95rem; color:inherit; background: rgba(255,255,255,0.04);
    }
    .top-nav a:hover {background: rgba(255,255,255,0.08);}
    div[data-testid="stMetricValue"] {font-size: 2.2rem;}
    div[data-testid="stFileUploaderDropzone"] {padding-top: 0.5rem; padding-bottom: 0.5rem;}
    div[data-testid="stDataFrame"] [role="columnheader"] {
        font-weight: 700;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ============================================================
# 공통 함수
# ============================================================
def safe_str(x):
    if pd.isna(x):
        return ""
    return str(x).strip()


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {}
    current_cols = list(df.columns)
    for std_col, aliases in STD_COLUMNS.items():
        alias_lower = [a.lower() for a in aliases]
        for c in current_cols:
            if safe_str(c).lower() in alias_lower:
                rename_map[c] = std_col
                break
    return df.rename(columns=rename_map)


def ensure_required_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in STD_COLUMNS.keys():
        if col not in df.columns:
            df[col] = ""
    return df[["거래일자", "거래구분", "종목코드", "종목명", "거래수량", "거래단가", "운용사"]].copy()


def normalize_trade_type(x: str) -> str:
    val = safe_str(x).lower()
    if any(w == val or w in val for w in BUY_WORDS):
        return "매수"
    if any(w == val or w in val for w in SELL_WORDS):
        return "매도"
    return safe_str(x)


def normalize_code(code: str) -> str:
    code = safe_str(code)
    if not code:
        return ""
    if code.endswith(".KS") or code.endswith(".KQ"):
        return code
    if code.isdigit() and len(code) <= 6:
        return code.zfill(6)
    return code


def parse_date(x):
    if pd.isna(x) or x == "":
        return pd.NaT
    try:
        return pd.to_datetime(x)
    except Exception:
        return pd.NaT


def clean_transactions(df: pd.DataFrame) -> pd.DataFrame:
    df = normalize_columns(df)
    df = ensure_required_columns(df)

    df["거래일자"] = df["거래일자"].apply(parse_date)
    df["거래구분"] = df["거래구분"].apply(normalize_trade_type)
    df["종목코드"] = df["종목코드"].apply(normalize_code)
    df["종목명"] = df["종목명"].apply(safe_str)
    df["운용사"] = df["운용사"].apply(safe_str)

    df["거래수량"] = pd.to_numeric(df["거래수량"], errors="coerce").fillna(0)
    df["거래단가"] = pd.to_numeric(df["거래단가"], errors="coerce").fillna(0)

    if "입력순서" not in df.columns:
        df["입력순서"] = range(1, len(df) + 1)

    df = df.dropna(subset=["거래일자"])
    df = df[(df["거래구분"].isin(["매수", "매도"])) & (df["거래수량"] > 0) & (df["거래단가"] >= 0)]
    df = df.sort_values(["거래일자", "입력순서"], ascending=[True, True]).reset_index(drop=True)
    return df


def load_uploaded_file(uploaded_file) -> pd.DataFrame:
    file_name = uploaded_file.name.lower()
    if file_name.endswith(".csv"):
        df = pd.read_csv(uploaded_file)
    elif file_name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(uploaded_file)
    elif file_name.endswith(".json"):
        data = json.load(uploaded_file)
        df = pd.DataFrame(data)
    else:
        raise ValueError("지원하지 않는 파일 형식입니다.")
    return clean_transactions(df)


def save_transactions_json(df: pd.DataFrame, path: str = AUTO_SAVE_PATH):
    if df.empty:
        return
    save_df = df.copy()
    save_df["거래일자"] = pd.to_datetime(save_df["거래일자"]).dt.strftime("%Y-%m-%d")
    save_df.to_json(path, orient="records", force_ascii=False, indent=2)


def load_transactions_json(path: str = AUTO_SAVE_PATH) -> pd.DataFrame:
    empty_df = pd.DataFrame(columns=["거래일자", "거래구분", "종목코드", "종목명", "거래수량", "거래단가", "운용사", "입력순서"])
    if not os.path.exists(path):
        return empty_df
    try:
        df = pd.read_json(path)
        return clean_transactions(df)
    except Exception:
        return empty_df


def apply_excel_number_format(writer, sheet_name: str, df: pd.DataFrame):
    ws = writer.sheets[sheet_name[:31]]
    for idx, col_name in enumerate(df.columns, start=1):
        if col_name in NUMERIC_COLUMNS:
            for cell in ws.iter_cols(min_col=idx, max_col=idx, min_row=2, max_row=ws.max_row):
                for c in cell:
                    c.number_format = '#,##0'
                    c.alignment = c.alignment.copy(horizontal='right')
        else:
            for cell in ws.iter_cols(min_col=idx, max_col=idx, min_row=1, max_row=ws.max_row):
                for c in cell:
                    c.alignment = c.alignment.copy(horizontal='left')


def dataframe_to_excel_bytes(df_dict: dict) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, df in df_dict.items():
            if df is None or len(df) == 0:
                pd.DataFrame().to_excel(writer, index=False, sheet_name=sheet_name[:31])
                continue
            export_df = df.copy()
            if "거래일자" in export_df.columns and pd.api.types.is_datetime64_any_dtype(export_df["거래일자"]):
                export_df["거래일자"] = export_df["거래일자"].dt.strftime("%Y-%m-%d")
            export_df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
            apply_excel_number_format(writer, sheet_name, export_df)
    return output.getvalue()


def detect_asset_class(code: str, name: str):
    code = normalize_code(code)
    name_l = safe_str(name).lower()

    if code in MANUAL_CLASSIFICATION:
        return MANUAL_CLASSIFICATION[code]["asset_class"], MANUAL_CLASSIFICATION[code]["market_type"]

    if any(k in name_l for k in ETF_NAME_KEYWORDS):
        return "ETF", "ETF"

    if code.isdigit() and len(code) == 6:
        return "종목", "국내주식"

    return "기타", "기타"


def symbol_candidates(code: str):
    code = normalize_code(code)
    if code.endswith(".KS") or code.endswith(".KQ"):
        return [code]
    if code.isdigit() and len(code) == 6:
        return [f"{code}.KS", f"{code}.KQ"]
    return [code]


@st.cache_data(ttl=600, show_spinner=False)
def fetch_history(code: str, period: str = "1y") -> pd.DataFrame:
    candidates = symbol_candidates(code)
    for symbol in candidates:
        try:
            hist = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False, actions=False)
            if hist is not None and not hist.empty and hist["Close"].dropna().shape[0] > 0:
                hist = hist.reset_index()
                hist["Symbol"] = symbol
                return hist
        except Exception:
            continue
    return pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def fetch_current_price_and_info(code: str, market_type_hint: str = "국내주식"):
    # 현재가 반영 안정성을 높이기 위해 1개월 데이터를 사용
    hist = fetch_history(code, period="1mo")
    if hist.empty:
        candidates = symbol_candidates(code)
        return {
            "symbol": candidates[0] if candidates else normalize_code(code),
            "current_price": np.nan,
            "volume": np.nan,
            "market_type": market_type_hint,
        }

    hist = hist.dropna(subset=["Close"]).copy()
    if hist.empty:
        candidates = symbol_candidates(code)
        return {
            "symbol": candidates[0] if candidates else normalize_code(code),
            "current_price": np.nan,
            "volume": np.nan,
            "market_type": market_type_hint,
        }

    symbol = hist["Symbol"].iloc[-1]
    last_row = hist.iloc[-1]
    close = float(last_row["Close"])
    volume = float(last_row["Volume"]) if "Volume" in hist.columns and pd.notna(last_row.get("Volume", np.nan)) else np.nan
    market_type = "KOSDAQ" if symbol.endswith(".KQ") else ("KOSPI" if symbol.endswith(".KS") else market_type_hint)
    return {
        "symbol": symbol,
        "current_price": close,
        "volume": volume,
        "market_type": market_type,
    }


def add_indicators(hist: pd.DataFrame) -> pd.DataFrame:
    df = hist.copy()
    if df.empty:
        return df

    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    df["MA120"] = df["Close"].rolling(120).mean()

    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI14"] = 100 - (100 / (1 + rs))

    df["VOL20"] = df["Volume"].rolling(20).mean() if "Volume" in df.columns else np.nan
    df["HIGH20"] = df["Close"].rolling(20).max()
    df["LOW20"] = df["Close"].rolling(20).min()
    return df


def signal_from_history(hist: pd.DataFrame):
    if hist.empty or len(hist) < 30:
        return {
            "판정": "데이터부족",
            "점수": np.nan,
            "설명": "가격 데이터가 충분하지 않아 판단을 보류합니다.",
            "RSI": np.nan,
            "MA20": np.nan,
            "MA60": np.nan,
            "거래량배수": np.nan,
            "20일위치": np.nan,
        }

    df = add_indicators(hist)
    last = df.iloc[-1]

    close = float(last["Close"])
    ma20 = float(last["MA20"]) if pd.notna(last["MA20"]) else np.nan
    ma60 = float(last["MA60"]) if pd.notna(last["MA60"]) else np.nan
    rsi = float(last["RSI14"]) if pd.notna(last["RSI14"]) else np.nan
    vol_ratio = (float(last["Volume"]) / float(last["VOL20"])) if pd.notna(last.get("VOL20", np.nan)) and float(last["VOL20"]) > 0 else np.nan

    high20 = float(last["HIGH20"]) if pd.notna(last["HIGH20"]) else np.nan
    low20 = float(last["LOW20"]) if pd.notna(last["LOW20"]) else np.nan
    pos20 = (close - low20) / (high20 - low20) * 100 if pd.notna(high20) and pd.notna(low20) and high20 > low20 else np.nan

    score = 0
    reasons = []

    if pd.notna(ma20) and pd.notna(ma60):
        if close > ma20 > ma60:
            score += 3
            reasons.append("추세가 우상향입니다")
        elif close > ma20:
            score += 1
            reasons.append("단기 추세는 양호합니다")
        elif close < ma20 < ma60:
            score -= 3
            reasons.append("추세가 약합니다")
        else:
            score -= 1
            reasons.append("추세가 혼조입니다")

    if pd.notna(rsi):
        if rsi <= 30:
            score += 3
            reasons.append("RSI가 낮아 반등 가능 구간입니다")
        elif rsi <= 40:
            score += 2
            reasons.append("RSI 기준 분할매수 검토 구간입니다")
        elif rsi < 65:
            reasons.append("RSI는 중립 구간입니다")
        elif rsi < 75:
            score -= 2
            reasons.append("RSI가 높아 과열 주의 구간입니다")
        else:
            score -= 3
            reasons.append("RSI 과열 구간입니다")

    if pd.notna(pos20):
        if pos20 <= 20:
            score += 2
            reasons.append("20일 가격범위 하단에 있어 가격 부담이 낮습니다")
        elif pos20 <= 50:
            score += 1
            reasons.append("가격 위치가 중하단입니다")
        elif pos20 >= 85:
            score -= 2
            reasons.append("20일 가격범위 상단에 가까워 추격매수 주의가 필요합니다")

    if pd.notna(vol_ratio):
        if vol_ratio >= 1.5:
            score += 1
            reasons.append("거래량이 평균보다 늘어 신호 신뢰도가 높습니다")
        elif vol_ratio < 0.7:
            score -= 1
            reasons.append("거래량이 약해 신호 신뢰도가 낮습니다")

    if score >= 6:
        judgment = "강매수"
    elif score >= 4:
        judgment = "분할매수"
    elif score >= 2:
        judgment = "반등매수"
    elif score >= 0:
        judgment = "보유"
    elif score >= -2:
        judgment = "관망"
    elif score >= -4:
        judgment = "비중축소"
    else:
        judgment = "차익실현"

    explanation = " / ".join(reasons[:4]) if reasons else "기술적 신호가 혼재합니다."
    return {
        "판정": judgment,
        "점수": score,
        "설명": explanation,
        "RSI": rsi,
        "MA20": ma20,
        "MA60": ma60,
        "거래량배수": vol_ratio,
        "20일위치": pos20,
    }


def build_portfolio(transactions: pd.DataFrame) -> pd.DataFrame:
    if transactions.empty:
        return pd.DataFrame()

    rows = []
    for code, g in transactions.groupby("종목코드", dropna=False):
        g = g.sort_values(["거래일자", "입력순서"]).copy()
        name = g["종목명"].iloc[-1] if g["종목명"].replace("", np.nan).notna().any() else code
        broker = g["운용사"].iloc[-1] if g["운용사"].replace("", np.nan).notna().any() else ""

        asset_class, market_type = detect_asset_class(code, name)

        qty = 0.0
        avg_cost = 0.0
        realized_pnl = 0.0
        buy_amount = 0.0
        sell_amount = 0.0

        for _, row in g.iterrows():
            trade_type = row["거래구분"]
            q = float(row["거래수량"])
            p = float(row["거래단가"])

            if trade_type == "매수":
                total_cost = qty * avg_cost + q * p
                qty += q
                avg_cost = total_cost / qty if qty > 0 else 0.0
                buy_amount += q * p
            elif trade_type == "매도":
                if q > qty:
                    q = qty
                realized_pnl += q * (p - avg_cost)
                qty -= q
                sell_amount += q * p
                if qty == 0:
                    avg_cost = 0.0

        info = fetch_current_price_and_info(code, market_type_hint=market_type)
        current_price = info["current_price"]
        resolved_market_type = "ETF" if asset_class == "ETF" else info["market_type"]

        hist = fetch_history(code, period="1y")
        sig = signal_from_history(hist)

        market_value = qty * current_price if pd.notna(current_price) else np.nan
        unrealized_pnl = qty * (current_price - avg_cost) if pd.notna(current_price) else np.nan
        return_pct = ((current_price / avg_cost - 1) * 100) if (pd.notna(current_price) and avg_cost > 0 and qty > 0) else np.nan

        rows.append({
            "자산구분": asset_class,
            "시장구분": resolved_market_type,
            "종목코드": normalize_code(code),
            "종목명": name,
            "운용사": broker,
            "보유수량": qty,
            "평균매입가": avg_cost,
            "현재가": current_price,
            "평가금액": market_value,
            "미실현손익": unrealized_pnl,
            "실현손익": realized_pnl,
            "수익률(%)": return_pct,
            "총매수금액": buy_amount,
            "총매도금액": sell_amount,
            "자동판정": sig["판정"],
            "판정점수": sig["점수"],
            "RSI": sig["RSI"],
            "MA20": sig["MA20"],
            "MA60": sig["MA60"],
            "거래량배수": sig["거래량배수"],
            "20일위치(%)": sig["20일위치"],
            "판정설명": sig["설명"],
        })

    pf = pd.DataFrame(rows)
    pf = pf[pf["보유수량"] > 0].copy()
    if pf.empty:
        return pf

    total_value = pf["평가금액"].fillna(0).sum()
    pf["비중(%)"] = np.where(total_value > 0, pf["평가금액"].fillna(0) / total_value * 100, 0)
    return pf.sort_values(["자산구분", "시장구분", "평가금액"], ascending=[True, True, False]).reset_index(drop=True)


def summarize_by_category(pf: pd.DataFrame, category_col: str, exclude_etf_in_market: bool = False) -> pd.DataFrame:
    if pf.empty:
        return pd.DataFrame()
    working = pf.copy()
    if exclude_etf_in_market and category_col == "시장구분":
        working = working[working["자산구분"] != "ETF"].copy()
    if working.empty:
        return pd.DataFrame(columns=[category_col, "종목수", "평가금액", "미실현손익", "실현손익", "비중(%)"])

    g = (
        working.groupby(category_col, dropna=False)
        .agg(
            종목수=("종목코드", "count"),
            평가금액=("평가금액", "sum"),
            미실현손익=("미실현손익", "sum"),
            실현손익=("실현손익", "sum"),
        )
        .reset_index()
    )
    total = working["평가금액"].sum()
    g["비중(%)"] = np.where(total > 0, g["평가금액"] / total * 100, 0)
    return g.sort_values("평가금액", ascending=False).reset_index(drop=True)


def rebalance_by_asset(portfolio: pd.DataFrame, target_etf: float, target_stock: float):
    total_eval = portfolio["평가금액"].fillna(0).sum()
    current_asset = summarize_by_category(portfolio, "자산구분")

    cur_etf = 0.0
    cur_stock = 0.0
    if not current_asset.empty:
        if (current_asset["자산구분"] == "ETF").any():
            cur_etf = float(current_asset.loc[current_asset["자산구분"] == "ETF", "평가금액"].iloc[0])
        if (current_asset["자산구분"] == "종목").any():
            cur_stock = float(current_asset.loc[current_asset["자산구분"] == "종목", "평가금액"].iloc[0])

    reb_df = pd.DataFrame([
        {"구분": "ETF", "현재평가금액": cur_etf, "목표평가금액": total_eval * target_etf / 100},
        {"구분": "일반 종목", "현재평가금액": cur_stock, "목표평가금액": total_eval * target_stock / 100},
    ])
    reb_df["차이금액"] = reb_df["목표평가금액"] - reb_df["현재평가금액"]
    reb_df["조치"] = reb_df["차이금액"].apply(lambda x: "매수 필요" if x > 0 else ("비중 축소" if x < 0 else "유지"))
    return reb_df


def per_stock_rebalance(portfolio: pd.DataFrame, targets: dict):
    if portfolio.empty:
        return pd.DataFrame()

    total_eval = portfolio["평가금액"].fillna(0).sum()
    rows = []
    for _, row in portfolio.iterrows():
        code = row["종목코드"]
        weight = float(targets.get(code, 0))
        current_value = float(row["평가금액"]) if pd.notna(row["평가금액"]) else 0.0
        target_value = total_eval * weight / 100
        diff_value = target_value - current_value
        current_price = float(row["현재가"]) if pd.notna(row["현재가"]) and row["현재가"] > 0 else np.nan
        diff_qty = int(diff_value / current_price) if pd.notna(current_price) and current_price > 0 else 0
        rows.append({
            "종목코드": code,
            "종목명": row["종목명"],
            "현재비중(%)": row["비중(%)"],
            "목표비중(%)": weight,
            "현재평가금액": current_value,
            "목표평가금액": target_value,
            "차이금액": diff_value,
            "제안수량": diff_qty,
            "조치": "매수" if diff_value > 0 else ("매도" if diff_value < 0 else "유지"),
        })
    return pd.DataFrame(rows).sort_values("차이금액", ascending=False).reset_index(drop=True)


def make_display_df(df: pd.DataFrame, int_like_cols=None, pct_cols=None, one_decimal_cols=None):
    out = df.copy()
    int_like_cols = int_like_cols or []
    pct_cols = pct_cols or []
    one_decimal_cols = one_decimal_cols or []

    for col in int_like_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(0)
    for col in pct_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(2)
    for col in one_decimal_cols:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(1)
    return out


def build_column_config(df: pd.DataFrame, pct_cols=None):
    pct_cols = pct_cols or []
    config = {}
    for col in df.columns:
        if col in pct_cols:
            config[col] = st.column_config.NumberColumn(col, format="%.2f")
        elif pd.api.types.is_numeric_dtype(df[col]):
            config[col] = st.column_config.NumberColumn(col, format="%d")
        else:
            config[col] = st.column_config.TextColumn(col)
    return config


def show_table(df: pd.DataFrame, height: int | None = None, pct_cols=None):
    pct_cols = pct_cols or []
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=height,
        column_config=build_column_config(df, pct_cols=pct_cols),
    )


def anchor(name: str):
    st.markdown(f'<div id="{name}"></div>', unsafe_allow_html=True)


# ============================================================
# 세션 초기화
# ============================================================
if "transactions" not in st.session_state:
    st.session_state.transactions = load_transactions_json()


# ============================================================
# 사이드바
# ============================================================
with st.sidebar:
    st.title("⚙️ 설정")
    st.caption("자동저장 · 백업 · 표시 옵션")

    if st.button("현재 거래내역 저장", use_container_width=True):
        save_transactions_json(st.session_state.transactions)
        st.success("저장되었습니다.")

    if not st.session_state.transactions.empty:
        export_json_df = st.session_state.transactions.copy()
        export_json_df["거래일자"] = export_json_df["거래일자"].dt.strftime("%Y-%m-%d")
        st.download_button(
            "거래이력 JSON 백업",
            data=export_json_df.to_json(orient="records", force_ascii=False, indent=2),
            file_name=f"transaction_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json",
            use_container_width=True,
        )

    st.markdown("---")
    st.caption("표시 설정")
    chart_period = st.selectbox("차트 기간", ["6mo", "1y", "2y"], index=1)
    show_all_history = st.checkbox("거래이력 전체 표시", value=False)


# ============================================================
# 헤더 + 상단 이동 메뉴
# ============================================================
st.title("📊 개인 투자 포트폴리오")
st.caption("거래이력 입력 · ETF/종목 구분 · 기술적 분석 · 자동판정 · 리밸런싱")
st.markdown(
    """
    <div class="top-nav">
        <a href="#section-input">거래이력 입력</a>
        <a href="#section-history">거래 이력</a>
        <a href="#section-portfolio">포트폴리오 현황</a>
        <a href="#section-judgment">자동판정</a>
        <a href="#section-detail">종목별 상세 분석</a>
        <a href="#section-rebalance">리밸런싱</a>
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# 입력 대시보드 (축소/간소화)
# ============================================================
anchor("section-input")
with st.container(border=True):
    st.subheader("📂 거래이력 입력")
    st.caption("거래일자 · 거래구분 · 종목코드/종목명 · 거래수량 · 거래단가 포함 파일 업로드")

    top1, top2 = st.columns([3.2, 1.1])
    with top1:
        uploaded_file = st.file_uploader(
            "거래이력 파일 업로드",
            type=["csv", "xlsx", "xls", "json"],
            label_visibility="collapsed",
            help="CSV, XLSX, XLS, JSON 파일 업로드 가능",
        )
    with top2:
        st.write("")
        if st.button("업로드 파일 반영", use_container_width=True, type="primary"):
            if uploaded_file is None:
                st.warning("업로드 파일을 먼저 선택해 주세요.")
            else:
                try:
                    upload_df = load_uploaded_file(uploaded_file)
                    if st.session_state.transactions.empty:
                        st.session_state.transactions = upload_df.copy()
                    else:
                        merged = pd.concat([st.session_state.transactions, upload_df], ignore_index=True)
                        merged["입력순서"] = range(1, len(merged) + 1)
                        st.session_state.transactions = clean_transactions(merged)
                    save_transactions_json(st.session_state.transactions)
                    st.success(f"{len(upload_df):,}건 반영 완료")
                except Exception as e:
                    st.error(f"파일 반영 중 오류: {e}")

    with st.expander("직접 입력", expanded=False):
        c1, c2, c3, c4, c5, c6 = st.columns([1.1, 0.9, 1.1, 1.1, 0.9, 1.0])
        with c1:
            input_date = st.date_input("거래일자", value=datetime.today())
        with c2:
            input_type = st.selectbox("거래구분", ["매수", "매도"])
        with c3:
            input_code = st.text_input("종목코드", placeholder="예: 069500")
        with c4:
            input_name = st.text_input("종목명", placeholder="예: KODEX200")
        with c5:
            input_qty = st.number_input("거래수량", min_value=0, step=1, value=0, format="%d")
        with c6:
            input_price = st.number_input("거래단가", min_value=0, step=100, value=0, format="%d")

        c7, c8, c9 = st.columns([1.3, 1.1, 3.5])
        with c7:
            input_broker = st.text_input("운용사", placeholder="예: 신한투자증권")
        with c8:
            st.write("")
            st.write("")
            if st.button("직접 입력 추가", use_container_width=True):
                if input_qty <= 0 or input_price < 0 or (not input_code and not input_name):
                    st.warning("종목코드/종목명, 거래수량, 거래단가를 확인해 주세요.")
                else:
                    new_row = pd.DataFrame([
                        {
                            "거래일자": pd.to_datetime(input_date),
                            "거래구분": input_type,
                            "종목코드": normalize_code(input_code),
                            "종목명": safe_str(input_name),
                            "거래수량": int(input_qty),
                            "거래단가": int(input_price),
                            "운용사": safe_str(input_broker),
                        }
                    ])
                    merged = pd.concat([st.session_state.transactions, new_row], ignore_index=True)
                    merged["입력순서"] = range(1, len(merged) + 1)
                    st.session_state.transactions = clean_transactions(merged)
                    save_transactions_json(st.session_state.transactions)
                    st.success("거래내역이 추가되었습니다.")
        with c9:
            pass


# ============================================================
# 거래 이력
# ============================================================
anchor("section-history")
st.markdown("---")
st.subheader("🧾 거래 이력")
if st.session_state.transactions.empty:
    st.info("거래 파일을 업로드하거나 직접 입력해 주세요.")
    st.stop()

view_tx = st.session_state.transactions.copy()
view_tx["거래일자"] = view_tx["거래일자"].dt.strftime("%Y-%m-%d")
if not show_all_history:
    view_tx = view_tx.tail(20)

view_tx_display = make_display_df(view_tx, int_like_cols=["거래수량", "거래단가"])
show_table(view_tx_display, height=320)


# ============================================================
# 포트폴리오 집계
# ============================================================
portfolio = build_portfolio(st.session_state.transactions)

anchor("section-portfolio")
st.markdown("---")
st.subheader("💼 포트폴리오 현황")

if portfolio.empty:
    st.warning("현재 보유 종목이 없습니다. 전량 매도 상태이거나 거래내역을 확인해 주세요.")
    st.stop()

# KPI
col1, col2, col3, col4 = st.columns(4)
total_eval = portfolio["평가금액"].fillna(0).sum()
total_unrealized = portfolio["미실현손익"].fillna(0).sum()
total_realized = portfolio["실현손익"].fillna(0).sum()
by_asset = summarize_by_category(portfolio, "자산구분")
by_market = summarize_by_category(portfolio, "시장구분", exclude_etf_in_market=True)
etf_weight = 0.0
if not by_asset.empty and (by_asset["자산구분"] == "ETF").any():
    etf_weight = float(by_asset.loc[by_asset["자산구분"] == "ETF", "비중(%)"].iloc[0])

col1.metric("총 평가금액", f"{total_eval:,.0f}원")
col2.metric("미실현손익", f"{total_unrealized:,.0f}원")
col3.metric("실현손익", f"{total_realized:,.0f}원")
col4.metric("ETF 비중", f"{etf_weight:,.2f}%")

sum1, sum2 = st.columns(2)
with sum1:
    st.markdown("#### 자산구분 요약")
    show_table(make_display_df(by_asset, int_like_cols=["평가금액", "미실현손익", "실현손익", "종목수"], pct_cols=["비중(%)"]), pct_cols=["비중(%)"])

with sum2:
    st.markdown("#### 시장구분 요약")
    if by_market.empty:
        st.caption("일반 종목 시장구분 데이터가 없습니다.")
    else:
        show_table(make_display_df(by_market, int_like_cols=["평가금액", "미실현손익", "실현손익", "종목수"], pct_cols=["비중(%)"]), pct_cols=["비중(%)"])


st.markdown("#### ETF 보유 현황")
etf_pf = portfolio[portfolio["자산구분"] == "ETF"].copy()
if etf_pf.empty:
    st.caption("보유 중인 ETF가 없습니다.")
else:
    etf_display = make_display_df(
        etf_pf,
        int_like_cols=["보유수량", "평균매입가", "현재가", "평가금액", "미실현손익", "실현손익", "총매수금액", "총매도금액", "판정점수", "RSI", "MA20", "MA60", "거래량배수"],
        pct_cols=["수익률(%)", "비중(%)", "20일위치(%)"],
    )
    show_table(etf_display, height=240, pct_cols=["수익률(%)", "비중(%)", "20일위치(%)"])

st.markdown("#### 일반 종목 보유 현황")
stock_pf = portfolio[portfolio["자산구분"] == "종목"].copy()
if stock_pf.empty:
    st.caption("보유 중인 일반 종목이 없습니다.")
else:
    stock_display = make_display_df(
        stock_pf,
        int_like_cols=["보유수량", "평균매입가", "현재가", "평가금액", "미실현손익", "실현손익", "총매수금액", "총매도금액", "판정점수", "RSI", "MA20", "MA60", "거래량배수"],
        pct_cols=["수익률(%)", "비중(%)", "20일위치(%)"],
    )
    show_table(stock_display, height=280, pct_cols=["수익률(%)", "비중(%)", "20일위치(%)"])

with st.expander("기타 자산/미분류 보기", expanded=False):
    other_pf = portfolio[~portfolio["자산구분"].isin(["ETF", "종목"])].copy()
    if other_pf.empty:
        st.caption("기타 자산이 없습니다.")
    else:
        other_display = make_display_df(
            other_pf,
            int_like_cols=["보유수량", "평균매입가", "현재가", "평가금액", "미실현손익", "실현손익", "총매수금액", "총매도금액", "판정점수", "RSI", "MA20", "MA60", "거래량배수"],
            pct_cols=["수익률(%)", "비중(%)", "20일위치(%)"],
        )
        show_table(other_display, pct_cols=["수익률(%)", "비중(%)", "20일위치(%)"])


# ============================================================
# 자동판정 현황
# ============================================================
anchor("section-judgment")
st.markdown("---")
st.subheader("🤖 자동 매수·매도 판단")
judgment_view = portfolio[[
    "종목코드", "종목명", "자산구분", "시장구분", "현재가", "RSI", "거래량배수", "20일위치(%)",
    "자동판정", "판정점수", "판정설명"
]].copy()
judgment_display = make_display_df(
    judgment_view,
    int_like_cols=["현재가", "RSI", "거래량배수", "판정점수"],
    pct_cols=["20일위치(%)"],
)
show_table(judgment_display, height=300, pct_cols=["20일위치(%)"])


# ============================================================
# 종목별 상세 분석
# ============================================================
anchor("section-detail")
st.markdown("---")
st.subheader("🔍 종목별 상세 분석")
select_options = [f"{row['종목명']} ({row['종목코드']})" for _, row in portfolio.iterrows()]
selected_label = st.selectbox("분석할 종목 선택", select_options)
selected_code = selected_label.split("(")[-1].replace(")", "").strip()
selected_row = portfolio[portfolio["종목코드"] == selected_code].iloc[0]
selected_hist = add_indicators(fetch_history(selected_code, chart_period))
selected_trades = st.session_state.transactions[st.session_state.transactions["종목코드"] == selected_code].copy()
selected_trades["거래일자"] = selected_trades["거래일자"].dt.strftime("%Y-%m-%d")

info1, info2, info3, info4 = st.columns(4)
info1.metric("현재가", f"{selected_row['현재가']:,.0f}" if pd.notna(selected_row["현재가"]) else "-")
info2.metric("수익률", f"{selected_row['수익률(%)']:.2f}%" if pd.notna(selected_row["수익률(%)"]) else "-")
info3.metric("자동판정", str(selected_row["자동판정"]))
info4.metric("RSI", f"{selected_row['RSI']:,.0f}" if pd.notna(selected_row["RSI"]) else "-")

st.markdown("#### 상세 해설")
st.write(selected_row["판정설명"])

if not selected_hist.empty:
    time_col = selected_hist.columns[0]
    chart_df = selected_hist.set_index(time_col)[["Close", "MA20", "MA60", "MA120"]].copy()
    st.line_chart(chart_df)

    r1, r2, r3 = st.columns(3)
    r1.write(f"- 20일 이동평균: {selected_row['MA20']:,.0f}" if pd.notna(selected_row['MA20']) else "- 20일 이동평균: -")
    r2.write(f"- 60일 이동평균: {selected_row['MA60']:,.0f}" if pd.notna(selected_row['MA60']) else "- 60일 이동평균: -")
    r3.write(f"- 거래량배수: {selected_row['거래량배수']:,.0f}" if pd.notna(selected_row['거래량배수']) else "- 거래량배수: -")
else:
    st.caption("차트 데이터를 불러오지 못했습니다.")

st.markdown("#### 해당 종목 거래이력")
selected_trades_display = make_display_df(selected_trades, int_like_cols=["거래수량", "거래단가"])
show_table(selected_trades_display, height=220)


# ============================================================
# 리밸런싱
# ============================================================
anchor("section-rebalance")
st.markdown("---")
st.subheader("⚖️ 리밸런싱")

reb_tab1, reb_tab2 = st.tabs(["자산구분 기준", "종목별 목표비중"])

with reb_tab1:
    st.caption("ETF와 일반 종목의 목표 비중을 입력하면 현재 평가금액 기준 부족/초과 금액을 계산합니다.")
    r1, r2 = st.columns(2)
    with r1:
        target_etf = st.number_input("ETF 목표 비중(%)", min_value=0, max_value=100, value=60, step=1, format="%d")
    with r2:
        target_stock = st.number_input("일반 종목 목표 비중(%)", min_value=0, max_value=100, value=40, step=1, format="%d")

    if (target_etf + target_stock) != 100:
        st.error("ETF 목표 비중과 일반 종목 목표 비중의 합은 100이어야 합니다.")
    else:
        reb_df = rebalance_by_asset(portfolio, target_etf, target_stock)
        reb_display = make_display_df(reb_df, int_like_cols=["현재평가금액", "목표평가금액", "차이금액"])
        show_table(reb_display)

with reb_tab2:
    st.caption("현재 보유 종목별 목표비중을 입력하면 매수/매도 제안 수량을 계산합니다.")
    target_inputs = {}
    cols = st.columns(3)
    for i, (_, row) in enumerate(portfolio.iterrows()):
        with cols[i % 3]:
            default_weight = int(round(float(row["비중(%)"]))) if pd.notna(row["비중(%)"]) else 0
            target_inputs[row["종목코드"]] = st.number_input(
                f"{row['종목명']} ({row['종목코드']}) 목표비중",
                min_value=0,
                max_value=100,
                value=default_weight,
                step=1,
                format="%d",
                key=f"target_{row['종목코드']}",
            )

    total_target = sum(target_inputs.values())
    st.write(f"목표비중 합계: {total_target}%")

    if total_target != 100:
        st.warning("종목별 목표비중 합계는 100이어야 정확한 계산이 가능합니다.")
    else:
        stock_reb_df = per_stock_rebalance(portfolio, target_inputs)
        stock_reb_display = make_display_df(
            stock_reb_df,
            int_like_cols=["현재비중(%)", "목표비중(%)", "현재평가금액", "목표평가금액", "차이금액", "제안수량"],
        )
        show_table(stock_reb_display, height=320)


# ============================================================
# 다운로드
# ============================================================
st.markdown("---")
report_excel = dataframe_to_excel_bytes({
    "거래이력": st.session_state.transactions,
    "포트폴리오": make_display_df(
        portfolio,
        int_like_cols=["보유수량", "평균매입가", "현재가", "평가금액", "미실현손익", "실현손익", "총매수금액", "총매도금액", "RSI", "MA20", "MA60", "거래량배수", "판정점수"],
        pct_cols=["수익률(%)", "비중(%)", "20일위치(%)"],
    ),
    "자산구분요약": make_display_df(by_asset, int_like_cols=["종목수", "평가금액", "미실현손익", "실현손익"], pct_cols=["비중(%)"]),
    "시장구분요약": make_display_df(by_market, int_like_cols=["종목수", "평가금액", "미실현손익", "실현손익"], pct_cols=["비중(%)"]),
    "자동판정": judgment_display,
})

st.download_button(
    "전체 결과 엑셀 다운로드",
    data=report_excel,
    file_name=f"portfolio_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
