import json
import math
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st

try:
    from pykrx import stock
    PYKRX_AVAILABLE = True
except Exception:
    stock = None
    PYKRX_AVAILABLE = False

try:
    import yfinance as yf
    YF_AVAILABLE = True
except Exception:
    yf = None
    YF_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except Exception:
    BeautifulSoup = None
    BS4_AVAILABLE = False

try:
    from streamlit_plotly_events import plotly_events
    PLOTLY_CLICK_AVAILABLE = True
except Exception:
    PLOTLY_CLICK_AVAILABLE = False

st.set_page_config(page_title="투자 분석 시스템", layout="wide")

st.title("📈 투자 분석 시스템 Final")
st.caption("주요 지수·대표 종목·환율·원자재·포트폴리오를 한 화면에서 점검합니다. Final 버전은 Streamlit Cloud 배포 안정성을 위해 pykrx 부재 시 Yahoo 대체 경로를 자동 사용합니다.")

if not PYKRX_AVAILABLE:
    st.info("배포 환경에서 pykrx를 불러오지 못해 Yahoo 대체 데이터로 실행 중입니다. 국내 일부 수치와 시간차가 있을 수 있습니다.")

USER_AGENT = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}

야후인덱스심볼 = {
    "1001": "^KS11",
    "2001": "^KQ11",
}

야후주요지표심볼 = {
    "USD/KRW": "KRW=X",
    "국제 금": "GC=F",
    "WTI": "CL=F",
    "브렌트유": "BZ=F",
}

야후자산심볼 = {
    ("index", "1001"): "^KS11",
    ("index", "2001"): "^KQ11",
    ("stock", "005930"): "005930.KS",
    ("stock", "000660"): "000660.KS",
    ("etf", "069500"): "069500.KS",
    ("etf", "229200"): "229200.KQ",
}

지표대체우선순위 = {
    "USD/KRW": ["naver", "yahoo"],
    "국제 금": ["naver", "yahoo"],
    "WTI": ["naver", "yahoo"],
    "브렌트유": ["naver", "yahoo"],
    "국내 금": ["naver", "derived_domestic_gold"],
    "두바이유": ["naver", "derived_dubai"],
}

# -----------------------------------
# 기본 설정
# -----------------------------------
주요자산 = {
    "코스피": {"구분": "index", "코드": "1001"},
    "코스닥": {"구분": "index", "코드": "2001"},
    "KODEX 200": {"구분": "etf", "코드": "069500"},
    "KODEX 코스닥150": {"구분": "etf", "코드": "229200"},
    "삼성전자": {"구분": "stock", "코드": "005930"},
    "SK하이닉스": {"구분": "stock", "코드": "000660"},
}

관심종목 = {
    "069500": "KODEX 200",
    "229200": "KODEX 코스닥150",
    "005930": "삼성전자",
    "000660": "SK하이닉스",
}

기본포트폴리오 = pd.DataFrame([
    {"종목코드": "069500", "종목명": "KODEX 200", "보유수량": 94, "매입단가": 84026},
    {"종목코드": "229200", "종목명": "KODEX 코스닥150", "보유수량": 5, "매입단가": 19929},
    {"종목코드": "005930", "종목명": "삼성전자", "보유수량": 27, "매입단가": 187700},
    {"종목코드": "000660", "종목명": "SK하이닉스", "보유수량": 1, "매입단가": 941000},
])

시장지표네이버URL = {
    "USD/KRW": "https://finance.naver.com/marketindex/exchangeDetail.naver?marketindexCd=FX_USDKRW",
    "국제 금": "https://finance.naver.com/marketindex/worldGoldDetail.naver",
    "국내 금": "https://finance.naver.com/marketindex/goldDetail.naver",
    "WTI": "https://finance.naver.com/marketindex/worldDailyQuoteDetail.naver?marketindexCd=OIL_CL",
    "브렌트유": "https://finance.naver.com/marketindex/worldDailyQuoteDetail.naver?marketindexCd=OIL_BRT",
    "두바이유": "https://finance.naver.com/marketindex/worldDailyQuoteDetail.naver?marketindexCd=OIL_DUB",
}

목표비중저장파일 = "target_weights.json"


# -----------------------------------
# 표시용 함수
# -----------------------------------
def 금액표시(값):
    if pd.isna(값) or 값 is None:
        return "-"
    return f"{값:,.0f}원"


def 숫자표시(값, 소수점=0):
    if pd.isna(값) or 값 is None:
        return "-"
    if 소수점 == 0:
        return f"{값:,.0f}"
    return f"{값:,.{소수점}f}"


def 비율표시(값):
    if pd.isna(값) or 값 is None:
        return "-"
    return f"{값:.2f}%"


def 증감문자열(값, suffix=""):
    if pd.isna(값) or 값 is None:
        return "-"
    if 값 > 0:
        return f"+{값:,.2f}{suffix}"
    return f"{값:,.2f}{suffix}"


def 손익색상(값):
    if pd.isna(값):
        return ""
    if 값 > 0:
        return "color: red; font-weight: bold;"
    if 값 < 0:
        return "color: blue; font-weight: bold;"
    return ""


def 수익률색상(값):
    return 손익색상(값)


def 손익문자열(값):
    if pd.isna(값):
        return "-"
    if 값 > 0:
        return f"+{값:,.0f}"
    return f"{값:,.0f}"


def 수익률문자열(값):
    if pd.isna(값):
        return "-"
    if 값 > 0:
        return f"+{값:.2f}%"
    return f"{값:.2f}%"


def index_1부터(df):
    표시용 = df.copy()
    표시용.index = range(1, len(표시용) + 1)
    return 표시용


def 안전실수변환(값):
    if 값 is None:
        return None
    if isinstance(값, (int, float)):
        return float(값)
    문자열 = re.sub(r"[^0-9.\-]", "", str(값))
    if 문자열 in ["", ".", "-", "-."]:
        return None
    try:
        return float(문자열)
    except Exception:
        return None


def 시장지표표시문자열df(df):
    표시용 = df.copy()
    if 표시용.empty:
        return 표시용
    표시용["현재값"] = 표시용["현재값"].apply(lambda x: 숫자표시(x, 2))
    표시용["전일대비"] = 표시용["전일대비"].apply(lambda x: 증감문자열(x))
    표시용["등락률"] = 표시용["등락률"].apply(lambda x: 증감문자열(x, "%"))
    return 표시용


def 시장지표스타일적용(df):
    if df is None or df.empty:
        return df

    def 변화색상(v):
        실수값 = 안전실수변환(v)
        if 실수값 is None:
            return ""
        if 실수값 > 0:
            return "color: #ef4444; font-weight: 700;"
        if 실수값 < 0:
            return "color: #3b82f6; font-weight: 700;"
        return "color: #94a3b8; font-weight: 700;"

    styled = df.style.map(변화색상, subset=["전일대비", "등락률"])
    return styled


# -----------------------------------
# 목표비중 저장/불러오기
# -----------------------------------
def 목표비중불러오기():
    기본값 = {
        "069500": 40.0,
        "229200": 20.0,
        "005930": 20.0,
        "000660": 20.0,
    }

    if os.path.exists(목표비중저장파일):
        try:
            with open(목표비중저장파일, "r", encoding="utf-8") as f:
                저장값 = json.load(f)
            return {
                "069500": float(저장값.get("069500", 40.0)),
                "229200": float(저장값.get("229200", 20.0)),
                "005930": float(저장값.get("005930", 20.0)),
                "000660": float(저장값.get("000660", 20.0)),
            }
        except Exception:
            return 기본값

    return 기본값


def 목표비중저장(목표비중):
    with open(목표비중저장파일, "w", encoding="utf-8") as f:
        json.dump(목표비중, f, ensure_ascii=False, indent=2)


# -----------------------------------
# 데이터 조회 함수
# -----------------------------------
@st.cache_data(ttl=180)
def 네이버페이지가져오기(url):
    try:
        응답 = requests.get(url, headers=USER_AGENT, timeout=10)
        응답.raise_for_status()
        return 응답.text
    except Exception:
        return None


@st.cache_data(ttl=180)
def 야후현재가요약가져오기(심볼, 이름):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{심볼}"
        params = {"interval": "1d", "range": "7d", "includePrePost": "false", "events": "div,splits"}
        응답 = requests.get(url, params=params, headers=USER_AGENT, timeout=10)
        응답.raise_for_status()
        payload = 응답.json()
        결과목록 = payload.get("chart", {}).get("result", [])
        if not 결과목록:
            return {"지표": 이름, "현재값": None, "전일대비": None, "등락률": None, "출처": "Yahoo"}

        결과 = 결과목록[0]
        timestamps = 결과.get("timestamp", [])
        quotes = 결과.get("indicators", {}).get("quote", [{}])[0]
        종가목록 = quotes.get("close", [])
        if not timestamps or not 종가목록:
            return {"지표": 이름, "현재값": None, "전일대비": None, "등락률": None, "출처": "Yahoo"}

        df = pd.DataFrame({
            "날짜": pd.to_datetime(timestamps, unit="s"),
            "종가": 종가목록,
        }).dropna(subset=["종가"]).sort_values("날짜")

        if df.empty:
            return {"지표": 이름, "현재값": None, "전일대비": None, "등락률": None, "출처": "Yahoo"}

        현재값 = float(df.iloc[-1]["종가"])
        전일대비 = None
        등락률 = None
        if len(df) >= 2:
            전일값 = float(df.iloc[-2]["종가"])
            전일대비 = 현재값 - 전일값
            if 전일값 != 0:
                등락률 = (전일대비 / 전일값) * 100

        return {"지표": 이름, "현재값": 현재값, "전일대비": 전일대비, "등락률": 등락률, "출처": "Yahoo"}
    except Exception:
        return {"지표": 이름, "현재값": None, "전일대비": None, "등락률": None, "출처": "Yahoo"}


@st.cache_data(ttl=180)
def 파생주요지표가져오기(이름):
    빈결과 = {"지표": 이름, "현재값": None, "전일대비": None, "등락률": None, "출처": "Derived"}

    try:
        if 이름 == "국내 금":
            usdkrw = 야후현재가요약가져오기("KRW=X", "USD/KRW")
            intl_gold = 야후현재가요약가져오기("GC=F", "국제 금")

            if usdkrw.get("현재값") is None or intl_gold.get("현재값") is None:
                return 빈결과

            현재값 = float(intl_gold["현재값"]) * float(usdkrw["현재값"]) / 31.1035

            전일대비 = None
            등락률 = None
            if usdkrw.get("전일대비") is not None and intl_gold.get("전일대비") is not None:
                전일환율 = float(usdkrw["현재값"]) - float(usdkrw["전일대비"])
                전일국제금 = float(intl_gold["현재값"]) - float(intl_gold["전일대비"])
                if 전일환율 > 0 and 전일국제금 > 0:
                    전일값 = 전일국제금 * 전일환율 / 31.1035
                    전일대비 = 현재값 - 전일값
                    if 전일값 != 0:
                        등락률 = (전일대비 / 전일값) * 100

            return {"지표": 이름, "현재값": 현재값, "전일대비": 전일대비, "등락률": 등락률, "출처": "Derived(KRW×Gold)"}

        if 이름 == "두바이유":
            brent = 야후현재가요약가져오기("BZ=F", "브렌트유")
            wti = 야후현재가요약가져오기("CL=F", "WTI")

            기준값 = None
            기준전일대비 = None
            if brent.get("현재값") is not None:
                기준값 = float(brent["현재값"])
                기준전일대비 = brent.get("전일대비")
            elif wti.get("현재값") is not None:
                기준값 = float(wti["현재값"])
                기준전일대비 = wti.get("전일대비")

            if 기준값 is None:
                return 빈결과

            현재값 = 기준값
            전일대비 = float(기준전일대비) if 기준전일대비 is not None else None
            등락률 = (전일대비 / (현재값 - 전일대비) * 100) if 전일대비 is not None and (현재값 - 전일대비) != 0 else None

            return {"지표": 이름, "현재값": 현재값, "전일대비": 전일대비, "등락률": 등락률, "출처": "Derived(Brent/WTI proxy)"}
    except Exception:
        return 빈결과

    return 빈결과


def 시장지표단건가져오기(이름, url):
    우선순위 = 지표대체우선순위.get(이름, ["naver", "yahoo"])

    for 소스 in 우선순위:
        if 소스 == "naver":
            결과 = 네이버시장지표현재가가져오기(이름, url, fallback_to_yahoo=False)
        elif 소스 == "yahoo":
            심볼 = 야후주요지표심볼.get(이름)
            결과 = 야후현재가요약가져오기(심볼, 이름) if 심볼 else None
            if 결과:
                결과["링크"] = url
        elif 소스 in ["derived_domestic_gold", "derived_dubai"]:
            결과 = 파생주요지표가져오기(이름)
            결과["링크"] = url
        else:
            결과 = None

        if 결과 and 결과.get("현재값") is not None:
            return 결과

    return {"지표": 이름, "현재값": None, "전일대비": None, "등락률": None, "링크": url, "출처": "-"}


@st.cache_data(ttl=180)
def 네이버시장지표현재가가져오기(이름, url, fallback_to_yahoo=True):
    if BS4_AVAILABLE:
        html = 네이버페이지가져오기(url)
        if html:
            try:
                soup = BeautifulSoup(html, "html.parser")

                현재값 = None
                전일대비 = None
                등락률 = None

                candidates = soup.select("span.value") + soup.select("p.no_today span.blind") + soup.select("span.blind")
                for tag in candidates:
                    값 = 안전실수변환(tag.get_text(strip=True))
                    if 값 is not None:
                        현재값 = 값
                        break

                blind_texts = [x.get_text(" ", strip=True) for x in soup.select("span.blind")]
                for txt in blind_texts:
                    if 전일대비 is None and any(key in txt for key in ["전일대비", "상승", "하락"]):
                        값 = 안전실수변환(txt)
                        if 값 is not None:
                            전일대비 = 값
                    if 등락률 is None and "%" in txt:
                        값 = 안전실수변환(txt)
                        if 값 is not None:
                            등락률 = 값

                if 현재값 is not None:
                    return {"지표": 이름, "현재값": 현재값, "전일대비": 전일대비, "등락률": 등락률, "링크": url, "출처": "Naver"}
            except Exception:
                pass

    if fallback_to_yahoo:
        심볼 = 야후주요지표심볼.get(이름)
        if 심볼:
            결과 = 야후현재가요약가져오기(심볼, 이름)
            결과["링크"] = url
            return 결과

    return {"지표": 이름, "현재값": None, "전일대비": None, "등락률": None, "링크": url, "출처": "Naver"}


def _정렬정제_OHLCV(데이터):
    if 데이터 is None or len(데이터) == 0:
        return pd.DataFrame()
    데이터 = 데이터.copy()
    try:
        데이터 = 데이터.sort_index()
    except Exception:
        pass
    return 데이터


def _시장OHLCV조회(시작문자열, 종료문자열, 코드):
    if PYKRX_AVAILABLE:
        try:
            데이터 = stock.get_market_ohlcv_by_date(시작문자열, 종료문자열, 코드)
            if 데이터 is not None and not 데이터.empty:
                return _정렬정제_OHLCV(데이터)
        except Exception:
            pass
    return _야후자산OHLCV조회(시작문자열, 종료문자열, "stock", 코드)


def _ETF_OHLCV조회(시작문자열, 종료문자열, 코드):
    if PYKRX_AVAILABLE:
        try:
            데이터 = stock.get_etf_ohlcv_by_date(시작문자열, 종료문자열, 코드)
            if 데이터 is not None and not 데이터.empty:
                return _정렬정제_OHLCV(데이터)
        except Exception:
            pass
        시장데이터 = _시장OHLCV조회(시작문자열, 종료문자열, 코드)
        if 시장데이터 is not None and not 시장데이터.empty:
            return 시장데이터
    return _야후자산OHLCV조회(시작문자열, 종료문자열, "etf", 코드)


def _야후자산OHLCV조회(시작문자열, 종료문자열, 구분, 코드):
    심볼 = 야후자산심볼.get((str(구분), str(코드))) or 야후인덱스심볼.get(str(코드))
    if not 심볼:
        return pd.DataFrame()

    시작일 = datetime.strptime(시작문자열, "%Y%m%d")
    종료일 = datetime.strptime(종료문자열, "%Y%m%d") + timedelta(days=1)

    if YF_AVAILABLE:
        try:
            df = yf.download(
                tickers=심볼,
                start=시작일.strftime("%Y-%m-%d"),
                end=종료일.strftime("%Y-%m-%d"),
                progress=False,
                auto_adjust=False,
                actions=False,
                threads=False,
            )
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                rename_map = {
                    "Open": "시가",
                    "High": "고가",
                    "Low": "저가",
                    "Close": "종가",
                    "Volume": "거래량",
                }
                usable_cols = [c for c in rename_map if c in df.columns]
                df = df[usable_cols].rename(columns=rename_map)
                df.index = pd.to_datetime(df.index).tz_localize(None)
                return df.sort_index()
        except Exception:
            pass

    try:
        시작초 = int(시작일.timestamp())
        종료초 = int(종료일.timestamp())
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{심볼}"
        params = {
            "period1": 시작초,
            "period2": 종료초,
            "interval": "1d",
            "includePrePost": "false",
            "events": "div,splits",
        }
        응답 = requests.get(url, params=params, headers=USER_AGENT, timeout=10)
        응답.raise_for_status()
        payload = 응답.json()
        결과목록 = payload.get("chart", {}).get("result", [])
        if not 결과목록:
            return pd.DataFrame()
        결과 = 결과목록[0]
        timestamps = 결과.get("timestamp", [])
        quotes = 결과.get("indicators", {}).get("quote", [{}])[0]
        if not timestamps:
            return pd.DataFrame()
        df = pd.DataFrame({
            "날짜": pd.to_datetime(timestamps, unit="s"),
            "시가": quotes.get("open", []),
            "고가": quotes.get("high", []),
            "저가": quotes.get("low", []),
            "종가": quotes.get("close", []),
            "거래량": quotes.get("volume", []),
        }).dropna(subset=["종가"])
        if df.empty:
            return pd.DataFrame()
        df["날짜"] = pd.to_datetime(df["날짜"]).dt.tz_localize(None)
        df = df.set_index("날짜").sort_index()
        return df
    except Exception:
        return pd.DataFrame()


def _야후인덱스OHLCV조회(시작문자열, 종료문자열, 코드):
    심볼 = 야후인덱스심볼.get(str(코드))
    if not 심볼:
        return pd.DataFrame()

    try:
        시작초 = int(datetime.strptime(시작문자열, "%Y%m%d").timestamp())
        종료초 = int((datetime.strptime(종료문자열, "%Y%m%d") + timedelta(days=1)).timestamp())
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{심볼}"
        params = {
            "period1": 시작초,
            "period2": 종료초,
            "interval": "1d",
            "includePrePost": "false",
            "events": "div,splits",
        }
        응답 = requests.get(url, params=params, headers=USER_AGENT, timeout=10)
        응답.raise_for_status()
        payload = 응답.json()
        결과목록 = payload.get("chart", {}).get("result", [])
        if not 결과목록:
            return pd.DataFrame()

        결과 = 결과목록[0]
        timestamps = 결과.get("timestamp", [])
        quotes = 결과.get("indicators", {}).get("quote", [{}])[0]
        if not timestamps:
            return pd.DataFrame()

        df = pd.DataFrame({
            "날짜": pd.to_datetime(timestamps, unit="s"),
            "시가": quotes.get("open", []),
            "고가": quotes.get("high", []),
            "저가": quotes.get("low", []),
            "종가": quotes.get("close", []),
            "거래량": quotes.get("volume", []),
        }).dropna(subset=["종가"])

        if df.empty:
            return pd.DataFrame()

        df["날짜"] = pd.to_datetime(df["날짜"]).dt.tz_localize(None)
        df = df.set_index("날짜").sort_index()
        return df
    except Exception:
        return pd.DataFrame()


def _인덱스OHLCV조회(시작문자열, 종료문자열, 코드):
    if PYKRX_AVAILABLE:
        try:
            데이터 = stock.get_index_ohlcv(시작문자열, 종료문자열, 코드)
            if 데이터 is not None and not 데이터.empty:
                return _정렬정제_OHLCV(데이터)
        except Exception:
            pass
        try:
            데이터 = stock.get_index_ohlcv_by_date(시작문자열, 종료문자열, code=코드)
            if 데이터 is not None and not 데이터.empty:
                return _정렬정제_OHLCV(데이터)
        except Exception:
            pass
    return _야후자산OHLCV조회(시작문자열, 종료문자열, "index", 코드)


@st.cache_data(ttl=300)
def 최근OHLCV가져오기(구분, 코드, lookback_days=15):
    종료일 = datetime.today()
    시작일 = 종료일 - timedelta(days=lookback_days)
    시작문자열 = 시작일.strftime("%Y%m%d")
    종료문자열 = 종료일.strftime("%Y%m%d")

    if 구분 == "index":
        return _인덱스OHLCV조회(시작문자열, 종료문자열, 코드)
    if 구분 == "etf":
        return _ETF_OHLCV조회(시작문자열, 종료문자열, 코드)
    return _시장OHLCV조회(시작문자열, 종료문자열, 코드)


@st.cache_data(ttl=300)
def 자산현재가정보(자산명, 자산정보):
    구분 = 자산정보["구분"]
    코드 = 자산정보["코드"]
    데이터 = 최근OHLCV가져오기(구분, 코드, lookback_days=15)

    현재가 = None
    전일가 = None
    전일대비 = None
    등락률 = None

    if not 데이터.empty:
        현재가 = float(데이터.iloc[-1]["종가"])
        if len(데이터) >= 2:
            전일가 = float(데이터.iloc[-2]["종가"])
            전일대비 = 현재가 - 전일가
            if 전일가 not in [0, None]:
                등락률 = (전일대비 / 전일가) * 100

    return {
        "자산명": 자산명,
        "현재가": 현재가,
        "전일가": 전일가,
        "전일대비": 전일대비,
        "등락률": 등락률,
    }


@st.cache_data(ttl=300)
def 종목현재가가져오기(종목코드):
    데이터 = 최근OHLCV가져오기("stock", 종목코드, lookback_days=15)
    if 데이터.empty:
        return None
    return float(데이터.iloc[-1]["종가"])


@st.cache_data(ttl=300)
def ETF현재가가져오기(종목코드):
    데이터 = 최근OHLCV가져오기("etf", 종목코드, lookback_days=15)
    if 데이터.empty:
        return None
    return float(데이터.iloc[-1]["종가"])


@st.cache_data(ttl=300)
def 인덱스현재가가져오기(지수코드):
    데이터 = 최근OHLCV가져오기("index", 지수코드, lookback_days=15)
    if 데이터.empty:
        return None
    return float(데이터.iloc[-1]["종가"])


@st.cache_data(ttl=300)
def 자산과거가격가져오기(구분, 코드, 개월수=6):
    try:
        종료일 = datetime.today()
        시작일 = 종료일 - timedelta(days=30 * 개월수)
        시작문자열 = 시작일.strftime("%Y%m%d")
        종료문자열 = 종료일.strftime("%Y%m%d")

        if 구분 == "index":
            데이터 = _인덱스OHLCV조회(시작문자열, 종료문자열, 코드)
        elif 구분 == "etf":
            데이터 = _ETF_OHLCV조회(시작문자열, 종료문자열, 코드)
        else:
            데이터 = _시장OHLCV조회(시작문자열, 종료문자열, 코드)

        if 데이터 is None or 데이터.empty:
            return pd.DataFrame()

        데이터 = 데이터.copy()
        데이터.index = pd.to_datetime(데이터.index).tz_localize(None)
        데이터 = 데이터.sort_index()
        데이터 = 데이터[~데이터.index.duplicated(keep="last")]

        필수열 = ["시가", "고가", "저가", "종가"]
        for 열 in 필수열:
            if 열 not in 데이터.columns:
                return pd.DataFrame()
            데이터[열] = pd.to_numeric(데이터[열], errors="coerce")

        if "거래량" not in 데이터.columns:
            데이터["거래량"] = 0
        데이터["거래량"] = pd.to_numeric(데이터["거래량"], errors="coerce").fillna(0)
        데이터 = 데이터.dropna(subset=["종가"])

        if 데이터.empty:
            return pd.DataFrame()

        데이터["5일평균"] = 데이터["종가"].rolling(5, min_periods=1).mean()
        데이터["20일평균"] = 데이터["종가"].rolling(20, min_periods=1).mean()
        데이터["60일평균"] = 데이터["종가"].rolling(60, min_periods=1).mean()
        데이터["120일평균"] = 데이터["종가"].rolling(120, min_periods=1).mean()

        변화량 = 데이터["종가"].diff()
        상승분 = 변화량.clip(lower=0)
        하락분 = -변화량.clip(upper=0)
        평균상승 = 상승분.rolling(14, min_periods=14).mean()
        평균하락 = 하락분.rolling(14, min_periods=14).mean()
        rs = 평균상승 / 평균하락.replace(0, pd.NA)
        데이터["RSI(14)"] = 100 - (100 / (1 + rs))

        return 데이터
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=1800)
def 네이버시장지표목록가져오기():
    결과 = []
    표시순서 = ["USD/KRW", "국제 금", "WTI", "브렌트유", "국내 금", "두바이유"]
    for 이름 in 표시순서:
        url = 시장지표네이버URL.get(이름)
        if not url:
            continue
        결과.append(시장지표단건가져오기(이름, url))

    df = pd.DataFrame(결과)
    if df.empty:
        return pd.DataFrame([
            {"지표": "USD/KRW", "현재값": None, "전일대비": None, "등락률": None, "출처": "-"},
            {"지표": "국제 금", "현재값": None, "전일대비": None, "등락률": None, "출처": "-"},
            {"지표": "WTI", "현재값": None, "전일대비": None, "등락률": None, "출처": "-"},
        ])
    return df


def 일간수익률가져오기(종목코드, 개월수=6):
    구분 = "stock"
    if 종목코드 == "069500" or 종목코드 == "229200":
        구분 = "etf"
    데이터 = 자산과거가격가져오기(구분, 종목코드, 개월수)
    if 데이터.empty:
        return pd.Series(dtype=float)
    return 데이터["종가"].pct_change().dropna()


# -----------------------------------
# 계산 함수
# -----------------------------------
def 포트폴리오계산(원본포트폴리오):
    계산표 = 원본포트폴리오.copy()
    계산표["종목코드"] = 계산표["종목코드"].astype(str).str.zfill(6)
    계산표["보유수량"] = pd.to_numeric(계산표["보유수량"], errors="coerce").fillna(0).clip(lower=0)
    계산표["매입단가"] = pd.to_numeric(계산표["매입단가"], errors="coerce").fillna(0).clip(lower=0)

    def 현재가조회(code):
        if code in ["069500", "229200"]:
            return ETF현재가가져오기(code)
        return 종목현재가가져오기(code)

    계산표["현재가"] = 계산표["종목코드"].apply(현재가조회)
    계산표["평가금액"] = 계산표["현재가"] * 계산표["보유수량"]
    계산표["투자원금"] = 계산표["매입단가"] * 계산표["보유수량"]
    계산표["평가손익"] = 계산표["평가금액"] - 계산표["투자원금"]

    계산표["수익률"] = 계산표.apply(
        lambda 행: (행["평가손익"] / 행["투자원금"] * 100) if 행["투자원금"] not in [0, None] else 0,
        axis=1,
    )

    총평가금액 = 계산표["평가금액"].sum()
    계산표["현재비중"] = (계산표["평가금액"] / 총평가금액 * 100) if 총평가금액 != 0 else 0.0
    return 계산표


def 리밸런싱계산(계산표, 목표비중사전):
    결과표 = 계산표.copy()
    총평가금액 = 결과표["평가금액"].sum()

    결과표["목표비중"] = 결과표["종목코드"].map(목표비중사전).fillna(0.0)
    결과표["비중차이"] = 결과표["현재비중"] - 결과표["목표비중"]
    결과표["목표평가금액"] = 총평가금액 * 결과표["목표비중"] / 100
    결과표["리밸런싱금액"] = 결과표["목표평가금액"] - 결과표["평가금액"]

    결과표["정확계산수량"] = 결과표.apply(
        lambda 행: (행["리밸런싱금액"] / 행["현재가"])
        if pd.notna(행["현재가"]) and 행["현재가"] not in [0, None] else 0,
        axis=1,
    )
    결과표["주문참고수량"] = 결과표["정확계산수량"].round().astype(int)

    def 권장문구(행):
        수량 = int(행["주문참고수량"])
        금액 = 행["리밸런싱금액"]
        if 수량 > 0:
            return f"{abs(수량):,}주 추가 매수 검토"
        if 수량 < 0:
            return f"{abs(수량):,}주 비중 축소 검토"
        if pd.notna(행["현재가"]) and abs(금액) < 행["현재가"] * 0.5:
            return "거의 적정 비중"
        return "소액 조정 가능"

    결과표["권장방향"] = 결과표.apply(권장문구, axis=1)
    return 결과표, 총평가금액


def 추가투자금배분계산(계산표, 목표비중사전, 추가투자금):
    결과표 = 계산표.copy()

    if 추가투자금 <= 0:
        결과표["부족금액"] = 0.0
        결과표["추천배정금액"] = 0.0
        결과표["추천매수수량"] = 0
        결과표["실사용금액"] = 0.0
        결과표["추가매수의견"] = "추가 투자금 없음"
        return 결과표, 추가투자금, 추가투자금

    현재총평가금액 = 결과표["평가금액"].sum()
    목표총자산 = 현재총평가금액 + 추가투자금

    결과표["목표비중"] = 결과표["종목코드"].map(목표비중사전).fillna(0.0)
    결과표["추가투자후목표금액"] = 목표총자산 * 결과표["목표비중"] / 100
    결과표["부족금액"] = (결과표["추가투자후목표금액"] - 결과표["평가금액"]).clip(lower=0)

    부족금액합계 = 결과표["부족금액"].sum()
    if 부족금액합계 == 0:
        결과표["추천배정금액"] = 0.0
        결과표["추천매수수량"] = 0
        결과표["실사용금액"] = 0.0
        결과표["추가매수의견"] = "현재 비중이 목표 수준과 유사"
        return 결과표, 0.0, 추가투자금

    결과표["추천배정금액"] = 결과표["부족금액"] / 부족금액합계 * 추가투자금

    def 매수가능수량계산(행):
        if pd.isna(행["현재가"]) or 행["현재가"] in [0, None]:
            return 0
        return math.floor(행["추천배정금액"] / 행["현재가"])

    결과표["추천매수수량"] = 결과표.apply(매수가능수량계산, axis=1)
    결과표["실사용금액"] = 결과표["추천매수수량"] * 결과표["현재가"]

    총실사용금액 = 결과표["실사용금액"].sum()
    남는현금 = 추가투자금 - 총실사용금액

    while 남는현금 > 0:
        매수후보 = 결과표[(결과표["현재가"].notna()) & (결과표["현재가"] > 0)].copy()
        if 매수후보.empty:
            break

        매수후보["남은부족금액"] = (결과표["부족금액"] - 결과표["실사용금액"]).clip(lower=0)
        매수후보 = 매수후보.sort_values(["남은부족금액", "현재비중"], ascending=[False, True])

        추가매수실행 = False
        for idx in 매수후보.index:
            현재가 = 결과표.loc[idx, "현재가"]
            남은부족금액 = max(결과표.loc[idx, "부족금액"] - 결과표.loc[idx, "실사용금액"], 0)
            if 남는현금 >= 현재가 and 남은부족금액 >= 현재가 * 0.5:
                결과표.loc[idx, "추천매수수량"] += 1
                결과표.loc[idx, "실사용금액"] += 현재가
                남는현금 -= 현재가
                추가매수실행 = True
                break

        if not 추가매수실행:
            break

    총실사용금액 = 결과표["실사용금액"].sum()
    남는현금 = 추가투자금 - 총실사용금액

    def 추가매수의견생성(행):
        수량 = int(행["추천매수수량"])
        if 수량 > 0:
            return f"{수량:,}주 추가 매수 추천"
        if 행["추천배정금액"] > 0:
            return "배정금액은 있으나 1주 매수 금액 부족"
        return "추가 매수 우선순위 낮음"

    결과표["추가매수의견"] = 결과표.apply(추가매수의견생성, axis=1)
    return 결과표, 총실사용금액, 남는현금


def 포트폴리오위험도분석(계산포트폴리오, 목표비중사전, 개월수=6):
    분석표 = 계산포트폴리오.copy()
    총평가금액 = 분석표["평가금액"].sum()
    if 총평가금액 == 0:
        return {
            "변동성": 0.0,
            "최대낙폭": 0.0,
            "집중도": 0.0,
            "비중이탈도": 0.0,
            "위험수준": "계산 불가",
            "위험코멘트": "포트폴리오 평가금액이 없어 위험도 분석이 어렵습니다.",
        }

    일간수익률목록 = []
    가중치목록 = []
    for _, 행 in 분석표.iterrows():
        종목코드 = 행["종목코드"]
        현재비중 = 행["현재비중"] / 100
        수익률시리즈 = 일간수익률가져오기(종목코드, 개월수)
        if not 수익률시리즈.empty:
            일간수익률목록.append(수익률시리즈.rename(종목코드))
            가중치목록.append((종목코드, 현재비중))

    if not 일간수익률목록:
        return {
            "변동성": 0.0,
            "최대낙폭": 0.0,
            "집중도": float(분석표["현재비중"].max()),
            "비중이탈도": 0.0,
            "위험수준": "계산 제한",
            "위험코멘트": "충분한 과거 수익률 데이터가 없어 위험도 계산이 제한됩니다.",
        }

    수익률데이터 = pd.concat(일간수익률목록, axis=1).fillna(0)
    포트폴리오일간수익률 = pd.Series(0.0, index=수익률데이터.index, dtype=float)
    for 종목코드, 가중치 in 가중치목록:
        if 종목코드 in 수익률데이터.columns:
            포트폴리오일간수익률 += 수익률데이터[종목코드] * 가중치

    변동성 = float(포트폴리오일간수익률.std() * (252 ** 0.5) * 100)
    누적수익 = (1 + 포트폴리오일간수익률).cumprod()
    최고점 = 누적수익.cummax()
    낙폭 = (누적수익 / 최고점) - 1
    최대낙폭 = float(낙폭.min() * 100)
    집중도 = float(분석표["현재비중"].max())

    분석표["목표비중"] = 분석표["종목코드"].map(목표비중사전).fillna(0.0)
    분석표["비중절대차"] = (분석표["현재비중"] - 분석표["목표비중"]).abs()
    비중이탈도 = float(분석표["비중절대차"].sum())

    위험점수 = 0
    if 변동성 >= 35:
        위험점수 += 3
    elif 변동성 >= 20:
        위험점수 += 2
    elif 변동성 >= 10:
        위험점수 += 1

    if abs(최대낙폭) >= 25:
        위험점수 += 3
    elif abs(최대낙폭) >= 15:
        위험점수 += 2
    elif abs(최대낙폭) >= 8:
        위험점수 += 1

    if 집중도 >= 60:
        위험점수 += 3
    elif 집중도 >= 45:
        위험점수 += 2
    elif 집중도 >= 35:
        위험점수 += 1

    if 비중이탈도 >= 30:
        위험점수 += 3
    elif 비중이탈도 >= 15:
        위험점수 += 2
    elif 비중이탈도 >= 8:
        위험점수 += 1

    if 위험점수 >= 9:
        위험수준 = "높음"
        위험코멘트 = "포트폴리오 변동성과 쏠림이 큰 편이어서 보수적 점검이 필요합니다."
    elif 위험점수 >= 5:
        위험수준 = "보통"
        위험코멘트 = "포트폴리오 위험은 중간 수준이며, 비중 조정 여부를 점검할 필요가 있습니다."
    else:
        위험수준 = "낮음"
        위험코멘트 = "현재 구조는 비교적 안정적인 편입니다."

    return {
        "변동성": 변동성,
        "최대낙폭": 최대낙폭,
        "집중도": 집중도,
        "비중이탈도": 비중이탈도,
        "위험수준": 위험수준,
        "위험코멘트": 위험코멘트,
    }


def 오늘의요약생성(계산포트폴리오, 리밸런싱표, 추가배분표, 총수익률, 위험분석결과, 추가투자금):
    요약문 = []

    if 총수익률 > 5:
        요약문.append(f"포트폴리오 전체 수익률은 {총수익률:.2f}%로 매우 양호한 상태입니다.")
    elif 총수익률 > 0:
        요약문.append(f"포트폴리오 전체 수익률은 {총수익률:.2f}%로 안정적인 수익 구간입니다.")
    elif 총수익률 < 0:
        요약문.append(f"포트폴리오 전체 수익률은 {총수익률:.2f}%로 단기 손실 구간입니다.")
    else:
        요약문.append("포트폴리오 수익률은 현재 보합 수준입니다.")

    요약문.append(f"현재 포트폴리오 위험 수준은 '{위험분석결과['위험수준']}'으로 평가되며, {위험분석결과['위험코멘트']}")

    if not 계산포트폴리오.empty:
        최대비중종목 = 계산포트폴리오.sort_values("현재비중", ascending=False).iloc[0]
        최고수익종목 = 계산포트폴리오.sort_values("수익률", ascending=False).iloc[0]
        최저수익종목 = 계산포트폴리오.sort_values("수익률", ascending=True).iloc[0]
        요약문.append(f"현재 비중이 가장 큰 종목은 {최대비중종목['종목명']}이며 비중은 {최대비중종목['현재비중']:.2f}%입니다.")
        요약문.append(f"수익률이 가장 높은 종목은 {최고수익종목['종목명']}({최고수익종목['수익률']:.2f}%)입니다.")
        요약문.append(f"수익률이 가장 낮은 종목은 {최저수익종목['종목명']}({최저수익종목['수익률']:.2f}%)입니다.")

    과대 = 리밸런싱표[리밸런싱표["비중차이"] > 0]
    if not 과대.empty:
        종목 = 과대.sort_values("비중차이", ascending=False).iloc[0]
        요약문.append(f"{종목['종목명']} 비중이 목표보다 {종목['비중차이']:.2f}%p 높아 상대적으로 비중이 큰 상태입니다.")

    부족 = 리밸런싱표[리밸런싱표["비중차이"] < 0]
    if not 부족.empty:
        종목 = 부족.sort_values("비중차이").iloc[0]
        요약문.append(f"{종목['종목명']} 비중은 목표보다 {abs(종목['비중차이']):.2f}%p 낮아 보완 우선순위가 높습니다.")

    if 추가투자금 > 0:
        추천 = 추가배분표[추가배분표["추천배정금액"] > 0].sort_values("추천배정금액", ascending=False)
        if not 추천.empty:
            종목 = 추천.iloc[0]
            요약문.append(f"추가 투자금은 {종목['종목명']} 중심으로 배분하는 것이 목표 비중에 더 가까워지는 전략입니다.")

    return 요약문


# -----------------------------------
# 그래프/분석 함수
# -----------------------------------
def 가격그래프(데이터, 제목):
    x값 = pd.to_datetime(데이터.index)
    그림 = go.Figure()

    그림.add_trace(
        go.Scatter(
            x=x값, y=데이터["종가"], mode="lines", name="종가",
            line=dict(color="#7cc4ff", width=2.2),
            hovertemplate="종가: %{y:,.0f}<extra></extra>"
        )
    )

    이동평균설정 = [
        ("5일평균", "5일 평균", "#f59e0b"),
        ("20일평균", "20일 평균", "#3b82f6"),
        ("60일평균", "60일 평균", "#6b8f5a"),
        ("120일평균", "120일 평균", "#34d399"),
    ]
    for 컬럼, 이름, 색상 in 이동평균설정:
        if 컬럼 in 데이터.columns:
            그림.add_trace(
                go.Scatter(
                    x=x값, y=데이터[컬럼], mode="lines", name=이름,
                    line=dict(color=색상, width=2),
                    hovertemplate=f"{이름}: %{{y:,.0f}}<extra></extra>"
                )
            )

    그림.update_layout(
        title=제목,
        height=460,
        margin=dict(l=20, r=20, t=55, b=20),
        legend=dict(orientation="v", yanchor="top", y=0.98, xanchor="left", x=1.01),
        hovermode="x unified",
        xaxis_title="날짜",
        yaxis_title="가격",
        xaxis=dict(showgrid=True, gridcolor="rgba(148,163,184,0.15)"),
        yaxis=dict(showgrid=True, gridcolor="rgba(148,163,184,0.15)", tickformat=","),
    )
    return 그림


def 캔들차트그래프(데이터, 제목):
    x값 = pd.to_datetime(데이터.index)
    표시데이터 = 데이터.copy()
    그림 = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=[0.74, 0.26], specs=[[{"secondary_y": False}], [{"secondary_y": False}]]
    )

    그림.add_trace(go.Candlestick(
        x=x값,
        open=표시데이터["시가"],
        high=표시데이터["고가"],
        low=표시데이터["저가"],
        close=표시데이터["종가"],
        name="캔들",
        showlegend=False,
        increasing_line_color="#e05a63",
        decreasing_line_color="#4f86d9",
        increasing_fillcolor="#e05a63",
        decreasing_fillcolor="#4f86d9",
    ), row=1, col=1)

    그림.add_trace(
        go.Scatter(
            x=[x값[0]] if len(x값) > 0 else [None],
            y=[float(표시데이터["종가"].iloc[0])] if len(표시데이터) > 0 else [None],
            mode="markers",
            name="캔들",
            marker=dict(
                symbol="square",
                size=16,
                color="#e05a63",
                line=dict(color="#4f86d9", width=2)
            ),
            hoverinfo="skip"
        ),
        row=1, col=1
    )

    이동평균설정 = [
        ("5일평균", "5", "#f59e0b"),
        ("20일평균", "20", "#3b82f6"),
        ("60일평균", "60", "#6b8f5a"),
        ("120일평균", "120", "#34d399"),
    ]
    for 컬럼, 이름, 색상 in 이동평균설정:
        if 컬럼 in 표시데이터.columns:
            그림.add_trace(
                go.Scatter(
                    x=x값, y=표시데이터[컬럼], mode="lines", name=이름,
                    line=dict(color=색상, width=2), hovertemplate=f"{이름}선: %{{y:,.0f}}<extra></extra>"
                ),
                row=1, col=1
            )

    if "거래량" in 표시데이터.columns:
        거래량색 = ["#e05a63" if 종가 >= 시가 else "#4f86d9" for 종가, 시가 in zip(표시데이터["종가"], 표시데이터["시가"])]
        그림.add_trace(
            go.Bar(
                x=x값,
                y=표시데이터["거래량"],
                name="거래량",
                marker_color=거래량색,
                opacity=0.9,
                hovertemplate="거래량: %{y:,.0f}<extra></extra>"
            ),
            row=2, col=1
        )

    최고행 = 표시데이터["고가"].idxmax()
    최저행 = 표시데이터["저가"].idxmin()
    최고값 = float(표시데이터.loc[최고행, "고가"])
    최저값 = float(표시데이터.loc[최저행, "저가"])
    최저대비상승률 = ((최고값 - 최저값) / 최저값 * 100) if 최저값 else 0
    최고대비하락률 = ((표시데이터.iloc[-1]["종가"] - 최고값) / 최고값 * 100) if 최고값 else 0
    날짜포맷 = '%y.%m.%d' if len(표시데이터) <= 8 else ('%y.%m' if len(표시데이터) <= 18 else '%Y')

    그림.add_annotation(
        x=pd.to_datetime(최고행), y=최고값,
        text=f"↗ {최고값:,.0f}({pd.to_datetime(최고행).strftime(날짜포맷)}), {최고대비하락률:+.2f}%",
        showarrow=False, font=dict(color="#e05a63", size=13), xanchor="left", yanchor="bottom", row=1, col=1,
        bgcolor="rgba(255,255,255,0.85)"
    )
    그림.add_annotation(
        x=pd.to_datetime(최저행), y=최저값,
        text=f"↘ {최저값:,.0f}({pd.to_datetime(최저행).strftime(날짜포맷)}), {최저대비상승률:+.2f}%",
        showarrow=False, font=dict(color="#4f86d9", size=13), xanchor="left", yanchor="top", row=1, col=1,
        bgcolor="rgba(255,255,255,0.85)"
    )

    그림.update_layout(
        title=제목,
        height=660,
        margin=dict(l=30, r=130, t=90, b=56),
        xaxis_rangeslider_visible=False,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.10, xanchor="left", x=0.01,
            bgcolor="rgba(255,255,255,1)", bordercolor="#9ca3af", borderwidth=1.5,
            font=dict(color="#111827", size=16), itemsizing="constant"
        ),
        bargap=0.14,
        hovermode="x unified",
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        font=dict(color="#111827"),
        autosize=True,
    )
    그림.update_yaxes(
        side="right", tickformat=",", row=1, col=1, showgrid=True, gridcolor="#d1d5db", zeroline=False,
        tickfont=dict(color="#374151", size=13), title_font=dict(color="#111827"), automargin=True
    )
    그림.update_yaxes(
        side="right", tickformat="~s", row=2, col=1, showgrid=True, gridcolor="#e5e7eb", zeroline=False,
        tickfont=dict(color="#374151", size=13), title_font=dict(color="#111827"), automargin=True
    )
    그림.update_xaxes(
        showgrid=True, gridcolor="#e5e7eb", tickfont=dict(color="#374151", size=13),
        automargin=True, tickangle=0, showline=False, zeroline=False
    )
    return 그림


def 클릭캔들행가져오기(데이터, clicked_points):
    if 데이터.empty:
        return None, None

    기본날짜 = 데이터.index[-1]
    기본행 = 데이터.iloc[-1]

    if not clicked_points:
        return 기본날짜, 기본행

    try:
        클릭x = pd.to_datetime(clicked_points[0].get("x"))
        인덱스 = pd.to_datetime(pd.Index(데이터.index))
        위치 = (인덱스 - 클릭x).asi8
        절대차이 = pd.Series(위치, index=데이터.index).abs()
        선택날짜 = 절대차이.idxmin()
        return 선택날짜, 데이터.loc[선택날짜]
    except Exception:
        return 기본날짜, 기본행


def 캔들분석결과가져오기(데이터, 선택날짜, 선택행):
    if 선택행 is None or 데이터.empty:
        return {
            "캔들유형": "분석 불가",
            "방향": "중립",
            "설명": "캔들 데이터를 선택하지 못했습니다.",
            "상세": [],
            "체크표": pd.DataFrame(),
        }

    시가 = float(선택행["시가"])
    고가 = float(선택행["고가"])
    저가 = float(선택행["저가"])
    종가 = float(선택행["종가"])
    거래량 = float(선택행["거래량"]) if pd.notna(선택행.get("거래량")) else None

    전체범위 = max(고가 - 저가, 1e-9)
    몸통 = abs(종가 - 시가)
    윗꼬리 = max(고가 - max(시가, 종가), 0)
    아랫꼬리 = max(min(시가, 종가) - 저가, 0)

    몸통비율 = 몸통 / 전체범위 * 100
    윗꼬리비율 = 윗꼬리 / 전체범위 * 100
    아랫꼬리비율 = 아랫꼬리 / 전체범위 * 100

    if 몸통비율 <= 15:
        캔들유형 = "도지형"
    elif 종가 > 시가 and 아랫꼬리비율 >= 35 and 몸통비율 <= 45:
        캔들유형 = "망치형 가능성"
    elif 종가 < 시가 and 윗꼬리비율 >= 35 and 몸통비율 <= 45:
        캔들유형 = "슈팅스타 가능성"
    elif 종가 > 시가:
        캔들유형 = "양봉"
    else:
        캔들유형 = "음봉"

    방향 = "상승 우세" if 종가 > 시가 else "하락 우세" if 종가 < 시가 else "중립"

    보정선택날짜 = 인덱스기준가까운날짜찾기(데이터, 선택날짜)
    if 보정선택날짜 is None:
        보정선택날짜 = 데이터.index[-1]
    위치 = 데이터.index.get_loc(보정선택날짜)
    최근20 = 데이터.iloc[max(0, 위치 - 19): 위치 + 1]
    최근거래량20 = 최근20["거래량"].mean() if "거래량" in 최근20.columns and len(최근20) > 0 else None

    최근20고가 = 최근20["고가"].max() if len(최근20) > 0 else 고가
    최근20저가 = 최근20["저가"].min() if len(최근20) > 0 else 저가

    돌파판정 = "중립"
    if 종가 >= 최근20고가:
        돌파판정 = "20일 고점 돌파 시도"
    elif 종가 <= 최근20저가:
        돌파판정 = "20일 저점 이탈 경계"

    거래량판정 = "판정 제한"
    if 거래량 is not None and 최근거래량20 not in [None, 0] and pd.notna(최근거래량20):
        배수 = 거래량 / 최근거래량20
        if 배수 >= 1.5:
            거래량판정 = f"평균 대비 {배수:.2f}배 급증"
        elif 배수 >= 1.0:
            거래량판정 = f"평균 대비 {배수:.2f}배로 보통 이상"
        else:
            거래량판정 = f"평균 대비 {배수:.2f}배로 차분"

    if 캔들유형 == "망치형 가능성":
        설명 = "하단에서 매수 유입이 들어오며 종가를 끌어올린 형태로 해석할 수 있습니다. 다음 1~2일 안에 고점 돌파가 이어지는지 확인이 중요합니다."
    elif 캔들유형 == "슈팅스타 가능성":
        설명 = "상단에서 매도 압력이 강하게 나온 흔적으로 볼 수 있습니다. 다음 봉에서 저점 이탈이 나오면 단기 조정 신호가 강화됩니다."
    elif 캔들유형 == "도지형":
        설명 = "매수와 매도가 팽팽하게 맞선 날입니다. 추세 전환의 단서가 될 수 있으므로 다음 봉 방향 확인이 중요합니다."
    elif 캔들유형 == "양봉":
        설명 = "당일 종가가 시가보다 높아 매수 우위가 확인된 날입니다. 다만 윗꼬리 길이에 따라 상단 저항도 함께 점검해야 합니다."
    else:
        설명 = "당일 종가가 시가보다 낮아 매도 우위가 나타난 날입니다. 아랫꼬리가 길면 저가 매수 유입도 일부 있었다고 볼 수 있습니다."

    체크표 = pd.DataFrame([
        {"항목": "선택 날짜", "현재": str(pd.to_datetime(보정선택날짜 if "보정선택날짜" in locals() else 선택날짜).date()), "기준": "클릭한 캔들", "판정": "선택됨"},
        {"항목": "시가/종가", "현재": f"{시가:,.0f} / {종가:,.0f}", "기준": "종가 > 시가면 양봉", "판정": 방향},
        {"항목": "고가/저가", "현재": f"{고가:,.0f} / {저가:,.0f}", "기준": "당일 변동폭", "판정": f"{전체범위:,.0f}"},
        {"항목": "몸통 비율", "현재": f"{몸통비율:.1f}%", "기준": "15% 이하면 도지형", "판정": 캔들유형},
        {"항목": "윗꼬리/아랫꼬리", "현재": f"{윗꼬리비율:.1f}% / {아랫꼬리비율:.1f}%", "기준": "꼬리 길이 비교", "판정": 돌파판정},
        {"항목": "거래량", "현재": 숫자표시(거래량), "기준": "20일 평균 대비", "판정": 거래량판정},
    ])

    상세 = [
        f"선택한 캔들은 **{캔들유형}**으로 볼 수 있고, 당일 방향은 **{방향}**입니다.",
        f"몸통 비율은 {몸통비율:.1f}%이며 윗꼬리 {윗꼬리비율:.1f}%, 아랫꼬리 {아랫꼬리비율:.1f}%입니다.",
        f"가격 위치는 **{돌파판정}**으로 해석됩니다.",
        f"거래량은 **{거래량판정}**입니다.",
    ]

    return {
        "캔들유형": 캔들유형,
        "방향": 방향,
        "설명": 설명,
        "상세": 상세,
        "체크표": 체크표,
    }


def 비중그래프(계산표):
    그림 = go.Figure(go.Pie(labels=계산표["종목명"], values=계산표["평가금액"], hole=0.45))
    그림.update_layout(title="현재 포트폴리오 비중")
    return 그림


def 목표비중비교그래프(리밸런싱표):
    그림 = go.Figure()
    그림.add_trace(go.Bar(x=리밸런싱표["종목명"], y=리밸런싱표["현재비중"], name="현재 비중"))
    그림.add_trace(go.Bar(x=리밸런싱표["종목명"], y=리밸런싱표["목표비중"], name="목표 비중"))
    그림.update_layout(title="현재 비중 vs 목표 비중", xaxis_title="종목", yaxis_title="비중(%)", barmode="group", height=420)
    return 그림


def 지표라인그래프(df, 값열, 제목):
    그림 = go.Figure()
    그림.add_trace(go.Scatter(x=pd.to_datetime(df.index), y=df[값열], mode="lines", name=제목))
    그림.update_layout(title=제목, xaxis_title="날짜", yaxis_title=값열, height=320, margin=dict(l=10, r=10, t=50, b=10))
    return 그림


def 차트분석문구(자산명, 데이터):
    if 데이터.empty or len(데이터) < 20:
        return {
            "ChatGPT": "데이터가 충분하지 않아 차트 해석이 제한됩니다.",
            "Gemini": "최근 시계열이 짧아 추세·모멘텀 판단 신뢰도가 낮습니다.",
            "Perplexity": "추가 데이터 확보 후 이동평균과 RSI를 함께 확인하는 것이 좋습니다.",
        }

    최신 = 데이터.iloc[-1]
    종가 = 최신["종가"]
    ma20 = 최신.get("20일평균")
    ma60 = 최신.get("60일평균")
    rsi = 최신.get("RSI(14)")

    추세 = "중립"
    if pd.notna(ma20) and pd.notna(ma60):
        if 종가 > ma20 > ma60:
            추세 = "상승 추세"
        elif 종가 < ma20 < ma60:
            추세 = "하락 추세"

    rsi해석 = "중립권"
    if pd.notna(rsi):
        if rsi >= 70:
            rsi해석 = "과열권"
        elif rsi <= 30:
            rsi해석 = "침체권"

    변동률20일 = None
    if len(데이터) >= 21:
        기준가 = 데이터.iloc[-21]["종가"]
        if 기준가 not in [0, None]:
            변동률20일 = (종가 / 기준가 - 1) * 100

    변동문구 = f"최근 20거래일 수익률은 {변동률20일:.2f}% 수준입니다." if 변동률20일 is not None else "최근 20거래일 변화율 계산은 제한됩니다."

    return {
        "ChatGPT": f"{자산명}은 현재 {추세}로 해석됩니다. 종가와 20일·60일 이동평균선의 배열을 보면 방향성은 {'양호' if 추세 == '상승 추세' else '둔화 또는 중립'}합니다. RSI는 {rsi해석}이며, 단기 추격 매수보다 지지 확인 후 대응이 더 안정적입니다.",
        "Gemini": f"기술적으로 보면 {자산명}의 핵심 포인트는 이동평균선 정렬과 RSI입니다. 현재 판독은 {추세}, RSI는 {rsi해석}입니다. {변동문구} 추세 지속 여부는 20일선 이탈 여부를 함께 보시는 것이 좋습니다.",
        "Perplexity": f"요약하면 {자산명}은 {추세} 신호가 우세하지만, RSI 기준 {rsi해석} 구간 여부를 같이 보아야 합니다. 실전에서는 20일선 회복·이탈, 거래량 동반 여부, 최근 1개월 변동률을 함께 확인하는 접근이 유효합니다.",
    }


def 미니차트그래프(데이터, 제목):
    그림 = go.Figure()
    그림.add_trace(go.Scatter(x=pd.to_datetime(데이터.index), y=데이터["종가"], mode="lines", name=제목))
    그림.update_layout(height=180, margin=dict(l=10, r=10, t=25, b=10), showlegend=False)
    그림.update_xaxes(visible=False)
    그림.update_yaxes(visible=False)
    return 그림


def 신호판정계산(데이터):
    if 데이터.empty or len(데이터) < 20:
        return {
            "종합신호": "데이터 부족",
            "색상": "#6b7280",
            "추세점수": 0,
            "추세설명": "데이터 부족",
            "모멘텀": "판정 제한",
            "RSI판정": "판정 제한",
            "실행의견": "데이터를 더 확인하세요.",
            "체크표": pd.DataFrame([
                {"항목": "데이터 길이", "현재": len(데이터), "기준": "20거래일 이상", "판정": "부족"}
            ]),
        }

    최신 = 데이터.iloc[-1]
    종가 = float(최신["종가"])
    ma20 = 최신.get("20일평균")
    ma60 = 최신.get("60일평균")
    rsi = 최신.get("RSI(14)")
    최근20수익률 = None
    if len(데이터) >= 21 and 데이터.iloc[-21]["종가"] not in [0, None]:
        최근20수익률 = (종가 / float(데이터.iloc[-21]["종가"]) - 1) * 100

    score = 0
    if pd.notna(ma20) and 종가 > ma20:
        score += 1
    if pd.notna(ma60) and 종가 > ma60:
        score += 1
    if pd.notna(ma20) and pd.notna(ma60) and ma20 > ma60:
        score += 1

    if score >= 3:
        추세설명 = "상승 배열"
    elif score == 2:
        추세설명 = "완만한 상승"
    elif score == 1:
        추세설명 = "중립"
    else:
        추세설명 = "약세"

    if 최근20수익률 is None:
        모멘텀 = "판정 제한"
    elif 최근20수익률 >= 8:
        모멘텀 = "강함"
    elif 최근20수익률 >= 0:
        모멘텀 = "보통"
    else:
        모멘텀 = "약함"

    if pd.isna(rsi):
        rsi판정 = "판정 제한"
    elif rsi >= 70:
        rsi판정 = "과열"
    elif rsi <= 35:
        rsi판정 = "저점권 관심"
    else:
        rsi판정 = "중립"

    if score >= 3 and (pd.isna(rsi) or 40 <= rsi <= 68):
        종합신호, 색상, 실행의견 = "매수 관심", "#16a34a", "추세가 양호해 분할매수 관심 구간으로 볼 수 있습니다."
    elif score >= 2 and pd.notna(rsi) and rsi > 68:
        종합신호, 색상, 실행의견 = "보유", "#2563eb", "상승 흐름은 유지되지만 단기 과열 가능성이 있어 추격 매수는 신중한 편이 좋습니다."
    elif score <= 1 and pd.notna(rsi) and rsi <= 35:
        종합신호, 색상, 실행의견 = "관찰", "#f59e0b", "낙폭 이후 반등 후보 구간일 수 있어 지지 확인 후 접근이 좋습니다."
    else:
        종합신호, 색상, 실행의견 = "관망", "#6b7280", "추세와 모멘텀이 애매해 방향 확인이 우선입니다."

    체크표 = pd.DataFrame([
        {"항목": "종가 vs 20일선", "현재": f"{종가:,.0f} / {ma20:,.0f}" if pd.notna(ma20) else f"{종가:,.0f} / -", "기준": "종가 > 20일선", "판정": "양호" if pd.notna(ma20) and 종가 > ma20 else "보통"},
        {"항목": "종가 vs 60일선", "현재": f"{종가:,.0f} / {ma60:,.0f}" if pd.notna(ma60) else f"{종가:,.0f} / -", "기준": "종가 > 60일선", "판정": "양호" if pd.notna(ma60) and 종가 > ma60 else "보통"},
        {"항목": "20일선 vs 60일선", "현재": f"{ma20:,.0f} / {ma60:,.0f}" if pd.notna(ma20) and pd.notna(ma60) else "-", "기준": "20일선 > 60일선", "판정": "양호" if pd.notna(ma20) and pd.notna(ma60) and ma20 > ma60 else "보통"},
        {"항목": "RSI(14)", "현재": f"{rsi:.2f}" if pd.notna(rsi) else "-", "기준": "40~70 중립권", "판정": rsi판정},
        {"항목": "최근 20거래일 수익률", "현재": f"{최근20수익률:.2f}%" if 최근20수익률 is not None else "-", "기준": "> 0%", "판정": 모멘텀},
    ])

    return {
        "종합신호": 종합신호,
        "색상": 색상,
        "추세점수": score,
        "추세설명": 추세설명,
        "모멘텀": 모멘텀,
        "RSI판정": rsi판정,
        "실행의견": 실행의견,
        "체크표": 체크표,
    }


def 대시보드스타일적용():
    st.markdown("""
    <style>
    .market-card {
        border: 1px solid #e5e7eb;
        border-radius: 18px;
        padding: 16px 18px;
        background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
        box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);
        margin-bottom: 8px;
    }
    .market-title {font-size: 0.95rem; font-weight: 700; margin-bottom: 6px; color: #111827;}
    .market-price {font-size: 1.45rem; font-weight: 800; color: #111827;}
    .market-delta-up {font-size: 0.95rem; font-weight: 700; color: #dc2626;}
    .market-delta-down {font-size: 0.95rem; font-weight: 700; color: #2563eb;}
    .market-delta-flat {font-size: 0.95rem; font-weight: 700; color: #6b7280;}
    .signal-box {
        border-radius: 18px;
        padding: 14px 16px;
        color: white;
        box-shadow: 0 6px 16px rgba(15, 23, 42, 0.12);
        margin-bottom: 10px;
    }
    .signal-title {font-size: 0.9rem; opacity: 0.9;}
    .signal-main {font-size: 1.35rem; font-weight: 800; margin-top: 4px;}
    div[role="radiogroup"] label {cursor: pointer !important;}
    div[role="radiogroup"] p {font-weight: 600;}
    div[data-baseweb="select"] * {cursor: pointer !important;}
    button[role="tab"] {cursor: pointer !important;}
    .stTabs [data-baseweb="tab"] {cursor: pointer !important;}
    </style>
    """, unsafe_allow_html=True)


def 카드HTML(이름, 현재가, 전일대비, 등락률):
    delta_class = "market-delta-flat"
    delta_text = "데이터 확인 실패"
    if 등락률 is not None and not pd.isna(등락률):
        if 등락률 > 0:
            delta_class = "market-delta-up"
        elif 등락률 < 0:
            delta_class = "market-delta-down"
        if 전일대비 is not None and not pd.isna(전일대비):
            delta_text = f"{전일대비:+,.2f} ({등락률:+.2f}%)"
        else:
            delta_text = f"{등락률:+.2f}%"
    price_text = 숫자표시(현재가, 2)
    return f"""
    <div class=\"market-card\">
        <div class=\"market-title\">{이름}</div>
        <div class=\"market-price\">{price_text}</div>
        <div class=\"{delta_class}\">{delta_text}</div>
    </div>
    """




def 세션선택초기화():
    if "main_asset_choice_v44" not in st.session_state or st.session_state["main_asset_choice_v44"] not in 주요자산:
        st.session_state["main_asset_choice_v44"] = list(주요자산.keys())[0]
    if "holding_asset_choice_v44" not in st.session_state or st.session_state["holding_asset_choice_v44"] not in 관심종목.values():
        st.session_state["holding_asset_choice_v44"] = list(관심종목.values())[0]


def 선택위젯키정리():
    # 이전 버전 위젯 상태가 남아 있으면 선택 표시와 실제 값이 어긋날 수 있어 정리합니다.
    for 이전키 in ["main_asset_selector_v42", "holding_selector_v42"]:
        if 이전키 in st.session_state:
            del st.session_state[이전키]




def 인덱스기준가까운날짜찾기(데이터, 입력날짜):
    if 데이터 is None or 데이터.empty or 입력날짜 is None:
        return None
    try:
        인덱스 = pd.to_datetime(pd.Index(데이터.index))
        목표 = pd.to_datetime(입력날짜)

        # 날짜만 선택된 경우가 많으므로 먼저 날짜 기준으로 정확히 맞는 값을 찾습니다.
        인덱스정규화 = 인덱스.normalize()
        목표정규화 = 목표.normalize()
        일치위치 = np.where(인덱스정규화 == 목표정규화)[0]
        if len(일치위치) > 0:
            return 데이터.index[일치위치[-1]]

        # 정확히 일치하는 값이 없으면 가장 가까운 시점으로 보정합니다.
        차이 = np.abs((인덱스 - 목표).asi8)
        if len(차이) == 0:
            return None
        return 데이터.index[int(np.argmin(차이))]
    except Exception:
        return 데이터.index[-1] if len(데이터.index) > 0 else None

def 날짜선택옵션(데이터, 기본개수=20):
    if 데이터 is None or 데이터.empty:
        return []
    최근 = list(pd.to_datetime(데이터.index).date.astype(str))
    최근 = 최근[-기본개수:]
    최근.reverse()
    return 최근


def 캔들표시구간제한(데이터, 구간):
    if 데이터 is None or 데이터.empty:
        return pd.DataFrame()
    개수맵 = {"일": 5, "주": 5, "월": 5, "년": 5}
    표시개수 = 개수맵.get(구간, 5)
    제한데이터 = 데이터.tail(표시개수).copy()
    return 제한데이터


def 기간별OHLCV변환(데이터, 구간):
    if 데이터 is None or 데이터.empty:
        return pd.DataFrame()
    if 구간 == "일":
        return 데이터.copy()

    규칙 = {"주": "W-FRI", "월": "ME", "년": "YE"}.get(구간)
    if 규칙 is None:
        return 데이터.copy()

    변환 = 데이터.copy()
    변환.index = pd.to_datetime(변환.index)
    집계 = pd.DataFrame({
        "시가": 변환["시가"].resample(규칙).first(),
        "고가": 변환["고가"].resample(규칙).max(),
        "저가": 변환["저가"].resample(규칙).min(),
        "종가": 변환["종가"].resample(규칙).last(),
        "거래량": 변환["거래량"].resample(규칙).sum(),
    }).dropna(subset=["시가", "고가", "저가", "종가"])

    if 집계.empty:
        return pd.DataFrame()

    집계["5일평균"] = 집계["종가"].rolling(5, min_periods=1).mean()
    집계["20일평균"] = 집계["종가"].rolling(20, min_periods=1).mean()
    집계["60일평균"] = 집계["종가"].rolling(60, min_periods=1).mean()
    집계["120일평균"] = 집계["종가"].rolling(120, min_periods=1).mean()

    변화량 = 집계["종가"].diff()
    상승분 = 변화량.clip(lower=0)
    하락분 = -변화량.clip(upper=0)
    평균상승 = 상승분.rolling(14, min_periods=14).mean()
    평균하락 = 하락분.rolling(14, min_periods=14).mean()
    rs = 평균상승 / 평균하락.replace(0, pd.NA)
    집계["RSI(14)"] = 100 - (100 / (1 + rs))
    return 집계


def 지표변화HTML(지표명, 현재값, 전일대비):
    현재값문자 = 숫자표시(현재값, 2)
    if 전일대비 is None or pd.isna(전일대비):
        델타문자 = "-"
        델타색 = "#94a3b8"
        화살표 = ""
    elif 전일대비 > 0:
        델타문자 = 증감문자열(전일대비)
        델타색 = "#ef4444"
        화살표 = "▲ "
    elif 전일대비 < 0:
        델타문자 = 증감문자열(전일대비)
        델타색 = "#3b82f6"
        화살표 = "▼ "
    else:
        델타문자 = 증감문자열(전일대비)
        델타색 = "#94a3b8"
        화살표 = "■ "

    return f"""
    <div style="background:#020817; border:1px solid #1f2937; border-radius:18px; padding:18px 18px 14px 18px; min-height:140px;">
        <div style="font-size:0.95rem; color:#ffffff; font-weight:700; margin-bottom:8px;">{지표명}</div>
        <div style="font-size:2.1rem; color:#f8fafc; font-weight:800; line-height:1.2; margin-bottom:12px;">{현재값문자}</div>
        <div style="display:inline-block; background:rgba(15,23,42,0.65); border:1px solid {델타색}; color:{델타색}; padding:6px 12px; border-radius:999px; font-size:1rem; font-weight:700;">{화살표}{델타문자}</div>
    </div>
    """


def 캔들유형HTML(캔들유형):
    유형 = str(캔들유형)

    if "망치형" in 유형:
        색상 = "#ef4444"
        몸통배경 = "rgba(239,68,68,0.18)"
        라벨 = "망치형"
        아이콘 = "🔨"
        top_pos = "9px"
        height = "14px"
        wick_top = "2px"
        wick_height = "40px"
    elif "슈팅스타" in 유형:
        색상 = "#3b82f6"
        몸통배경 = "rgba(59,130,246,0.18)"
        라벨 = "슈팅스타"
        아이콘 = "🌠"
        top_pos = "6px"
        height = "12px"
        wick_top = "2px"
        wick_height = "40px"
    elif "도지" in 유형:
        색상 = "#f59e0b"
        몸통배경 = "rgba(245,158,11,0.10)"
        라벨 = "도지형"
        아이콘 = "✚"
        top_pos = "20px"
        height = "4px"
        wick_top = "2px"
        wick_height = "40px"
    elif "양봉" in 유형:
        색상 = "#ef4444"
        몸통배경 = "rgba(239,68,68,0.18)"
        라벨 = "양봉"
        아이콘 = "🟥"
        top_pos = "10px"
        height = "20px"
        wick_top = "2px"
        wick_height = "40px"
    else:
        색상 = "#3b82f6"
        몸통배경 = "rgba(59,130,246,0.18)"
        라벨 = "음봉"
        아이콘 = "🟦"
        top_pos = "14px"
        height = "16px"
        wick_top = "2px"
        wick_height = "40px"

    return f"""
    <div style="display:flex; align-items:center; gap:10px; padding:10px 12px; border:1px solid #334155; border-radius:14px; background:#0f172a; width:fit-content;">
        <div style="font-size:1.15rem;">{아이콘}</div>
        <div style="position:relative; width:20px; height:44px;">
            <div style="position:absolute; left:9px; top:{wick_top}; width:2px; height:{wick_height}; background:{색상};"></div>
            <div style="position:absolute; left:4px; top:{top_pos}; width:12px; height:{height}; background:{몸통배경}; border:2px solid {색상}; border-radius:2px;"></div>
        </div>
        <div style="font-size:1.05rem; font-weight:800; color:{색상};">{라벨}</div>
    </div>
    """



선택위젯키정리()
세션선택초기화()

# -----------------------------------
# 상단: 주요 지수/대표 종목 모니터
# -----------------------------------
st.markdown("---")
대시보드스타일적용()
st.subheader("주요 지수 및 대표 종목 모니터")
st.caption("핵심 자산을 카드형 대시보드와 미니 차트로 빠르게 점검합니다.")
st.caption("주요 지수와 대표 종목의 현재가·전일대비·등락률은 최근 거래일 종가 기준으로 계산하며, pykrx 실패 시 Yahoo 대체 데이터를 사용합니다.")

if st.button("시세 새로고침"):
    st.cache_data.clear()
    st.rerun()

요약데이터 = []
for 자산명, 자산정보 in 주요자산.items():
    정보 = 자산현재가정보(자산명, 자산정보)
    과거 = 자산과거가격가져오기(자산정보["구분"], 자산정보["코드"], 개월수=6)
    정보["차트데이터"] = 과거 if not 과거.empty else pd.DataFrame()
    요약데이터.append(정보)

for row_start in range(0, len(요약데이터), 3):
    cols = st.columns(3)
    for col, item in zip(cols, 요약데이터[row_start:row_start + 3]):
        with col:
            st.markdown(카드HTML(item["자산명"], item.get("현재가"), item.get("전일대비"), item.get("등락률")), unsafe_allow_html=True)
            if not item["차트데이터"].empty:
                st.plotly_chart(미니차트그래프(item["차트데이터"], item["자산명"]), width="stretch")

st.markdown("### 주요 지수/종목 차트")
차트_선택자산 = st.selectbox(
    "차트 분석 자산 선택",
    list(주요자산.keys()),
    key="main_asset_choice_v44",
)
선택정보 = 주요자산[차트_선택자산]
선택데이터 = 자산과거가격가져오기(선택정보["구분"], 선택정보["코드"], 개월수=6)
캔들원본데이터 = 자산과거가격가져오기(선택정보["구분"], 선택정보["코드"], 개월수=48)
if 캔들원본데이터.empty:
    캔들원본데이터 = 선택데이터.copy()
st.caption(f"현재 선택 자산: {차트_선택자산} · 구분: {선택정보['구분']} · 코드: {선택정보['코드']}")

if 선택데이터.empty:
    st.warning("선택 자산의 차트 데이터를 불러오지 못했습니다.")
else:
    st.caption(f"최근 기준일: {pd.to_datetime(선택데이터.index[-1]).date()} · 데이터 {len(선택데이터):,}건")
    신호결과 = 신호판정계산(선택데이터)

    st.markdown("#### 차트 분석")
    신호칸1, 신호칸2, 신호칸3, 신호칸4 = st.columns(4)
    with 신호칸1:
        st.markdown(f'<div class="signal-box" style="background:{신호결과["색상"]};"><div class="signal-title">종합 신호</div><div class="signal-main">{신호결과["종합신호"]}</div></div>', unsafe_allow_html=True)
    with 신호칸2:
        st.markdown(f'<div class="signal-box" style="background:#334155;"><div class="signal-title">추세 점수</div><div class="signal-main">{신호결과["추세점수"]} / 3</div></div>', unsafe_allow_html=True)
    with 신호칸3:
        st.markdown(f'<div class="signal-box" style="background:#0f766e;"><div class="signal-title">추세 해석</div><div class="signal-main">{신호결과["추세설명"]}</div></div>', unsafe_allow_html=True)
    with 신호칸4:
        st.markdown(f'<div class="signal-box" style="background:#7c3aed;"><div class="signal-title">RSI/모멘텀</div><div class="signal-main">{신호결과["RSI판정"]} · {신호결과["모멘텀"]}</div></div>', unsafe_allow_html=True)

    st.info(신호결과["실행의견"])

    차트탭1, 차트탭2, 차트탭3 = st.tabs(["라인 차트", "캔들 차트", "해석 비교"])

    with 차트탭1:
        st.caption("라인 차트는 6개월 종가 흐름과 5일·20일·60일·120일 이동평균선을 함께 보여줍니다.")
        st.plotly_chart(가격그래프(선택데이터, f"{차트_선택자산} 6개월 라인 차트"), width="stretch", config={"displaylogo": False, "responsive": True})
        st.markdown("##### 신호 체크표")
        st.dataframe(index_1부터(신호결과["체크표"]), width="stretch")

    with 차트탭2:
        st.caption("주식 앱처럼 일·주·월·년 탭으로 전환하며, 최근 5개 구간의 캔들과 거래량을 한 화면에 선명하게 보이도록 조정했습니다.")
        기간탭들 = st.tabs(["일", "주", "월", "년"])
        for 기간라벨, 기간탭 in zip(["일", "주", "월", "년"], 기간탭들):
            with 기간탭:
                기간데이터전체 = 기간별OHLCV변환(캔들원본데이터, 기간라벨)
                기간데이터 = 캔들표시구간제한(기간데이터전체, 기간라벨)
                if 기간데이터.empty:
                    st.warning(f"{기간라벨} 단위 캔들 데이터를 만들지 못했습니다.")
                    continue

                설명맵 = {"일": "최근 5거래일", "주": "최근 5주", "월": "최근 5개월", "년": "최근 5년"}
                st.caption(f"{설명맵.get(기간라벨, '')} 기준으로 주식 앱 스타일 캔들과 거래량을 표시합니다.")
                캔들그림 = 캔들차트그래프(기간데이터, f"{차트_선택자산} {설명맵.get(기간라벨, 기간라벨)} 캔들 차트")
                st.plotly_chart(캔들그림, width="stretch", config={"displaylogo": False, "responsive": True})

                날짜옵션 = 날짜선택옵션(기간데이터, 기본개수=len(기간데이터))
                기본날짜문자열 = str(pd.to_datetime(기간데이터.index[-1]).date()) if not 기간데이터.empty else None
                선택날짜문자열 = st.selectbox(
                    f"분석할 {기간라벨} 캔들 날짜 선택",
                    날짜옵션,
                    index=0 if 날짜옵션 else None,
                    key=f"candle_date_{차트_선택자산}_{기간라벨}",
                ) if 날짜옵션 else None

                if 선택날짜문자열:
                    입력선택날짜 = pd.to_datetime(선택날짜문자열)
                else:
                    입력선택날짜 = pd.to_datetime(기본날짜문자열) if 기본날짜문자열 else None

                보정선택날짜 = 인덱스기준가까운날짜찾기(기간데이터, 입력선택날짜)
                선택날짜 = pd.to_datetime(보정선택날짜) if 보정선택날짜 is not None else None
                선택행 = 기간데이터.loc[보정선택날짜] if 보정선택날짜 is not None else (기간데이터.iloc[-1] if not 기간데이터.empty else None)

                캔들분석 = 캔들분석결과가져오기(기간데이터, 선택날짜, 선택행)

                분석칸1, 분석칸2, 분석칸3 = st.columns([1.2, 1, 1])
                with 분석칸1:
                    st.metric("선택 캔들 날짜", str(pd.to_datetime(보정선택날짜 if "보정선택날짜" in locals() else 선택날짜).date()) if 선택날짜 is not None else "-")
                with 분석칸2:
                    st.markdown("**캔들 유형**")
                    st.markdown(캔들유형HTML(캔들분석["캔들유형"]), unsafe_allow_html=True)
                with 분석칸3:
                    st.metric("당일 방향", 캔들분석["방향"])

                st.success(캔들분석["설명"])
                for 문장 in 캔들분석["상세"]:
                    st.write(f"- {문장}")

                st.markdown(f"##### {기간라벨} 단위 캔들 체크표")
                st.dataframe(index_1부터(캔들분석["체크표"]), width="stretch")

    with 차트탭3:
        분석문구 = 차트분석문구(차트_선택자산, 선택데이터)
        분석탭 = st.tabs(["ChatGPT 스타일", "Gemini 스타일", "Perplexity 스타일"])
        with 분석탭[0]:
            st.write(분석문구["ChatGPT"])
        with 분석탭[1]:
            st.write(분석문구["Gemini"])
        with 분석탭[2]:
            st.write(분석문구["Perplexity"])

# -----------------------------------
# 네이버 경제 기반 시장지표
# -----------------------------------
st.markdown("---")
st.subheader("주요 지표")
st.caption("USD/KRW, 금, 원유 관련 주요 지표를 표 형태로 확인합니다.")

시장지표df = 네이버시장지표목록가져오기()

if 시장지표df.empty:
    st.warning("시장지표 데이터를 불러오지 못했습니다.")
else:
    지표카드cols = st.columns(min(4, len(시장지표df)))
    for col, (_, row) in zip(지표카드cols, 시장지표df.head(4).iterrows()):
        with col:
            st.markdown(
                지표변화HTML(row["지표"], row.get("현재값"), row.get("전일대비")),
                unsafe_allow_html=True,
            )

    표시용시장지표원본 = 시장지표df.copy()[["지표", "현재값", "전일대비", "등락률", "출처"]]
    표시용시장지표 = 시장지표표시문자열df(표시용시장지표원본.copy())

    st.dataframe(
        시장지표스타일적용(index_1부터(표시용시장지표)),
        width="stretch",
    )

    if not BS4_AVAILABLE:
        st.info("bs4가 없어도 Yahoo 기반 대체값으로 주요 지표를 표시합니다. 네이버 구조가 바뀌면 Yahoo 값이 우선 사용됩니다.")



# -----------------------------------
# 보유 종목 개별 분석
# -----------------------------------
st.markdown("---")
st.subheader("보유 종목 개별 분석")

try:
    선택종목명 = st.selectbox(
        "분석할 보유 종목 선택",
        list(관심종목.values()),
        key="holding_asset_choice_v44",
    )
    선택종목코드 = [코드 for 코드, 이름 in 관심종목.items() if 이름 == 선택종목명][0]
    선택종목구분 = "etf" if 선택종목코드 in ["069500", "229200"] else "stock"
    가격데이터 = 자산과거가격가져오기(선택종목구분, 선택종목코드, 개월수=6)

    st.caption(f"현재 선택 종목: {선택종목명} · 구분: {선택종목구분} · 코드: {선택종목코드}")

    if 가격데이터.empty:
        st.warning("가격 데이터를 불러오지 못했습니다.")
    else:
        최신값 = 가격데이터.iloc[-1]
        이전값 = 가격데이터.iloc[-2] if len(가격데이터) >= 2 else 최신값
        가격변화 = 최신값["종가"] - 이전값["종가"]
        등락률 = (가격변화 / 이전값["종가"] * 100) if 이전값["종가"] != 0 else 0

        보유분석탭1, 보유분석탭2 = st.tabs(["개요", "기술적 분석"])

        with 보유분석탭1:
            칸1, 칸2, 칸3, 칸4 = st.columns(4)
            칸1.metric("현재가", 금액표시(최신값["종가"]), f"{가격변화:,.0f}원")
            칸2.metric("등락률", 비율표시(등락률))
            칸3.metric("거래량", 숫자표시(최신값["거래량"]))
            칸4.metric("RSI(14)", f"{최신값['RSI(14)']:.2f}" if pd.notna(최신값["RSI(14)"]) else "-")
            st.plotly_chart(가격그래프(가격데이터, f"{선택종목명} 주가 추이"), width="stretch", config={"displaylogo": False, "responsive": True})

        with 보유분석탭2:
            기술차트탭1, 기술차트탭2 = st.tabs(["캔들 차트", "체크표"])
            with 기술차트탭1:
                보유기간탭들 = st.tabs(["일", "주", "월", "년"])
                for 기간라벨, 기간탭 in zip(["일", "주", "월", "년"], 보유기간탭들):
                    with 기간탭:
                        기간데이터 = 기간별OHLCV변환(가격데이터, 기간라벨)
                        if 기간데이터.empty:
                            st.warning(f"{기간라벨} 단위 캔들 데이터를 만들지 못했습니다.")
                            continue
                        표시기간데이터 = 캔들표시구간제한(기간데이터, 기간라벨)
                        st.caption(f"최근 {5}{'거래일' if 기간라벨=='일' else '주' if 기간라벨=='주' else '개월' if 기간라벨=='월' else '년'} 기준으로 표시합니다.")
                        st.plotly_chart(캔들차트그래프(표시기간데이터, f"{선택종목명} {기간라벨} 단위 기술적 분석"), width="stretch", config={"displaylogo": False, "responsive": True})
            with 기술차트탭2:
                기술체크 = pd.DataFrame([
                    {"항목": "최근 기준일", "값": str(pd.to_datetime(가격데이터.index[-1]).date())},
                    {"항목": "20일 이동평균", "값": 숫자표시(최신값.get("20일평균"), 2)},
                    {"항목": "60일 이동평균", "값": 숫자표시(최신값.get("60일평균"), 2)},
                    {"항목": "RSI(14)", "값": 숫자표시(최신값.get("RSI(14)"), 2)},
                    {"항목": "최근 거래량", "값": 숫자표시(최신값.get("거래량"))},
                ])
                st.dataframe(index_1부터(기술체크), width="stretch")
except Exception as e:
    st.error(f"보유 종목 개별 분석 영역 오류: {e}")


# -----------------------------------
# 포트폴리오 입력/수정
# -----------------------------------
st.markdown("---")
st.subheader("포트폴리오 입력 정보")
st.write("종목코드와 종목명은 고정하고, 보유수량과 매입단가만 수정할 수 있습니다.")

수정포트폴리오 = st.data_editor(
    index_1부터(기본포트폴리오),
    num_rows="fixed",
    width="stretch",
    disabled=["종목코드", "종목명"],
    column_config={
        "종목코드": st.column_config.TextColumn("종목코드"),
        "종목명": st.column_config.TextColumn("종목명"),
        "보유수량": st.column_config.NumberColumn("보유수량", min_value=0, step=1),
        "매입단가": st.column_config.NumberColumn("매입단가", min_value=0, step=1),
    },
)
수정포트폴리오 = 수정포트폴리오.reset_index(drop=True)


# -----------------------------------
# 포트폴리오 계산 결과
# -----------------------------------
계산포트폴리오 = 포트폴리오계산(수정포트폴리오)

st.markdown("---")
st.subheader("포트폴리오 현황")

if 계산포트폴리오.empty:
    st.warning("포트폴리오 데이터를 계산할 수 없습니다.")
else:
    총투자원금 = 계산포트폴리오["투자원금"].sum()
    총평가금액 = 계산포트폴리오["평가금액"].sum()
    총평가손익 = 계산포트폴리오["평가손익"].sum()
    총수익률 = (총평가손익 / 총투자원금 * 100) if 총투자원금 != 0 else 0

    위칸1, 위칸2, 위칸3, 위칸4 = st.columns(4)
    위칸1.metric("총 투자원금", 금액표시(총투자원금))
    위칸2.metric("총 평가금액", 금액표시(총평가금액))
    위칸3.metric("총 평가손익", 손익문자열(총평가손익) + "원")
    위칸4.metric("총 수익률", 수익률문자열(총수익률))

    포트폴리오표시 = 계산포트폴리오[["종목명", "보유수량", "매입단가", "현재가", "투자원금", "평가금액", "평가손익", "수익률", "현재비중"]].copy()
    포트폴리오표시 = index_1부터(포트폴리오표시)

    st.dataframe(
        포트폴리오표시.style.format({
            "보유수량": "{:,.0f}",
            "매입단가": "{:,.0f}",
            "현재가": "{:,.0f}",
            "투자원금": "{:,.0f}",
            "평가금액": "{:,.0f}",
            "평가손익": 손익문자열,
            "수익률": 수익률문자열,
            "현재비중": "{:.2f}",
        }).map(손익색상, subset=["평가손익"]).map(수익률색상, subset=["수익률"]),
        width="stretch",
    )
    st.plotly_chart(비중그래프(계산포트폴리오), width="stretch")


# -----------------------------------
# 목표비중 설정
# -----------------------------------
st.markdown("---")
st.subheader("목표 비중 설정")

저장된목표비중 = 목표비중불러오기()
목표칸1, 목표칸2, 목표칸3, 목표칸4 = st.columns(4)

with 목표칸1:
    목표_KODEX200 = st.number_input("KODEX 200 목표 비중(%)", 0.0, 100.0, float(저장된목표비중["069500"]), 1.0)
with 목표칸2:
    목표_KODEX코스닥150 = st.number_input("KODEX 코스닥150 목표 비중(%)", 0.0, 100.0, float(저장된목표비중["229200"]), 1.0)
with 목표칸3:
    목표_삼성전자 = st.number_input("삼성전자 목표 비중(%)", 0.0, 100.0, float(저장된목표비중["005930"]), 1.0)
with 목표칸4:
    목표_SK하이닉스 = st.number_input("SK하이닉스 목표 비중(%)", 0.0, 100.0, float(저장된목표비중["000660"]), 1.0)

목표비중합계 = 목표_KODEX200 + 목표_KODEX코스닥150 + 목표_삼성전자 + 목표_SK하이닉스
목표비중사전 = {
    "069500": 목표_KODEX200,
    "229200": 목표_KODEX코스닥150,
    "005930": 목표_삼성전자,
    "000660": 목표_SK하이닉스,
}

버튼칸1, 버튼칸2 = st.columns([1, 3])
with 버튼칸1:
    if st.button("목표비중 저장"):
        목표비중저장(목표비중사전)
        st.success("목표비중이 저장되었습니다.")
with 버튼칸2:
    st.write(f"현재 목표 비중 합계: {목표비중합계:.2f}%")

if abs(목표비중합계 - 100.0) > 0.001:
    st.error(f"목표 비중의 합계가 100%가 되어야 합니다. 현재 합계: {목표비중합계:.2f}%")
else:
    st.markdown("---")
    st.subheader("목표 비중 대비 리밸런싱")
    리밸런싱표, 현재총평가금액 = 리밸런싱계산(계산포트폴리오, 목표비중사전)

    결과칸1, 결과칸2, 결과칸3 = st.columns(3)
    결과칸1.metric("현재 총 평가금액", 금액표시(현재총평가금액))
    결과칸2.metric("목표 비중 합계", 비율표시(목표비중합계))
    결과칸3.metric("리밸런싱 대상 종목 수", f"{len(리밸런싱표):,}개")

    st.plotly_chart(목표비중비교그래프(리밸런싱표), width="stretch")

    리밸런싱표시 = 리밸런싱표[["종목명", "현재가", "현재비중", "목표비중", "비중차이", "평가금액", "목표평가금액", "리밸런싱금액", "정확계산수량", "주문참고수량", "권장방향"]].copy()
    리밸런싱표시 = index_1부터(리밸런싱표시)

    st.dataframe(
        리밸런싱표시.style.format({
            "현재가": "{:,.0f}",
            "현재비중": "{:.2f}",
            "목표비중": "{:.2f}",
            "비중차이": 수익률문자열,
            "평가금액": "{:,.0f}",
            "목표평가금액": "{:,.0f}",
            "리밸런싱금액": 손익문자열,
            "정확계산수량": "{:.2f}",
            "주문참고수량": "{:,.0f}",
        }).map(수익률색상, subset=["비중차이"]).map(손익색상, subset=["리밸런싱금액"]),
        width="stretch",
    )

    st.markdown("---")
    st.subheader("추가 투자금 배분표")
    추가투자금 = st.number_input("추가 투자금 입력(원)", min_value=0, value=1000000, step=100000)
    추가배분표, 총실사용금액, 남는현금 = 추가투자금배분계산(계산포트폴리오, 목표비중사전, 추가투자금)

    추가칸1, 추가칸2, 추가칸3 = st.columns(3)
    추가칸1.metric("입력한 추가 투자금", 금액표시(추가투자금))
    추가칸2.metric("실제 매수 사용 금액", 금액표시(총실사용금액))
    추가칸3.metric("남는 현금", 금액표시(남는현금))

    추가배분표시 = 추가배분표[["종목명", "현재가", "현재비중", "목표비중", "부족금액", "추천배정금액", "추천매수수량", "실사용금액", "추가매수의견"]].copy()
    추가배분표시 = index_1부터(추가배분표시)
    st.dataframe(
        추가배분표시.style.format({
            "현재가": "{:,.0f}",
            "현재비중": "{:.2f}",
            "목표비중": "{:.2f}",
            "부족금액": "{:,.0f}",
            "추천배정금액": "{:,.0f}",
            "추천매수수량": "{:,.0f}",
            "실사용금액": "{:,.0f}",
        }).map(손익색상, subset=["부족금액", "추천배정금액"]),
        width="stretch",
    )

    st.markdown("---")
    st.subheader("위험도 분석 대시보드")
    위험분석결과 = 포트폴리오위험도분석(계산포트폴리오, 목표비중사전, 개월수=6)
    위험칸1, 위험칸2, 위험칸3, 위험칸4 = st.columns(4)
    위험칸1.metric("포트폴리오 변동성", 비율표시(위험분석결과["변동성"]))
    위험칸2.metric("최대낙폭(MDD)", 비율표시(위험분석결과["최대낙폭"]))
    위험칸3.metric("최대 종목 비중", 비율표시(위험분석결과["집중도"]))
    위험칸4.metric("목표비중 이탈도", 비율표시(위험분석결과["비중이탈도"]))

    if 위험분석결과["위험수준"] == "높음":
        st.error(f"위험 수준: {위험분석결과['위험수준']} | {위험분석결과['위험코멘트']}")
    elif 위험분석결과["위험수준"] == "보통":
        st.warning(f"위험 수준: {위험분석결과['위험수준']} | {위험분석결과['위험코멘트']}")
    else:
        st.success(f"위험 수준: {위험분석결과['위험수준']} | {위험분석결과['위험코멘트']}")

    st.markdown("---")
    st.subheader("오늘의 포트폴리오 요약 의견")
    요약문목록 = 오늘의요약생성(계산포트폴리오, 리밸런싱표, 추가배분표, 총수익률, 위험분석결과, 추가투자금)
    st.info("\n\n".join([f"{i + 1}. {문장}" for i, 문장 in enumerate(요약문목록)]))

st.markdown("---")
st.write("※ 주요 지수와 종목 차트는 과거 시세는 pykrx 우선·Yahoo 대체 구조를 사용하고, 현재값은 네이버 금융 우선·시장 데이터 보조 방식으로 조회하도록 구성했습니다.")
st.write("※ 캔들 분석은 날짜 선택 방식으로 단순화했고, 일·주·월·년 단위 전환과 거래량 차트를 함께 표시하도록 조정했습니다.")
st.write("※ 네이버 금융 시장지표 HTML 구조가 바뀌면 Yahoo 및 파생 계산값으로 가능한 범위까지 대체하도록 구성했습니다.")
