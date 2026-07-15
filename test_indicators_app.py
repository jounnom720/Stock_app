# -*- coding: utf-8 -*-
"""
test_indicators_app.py

레이어 1(시장 국면) + 레이어 2(종목 지표) + 레이어 3(문장 생성) +
투자자별(외국인/기관/개인) 순매수 동향까지 모두 눈으로 확인하는
독립 테스트용 Streamlit 앱입니다.

본 서비스(stock_app_main.py)와는 완전히 별개로 동작하므로,
여기서 무슨 일이 생겨도 지인들이 쓰는 본 앱에는 영향이 없습니다.

이번 버전에 추가된 것:
- 오늘 실제 종가·등락률을 화면 상단에 명확히 표시 (지표만 봐서는 오늘 얼마나
  올랐는지 안 보였던 문제 보완)
- "이 데이터는 실시간이 아니라 KRX 확정 기준"이라는 안내 문구
- 캐시를 무시하고 강제로 새로 조회하는 버튼 (이전 조회 결과가 남아있을 가능성 차단)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import streamlit as st

st.set_page_config(page_title="종목 지표 테스트", layout="centered")


# ============================================================
# [레이어 1] 시장 국면 분류
# ============================================================
REGIME_THRESHOLDS = {
    "패닉_급락장": (-100.0, -5.0),
    "조정_국면": (-5.0, -3.0),
    "변동성_확대_하락": (-3.0, -1.0),
    "평상시": (-1.0, 1.0),
    "변동성_확대_상승": (1.0, 3.0),
    "급등장": (3.0, 100.0),
}
REGIME_DESCRIPTIONS = {
    "패닉_급락장": "패닉성 급락장",
    "조정_국면": "단기 조정 국면",
    "변동성_확대_하락": "변동성이 다소 커진 하락 장세",
    "평상시": "평이한 시장 흐름",
    "변동성_확대_상승": "변동성이 다소 커진 상승 장세",
    "급등장": "이례적인 급등장",
}


@dataclass
class MarketRegimeResult:
    regime: str
    description: str
    change_pct: float
    index_name: str


def classify_market_regime(change_pct: float, index_name: str = "코스피") -> MarketRegimeResult:
    matched = "평상시"
    for regime, (lower, upper) in REGIME_THRESHOLDS.items():
        if regime == "급등장":
            if change_pct >= lower:
                matched = regime
                break
        else:
            if lower <= change_pct < upper:
                matched = regime
                break
    return MarketRegimeResult(matched, REGIME_DESCRIPTIONS[matched], change_pct, index_name)


# ============================================================
# [레이어 2] 종목별 지표 계산
# ============================================================
MA_WINDOW = 20
RSI_PERIOD = 14
VOLUME_WINDOW = 20
LOOKBACK_DAYS = 60


@dataclass
class StockIndicatorResult:
    ticker: str
    latest_date: str
    latest_close: float
    day_change_pct: Optional[float]   # 오늘 등락률 (pykrx가 제공하는 원본 값)
    ma_deviation_pct: Optional[float]
    rsi: Optional[float]
    volume_ratio: Optional[float]
    data_points: int


@st.cache_data(ttl=6 * 60 * 60)
def fetch_ohlcv(ticker: str, lookback_days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    from pykrx import stock
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    try:
        df = stock.get_market_ohlcv_by_date(start_date, end_date, ticker)
    except Exception as e:
        st.error(f"시세 조회 중 오류: {e}")
        return pd.DataFrame()
    return df if df is not None and not df.empty else pd.DataFrame()


def calc_ma_deviation(close_series: pd.Series, window: int = MA_WINDOW):
    if len(close_series) < window:
        return None
    ma = close_series.rolling(window=window).mean().iloc[-1]
    latest = close_series.iloc[-1]
    if ma == 0 or pd.isna(ma):
        return None
    return round(float((latest - ma) / ma * 100), 2)


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


def get_stock_indicators(ticker: str) -> StockIndicatorResult:
    df = fetch_ohlcv(ticker)
    if df.empty:
        return StockIndicatorResult(ticker, "", 0.0, None, None, None, None, 0)
    close = df["종가"]
    vol = df["거래량"]

    # pykrx가 반환하는 원본 등락률 컬럼을 그대로 사용 (직접 계산하지 않고 원본 신뢰)
    day_change_pct = None
    if "등락률" in df.columns:
        val = df["등락률"].iloc[-1]
        if not pd.isna(val):
            day_change_pct = round(float(val), 2)

    return StockIndicatorResult(
        ticker=ticker,
        latest_date=str(df.index[-1].date()),
        latest_close=float(close.iloc[-1]),
        day_change_pct=day_change_pct,
        ma_deviation_pct=calc_ma_deviation(close),
        rsi=calc_rsi(close),
        volume_ratio=calc_volume_ratio(vol),
        data_points=len(df),
    )


# ============================================================
# 투자자별(외국인/기관/개인) 순매수 동향
# ============================================================
INVESTOR_TREND_LOOKBACK = 15
INVESTOR_COLUMN_HINTS = {
    "외국인": ["외국인합계", "외국인"],
    "기관": ["기관합계", "기관"],
    "개인": ["개인"],
}


@dataclass
class InvestorTrendResult:
    ticker: str
    latest_date: str
    trends: dict = field(default_factory=dict)
    data_available: bool = True


def _find_column(df, hints):
    for hint in hints:
        if hint in df.columns:
            return hint
    for hint in hints:
        for col in df.columns:
            if hint in col:
                return col
    return None


def _calc_streak(series: pd.Series):
    if series.empty:
        return None, 0
    values = series.tolist()
    values.reverse()
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


@st.cache_data(ttl=6 * 60 * 60)
def fetch_investor_trading_value(ticker: str, lookback_days: int = INVESTOR_TREND_LOOKBACK) -> pd.DataFrame:
    from pykrx import stock
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    try:
        df = stock.get_market_trading_value_by_date(start_date, end_date, ticker)
    except Exception as e:
        st.warning(f"투자자 동향 조회 중 오류(지표 결과에는 영향 없음): {e}")
        return pd.DataFrame()
    return df if df is not None and not df.empty else pd.DataFrame()


def get_investor_trend(ticker: str) -> InvestorTrendResult:
    df = fetch_investor_trading_value(ticker)
    if df.empty:
        return InvestorTrendResult(ticker, "", {}, False)

    trends = {}
    for investor_type, hints in INVESTOR_COLUMN_HINTS.items():
        col = _find_column(df, hints)
        if col is None:
            continue
        series = df[col]
        direction, streak_days = _calc_streak(series)
        trends[investor_type] = {
            "latest_net": int(series.iloc[-1]),
            "streak_direction": direction,
            "streak_days": streak_days,
        }
    return InvestorTrendResult(ticker, str(df.index[-1].date()), trends, True)


# ============================================================
# 용어 사전
# ============================================================
GLOSSARY = {
    "이동평균 이격도": "오늘 가격이 이동평균(최근 N일 평균 가격)보다 몇 % 위/아래에 있는지 나타낸 값. 많이 벗어나 있을수록 평소 흐름과 차이가 크다는 뜻입니다.",
    "RSI": "상대강도지수. 최근 가격이 얼마나 자주·많이 올랐는지를 0~100 사이 숫자로 나타낸 지표. 70 이상이면 과매수, 30 이하면 과매도로 해석합니다.",
    "과매수": "가격이 단기간에 많이 올라, 조정(하락) 가능성에 좀 더 무게가 실리는 상태.",
    "과매도": "가격이 단기간에 많이 떨어져, 반등 가능성에 좀 더 무게가 실리는 상태.",
    "거래량 배율": "오늘 거래량이 최근 평균 거래량 대비 몇 배인지 나타낸 값. 2배 이상이면 평소보다 확실히 관심이 몰렸다는 신호입니다.",
    "순매수": "매수한 금액에서 매도한 금액을 뺀 값. 양수면 사는 쪽이 더 많았다, 음수면 파는 쪽이 더 많았다는 뜻입니다.",
    "시장 국면": "코스피 등 전체 지수의 당일 등락 폭을 기준으로 평상시/조정/패닉 등으로 분류한 것.",
}


# ============================================================
# [레이어 3] 국면 + 지표 + 투자자동향 → 문장 생성
# ============================================================
VOLUME_SURGE_RATIO = 2.0
VOLUME_DROP_RATIO = 0.5
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
MA_DEVIATION_HIGH = 10.0
MA_DEVIATION_LOW = -10.0
INVESTOR_STREAK_MIN_DAYS = 2
PANIC_REGIMES = {"패닉_급락장", "조정_국면"}
SURGE_REGIMES = {"급등장"}


def _volume_sentence(volume_ratio, regime, index_name, regime_change_pct):
    if volume_ratio >= VOLUME_SURGE_RATIO:
        if regime in PANIC_REGIMES:
            return (f"오늘처럼 {index_name}가 {regime_change_pct:+.2f}%까지 급락한 패닉장에서는 "
                    f"평소 대비 거래량이 {volume_ratio}배 폭증하는 게 자연스러운 현상으로, "
                    f"비정상적인 수치는 아닌 것으로 보입니다.")
        if regime in SURGE_REGIMES:
            return (f"매수세가 집중되며 거래량이 평소 대비 {volume_ratio}배 급증했습니다. "
                    f"단기 과열 가능성에 유의하시기 바랍니다.")
        return f"거래량이 평소 대비 {volume_ratio}배 증가했습니다. 특별한 이슈가 있는지 확인해보시는 게 좋겠습니다."
    if volume_ratio <= VOLUME_DROP_RATIO:
        return f"거래량이 평소 대비 {volume_ratio}배 수준으로 뜸한 편으로, 시장의 관심이 다소 줄어든 상태로 보입니다."
    return None


def _rsi_sentence(rsi):
    if rsi >= RSI_OVERBOUGHT:
        return f"RSI가 {rsi}로 과매수 구간에 진입해, 단기적으로 조정 가능성을 염두에 두시는 게 좋겠습니다."
    if rsi <= RSI_OVERSOLD:
        return f"RSI가 {rsi}로 과매도 구간에 있어, 단기 반등 가능성도 함께 참고하실 만합니다."
    return None


def _ma_deviation_sentence(ma_deviation_pct):
    if ma_deviation_pct >= MA_DEVIATION_HIGH:
        return f"20일 이동평균 대비 {ma_deviation_pct:+.2f}% 위에 있어, 단기적으로 많이 오른 상태입니다."
    if ma_deviation_pct <= MA_DEVIATION_LOW:
        return f"20일 이동평균 대비 {ma_deviation_pct:+.2f}% 아래에 있어, 단기 하락폭이 다소 큰 편입니다."
    return None


def _investor_sentences(investor_trends):
    sentences = []
    tone_positive = {"외국인": "수급 측면에서 긍정적입니다.", "기관": "수급 측면에서 긍정적입니다.",
                      "개인": "개인 투자자들의 관심이 높아지고 있는 것으로 보입니다."}
    tone_negative = {"외국인": "단기 수급이 약한 편입니다.", "기관": "단기 수급이 약한 편입니다.",
                      "개인": "개인 매도세가 이어지고 있습니다."}
    for investor_type, info in investor_trends.items():
        direction = info.get("streak_direction")
        days = info.get("streak_days", 0)
        if direction is None or days < INVESTOR_STREAK_MIN_DAYS:
            continue
        tone = (tone_positive if direction == "순매수" else tone_negative).get(
            investor_type, "참고할 만한 변화입니다.")
        sentences.append(f"최근 {days}일간 {investor_type}이 연속 {direction} 중이라 {tone}")
    return sentences


def generate_insight(regime_result, indicator_result, investor_result=None):
    sentences = []
    terms_used = []

    if indicator_result.data_points == 0:
        return "데이터를 가져오지 못해 인사이트를 생성할 수 없습니다.", []

    if indicator_result.volume_ratio is not None:
        s = _volume_sentence(indicator_result.volume_ratio, regime_result.regime,
                              regime_result.index_name, regime_result.change_pct)
        if s:
            sentences.append(s)
            terms_used += ["거래량 배율", "시장 국면"]

    if indicator_result.rsi is not None:
        s = _rsi_sentence(indicator_result.rsi)
        if s:
            sentences.append(s)
            terms_used += ["RSI", "과매수" if indicator_result.rsi >= RSI_OVERBOUGHT else "과매도"]

    if indicator_result.ma_deviation_pct is not None:
        s = _ma_deviation_sentence(indicator_result.ma_deviation_pct)
        if s:
            sentences.append(s)
            terms_used.append("이동평균 이격도")

    if investor_result is not None and investor_result.data_available:
        inv_sentences = _investor_sentences(investor_result.trends)
        if inv_sentences:
            sentences.extend(inv_sentences)
            terms_used.append("순매수")

    summary = " ".join(sentences) if sentences else "특이 신호 없이 평이한 흐름을 보이고 있습니다."
    terms_used = list(dict.fromkeys(terms_used))
    return summary, terms_used


# ============================================================
# 화면 UI
# ============================================================
st.title("종목 지표 테스트")
st.caption("시장 국면 + 종목 지표 + 투자자별(외국인/기관/개인) 순매수 동향을 종합한 인사이트를 확인하는 화면입니다.")

st.markdown(
    """
    <style>
    div[data-testid="stPopover"] button {
        justify-content: flex-start !important;
        text-align: left !important;
    }
    div[data-testid="stPopover"] button p {
        text-align: left !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.popover(
    "⚠️ 이 화면의 시세·거래량·투자자 동향은 KRX(한국거래소) 기준 시세이며, "
    "통합시세(NXT 포함)와 다를 수 있습니다.",
    use_container_width=True,
):
    st.write(
        "2025년 3월부터 국내에는 KRX 외에 넥스트레이드(NXT)라는 대체거래소가 함께 운영되고 있어, "
        "증권사 앱 기본 화면(통합시세)은 두 거래소 가격을 합친 값을 보여주는 경우가 많습니다. "
        "증권사 앱과 비교하실 때는 앱에서 'KRX시세'로 전환한 값과 비교해주세요.\n\n"
        "또한 데이터는 실시간이 아니라 KRX가 그날그날 확정 발표하는 통계 기준이라, "
        "장중이나 장 마감 직후에는 값이 다를 수 있습니다."
    )

col1, col2, col3 = st.columns([2, 2, 1])
with col1:
    ticker = st.text_input("종목코드", value="005930", help="예: 삼성전자 005930, KODEX200 069500")
with col2:
    kospi_change = st.number_input("오늘 코스피 등락률(%)", value=-1.5, step=0.1,
                                    help="레이어1 국면 판단에 쓰입니다. 실제 값을 몰라도 임의로 테스트 가능합니다.")
with col3:
    st.write("")
    st.write("")
    run = st.button("조회하기", use_container_width=True)

ignore_cache = st.checkbox("캐시 무시하고 최신 데이터로 새로 조회", value=False,
                            help="이전에 조회한 결과가 남아있는 것 같으면 체크 후 조회하세요.")

if run:
    if ignore_cache:
        fetch_ohlcv.clear()
        fetch_investor_trading_value.clear()

    with st.spinner("데이터를 가져오는 중..."):
        indicators = get_stock_indicators(ticker)
        investor = get_investor_trend(ticker)
        regime = classify_market_regime(kospi_change, index_name="코스피")

    if indicators.data_points == 0:
        st.warning("시세 데이터를 가져오지 못했습니다. 종목코드를 다시 확인해주세요.")
    else:
        st.success(f"조회 완료 — 최근 {indicators.data_points}일치 데이터 확보 (기준일: {indicators.latest_date})")

        # 오늘 실제 종가와 등락률을 가장 눈에 띄게 표시
        price_col, change_col = st.columns(2)
        price_col.metric("오늘 종가 (KRX 확정 기준)", f"{indicators.latest_close:,.0f}원")
        if indicators.day_change_pct is not None:
            change_col.metric("오늘 등락률", f"{indicators.day_change_pct:+.2f}%")
        else:
            change_col.metric("오늘 등락률", "정보 없음")

        m1, m2, m3 = st.columns(3)
        m1.metric("이동평균 이격도", f"{indicators.ma_deviation_pct}%" if indicators.ma_deviation_pct is not None else "데이터 부족")
        m2.metric("RSI", f"{indicators.rsi}" if indicators.rsi is not None else "데이터 부족")
        m3.metric("거래량 배율", f"{indicators.volume_ratio}배" if indicators.volume_ratio is not None else "데이터 부족")

        st.divider()
        st.subheader("투자자별 순매수 동향")
        if investor.data_available and investor.trends:
            cols = st.columns(len(investor.trends))
            for i, (investor_type, info) in enumerate(investor.trends.items()):
                direction = info["streak_direction"] or "-"
                days = info["streak_days"]
                net = info["latest_net"]
                cols[i].metric(
                    investor_type,
                    f"{direction} {days}일" if info["streak_direction"] else "변화 없음",
                    f"{net:,}원 (오늘)"
                )
        else:
            st.info("투자자 동향 데이터를 아직 가져오지 못했습니다. (KRX 확정 지연일 가능성이 높습니다 — NXT 거래 종료 후 다시 시도해보세요.)")

        st.divider()
        st.subheader("종합 인사이트 코멘트")
        summary, terms_used = generate_insight(regime, indicators, investor)
        st.write(f"**[{regime.description}]** {summary}")

        if terms_used:
            with st.expander("용어 설명 보기"):
                for term in terms_used:
                    if term in GLOSSARY:
                        st.markdown(f"**{term}**: {GLOSSARY[term]}")

        st.divider()
        st.subheader("원본 데이터 (최근 10일)")
        raw_df = fetch_ohlcv(ticker)
        st.dataframe(raw_df.tail(10))
