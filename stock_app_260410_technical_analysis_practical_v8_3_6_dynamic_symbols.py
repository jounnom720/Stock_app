import io
import json
import math
import os
import re
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urljoin

import pandas as pd
import numpy as np
import requests
import streamlit as st

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except Exception:
    go = None
    make_subplots = None
    PLOTLY_AVAILABLE = False

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

모바일모드 = st.query_params.get("mobile", "0") == "1"

def 모바일여부():
    return 모바일모드

if 모바일여부():
    st.title("📈 투자 분석 시스템")
    st.caption("모바일 조회용 간소화 화면")
else:
    st.title("📈 투자 분석 시스템 v8.3.5")
    


if not PLOTLY_AVAILABLE:
    st.error("plotly가 설치되어 있지 않습니다. 터미널에서 'pip install plotly' 후 다시 실행해 주세요.")
    st.stop()


def 안전웹요청(url, params=None, timeout=10, attempts=2):
    마지막오류 = None
    for _ in range(attempts):
        try:
            응답 = requests.get(url, params=params, headers=USER_AGENT, timeout=timeout)
            응답.raise_for_status()
            return 응답
        except Exception as e:
            마지막오류 = e
    return None


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
    "KODEX AI반도체핵심장비": {"구분": "etf", "코드": "471990"},
    "TIGER 200": {"구분": "etf", "코드": "102110"},
    "삼성전자": {"구분": "stock", "코드": "005930"},
    "SK하이닉스": {"구분": "stock", "코드": "000660"},
}

관심종목 = {
    "069500": "KODEX 200",
    "229200": "KODEX 코스닥150",
    "471990": "KODEX AI반도체핵심장비",
    "102110": "TIGER 200",
    "005930": "삼성전자",
    "000660": "SK하이닉스",
}


코드명매핑 = {값["코드"]: 이름 for 이름, 값 in 주요자산.items()}
이름코드매핑 = {이름: 코드 for 코드, 이름 in 코드명매핑.items()}

@st.cache_data(ttl=86400)
def 전체종목매핑가져오기():
    return {}


def 종목명이름정리(종목명):
    이름 = "" if pd.isna(종목명) else str(종목명).strip()
    이름 = 이름.replace("\xa0", " ").replace("\u200b", "").strip()
    이름 = re.sub(r"\s+", " ", 이름)
    별칭매핑 = {
        "SK 하이닉스": "SK하이닉스",
        "sk 하이닉스": "SK하이닉스",
        "sk하이닉스": "SK하이닉스",
        "kodex 200": "KODEX 200",
        "kodex코스닥150": "KODEX 코스닥150",
        "kodex 코스닥150": "KODEX 코스닥150",
        "tiger 200": "TIGER 200",
        "tiger200": "TIGER 200",
        "TIGER200": "TIGER 200",
    }
    return 별칭매핑.get(이름, 이름)


def 종목코드기준종목명(종목코드):
    코드 = "" if pd.isna(종목코드) else re.sub(r"[^0-9]", "", str(종목코드)).zfill(6)
    if not 코드:
        return ""
    if 코드 in 코드명매핑:
        return 코드명매핑[코드]
    전체매핑 = 전체종목매핑가져오기()
    if isinstance(전체매핑, dict) and 코드 in 전체매핑:
        return str(전체매핑.get(코드, "")).strip()
    return ""



def 종목명기준종목코드(종목명):
    이름 = 종목명이름정리(종목명)
    if 이름 in 이름코드매핑:
        return 이름코드매핑[이름]
    return ""


def 종목코드종목명불일치정보(종목코드, 종목명):
    코드 = "" if pd.isna(종목코드) else re.sub(r"[^0-9]", "", str(종목코드)).zfill(6)
    이름 = 종목명이름정리(종목명)

    if not 코드 or not 이름 or len(코드) != 6 or not 코드.isdigit():
        return None

    코드기준이름 = 종목코드기준종목명(코드)
    이름기준코드 = 종목명기준종목코드(이름)

    if 코드기준이름 and 종목명이름정리(코드기준이름) != 이름:
        return {
            "입력코드": 코드,
            "입력이름": 이름,
            "코드기준이름": 코드기준이름,
            "이름기준코드": 이름기준코드,
        }
    return None


def 종목명자동보정(종목코드, 종목명=""):
    코드 = "" if pd.isna(종목코드) else re.sub(r"[^0-9]", "", str(종목코드)).zfill(6)
    이름 = 종목명이름정리(종목명)
    if 이름:
        return 이름
    코드기준이름 = 종목코드기준종목명(코드)
    return 코드기준이름 if 코드기준이름 else 코드

def 종목코드자동보정(종목명, 종목코드=""):
    이름 = 종목명이름정리(종목명)
    코드 = "" if pd.isna(종목코드) else str(종목코드).strip()
    숫자코드 = re.sub(r"[^0-9]", "", 코드)
    if len(숫자코드) == 6:
        return 숫자코드
    이름기준코드 = 종목명기준종목코드(이름)
    return 이름기준코드 if 이름기준코드 else (숫자코드.zfill(6) if 숫자코드 else "")


def 종목구분추정(종목명="", 종목코드=""):
    이름 = 종목명이름정리(종목명).upper()
    if any(키워드 in 이름 for 키워드 in ["KODEX", "TIGER", "KOSEF", "KBSTAR", "ARIRANG", "ACE", "SOL", "HANARO", "TIMEFOLIO", "PLUS"]):
        return "etf"
    return "stock"


def 동적종목매핑갱신(거래df):
    global 주요자산, 관심종목, 코드명매핑, 이름코드매핑

    if 거래df is None or 거래df.empty:
        return

    작업 = 거래df.copy()
    if "종목코드" not in 작업.columns:
        return

    if "종목명" not in 작업.columns:
        작업["종목명"] = ""

    작업["종목코드"] = 작업["종목코드"].apply(lambda 값: "" if pd.isna(값) else re.sub(r"[^0-9]", "", str(값)).zfill(6))
    작업["종목명"] = 작업["종목명"].apply(종목명이름정리)
    작업 = 작업[(작업["종목코드"] != "") & (작업["종목명"] != "")]
    if 작업.empty:
        return

    for _, 행 in 작업.drop_duplicates(subset=["종목코드", "종목명"]).iterrows():
        코드 = 행["종목코드"]
        이름 = 행["종목명"]
        if 코드 in ["1001", "2001"]:
            continue
        if 코드 not in 코드명매핑:
            코드명매핑[코드] = 이름
        if 이름 not in 이름코드매핑:
            이름코드매핑[이름] = 코드
        if 이름 not in 주요자산:
            주요자산[이름] = {"구분": 종목구분추정(이름, 코드), "코드": 코드}
        if 코드 not in 관심종목:
            관심종목[코드] = 이름


def 거래이력자동보정(df):
    보정 = df.copy()

    if 보정.empty:
        return pd.DataFrame(columns=["종목코드", "종목명", "거래일자", "거래구분", "거래수량", "거래단가", "운용사", "비고"])

    for 컬럼 in ["종목코드", "종목명", "운용사", "비고"]:
        if 컬럼 not in 보정.columns:
            보정[컬럼] = ""
    for 컬럼 in ["거래일자", "거래구분", "거래수량", "거래단가"]:
        if 컬럼 not in 보정.columns:
            보정[컬럼] = None

    보정["종목코드"] = 보정["종목코드"].apply(lambda 값: "" if pd.isna(값) else re.sub(r"[^0-9]", "", str(값)).zfill(6) if re.sub(r"[^0-9]", "", str(값)) else "")
    보정["종목명"] = 보정["종목명"].apply(종목명이름정리)
    보정["운용사"] = 보정["운용사"].apply(lambda 값: "" if pd.isna(값) else str(값).strip())
    보정["비고"] = 보정["비고"].apply(lambda 값: "" if pd.isna(값) else str(값).strip())

    def _행보정(행):
        코드 = 행.get("종목코드", "")
        이름 = 행.get("종목명", "")

        if 코드 and not 이름:
            이름 = 종목코드기준종목명(코드) or 이름
        elif 이름 and not 코드:
            코드 = 종목명기준종목코드(이름) or 코드
        else:
            불일치 = 종목코드종목명불일치정보(코드, 이름)
            if 불일치 is None:
                코드 = 종목명기준종목코드(이름) or 코드
                이름 = 종목코드기준종목명(코드) or 이름

        행["종목코드"] = 코드 if (isinstance(코드, str) and len(코드) == 6 and 코드.isdigit()) else ""
        행["종목명"] = 이름
        return 행

    보정 = 보정.apply(_행보정, axis=1)

    보정["거래일자"] = pd.to_datetime(보정["거래일자"], errors="coerce").dt.date
    보정["거래구분"] = 보정["거래구분"].astype(str).str.strip().replace({"buy": "매수", "BUY": "매수", "Buy": "매수", "sell": "매도", "SELL": "매도", "Sell": "매도"})
    보정["거래구분"] = 보정["거래구분"].replace({"매입": "매수", "구매": "매수", "매각": "매도", "sell ": "매도", "buy ": "매수"})
    보정.loc[보정["거래구분"].isin(["", "None", "nan"]), "거래구분"] = ""

    보정["거래수량"] = pd.to_numeric(보정["거래수량"], errors="coerce")
    보정["거래단가"] = pd.to_numeric(보정["거래단가"], errors="coerce")

    return 보정



def 거래이력검증표생성(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=["행", "점검항목", "현재값", "권장사항"])

    점검결과 = []
    작업 = 거래이력자동보정(df.reset_index(drop=True).copy())
    오늘 = datetime.today().date()

    for idx, 행 in 작업.iterrows():
        행번호 = idx + 1
        종목코드 = "" if pd.isna(행.get("종목코드")) else str(행.get("종목코드")).strip()
        종목명 = "" if pd.isna(행.get("종목명")) else str(행.get("종목명")).strip()
        거래일자 = 행.get("거래일자")
        거래구분 = "" if pd.isna(행.get("거래구분")) else str(행.get("거래구분")).strip()
        거래수량 = pd.to_numeric(pd.Series([행.get("거래수량")]), errors="coerce").fillna(0).iloc[0]
        거래단가 = pd.to_numeric(pd.Series([행.get("거래단가")]), errors="coerce").fillna(0).iloc[0]

        if 종목코드 == "" and 종목명 == "":
            점검결과.append({"행": 행번호, "점검항목": "종목 정보", "현재값": "공란", "권장사항": "종목코드 또는 종목명 입력"})

        if 종목코드 != "" and (len(종목코드) != 6 or not 종목코드.isdigit()):
            점검결과.append({"행": 행번호, "점검항목": "종목코드 형식", "현재값": 종목코드, "권장사항": "6자리 숫자로 입력"})

        불일치정보 = 종목코드종목명불일치정보(종목코드, 종목명)
        if 불일치정보 is not None:
            권장 = f'코드 {불일치정보["입력코드"]}는 "{불일치정보["코드기준이름"]}" 입니다'
            if 불일치정보.get("이름기준코드"):
                권장 += f' / "{불일치정보["입력이름"]}"의 코드는 {불일치정보["이름기준코드"]}'
            점검결과.append({"행": 행번호, "점검항목": "종목코드-종목명 불일치", "현재값": f'{종목코드} / {종목명}', "권장사항": 권장})

        변환일자 = pd.to_datetime(거래일자, errors="coerce")
        if pd.isna(변환일자):
            점검결과.append({"행": 행번호, "점검항목": "거래일자", "현재값": 거래일자, "권장사항": "YYYY-MM-DD 형식으로 입력"})
        elif 변환일자.date() > 오늘:
            점검결과.append({"행": 행번호, "점검항목": "미래 날짜", "현재값": str(거래일자), "권장사항": "오늘 또는 과거 날짜만 입력"})

        if 거래구분 not in ["매수", "매도"]:
            점검결과.append({"행": 행번호, "점검항목": "거래구분", "현재값": 거래구분, "권장사항": "매수 또는 매도만 입력"})

        if 거래수량 <= 0:
            점검결과.append({"행": 행번호, "점검항목": "거래수량", "현재값": 거래수량, "권장사항": "0보다 큰 수량 입력"})

        if 거래단가 <= 0:
            점검결과.append({"행": 행번호, "점검항목": "거래단가", "현재값": 거래단가, "권장사항": "0보다 큰 단가 입력"})

    정렬작업 = 작업.copy()
    정렬작업["_거래일자정렬"] = pd.to_datetime(정렬작업["거래일자"], errors="coerce")
    정렬작업["_원본행"] = 정렬작업.index + 1
    정렬작업 = 정렬작업.sort_values(["종목코드", "_거래일자정렬", "_원본행"])

    종목별보유수량 = {}

    for _, 행 in 정렬작업.iterrows():
        행번호 = int(행["_원본행"])
        종목코드 = "" if pd.isna(행.get("종목코드")) else str(행.get("종목코드")).strip()
        거래구분 = "" if pd.isna(행.get("거래구분")) else str(행.get("거래구분")).strip()
        거래수량 = pd.to_numeric(pd.Series([행.get("거래수량")]), errors="coerce").fillna(0).iloc[0]

        if not 종목코드 or 거래수량 <= 0 or 거래구분 not in ["매수", "매도"]:
            continue

        현재보유 = 종목별보유수량.get(종목코드, 0)

        if 거래구분 == "매수":
            종목별보유수량[종목코드] = 현재보유 + 거래수량
        else:
            if 거래수량 > 현재보유:
                점검결과.append({
                    "행": 행번호,
                    "점검항목": "초과매도",
                    "현재값": f"{거래수량}주 매도 / 보유 {현재보유}주",
                    "권장사항": "이전 거래이력 또는 수량 입력을 확인"
                })
            종목별보유수량[종목코드] = max(0, 현재보유 - 거래수량)

    return pd.DataFrame(점검결과)


종목별거래단가범위 = {
    "069500": {"최소": 50000, "최대": 120000, "이름": "KODEX 200"},
    "229200": {"최소": 10000, "최대": 40000, "이름": "KODEX 코스닥150"},
    "471990": {"최소": 10000, "최대": 50000, "이름": "KODEX AI반도체핵심장비"},
    "005930": {"최소": 100000, "최대": 300000, "이름": "삼성전자"},
    "000660": {"최소": 500000, "최대": 1500000, "이름": "SK하이닉스"},
}


def 거래이력이상치점검표생성(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=["행", "점검항목", "현재값", "권장사항"])

    작업 = 거래이력자동보정(df.reset_index(drop=True).copy())
    점검결과 = []

    중복기준열 = ["종목코드", "종목명", "거래일자", "거래구분", "거래수량", "거래단가"]
    중복마스크 = 작업.duplicated(subset=중복기준열, keep=False)
    if 중복마스크.any():
        for idx, 행 in 작업.loc[중복마스크].iterrows():
            점검결과.append({
                "행": idx + 1,
                "점검항목": "중복거래 가능성",
                "현재값": f"{행.get('종목명', '')} / {행.get('거래일자', '')} / {행.get('거래구분', '')} / {행.get('거래수량', '')}주 / {행.get('거래단가', '')}",
                "권장사항": "같은 거래가 2번 입력되지 않았는지 확인"
            })

    for idx, 행 in 작업.iterrows():
        종목코드 = str(행.get("종목코드", "") or "").zfill(6)
        종목명 = 종목명자동보정(종목코드, 행.get("종목명", ""))
        거래단가 = pd.to_numeric(pd.Series([행.get("거래단가")]), errors="coerce").iloc[0]
        if pd.isna(거래단가) or 거래단가 <= 0:
            continue

        범위정보 = 종목별거래단가범위.get(종목코드)
        if 범위정보:
            최소값 = 범위정보["최소"]
            최대값 = 범위정보["최대"]
            if 거래단가 < 최소값 or 거래단가 > 최대값:
                점검결과.append({
                    "행": idx + 1,
                    "점검항목": "거래단가 범위 확인",
                    "현재값": f"{종목명} {거래단가:,.0f}",
                    "권장사항": f"{종목명}의 통상 입력 범위({최소값:,.0f}~{최대값:,.0f}원)와 크게 다르면 실제 체결단가를 다시 확인"
                })
        else:
            if 거래단가 < 100 or 거래단가 > 5000000:
                점검결과.append({
                    "행": idx + 1,
                    "점검항목": "거래단가 극단값 확인",
                    "현재값": f"{종목명} {거래단가:,.0f}",
                    "권장사항": "입력 자릿수 또는 실제 체결단가를 다시 확인"
                })

    if not 점검결과:
        return pd.DataFrame(columns=["행", "점검항목", "현재값", "권장사항"])

    결과df = pd.DataFrame(점검결과)
    return 결과df.drop_duplicates().sort_values(["행", "점검항목"]).reset_index(drop=True)


기본포트폴리오 = pd.DataFrame([
    {"종목코드": "069500", "종목명": "KODEX 200", "거래일자": "2026-02-20", "거래구분": "매수", "거래수량": 11, "거래단가": 86239, "운용사": "신한은행 IRP", "비고": ""},
    {"종목코드": "069500", "종목명": "KODEX 200", "거래일자": "2026-03-04", "거래구분": "매도", "거래수량": 11, "거래단가": 78475, "운용사": "신한은행 IRP", "비고": "미국의 이란 침공으로 주식 폭락, -10% 기준 손절"},
    {"종목코드": "069500", "종목명": "KODEX 200", "거래일자": "2026-03-05", "거래구분": "매수", "거래수량": 35, "거래단가": 84936, "비고": "폭락 이후 반등"},
    {"종목코드": "069500", "종목명": "KODEX 200", "거래일자": "2026-03-10", "거래구분": "매수", "거래수량": 59, "거래단가": 82900, "비고": "트럼프 이란전 조기 종료 발표, 주요 국제 유가 하락"},
    {"종목코드": "000660", "종목명": "SK하이닉스", "거래일자": "2026-03-10", "거래구분": "매수", "거래수량": 1, "거래단가": 941000, "비고": "트럼프 이란전 조기 종료 발표"},
    {"종목코드": "005930", "종목명": "삼성전자", "거래일자": "2026-03-10", "거래구분": "매수", "거래수량": 10, "거래단가": 189300, "비고": "트럼프 이란전 조기 종료 발표"},
    {"종목코드": "005930", "종목명": "삼성전자", "거래일자": "2026-03-11", "거래구분": "매수", "거래수량": 1, "거래단가": 189000, "비고": "주가 등락 안정 판단"},
    {"종목코드": "005930", "종목명": "삼성전자", "거래일자": "2026-03-11", "거래구분": "매수", "거래수량": 5, "거래단가": 191700, "비고": "주가 등락 안정 판단"},
    {"종목코드": "005930", "종목명": "삼성전자", "거래일자": "2026-03-13", "거래구분": "매수", "거래수량": 11, "거래단가": 180800, "비고": "주가 최근 최저, 주가등록 안정 판단"},
    {"종목코드": "069500", "종목명": "KODEX 200", "거래일자": "2026-03-13", "거래구분": "매수", "거래수량": 3, "거래단가": 83635, "비고": "정윤"},
    {"종목코드": "229200", "종목명": "KODEX 코스닥150", "거래일자": "2026-03-13", "거래구분": "매수", "거래수량": 2, "거래단가": 19970, "비고": "정윤"},
    {"종목코드": "229200", "종목명": "KODEX 코스닥150", "거래일자": "2026-03-16", "거래구분": "매수", "거래수량": 5, "거래단가": 19929, "비고": "코스닥 150 관심 증가 이광수대표 발언"},
    {"종목코드": "069500", "종목명": "KODEX 200", "거래일자": "2026-03-23", "거래구분": "매수", "거래수량": 23, "거래단가": 84031, "비고": "코덱스 -6.5% 하락에 분할매수 유리 판단"},
    {"종목코드": "000660", "종목명": "SK하이닉스", "거래일자": "2026-03-23", "거래구분": "매수", "거래수량": 1, "거래단가": 933000, "비고": "전일 종가 대비 주가 -7.15% 하락"},
    {"종목코드": "005930", "종목명": "삼성전자", "거래일자": "2026-03-23", "거래구분": "매수", "거래수량": 5, "거래단가": 186300, "비고": "전일 종가 대비 주가 -6.27% 하락"},
    {"종목코드": "069500", "종목명": "KODEX 200", "거래일자": "2026-03-24", "거래구분": "매수", "거래수량": 35, "거래단가": 83872, "비고": "전일 폭락 후 오늘 트럼프의 이란 공격 중지 발표로 유가, 환율 하락"},
])
기본포트폴리오["거래일자"] = pd.to_datetime(기본포트폴리오["거래일자"], errors="coerce").dt.date

시장지표네이버URL = {

    "USD/KRW": "https://finance.naver.com/marketindex/exchangeDetail.naver?marketindexCd=FX_USDKRW",
    "국제 금": "https://finance.naver.com/marketindex/worldGoldDetail.naver",
    "국내 금": "https://finance.naver.com/marketindex/goldDetail.naver",
    "WTI": "https://finance.naver.com/marketindex/worldDailyQuoteDetail.naver?marketindexCd=OIL_CL",
    "브렌트유": "https://finance.naver.com/marketindex/worldDailyQuoteDetail.naver?marketindexCd=OIL_BRT",
    "두바이유": "https://finance.naver.com/marketindex/worldDailyQuoteDetail.naver?marketindexCd=OIL_DU",
}

목표비중저장파일 = "target_weights.json"
거래이력자동저장파일 = "trade_history_autosave.json"


def 안전JSON저장(data, file_path):
    temp_path = f"{file_path}.tmp"
    backup_path = f"{file_path}.bak"

    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        if os.path.exists(file_path):
            try:
                import shutil
                shutil.copy(file_path, backup_path)
            except Exception:
                pass

        os.replace(temp_path, file_path)
        return True, "저장 완료"
    except Exception as e:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
        return False, f"저장 실패: {e}"




def 거래이력JSON변환(df):
    저장df = df.copy()

    표준열 = ["종목코드", "종목명", "거래일자", "거래구분", "거래수량", "거래단가", "운용사", "비고", "_입력원본순서"]
    if "_입력원본순서" not in 저장df.columns:
        저장df["_입력원본순서"] = range(len(저장df))
    else:
        저장df["_입력원본순서"] = pd.to_numeric(저장df["_입력원본순서"], errors="coerce")
        저장df["_입력원본순서"] = 저장df["_입력원본순서"].fillna(pd.Series(range(len(저장df)), index=저장df.index)).astype(int)

    for 열 in ["종목코드", "종목명", "거래일자", "거래구분", "거래수량", "거래단가", "운용사", "비고"]:
        if 열 not in 저장df.columns:
            저장df[열] = ""

    저장df = 저장df[표준열].copy()
    저장df["거래일자"] = pd.to_datetime(저장df["거래일자"], errors="coerce").dt.strftime("%Y-%m-%d")
    저장df["종목코드"] = 저장df["종목코드"].apply(lambda 값: "" if pd.isna(값) else str(값).zfill(6))
    저장df = 저장df.fillna("")

    return 저장df.to_dict(orient="records")


def 자동저장불러오기(file_path):
    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        df = pd.DataFrame(data)
        df = 거래이력표준열맞추기(df)
        df = 거래이력자동보정(df)

        if "거래일자" in df.columns:
            df["거래일자"] = pd.to_datetime(df["거래일자"], errors="coerce").dt.date

        return 거래이력입력창정렬(df)
    except Exception:
        return None


def 거래이력자동저장실행(df):
    저장데이터 = 거래이력JSON변환(df)
    return 안전JSON저장(저장데이터, 거래이력자동저장파일)


def 거래이력열이름정리(열이름):
    if pd.isna(열이름):
        return ""
    이름 = str(열이름).strip()
    이름 = 이름.replace("\n", "").replace("\r", "").replace("\t", "")
    이름 = 이름.replace(" ", "").replace("_", "").replace("-", "")
    이름 = 이름.replace("(", "").replace(")", "")
    return 이름


def 거래이력셀문자정리(값):
    if pd.isna(값):
        return 값
    if isinstance(값, str):
        return 값.replace("\xa0", " ").replace("\u200b", "").strip()
    return 값


def 거래이력컬럼명매핑():
    return {
        "종목코드": "종목코드",
        "코드": "종목코드",
        "티커": "종목코드",
        "종목번호": "종목코드",
        "종목명": "종목명",
        "종목": "종목명",
        "종목이름": "종목명",
        "이름": "종목명",
        "거래일자": "거래일자",
        "일자": "거래일자",
        "날짜": "거래일자",
        "매매일": "거래일자",
        "거래날짜": "거래일자",
        "거래구분": "거래구분",
        "구분": "거래구분",
        "매매구분": "거래구분",
        "매수매도": "거래구분",
        "거래수량": "거래수량",
        "수량": "거래수량",
        "매매수량": "거래수량",
        "체결수량": "거래수량",
        "거래단가": "거래단가",
        "단가": "거래단가",
        "가격": "거래단가",
        "체결가": "거래단가",
        "체결단가": "거래단가",
        "매수가": "거래단가",
        "매도가": "거래단가",
        "비고": "비고",
        "메모": "비고",
        "참고": "비고",
        "노트": "비고",
        "운용사": "운용사",
        "계좌": "운용사",
        "증권사": "운용사",
        "계좌명": "운용사",
        "운용계좌": "운용사",
    }


def 거래이력원본정리(df):
    결과 = df.copy()
    결과.columns = [거래이력열이름정리(c) for c in 결과.columns]
    컬럼매핑 = 거래이력컬럼명매핑()
    rename_map = {}
    for col in 결과.columns:
        if col in 컬럼매핑:
            rename_map[col] = 컬럼매핑[col]
    결과 = 결과.rename(columns=rename_map)

    for col in 결과.columns:
        try:
            결과[col] = 결과[col].apply(거래이력셀문자정리)
        except Exception:
            pass

    return 결과


def 거래이력표준열맞추기(df):
    표준열 = ["종목코드", "종목명", "거래일자", "거래구분", "거래수량", "거래단가", "운용사", "비고"]
    결과 = 거래이력원본정리(df)

    if "_입력원본순서" not in 결과.columns:
        결과["_입력원본순서"] = range(len(결과))

    for 열 in 표준열:
        if 열 not in 결과.columns:
            결과[열] = None if 열 in ["거래일자", "거래수량", "거래단가"] else ""

    return 결과[표준열 + ["_입력원본순서"]]


def 업로드파일에서거래이력읽기(업로드파일):
    파일명 = (업로드파일.name or "").lower()

    if 파일명.endswith(".json"):
        내용 = json.load(업로드파일)
        if isinstance(내용, dict) and "data" in 내용:
            내용 = 내용["data"]
        return 거래이력표준열맞추기(pd.DataFrame(내용))

    if 파일명.endswith(".xlsx") or 파일명.endswith(".xls"):
        읽기df = pd.read_excel(업로드파일, dtype=object)
        return 거래이력표준열맞추기(읽기df)

    원본바이트 = 업로드파일.getvalue()
    마지막오류 = None

    for 인코딩 in ["utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        try:
            읽기df = pd.read_csv(io.BytesIO(원본바이트), encoding=인코딩, dtype=object)
            return 거래이력표준열맞추기(읽기df)
        except Exception as e:
            마지막오류 = e

    raise 마지막오류


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


def 안전정수포맷(값):
    if pd.isna(값) or 값 is None:
        return "-"
    try:
        return f"{float(값):,.0f}"
    except Exception:
        return "-"


def 안전소수포맷(값, 소수점=2):
    if pd.isna(값) or 값 is None:
        return "-"
    try:
        return f"{float(값):,.{소수점}f}"
    except Exception:
        return "-"


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


def 통화문자정리(값):
    if pd.isna(값) or 값 is None:
        return None
    if isinstance(값, (int, float)):
        return float(값)
    문자열 = str(값).strip()
    if 문자열 == "":
        return None
    문자열 = 문자열.replace("₩", "").replace("￦", "").replace("원", "").replace(",", "").strip()
    문자열 = re.sub(r"[^0-9.\-]", "", 문자열)
    if 문자열 in ["", ".", "-", "-."]:
        return None
    try:
        return float(문자열)
    except Exception:
        return None


def 거래단가표시문자열(값):
    숫자값 = 통화문자정리(값)
    if 숫자값 is None:
        return ""
    return f"₩ {int(round(숫자값)):,.0f}"


def 거래이력입력창정렬(df):
    if df is None:
        return pd.DataFrame(columns=["종목코드", "종목명", "거래일자", "거래구분", "거래수량", "거래단가", "비고"])

    결과 = df.copy()

    for 컬럼 in ["종목코드", "종목명", "거래일자", "거래구분", "거래수량", "거래단가", "운용사", "비고"]:
        if 컬럼 not in 결과.columns:
            결과[컬럼] = None if 컬럼 in ["거래일자", "거래수량", "거래단가"] else ""

    if "_입력원본순서" not in 결과.columns:
        결과["_입력원본순서"] = range(len(결과))

    결과 = 결과.dropna(how="all")
    결과["_입력원본순서"] = pd.to_numeric(
        결과["_입력원본순서"], errors="coerce"
    ).fillna(pd.Series(range(len(결과)), index=결과.index)).astype(int)

    # 표시 순서는 엑셀/입력 시트 원본 순서를 그대로 유지
    결과 = 결과.sort_values(by=["_입력원본순서"], ascending=[True], na_position="last", kind="stable")
    결과 = 결과.reset_index(drop=True)
    return 결과


def 거래이력정규화(df):
    if df is None:
        return pd.DataFrame(columns=["종목코드", "종목명", "거래일자", "거래구분", "거래수량", "거래단가", "운용사", "비고"])
    원본 = df.copy()
    if "_입력원본순서" not in 원본.columns:
        원본["_입력원본순서"] = range(len(원본))
    정규화 = 거래이력표준열맞추기(원본)
    정규화["_입력원본순서"] = 원본["_입력원본순서"].values
    정규화 = 거래이력자동보정(정규화)
    정규화 = 거래이력입력창정렬(정규화)
    return 정규화


def 보유포트폴리오필터(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=df.columns if df is not None else [])
    결과 = df.copy()
    if "보유수량" not in 결과.columns:
        return 결과.iloc[0:0].copy()
    결과["보유수량"] = pd.to_numeric(결과["보유수량"], errors="coerce").fillna(0)
    결과 = 결과[결과["보유수량"] > 0].copy()
    if 결과.empty:
        return 결과
    if "평가금액" in 결과.columns:
        결과 = 결과.sort_values(["평가금액", "종목명"], ascending=[False, True])
    else:
        결과 = 결과.sort_values(["종목명", "종목코드"], ascending=[True, True])
    return 결과.reset_index(drop=True)


def 보유종목선택옵션생성(df):
    보유df = 보유포트폴리오필터(df)
    if 보유df.empty:
        return []
    옵션 = []
    for _, 행 in 보유df.iterrows():
        종목코드 = str(행.get("종목코드", "")).zfill(6)
        종목명 = 종목명자동보정(종목코드, 행.get("종목명", ""))
        옵션.append({
            "종목코드": 종목코드,
            "종목명": 종목명,
            "표시": f"{종목명} ({종목코드})",
        })
    return 옵션

def 현재거래내역엑셀저장바이트(df):
    저장대상 = df.copy()

    for 컬럼 in ["종목코드", "종목명", "거래일자", "거래구분", "거래수량", "거래단가", "운용사", "비고"]:
        if 컬럼 not in 저장대상.columns:
            저장대상[컬럼] = ""

    저장대상 = 저장대상[["종목코드", "종목명", "거래일자", "거래구분", "거래수량", "거래단가", "운용사", "비고"]].copy()
    저장대상["거래일자"] = pd.to_datetime(저장대상["거래일자"], errors="coerce").dt.strftime("%Y-%m-%d")
    저장대상["종목코드"] = 저장대상["종목코드"].apply(lambda 값: "" if pd.isna(값) else str(값).zfill(6))
    저장대상 = 저장대상.fillna("")

    버퍼 = io.BytesIO()
    with pd.ExcelWriter(버퍼, engine="openpyxl") as writer:
        저장대상.to_excel(writer, index=False, sheet_name="거래이력")
    버퍼.seek(0)
    return 버퍼.getvalue()

def 거래이력표시용변환(df):
    표시 = df.copy()
    if "거래단가" in 표시.columns:
        표시["거래단가"] = 표시["거래단가"].apply(거래단가표시문자열)
    return 표시


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


def 모바일차트높이(데스크탑높이=460, 모바일높이=360):
    return 모바일높이 if 모바일여부() else 데스크탑높이


def 거래이력표_컬럼선택(df):
    if 모바일여부():
        사용컬럼 = [c for c in ["거래일자", "종목명", "거래구분", "거래수량", "거래단가"] if c in df.columns]
        if 사용컬럼:
            return df[사용컬럼].copy()
    return df.copy()


def 포트폴리오표_컬럼선택(df):
    if 모바일여부():
        사용컬럼 = [c for c in ["종목명", "보유수량", "현재가", "평가금액", "수익률"] if c in df.columns]
        if 사용컬럼:
            return df[사용컬럼].copy()
    return df.copy()


def 리밸런싱표_컬럼선택(df):
    if 모바일여부():
        사용컬럼 = [c for c in ["종목명", "현재비중", "목표비중", "권장방향"] if c in df.columns]
        if 사용컬럼:
            return df[사용컬럼].copy()
    return df.copy()


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



def 유효숫자인지(값):
    try:
        return 값 is not None and not pd.isna(값) and np.isfinite(float(값))
    except Exception:
        return False


def 마지막유효값시리즈(series):
    if series is None:
        return None
    try:
        정리 = pd.to_numeric(series, errors="coerce").dropna()
        if 정리.empty:
            return None
        return float(정리.iloc[-1])
    except Exception:
        return None


def 끝에서두번째유효값시리즈(series):
    if series is None:
        return None
    try:
        정리 = pd.to_numeric(series, errors="coerce").dropna()
        if len(정리) < 2:
            return None
        return float(정리.iloc[-2])
    except Exception:
        return None


def 시리즈길이맞추기(values, target_len):
    값목록 = list(values) if values is not None else []
    if len(값목록) < target_len:
        값목록 += [None] * (target_len - len(값목록))
    return 값목록[:target_len]


def OHLCV데이터정리(df):
    if df is None or df.empty:
        return pd.DataFrame()
    작업 = df.copy()
    if "날짜" in 작업.columns:
        작업["날짜"] = pd.to_datetime(작업["날짜"], errors="coerce")
        작업 = 작업.dropna(subset=["날짜"])
        작업["날짜"] = 작업["날짜"].dt.tz_localize(None)
        작업 = 작업.sort_values("날짜").drop_duplicates(subset=["날짜"], keep="last").set_index("날짜")
    for col in ["시가", "고가", "저가", "종가", "거래량"]:
        if col not in 작업.columns:
            작업[col] = np.nan
        작업[col] = pd.to_numeric(작업[col], errors="coerce")
    작업 = 작업.dropna(how="all", subset=["시가", "고가", "저가", "종가"])
    return 작업.sort_index()


def 최근유효OHLCV요약(df):
    if df is None or df.empty:
        return {"현재가": None, "전일가": None, "전일대비": None, "등락률": None, "기준일": None, "상태": "조회 실패"}
    작업 = OHLCV데이터정리(df)
    if 작업.empty or "종가" not in 작업.columns:
        return {"현재가": None, "전일가": None, "전일대비": None, "등락률": None, "기준일": None, "상태": "조회 실패"}
    현재가 = 마지막유효값시리즈(작업["종가"])
    전일가 = 끝에서두번째유효값시리즈(작업["종가"])
    if 현재가 is None:
        return {"현재가": None, "전일가": None, "전일대비": None, "등락률": None, "기준일": None, "상태": "조회 실패"}
    기준일 = pd.to_datetime(작업.index[-1]).date() if len(작업.index) > 0 else None
    전일대비 = None if 전일가 is None else 현재가 - 전일가
    등락률 = None if 전일가 in [None, 0] else (전일대비 / 전일가) * 100
    상태 = "직전 종가 반영" if 전일가 is not None else "최근 유효 종가 반영"
    return {"현재가": 현재가, "전일가": 전일가, "전일대비": 전일대비, "등락률": 등락률, "기준일": 기준일, "상태": 상태}


def 종목코드별야후심볼후보(코드):
    코드 = str(코드).zfill(6)
    return [f"{코드}.KS", f"{코드}.KQ"]

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
        "069500": 50.0,
        "229200": 3.0,
        "471990": 0.0,
        "005930": 27.0,
        "000660": 20.0,
    }

    if os.path.exists(목표비중저장파일):
        try:
            with open(목표비중저장파일, "r", encoding="utf-8") as f:
                저장값 = json.load(f)
            return {
                "069500": float(저장값.get("069500", 50.0)),
                "229200": float(저장값.get("229200", 3.0)),
                "471990": float(저장값.get("471990", 0.0)),
                "005930": float(저장값.get("005930", 27.0)),
                "000660": float(저장값.get("000660", 20.0)),
            }
        except Exception:
            return 기본값

    return 기본값


def 목표비중저장(목표비중):
    return 안전JSON저장(목표비중, 목표비중저장파일)



# -----------------------------------
# 데이터 조회 함수
# -----------------------------------
@st.cache_data(ttl=180)
def 네이버페이지가져오기(url):
    try:
        응답 = 안전웹요청(url, timeout=10, attempts=2)
        if 응답 is None:
            return None
        return 응답.text
    except Exception:
        return None


@st.cache_data(ttl=180)
def 야후현재가요약가져오기(심볼, 이름):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{심볼}"
        params = {"interval": "1d", "range": "7d", "includePrePost": "false", "events": "div,splits"}
        응답 = 안전웹요청(url, params=params, timeout=10, attempts=2)
        if 응답 is None:
            return {"지표": 이름, "현재값": None, "전일대비": None, "등락률": None, "출처": "Yahoo"}
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
def 네이버일별원자재표가져오기(시장코드):
    후보URL = [
        f"https://finance.naver.com/marketindex/worldDailyQuote.naver?marketindexCd={시장코드}&fdtc=2&page=1",
        f"https://finance.naver.com/marketindex/worldDailyQuoteDetail.naver?marketindexCd={시장코드}",
        f"https://finance.naver.com/marketindex/worldOilDetail.nhn?marketindexCd={시장코드}&fdtc=2",
    ]

    for url in 후보URL:
        html = 네이버페이지가져오기(url)
        if not html:
            continue

        # 1차: pandas.read_html 기반 표 추출
        try:
            tables = pd.read_html(io.StringIO(html))
            for table in tables:
                작업 = table.copy()
                작업.columns = [str(c).strip() for c in 작업.columns]
                컬럼문자 = " ".join(작업.columns).replace(" ", "")
                if ("날짜" in 컬럼문자 or "일자" in 컬럼문자) and ("종가" in 컬럼문자 or "종가*" in 컬럼문자 or "최종" in 컬럼문자 or "Close" in 컬럼문자):
                    작업 = 작업.dropna(how="all").copy()
                    if 작업.empty:
                        continue

                    날짜열 = next((c for c in 작업.columns if "날짜" in str(c) or "일자" in str(c)), None)
                    종가열 = next((c for c in 작업.columns if "종가" in str(c) or "최종" in str(c) or "Close" in str(c)), None)
                    등락률열 = next((c for c in 작업.columns if "등락률" in str(c) or "%" in str(c)), None)

                    if 종가열 is None:
                        continue

                    작업[종가열] = 작업[종가열].apply(통화문자정리)
                    if 날짜열 is not None:
                        작업[날짜열] = pd.to_datetime(작업[날짜열], errors="coerce")
                        작업 = 작업.sort_values(날짜열, ascending=False, na_position="last")
                    작업 = 작업.dropna(subset=[종가열])
                    if 작업.empty:
                        continue

                    현재값 = float(작업.iloc[0][종가열])
                    전일값 = float(작업.iloc[1][종가열]) if len(작업) >= 2 and pd.notna(작업.iloc[1][종가열]) else None
                    전일대비 = (현재값 - 전일값) if 전일값 not in [None, 0] else None
                    등락률 = None
                    if 전일값 not in [None, 0]:
                        등락률 = (전일대비 / 전일값) * 100
                    elif 등락률열 is not None:
                        등락률 = 안전실수변환(작업.iloc[0][등락률열])

                    return {
                        "현재값": 현재값,
                        "전일대비": 전일대비,
                        "등락률": 등락률,
                        "링크": url,
                        "출처": "네이버",
                    }
        except Exception:
            pass

        # 2차: 상세페이지 문구 추출
        if BS4_AVAILABLE:
            try:
                soup = BeautifulSoup(html, "html.parser")
                text_candidates = [x.get_text(" ", strip=True) for x in soup.select("span, p, em, td, li")]
                숫자후보 = []
                for t in text_candidates:
                    v = 안전실수변환(t)
                    if v is not None:
                        숫자후보.append(v)
                if 숫자후보:
                    현재값 = float(숫자후보[0])
                    전일대비 = 숫자후보[1] if len(숫자후보) >= 2 else None
                    등락률 = None
                    for t in text_candidates:
                        if "%" in t:
                            등락률 = 안전실수변환(t)
                            if 등락률 is not None:
                                break
                    return {
                        "현재값": 현재값,
                        "전일대비": 전일대비,
                        "등락률": 등락률,
                        "링크": url,
                        "출처": "네이버",
                    }
            except Exception:
                pass

    return {"현재값": None, "전일대비": None, "등락률": None, "링크": None, "출처": "네이버"}


@st.cache_data(ttl=180)
def 두바이유현재가가져오기():
    for 시장코드 in ["OIL_DU", "OIL_DUB"]:
        결과 = 네이버일별원자재표가져오기(시장코드)
        if 결과.get("현재값") is not None:
            return {
                "지표": "두바이유",
                "현재값": 결과.get("현재값"),
                "전일대비": 결과.get("전일대비"),
                "등락률": 결과.get("등락률"),
                "링크": 결과.get("링크", 시장지표네이버URL.get("두바이유")),
                "출처": "네이버" if str(결과.get("출처", "Naver")).lower().startswith("naver") else 결과.get("출처", "네이버"),
            }

    # 마지막 안전장치: 브렌트유/WTI 프록시
    프록시 = 파생주요지표가져오기("두바이유")
    프록시["링크"] = 시장지표네이버URL.get("두바이유")
    프록시["출처"] = "프록시(브렌트/WTI 대체)"
    return 프록시

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

            return {"지표": 이름, "현재값": 현재값, "전일대비": 전일대비, "등락률": 등락률, "출처": "Proxy(Brent/WTI)"}
    except Exception:
        return 빈결과

    return 빈결과


def 시장지표단건가져오기(이름, url):
    if 이름 == "두바이유":
        결과 = 두바이유현재가가져오기()
        if 결과 and 결과.get("현재값") is not None:
            return 결과
        return {"지표": 이름, "현재값": None, "전일대비": None, "등락률": None, "링크": url, "출처": "-"}

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
                    return {"지표": 이름, "현재값": 현재값, "전일대비": 전일대비, "등락률": 등락률, "링크": url, "출처": "네이버"}
            except Exception:
                pass

    if fallback_to_yahoo:
        심볼 = 야후주요지표심볼.get(이름)
        if 심볼:
            결과 = 야후현재가요약가져오기(심볼, 이름)
            결과["링크"] = url
            return 결과

    return {"지표": 이름, "현재값": None, "전일대비": None, "등락률": None, "링크": url, "출처": "네이버"}


def _정렬정제_OHLCV(데이터):
    if 데이터 is None or len(데이터) == 0:
        return pd.DataFrame()
    데이터 = 데이터.copy()
    try:
        데이터 = 데이터.sort_index()
    except Exception:
        pass
    return 데이터

def _야후차트OHLCV조회(심볼, 시작문자열, 종료문자열):
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
        응답 = 안전웹요청(url, params=params, timeout=12, attempts=3)
        if 응답 is None:
            return pd.DataFrame()
        payload = 응답.json()
        결과목록 = payload.get("chart", {}).get("result", [])
        if not 결과목록:
            return pd.DataFrame()
        결과 = 결과목록[0]
        timestamps = 결과.get("timestamp", []) or []
        quotes = 결과.get("indicators", {}).get("quote", [{}])[0] or {}
        if not timestamps:
            return pd.DataFrame()

        target_len = len(timestamps)
        df = pd.DataFrame({
            "날짜": pd.to_datetime(timestamps, unit="s", errors="coerce"),
            "시가": 시리즈길이맞추기(quotes.get("open", []), target_len),
            "고가": 시리즈길이맞추기(quotes.get("high", []), target_len),
            "저가": 시리즈길이맞추기(quotes.get("low", []), target_len),
            "종가": 시리즈길이맞추기(quotes.get("close", []), target_len),
            "거래량": 시리즈길이맞추기(quotes.get("volume", []), target_len),
        })
        return OHLCV데이터정리(df)
    except Exception:
        return pd.DataFrame()


def _야후종목ETF_OHLCV조회(코드, 시작문자열, 종료문자열):
    for 심볼 in 종목코드별야후심볼후보(코드):
        df = _야후차트OHLCV조회(심볼, 시작문자열, 종료문자열)
        if not df.empty:
            return df
    return pd.DataFrame()


def _시장OHLCV조회(시작문자열, 종료문자열, 코드):
    return _야후종목ETF_OHLCV조회(코드, 시작문자열, 종료문자열)


def _ETF_OHLCV조회(시작문자열, 종료문자열, 코드):
    return _야후종목ETF_OHLCV조회(코드, 시작문자열, 종료문자열)


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
        응답 = 안전웹요청(url, params=params, timeout=10, attempts=2)
        if 응답 is None:
            return pd.DataFrame()
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
    return _야후인덱스OHLCV조회(시작문자열, 종료문자열, 코드)



@st.cache_data(ttl=120)
def 야후실시간호가가져오기(심볼):
    if not 심볼:
        return None
    try:
        url = "https://query1.finance.yahoo.com/v7/finance/quote"
        응답 = 안전웹요청(url, params={"symbols": 심볼}, timeout=8, attempts=2)
        if 응답 is None:
            return None
        payload = 응답.json()
        결과목록 = payload.get("quoteResponse", {}).get("result", [])
        if not 결과목록:
            return None
        항목 = 결과목록[0]
        후보값 = [
            항목.get("regularMarketPrice"),
            항목.get("postMarketPrice"),
            항목.get("preMarketPrice"),
            항목.get("regularMarketPreviousClose"),
        ]
        for 값 in 후보값:
            값 = 안전실수변환(값)
            if 값 is not None and 값 > 0:
                return float(값)
    except Exception:
        return None
    return None


def 실시간현재가가져오기(구분, 코드):
    try:
        if 구분 == "index":
            심볼 = 야후인덱스심볼.get(str(코드))
            return 야후실시간호가가져오기(심볼)

        for 심볼 in 종목코드별야후심볼후보(코드):
            값 = 야후실시간호가가져오기(심볼)
            if 값 is not None and 값 > 0:
                return 값
    except Exception:
        return None
    return None


@st.cache_data(ttl=300)
def 최근OHLCV가져오기(구분, 코드, lookback_days=15):
    조회일수후보 = []
    for 일수 in [lookback_days, max(lookback_days, 45), max(lookback_days, 120)]:
        if 일수 not in 조회일수후보:
            조회일수후보.append(일수)

    for 조회일수 in 조회일수후보:
        종료일 = datetime.today()
        시작일 = 종료일 - timedelta(days=조회일수)
        시작문자열 = 시작일.strftime("%Y%m%d")
        종료문자열 = 종료일.strftime("%Y%m%d")

        if 구분 == "index":
            데이터 = _인덱스OHLCV조회(시작문자열, 종료문자열, 코드)
        elif 구분 in ["etf", "stock"]:
            데이터 = _야후종목ETF_OHLCV조회(코드, 시작문자열, 종료문자열)
        else:
            데이터 = _시장OHLCV조회(시작문자열, 종료문자열, 코드)

        데이터 = OHLCV데이터정리(데이터)
        if not 데이터.empty:
            return 데이터

    return pd.DataFrame()


@st.cache_data(ttl=300)
def 최근시세요약가져오기(구분, 코드, lookback_days=15):
    데이터 = 최근OHLCV가져오기(구분, 코드, lookback_days=lookback_days)
    return 최근유효OHLCV요약(데이터)


@st.cache_data(ttl=300)
def 자산현재가정보(자산명, 자산정보):
    구분 = 자산정보["구분"]
    코드 = 자산정보["코드"]
    정보 = 최근시세요약가져오기(구분, 코드, lookback_days=15)
    정보["자산명"] = 자산명
    return 정보


@st.cache_data(ttl=120)
def 종목현재가가져오기(종목코드):
    실시간값 = 실시간현재가가져오기("stock", 종목코드)
    if 실시간값 is not None and 실시간값 > 0:
        return 실시간값
    return 최근시세요약가져오기("stock", 종목코드, lookback_days=15).get("현재가")


@st.cache_data(ttl=120)
def ETF현재가가져오기(종목코드):
    실시간값 = 실시간현재가가져오기("etf", 종목코드)
    if 실시간값 is not None and 실시간값 > 0:
        return 실시간값
    return 최근시세요약가져오기("etf", 종목코드, lookback_days=15).get("현재가")


@st.cache_data(ttl=120)
def 인덱스현재가가져오기(지수코드):
    실시간값 = 실시간현재가가져오기("index", 지수코드)
    if 실시간값 is not None and 실시간값 > 0:
        return 실시간값
    return 최근시세요약가져오기("index", 지수코드, lookback_days=15).get("현재가")


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
        데이터.loc[(평균하락 == 0) & (평균상승 > 0), "RSI(14)"] = 100
        데이터.loc[(평균하락 == 0) & (평균상승 == 0), "RSI(14)"] = 50

        return 데이터
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=1800)
def 네이버시장지표목록가져오기():
    결과 = []
    표시순서 = ["USD/KRW", "국제 금", "국내 금", "WTI", "브렌트유", "두바이유"]
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
    if 종목코드 in ["069500", "229200", "471990"]:
        구분 = "etf"
    데이터 = 자산과거가격가져오기(구분, 종목코드, 개월수)
    if 데이터.empty:
        return pd.Series(dtype=float)
    return 데이터["종가"].pct_change().dropna()


# -----------------------------------
# 계산 함수
# -----------------------------------
def 포트폴리오입력집계(원본포트폴리오):
    거래원본 = 원본포트폴리오.copy()

    if "거래일자" not in 거래원본.columns and "구입일자" in 거래원본.columns:
        거래원본["거래일자"] = 거래원본["구입일자"]
    if "거래구분" not in 거래원본.columns:
        거래원본["거래구분"] = "매수"
    if "거래수량" not in 거래원본.columns and "보유수량" in 거래원본.columns:
        거래원본["거래수량"] = 거래원본["보유수량"]
    if "거래단가" not in 거래원본.columns and "매입단가" in 거래원본.columns:
        거래원본["거래단가"] = 거래원본["매입단가"]
    if "종목명" not in 거래원본.columns:
        거래원본["종목명"] = ""
    if "비고" not in 거래원본.columns:
        거래원본["비고"] = ""

    if "_입력원본순서" not in 거래원본.columns:
        거래원본["_입력원본순서"] = range(len(거래원본))
    거래원본["_원본순서"] = pd.to_numeric(거래원본["_입력원본순서"], errors="coerce").fillna(pd.Series(range(len(거래원본)), index=거래원본.index)).astype(int)
    거래원본["종목코드"] = 거래원본["종목코드"].astype(str).str.extract(r'(\d+)')[0].fillna('').str.zfill(6)
    거래원본["종목명"] = 거래원본.apply(lambda 행: 종목명자동보정(행.get("종목코드", ""), 행.get("종목명", "")), axis=1)
    거래원본["거래수량"] = pd.to_numeric(거래원본["거래수량"], errors="coerce").fillna(0).clip(lower=0)
    거래원본["거래단가"] = 거래원본["거래단가"].apply(통화문자정리)
    거래원본["거래단가"] = pd.to_numeric(거래원본["거래단가"], errors="coerce").fillna(0).clip(lower=0)
    거래원본["거래일자"] = pd.to_datetime(거래원본["거래일자"], errors="coerce")
    거래원본["거래구분"] = 거래원본["거래구분"].astype(str).str.strip()

    거래원본 = 거래원본[거래원본["종목코드"].str.len() == 6].copy()
    거래원본 = 거래원본[거래원본["거래수량"] > 0].copy()
    거래원본 = 거래원본[거래원본["거래구분"].isin(["매수", "매도"])].copy()
    거래원본 = 거래원본.sort_values(["종목코드", "거래일자", "_원본순서"], ascending=[True, True, True]).reset_index(drop=True)

    집계결과 = []

    for 종목코드, 그룹 in 거래원본.groupby("종목코드", sort=False):
        총매수수량 = 총매수금액 = 총매도수량 = 총매도금액 = 실현손익 = 0.0
        보유수량 = 보유원가 = 과잉매도수량 = 0.0
        최초매수일자 = pd.NaT
        최근거래일자 = pd.NaT
        최근종목명 = ""

        for _, 행 in 그룹.iterrows():
            거래일자 = 행["거래일자"]
            거래구분 = str(행["거래구분"]).strip()
            수량 = float(행["거래수량"])
            단가 = float(행["거래단가"])
            최근거래일자 = 거래일자
            최근종목명 = 종목명자동보정(종목코드, 행.get("종목명", ""))

            if 거래구분 == "매수":
                if pd.isna(최초매수일자):
                    최초매수일자 = 거래일자
                총매수수량 += 수량
                총매수금액 += 수량 * 단가
                보유수량 += 수량
                보유원가 += 수량 * 단가
            else:
                총매도수량 += 수량
                총매도금액 += 수량 * 단가
                평균원가 = (보유원가 / 보유수량) if 보유수량 > 0 else 0.0
                반영매도수량 = min(수량, 보유수량)
                과잉매도수량 += max(0.0, 수량 - 보유수량)
                실현손익 += (단가 - 평균원가) * 반영매도수량
                보유원가 -= 평균원가 * 반영매도수량
                보유수량 -= 반영매도수량
                보유수량 = max(0.0, 보유수량)
                보유원가 = max(0.0, 보유원가)

        매입평균단가 = (보유원가 / 보유수량) if 보유수량 > 0 else 0.0
        집계결과.append({
            "종목코드": str(종목코드).zfill(6),
            "종목명": 종목명자동보정(종목코드, 최근종목명),
            "보유수량": 보유수량,
            "투자원금": 보유원가,
            "매입평균단가": 매입평균단가,
            "매입단가": 매입평균단가,
            "총매수수량": 총매수수량,
            "총매수금액": 총매수금액,
            "총매도수량": 총매도수량,
            "총매도금액": 총매도금액,
            "실현손익": 실현손익,
            "과잉매도수량": 과잉매도수량,
            "최초매수일자": 최초매수일자,
            "최근거래일자": 최근거래일자,
        })

    집계표 = pd.DataFrame(집계결과)
    if 집계표.empty:
        return 집계표

    집계표["최초매수일자"] = pd.to_datetime(집계표["최초매수일자"], errors="coerce").dt.date
    집계표["최근거래일자"] = pd.to_datetime(집계표["최근거래일자"], errors="coerce").dt.date
    return 집계표



def 포트폴리오계산(원본포트폴리오):
    계산표 = 포트폴리오입력집계(원본포트폴리오).copy()
    계산표["종목코드"] = 계산표["종목코드"].astype(str).str.zfill(6)
    계산표["보유수량"] = pd.to_numeric(계산표["보유수량"], errors="coerce").fillna(0).clip(lower=0)
    계산표["매입단가"] = pd.to_numeric(계산표["매입단가"], errors="coerce").fillna(0).clip(lower=0)

    def 현재가조회(code):
        if code in ["069500", "229200", "471990"]:
            return ETF현재가가져오기(code)
        return 종목현재가가져오기(code)

    계산표["현재가"] = pd.to_numeric(계산표["종목코드"].apply(현재가조회), errors="coerce")
    계산표["데이터상태"] = 계산표["현재가"].apply(
        lambda 값: "정상" if pd.notna(값) and 값 > 0 else "현재가 조회 실패"
    )

    계산표["평가금액"] = 계산표.apply(
        lambda 행: 행["현재가"] * 행["보유수량"]
        if pd.notna(행["현재가"]) and 행["현재가"] > 0 else None,
        axis=1,
    )

    계산표["평가손익"] = 계산표.apply(
        lambda 행: 행["평가금액"] - 행["투자원금"]
        if pd.notna(행["평가금액"]) else None,
        axis=1,
    )

    계산표["수익률"] = 계산표.apply(
        lambda 행: (행["평가손익"] / 행["투자원금"] * 100)
        if pd.notna(행["평가손익"]) and 행["투자원금"] not in [0, None] else None,
        axis=1,
    )

    정상평가금액합계 = 계산표.loc[계산표["데이터상태"] == "정상", "평가금액"].sum(min_count=1)
    if pd.isna(정상평가금액합계) or 정상평가금액합계 == 0:
        계산표["현재비중"] = 0.0
    else:
        계산표["현재비중"] = 계산표.apply(
            lambda 행: (행["평가금액"] / 정상평가금액합계 * 100)
            if pd.notna(행["평가금액"]) else 0.0,
            axis=1,
        )

    return 계산표



def 리밸런싱계산(계산표, 목표비중사전):
    결과표 = 계산표.copy()
    총평가금액 = 결과표.loc[결과표["데이터상태"] == "정상", "평가금액"].sum()

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
        if 행.get("데이터상태") != "정상":
            return "현재가 확인 후 판단"
        수량 = int(행["주문참고수량"])
        금액 = 행["리밸런싱금액"]
        if 수량 > 0:
            return f"{abs(수량):,}주 추가 매수 검토"
        if 수량 < 0:
            return f"{abs(수량):,}주 비중 축소 검토"
        if pd.notna(행["현재가"]) and pd.notna(금액) and abs(금액) < 행["현재가"] * 0.5:
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

    정상행 = 결과표[결과표["데이터상태"] == "정상"].copy()
    if 정상행.empty:
        결과표["부족금액"] = 0.0
        결과표["추천배정금액"] = 0.0
        결과표["추천매수수량"] = 0
        결과표["실사용금액"] = 0.0
        결과표["추가매수의견"] = 결과표["데이터상태"].apply(lambda x: "현재가 확인 필요" if x != "정상" else "추가 매수 우선순위 낮음")
        return 결과표, 0.0, 추가투자금

    현재총평가금액 = 정상행["평가금액"].sum()
    목표총자산 = 현재총평가금액 + 추가투자금

    결과표["목표비중"] = 결과표["종목코드"].map(목표비중사전).fillna(0.0)
    결과표["추가투자후목표금액"] = 결과표["목표비중"] / 100 * 목표총자산
    결과표["부족금액"] = 결과표.apply(
        lambda 행: max(행["추가투자후목표금액"] - 행["평가금액"], 0)
        if 행.get("데이터상태") == "정상" and pd.notna(행.get("평가금액")) else 0.0,
        axis=1
    )

    부족금액합계 = 결과표["부족금액"].sum()
    if 부족금액합계 == 0:
        결과표["추천배정금액"] = 0.0
        결과표["추천매수수량"] = 0
        결과표["실사용금액"] = 0.0
        결과표["추가매수의견"] = 결과표["데이터상태"].apply(
            lambda x: "현재가 확인 필요" if x != "정상" else "현재 비중이 목표 수준과 유사"
        )
        return 결과표, 0.0, 추가투자금

    결과표["추천배정금액"] = 결과표["부족금액"] / 부족금액합계 * 추가투자금

    def 매수가능수량계산(행):
        if 행.get("데이터상태") != "정상":
            return 0
        if pd.isna(행["현재가"]) or 행["현재가"] in [0, None]:
            return 0
        return math.floor(행["추천배정금액"] / 행["현재가"])

    결과표["추천매수수량"] = 결과표.apply(매수가능수량계산, axis=1)
    결과표["실사용금액"] = 결과표.apply(
        lambda 행: 행["추천매수수량"] * 행["현재가"]
        if 행.get("데이터상태") == "정상" and pd.notna(행.get("현재가")) else 0.0,
        axis=1
    )

    총실사용금액 = 결과표["실사용금액"].sum()
    남는현금 = 추가투자금 - 총실사용금액

    while 남는현금 > 0:
        매수후보 = 결과표[
            (결과표["데이터상태"] == "정상") &
            (결과표["현재가"].notna()) &
            (결과표["현재가"] > 0)
        ].copy()
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
        if 행.get("데이터상태") != "정상":
            return "현재가 확인 필요"
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
        height=모바일차트높이(460, 340),
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

    이동평균설정 = []
    if len(표시데이터) >= 3:
        이동평균설정.append(("5일평균", "5일", "#f59e0b"))
    if len(표시데이터) >= 8:
        이동평균설정.append(("20일평균", "20일", "#3b82f6"))
    if len(표시데이터) >= 20:
        이동평균설정.append(("60일평균", "60일", "#6b8f5a"))
    if len(표시데이터) >= 40:
        이동평균설정.append(("120일평균", "120일", "#34d399"))
    for 컬럼, 이름, 색상 in 이동평균설정:
        if 컬럼 in 표시데이터.columns:
            그림.add_trace(
                go.Scatter(
                    x=x값,
                    y=표시데이터[컬럼],
                    mode="lines",
                    name=이름,
                    showlegend=False,
                    line=dict(color=색상, width=2),
                    hovertemplate=f"{이름}선: %{{y:,.0f}}<extra></extra>"
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
                showlegend=False,
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
        showarrow=False, font=dict(color="#e05a63", size=12), xanchor="left", yanchor="bottom", row=1, col=1,
        bgcolor="rgba(255,255,255,0.85)"
    )
    그림.add_annotation(
        x=pd.to_datetime(최저행), y=최저값,
        text=f"↘ {최저값:,.0f}({pd.to_datetime(최저행).strftime(날짜포맷)}), {최저대비상승률:+.2f}%",
        showarrow=False, font=dict(color="#4f86d9", size=12), xanchor="left", yanchor="top", row=1, col=1,
        bgcolor="rgba(255,255,255,0.85)"
    )

    범례항목 = ["캔들"] + [이름 for _, 이름, _ in 이동평균설정] + (["거래량"] if "거래량" in 표시데이터.columns else [])
    범례문구 = "범례: " + " · ".join(범례항목)

    그림.update_layout(
        title=dict(text=제목, x=0.01, xanchor="left", y=0.98, yanchor="top", font=dict(size=16)),
        height=660,
        margin=dict(l=30, r=30, t=110, b=56),
        xaxis_rangeslider_visible=False,
        showlegend=False,
        bargap=0.14,
        hovermode="x unified",
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        font=dict(color="#111827"),
        autosize=True,
        annotations=list(그림.layout.annotations) + [
            dict(
                text=범례문구,
                x=0.01,
                y=1.08,
                xref="paper",
                yref="paper",
                showarrow=False,
                align="left",
                font=dict(size=12, color="#374151"),
                bgcolor="rgba(255,255,255,0.92)",
                bordercolor="#d1d5db",
                borderwidth=1,
                borderpad=4,
            )
        ]
    )
    그림.update_yaxes(
        side="right", tickformat=",", row=1, col=1, showgrid=True, gridcolor="#d1d5db", zeroline=False,
        tickfont=dict(color="#374151", size=12), title_font=dict(color="#111827"), automargin=True
    )
    그림.update_yaxes(
        side="right", tickformat="~s", row=2, col=1, showgrid=True, gridcolor="#e5e7eb", zeroline=False,
        tickfont=dict(color="#374151", size=12), title_font=dict(color="#111827"), automargin=True
    )
    그림.update_xaxes(
        showgrid=True, gridcolor="#e5e7eb", tickfont=dict(color="#374151", size=12),
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
    작업 = 계산표.copy()
    if 작업 is None or 작업.empty:
        그림 = go.Figure()
        그림.update_layout(height=360, margin=dict(l=10, r=10, t=40, b=10), title="현재 포트폴리오 비중")
        return 그림

    작업 = 작업.copy()
    작업["평가금액"] = pd.to_numeric(작업["평가금액"], errors="coerce").fillna(0)
    작업 = 작업[작업["평가금액"] > 0].copy()
    작업 = 작업.sort_values("평가금액", ascending=False)

    그림 = go.Figure(
        go.Pie(
            labels=작업["종목명"],
            values=작업["평가금액"],
            hole=0.52,
            sort=False,
            direction="clockwise",
            textinfo="percent",
            textposition="inside",
            insidetextorientation="auto",
            hovertemplate="%{label}<br>평가금액: %{value:,.0f}원<br>비중: %{percent}<extra></extra>",
        )
    )
    그림.update_traces(marker=dict(line=dict(color="#0b1220", width=1.2)))
    그림.update_layout(
        title=dict(text="현재 포트폴리오 비중", x=0.02, xanchor="left", y=0.97),
        height=430,
        margin=dict(l=10, r=10, t=52, b=10),
        legend=dict(orientation="v", yanchor="top", y=0.98, xanchor="left", x=1.02, font=dict(size=13)),
    )
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
        부족문구 = "현재 데이터 길이만으로는 신뢰도 있는 해석을 제시하기 어렵습니다. 최소 20거래일 이상 확보한 뒤 추세와 RSI를 함께 보시는 편이 좋습니다."
        return {
            "ChatGPT": {
                "한줄요약": "데이터가 충분하지 않아 차트 해석이 제한됩니다.",
                "현재신호": 부족문구,
                "근거": ["시계열 길이가 짧음", "이동평균선 비교 제한", "RSI 해석 신뢰도 낮음"],
                "리스크": ["성급한 추세 판단 가능성", "단기 변동성 과대해석 가능성"],
                "보유자관점": "기존 보유자라면 추가 대응보다 데이터 축적을 먼저 확인하는 편이 좋습니다.",
                "신규진입관점": "신규 진입은 조금 더 긴 시계열 확보 후 검토하는 것이 바람직합니다.",
            },
            "Gemini": {
                "한줄요약": "데이터 부족으로 빠른 판단의 신뢰도가 낮습니다.",
                "현재신호": 부족문구,
                "근거": ["최근 데이터 부족", "모멘텀 확인 제한", "추세 지속성 판단 어려움"],
                "리스크": ["짧은 데이터에 따른 오판 가능성", "반등·하락 신호 왜곡 가능성"],
                "보유자관점": "보유 중이라면 섣부른 비중 조정보다 추세 확인이 우선입니다.",
                "신규진입관점": "신규 진입은 확인 가능한 데이터가 더 쌓인 뒤가 좋습니다.",
            },
            "Claude": {
                "한줄요약": "현재는 해석보다 관찰이 우선인 구간입니다.",
                "현재신호": 부족문구,
                "근거": ["데이터 길이 부족", "추세·RSI 동시 검증 어려움", "신호 지속성 확인 제한"],
                "리스크": ["짧은 구간을 추세로 오해할 수 있음", "가격 신호만 보고 대응할 위험"],
                "보유자관점": "기존 보유자라면 성급한 판단보다 추가 데이터 확인이 더 신중합니다.",
                "신규진입관점": "신규 진입은 최소 20거래일 이상 확보 후 검토하는 편이 적절합니다.",
            },
        }

    최신 = 데이터.iloc[-1]
    종가 = float(최신["종가"])
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

    지지저항문구 = "20일선 부근 공방 여부를 확인할 필요가 있습니다."
    if pd.notna(ma20):
        if 종가 > ma20:
            지지저항문구 = "단기적으로는 20일선 위에 있어 지지력은 아직 유지되는 편입니다."
        else:
            지지저항문구 = "20일선 아래에 있어 단기 반등이 나와도 저항 확인이 필요합니다."

    거래량문구 = "거래량 정보가 충분치 않다면 가격 신호만으로 성급히 판단하지 않는 편이 좋습니다."

    방향성문구 = "양호" if 추세 == "상승 추세" else "둔화 또는 중립"
    chatgpt_signal = f"{자산명}은 현재 {추세}로 해석됩니다. 종가와 20일·60일 이동평균선의 배열을 보면 방향성은 {방향성문구}합니다. RSI는 {rsi해석}이며, {지지저항문구}"
    gemini_signal = f"기술적으로 보면 {자산명}의 핵심 포인트는 이동평균선 정렬과 RSI입니다. 현재 판독은 {추세}, RSI는 {rsi해석}입니다. {변동문구}"
    claude_signal = f"종가와 이동평균선의 위치를 기준으로 보면 {추세}에 가깝고, RSI는 {rsi해석} 구간으로 읽힙니다. {지지저항문구}"

    return {
        "ChatGPT": {
            "한줄요약": f"{자산명}은 현재 {추세}로 보되, 단기 추격 대응보다 지지 확인이 우선입니다.",
            "현재신호": chatgpt_signal,
            "근거": [
                "종가와 20일·60일 이동평균선 배열",
                f"RSI는 {rsi해석} 수준",
                변동문구,
            ],
            "리스크": [
                "20일선 이탈이 이어지면 단기 약세가 재확대될 수 있음",
                "거래량 확인 없이 가격만 보고 대응하면 오판 가능성",
            ],
            "보유자관점": "기존 보유자라면 단기 추격 매수보다 지지선 유지 여부를 먼저 확인하는 접근이 더 안정적입니다.",
            "신규진입관점": "신규 진입은 한 번에 들어가기보다 분할 접근이 더 적절합니다.",
        },
        "Gemini": {
            "한줄요약": f"{자산명}은 현재 빠른 판단 기준으로 {추세} 쪽 신호가 우세합니다.",
            "현재신호": gemini_signal,
            "근거": [
                "이동평균선 정렬 상태",
                f"RSI 판독 결과는 {rsi해석}",
                "최근 20거래일 변동률 반영",
            ],
            "리스크": [
                "20일선 회복 실패 시 반등 지속성이 약해질 수 있음",
                "최근 변동성이 큰 구간이면 신호 왜곡 가능성 존재",
            ],
            "보유자관점": "보유 중이라면 단기 추세 유지 여부와 20일선 회복·이탈을 함께 보는 것이 좋습니다.",
            "신규진입관점": "신규 진입은 추세 확인 이후 소규모로 시작하는 편이 유리합니다.",
        },
        "Claude": {
            "한줄요약": f"{자산명}은 현재 {추세} 성격이 우세하지만, 이를 곧바로 강한 추세 전환으로 단정하기에는 아직 확인할 요소가 남아 있습니다.",
            "현재신호": claude_signal,
            "근거": [
                "종가의 이동평균선 대비 위치",
                f"RSI의 과열·침체 여부는 {rsi해석}",
                변동문구,
            ],
            "리스크": [
                "단기 반등이 나오더라도 20일선 안착이 실패하면 다시 변동성이 커질 수 있음",
                거래량문구,
            ],
            "보유자관점": "기존 보유자라면 성급한 비중 확대보다 지지선 유지 여부를 먼저 확인하는 접근이 더 신중합니다.",
            "신규진입관점": "신규 진입은 추세 확인 이후 분할 접근이 더 적절합니다.",
        },
    }


def 분석카드표시(분석데이터):
    st.markdown(f"### 한줄 요약")
    st.info(분석데이터["한줄요약"])

    col1, col2 = st.columns([1.6, 1])
    with col1:
        st.markdown("### 현재 신호")
        st.write(분석데이터["현재신호"])

        st.markdown("### 근거")
        for 항목 in 분석데이터["근거"]:
            st.markdown(f"- {항목}")

    with col2:
        st.markdown("### 주의할 리스크")
        for 항목 in 분석데이터["리스크"]:
            st.markdown(f"- {항목}")

    st.markdown("### 보유자 관점")
    st.write(분석데이터["보유자관점"])

    st.markdown("### 신규 진입 관점")
    st.write(분석데이터["신규진입관점"])


def 종목거래이력표생성(거래df, 종목코드=None):
    작업 = 거래이력정규화(거래df)
    if 작업.empty:
        return pd.DataFrame(columns=["거래일자", "종목코드", "종목명", "거래구분", "거래수량", "거래단가", "거래금액", "누적보유수량", "운용사", "비고"])

    if "_입력원본순서" not in 작업.columns:
        작업["_입력원본순서"] = range(len(작업))
    작업["_원본순서"] = pd.to_numeric(작업["_입력원본순서"], errors="coerce").fillna(pd.Series(range(len(작업)), index=작업.index)).astype(int)

    if 종목코드:
        코드 = str(종목코드).zfill(6)
        작업 = 작업[작업["종목코드"].astype(str).str.zfill(6) == 코드].copy()
        if 작업.empty:
            return pd.DataFrame(columns=["거래일자", "종목코드", "종목명", "거래구분", "거래수량", "거래단가", "거래금액", "누적보유수량", "운용사", "비고"])

    작업["거래일자"] = pd.to_datetime(작업["거래일자"], errors="coerce")
    작업["거래수량"] = pd.to_numeric(작업["거래수량"], errors="coerce").fillna(0)
    작업["거래단가"] = pd.to_numeric(작업["거래단가"], errors="coerce").fillna(0)

    # 누적보유수량 계산도 입력 원본 순서를 기준으로 수행
    작업 = 작업.sort_values(["_원본순서"], ascending=[True], na_position="last", kind="stable").reset_index(drop=True)
    작업["거래금액"] = 작업["거래수량"] * 작업["거래단가"]
    작업["signed_qty"] = 작업["거래수량"].where(작업["거래구분"] == "매수", -작업["거래수량"])
    작업["누적보유수량"] = 작업.groupby("종목코드", sort=False)["signed_qty"].cumsum()
    작업["거래일자"] = 작업["거래일자"].dt.date
    return 작업[["거래일자", "종목코드", "종목명", "거래구분", "거래수량", "거래단가", "거래금액", "누적보유수량", "운용사", "비고"]]


def 거래기록표시용서식(df):
    if df is None or df.empty:
        return df
    return df.style.format({
        "거래수량": 안전정수포맷,
        "거래단가": 안전정수포맷,
        "거래금액": 안전정수포맷,
        "누적보유수량": 안전정수포맷,
    }).map(lambda v: "color: #dc2626; font-weight: 700;" if v == "매수" else "color: #2563eb; font-weight: 700;", subset=["거래구분"])


def 보유종목상세해설생성(종목명, 가격데이터, 포트폴리오행=None):
    if 가격데이터 is None or 가격데이터.empty:
        return []

    최신 = 가격데이터.iloc[-1]
    이전 = 가격데이터.iloc[-2] if len(가격데이터) >= 2 else 최신
    종가 = float(최신["종가"])
    시가 = float(최신.get("시가", 종가))
    고가 = float(최신.get("고가", 종가))
    저가 = float(최신.get("저가", 종가))
    거래량 = float(최신.get("거래량", 0)) if pd.notna(최신.get("거래량")) else 0
    ma5 = 최신.get("5일평균")
    ma20 = 최신.get("20일평균")
    ma60 = 최신.get("60일평균")
    rsi = 최신.get("RSI(14)")

    최근20 = 가격데이터.tail(20).copy()
    최근20고가 = float(최근20["고가"].max()) if not 최근20.empty else 고가
    최근20저가 = float(최근20["저가"].min()) if not 최근20.empty else 저가
    평균거래량20 = float(최근20["거래량"].mean()) if "거래량" in 최근20.columns and len(최근20) > 0 else 0
    전일대비 = 종가 - float(이전["종가"])
    전일등락률 = (전일대비 / float(이전["종가"]) * 100) if float(이전["종가"]) != 0 else 0
    고점대비 = ((종가 / 최근20고가) - 1) * 100 if 최근20고가 else 0
    저점대비 = ((종가 / 최근20저가) - 1) * 100 if 최근20저가 else 0
    거래량배수 = (거래량 / 평균거래량20) if 평균거래량20 not in [0, None] else None

    if pd.notna(ma5) and pd.notna(ma20) and pd.notna(ma60):
        if 종가 > ma5 > ma20 > ma60:
            배열판정 = "정배열에 가까운 상승 구조"
        elif 종가 < ma5 < ma20 < ma60:
            배열판정 = "역배열에 가까운 하락 구조"
        elif 종가 >= ma20 and ma20 >= ma60:
            배열판정 = "중기 기준으로는 버티는 구조"
        else:
            배열판정 = "추세 전환 확인이 더 필요한 혼조 구조"
    else:
        배열판정 = "이동평균선 데이터가 충분하지 않아 배열 판정이 제한됩니다"

    if pd.isna(rsi):
        rsi판정 = "판정 제한"
    elif rsi >= 70:
        rsi판정 = "단기 과열권"
    elif rsi <= 30:
        rsi판정 = "단기 침체권"
    elif rsi < 45:
        rsi판정 = "약세 우위"
    else:
        rsi판정 = "중립 또는 완만한 회복 구간"

    최신캔들 = 캔들분석결과가져오기(가격데이터, 가격데이터.index[-1], 최신)
    문장목록 = [
        f"최근 종가는 **{종가:,.0f}원**이며 전일 대비 **{전일대비:,.0f}원 ({전일등락률:+.2f}%)** 움직였습니다.",
        f"최신 캔들은 **{최신캔들['캔들유형']} / {최신캔들['방향']}**으로 해석할 수 있고, 당일 가격 범위는 **{저가:,.0f}원 ~ {고가:,.0f}원**입니다.",
        f"이동평균선 기준으로는 **{배열판정}**입니다. 5일선 {숫자표시(ma5, 2)}, 20일선 {숫자표시(ma20, 2)}, 60일선 {숫자표시(ma60, 2)} 수준입니다.",
        f"최근 20거래일 기준 고점은 **{최근20고가:,.0f}원**, 저점은 **{최근20저가:,.0f}원**이며 현재가는 고점 대비 **{고점대비:.2f}%**, 저점 대비 **{저점대비:.2f}%** 위치입니다.",
        f"RSI(14)는 **{숫자표시(rsi, 2)}**로 **{rsi판정}**으로 읽을 수 있습니다.",
    ]

    if 거래량배수 is not None:
        문장목록.append(f"최근 거래량은 **{숫자표시(거래량)}주**로 20일 평균 대비 **{거래량배수:.2f}배** 수준입니다.")

    if 포트폴리오행 is not None and len(포트폴리오행) > 0:
        행 = 포트폴리오행.iloc[0]
        문장목록.append(
            f"Jone님 계좌 기준 현재 **보유수량 {숫자표시(행.get('보유수량'))}주**, 매입 평균단가는 **{금액표시(행.get('매입평균단가'))}**, 평가손익은 **{손익문자열(행.get('평가손익'))}원**입니다."
        )

    return 문장목록


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


def 기술분석진단계산(데이터):
    if 데이터 is None or 데이터.empty:
        return {
            "요약문장": ["가격 데이터가 없어 기술적 분석을 계산할 수 없습니다."],
            "핵심표": pd.DataFrame(columns=["항목", "값", "판정"]),
            "레벨표": pd.DataFrame(columns=["항목", "가격", "설명"]),
            "추세배열": "판정 불가",
            "지지": None,
            "저항": None,
        }

    최신 = 데이터.iloc[-1]
    종가 = float(최신.get("종가", 0) or 0)
    ma5 = float(최신.get("5일평균", 0) or 0)
    ma20 = float(최신.get("20일평균", 0) or 0)
    ma60 = float(최신.get("60일평균", 0) or 0)
    ma120 = float(최신.get("120일평균", 0) or 0)
    rsi = 최신.get("RSI(14)")
    거래량 = float(최신.get("거래량", 0) or 0)

    최근20 = 데이터.tail(min(len(데이터), 20)).copy()
    최근60 = 데이터.tail(min(len(데이터), 60)).copy()
    평균거래량20 = float(최근20["거래량"].mean()) if "거래량" in 최근20.columns and not 최근20.empty else 0
    거래량배수 = (거래량 / 평균거래량20) if 평균거래량20 not in [0, None] else None
    변동성20 = float(최근20["종가"].pct_change().std() * 100) if len(최근20) >= 2 else None

    지지 = float(최근20["저가"].min()) if not 최근20.empty else None
    저항 = float(최근20["고가"].max()) if not 최근20.empty else None
    장기지지 = float(최근60["저가"].min()) if not 최근60.empty else None
    장기저항 = float(최근60["고가"].max()) if not 최근60.empty else None

    if 종가 > ma20 > ma60 > 0:
        추세배열 = "상승 배열"
    elif 종가 > ma20 and ma20 > 0:
        추세배열 = "단기 우위"
    elif 종가 < ma20 < ma60 and ma20 > 0 and ma60 > 0:
        추세배열 = "하락 배열"
    else:
        추세배열 = "혼조"

    if pd.isna(rsi):
        rsi판정 = "판정 제한"
    elif rsi >= 70:
        rsi판정 = "과열"
    elif rsi <= 30:
        rsi판정 = "강한 침체"
    elif rsi <= 40:
        rsi판정 = "저점권 관심"
    else:
        rsi판정 = "중립"

    if 거래량배수 is None:
        거래량판정 = "판정 제한"
    elif 거래량배수 >= 1.8:
        거래량판정 = "강한 유입"
    elif 거래량배수 >= 1.2:
        거래량판정 = "증가"
    elif 거래량배수 >= 0.8:
        거래량판정 = "보통"
    else:
        거래량판정 = "감소"

    지지괴리 = ((종가 / 지지) - 1) * 100 if 지지 not in [0, None] else None
    저항괴리 = ((저항 / 종가) - 1) * 100 if 저항 not in [0, None] and 종가 != 0 else None
    ma20괴리 = ((종가 / ma20) - 1) * 100 if ma20 not in [0, None] else None
    ma60괴리 = ((종가 / ma60) - 1) * 100 if ma60 not in [0, None] else None

    요약문장 = []
    요약문장.append(f"현재 배열은 {추세배열}이며, 종가는 20일선 대비 {증감문자열(ma20괴리, '%') if ma20괴리 is not None else '-'} 수준입니다.")
    if 지지괴리 is not None and 저항괴리 is not None:
        요약문장.append(f"최근 20일 기준 지지선까지는 {지지괴리:.2f}%, 저항선까지는 {저항괴리:.2f}% 거리입니다.")
    요약문장.append(f"RSI는 {숫자표시(rsi, 2) if pd.notna(rsi) else '-'}로 {rsi판정}, 거래량은 20일 평균 대비 {숫자표시(거래량배수, 2) if 거래량배수 is not None else '-'}배로 {거래량판정}입니다.")
    if 변동성20 is not None:
        요약문장.append(f"최근 20거래일 일간 변동성은 {변동성20:.2f}%입니다.")

    핵심표 = pd.DataFrame([
        {"항목": "추세 배열", "값": 추세배열, "판정": "핵심"},
        {"항목": "종가 vs 20일선", "값": 증감문자열(ma20괴리, "%") if ma20괴리 is not None else "-", "판정": "상회" if ma20괴리 is not None and ma20괴리 >= 0 else "하회"},
        {"항목": "종가 vs 60일선", "값": 증감문자열(ma60괴리, "%") if ma60괴리 is not None else "-", "판정": "상회" if ma60괴리 is not None and ma60괴리 >= 0 else "하회"},
        {"항목": "RSI(14)", "값": 숫자표시(rsi, 2), "판정": rsi판정},
        {"항목": "거래량 배수", "값": f"{거래량배수:.2f}배" if 거래량배수 is not None else "-", "판정": 거래량판정},
        {"항목": "20일 변동성", "값": f"{변동성20:.2f}%" if 변동성20 is not None else "-", "판정": "참고"},
    ])

    레벨표 = pd.DataFrame([
        {"항목": "단기 지지", "가격": 지지, "설명": "최근 20일 저가 기준"},
        {"항목": "단기 저항", "가격": 저항, "설명": "최근 20일 고가 기준"},
        {"항목": "중기 지지", "가격": 장기지지, "설명": "최근 60일 저가 기준"},
        {"항목": "중기 저항", "가격": 장기저항, "설명": "최근 60일 고가 기준"},
        {"항목": "5일선", "가격": ma5 if ma5 > 0 else None, "설명": "단기 추세"},
        {"항목": "20일선", "가격": ma20 if ma20 > 0 else None, "설명": "기준선"},
        {"항목": "60일선", "가격": ma60 if ma60 > 0 else None, "설명": "중기 추세"},
        {"항목": "120일선", "가격": ma120 if ma120 > 0 else None, "설명": "장기 추세"},
    ])

    return {
        "요약문장": 요약문장,
        "핵심표": 핵심표,
        "레벨표": 레벨표,
        "추세배열": 추세배열,
        "지지": 지지,
        "저항": 저항,
    }




def 자동판정기준표():
    return pd.DataFrame([
        {"구분": "추세", "기준": "5개 조건", "설명": "종가≥5일선, 종가≥20일선, 종가≥60일선, 5일선≥20일선, 20일선≥60일선의 충족 개수(0~5점)"},
        {"구분": "가격 위치", "기준": "최근 20거래일", "설명": "최근 20거래일 고가·저가 범위에서 현재 종가가 어디에 있는지 백분율로 계산"},
        {"구분": "RSI(14)", "기준": "과매도/과열", "설명": "28 이하 강한 과매도, 38 이하 과매도 관심, 68 이상 과열 경계, 78 이상 강한 과열로 해석"},
        {"구분": "거래량", "기준": "20일 평균 대비", "설명": "1.8배 이상 강한 거래, 1.2배 이상 유효한 확인 신호, 0.7배 이하는 힘이 약한 구간으로 해석"},
        {"구분": "점수 합계", "기준": "복합 점수", "설명": "추세·위치·RSI·거래량·당일 흐름 점수를 합산해 최종 판정을 산출"},
        {"구분": "최종 판정", "기준": "7단계", "설명": "강매수 → 분할매수 → 반등매수 → 보유 → 관망 → 비중축소 → 차익실현 순으로 변환"},
    ])



def 고급매매코멘트생성(종목명, 가격데이터, 포트폴리오행=None):
    기준표 = 자동판정기준표()

    if 가격데이터 is None or 가격데이터.empty or len(가격데이터) < 20:
        return {
            "판정": "판정 보류",
            "실행": "데이터 보강 필요",
            "강도": 0,
            "핵심문구": "최근 20거래일 이상 데이터가 부족해 자동 매매 코멘트를 보류합니다.",
            "세부코멘트": ["가격 데이터가 충분하지 않아 추세·위치·모멘텀을 함께 판정하기 어렵습니다."],
            "근거": ["최소 20거래일 이상 데이터가 있어야 20일 범위, 이동평균선, RSI 해석이 가능합니다."],
            "근거표": pd.DataFrame([
                {"항목": "데이터 길이", "현재": len(가격데이터) if 가격데이터 is not None else 0, "기준": "20거래일 이상", "판정": "부족"}
            ]),
            "기준표": 기준표,
            "위험문구": "데이터 부족 상태에서는 자동 코멘트보다 직접 차트 확인이 우선입니다.",
            "추세판정": "판정 제한",
            "위치판정": "판정 제한",
            "RSI판정": "판정 제한",
            "거래량판정": "판정 제한",
            "총점": None,
        }

    최신 = 가격데이터.iloc[-1]
    이전 = 가격데이터.iloc[-2] if len(가격데이터) >= 2 else 최신
    종가 = float(최신["종가"])
    전일종가 = float(이전["종가"]) if pd.notna(이전.get("종가")) else 종가
    ma5 = float(최신["5일평균"]) if pd.notna(최신.get("5일평균")) else None
    ma20 = float(최신["20일평균"]) if pd.notna(최신.get("20일평균")) else None
    ma60 = float(최신["60일평균"]) if pd.notna(최신.get("60일평균")) else None
    rsi = float(최신["RSI(14)"]) if pd.notna(최신.get("RSI(14)")) else None
    거래량 = float(최신.get("거래량", 0)) if pd.notna(최신.get("거래량")) else 0

    최근20 = 가격데이터.tail(20).copy()
    최근20고가 = float(최근20["고가"].max()) if not 최근20.empty else 종가
    최근20저가 = float(최근20["저가"].min()) if not 최근20.empty else 종가
    평균거래량20 = float(최근20["거래량"].mean()) if "거래량" in 최근20.columns and len(최근20) > 0 else 0
    거래량배수 = (거래량 / 평균거래량20) if 평균거래량20 not in [0, None] else None
    전일등락률 = ((종가 - 전일종가) / 전일종가 * 100) if 전일종가 not in [0, None] else 0

    if 최근20고가 > 최근20저가:
        위치백분율 = ((종가 - 최근20저가) / (최근20고가 - 최근20저가)) * 100
    else:
        위치백분율 = 50.0

    추세점수 = 0
    if ma5 is not None and 종가 >= ma5:
        추세점수 += 1
    if ma20 is not None and 종가 >= ma20:
        추세점수 += 1
    if ma60 is not None and 종가 >= ma60:
        추세점수 += 1
    if ma5 is not None and ma20 is not None and ma5 >= ma20:
        추세점수 += 1
    if ma20 is not None and ma60 is not None and ma20 >= ma60:
        추세점수 += 1

    if 추세점수 >= 5:
        추세판정 = "강한 상승 추세"
    elif 추세점수 == 4:
        추세판정 = "상승 우위"
    elif 추세점수 == 3:
        추세판정 = "중립 이상"
    elif 추세점수 == 2:
        추세판정 = "약세 압력"
    else:
        추세판정 = "하락 추세"

    if rsi is None:
        rsi판정 = "판정 제한"
        rsi점수 = 0.0
    elif rsi <= 28:
        rsi판정 = "강한 과매도"
        rsi점수 = 2.0
    elif rsi <= 38:
        rsi판정 = "과매도 관심"
        rsi점수 = 1.2
    elif rsi < 48:
        rsi판정 = "약세권"
        rsi점수 = 0.2
    elif rsi < 68:
        rsi판정 = "중립권"
        rsi점수 = 0.5
    elif rsi < 78:
        rsi판정 = "과열 경계"
        rsi점수 = -1.0
    else:
        rsi판정 = "강한 과열"
        rsi점수 = -2.0

    if 위치백분율 <= 15:
        위치판정 = "저점권"
        위치점수 = 2.0
    elif 위치백분율 <= 30:
        위치판정 = "저점 근처"
        위치점수 = 1.2
    elif 위치백분율 < 70:
        위치판정 = "중립 구간"
        위치점수 = 0.0
    elif 위치백분율 < 85:
        위치판정 = "고점 근처"
        위치점수 = -1.2
    else:
        위치판정 = "고점권"
        위치점수 = -2.0

    if 거래량배수 is None:
        거래량판정 = "판정 제한"
        거래량점수 = 0.0
    elif 거래량배수 >= 1.8:
        거래량판정 = "강한 거래량"
        거래량점수 = 1.0
    elif 거래량배수 >= 1.2:
        거래량판정 = "평균 이상"
        거래량점수 = 0.5
    elif 거래량배수 <= 0.7:
        거래량판정 = "평균 이하"
        거래량점수 = -0.5
    else:
        거래량판정 = "보통"
        거래량점수 = 0.0

    추세가중점수 = (추세점수 - 2.5) * 0.8
    당일흐름점수 = 0.0
    if 전일등락률 >= 2.0 and ma5 is not None and 종가 >= ma5:
        당일흐름점수 = 0.5
    elif 전일등락률 <= -2.5 and 추세점수 <= 2:
        당일흐름점수 = -0.5

    총점 = 추세가중점수 + 위치점수 + rsi점수 + 거래량점수 + 당일흐름점수

    보유수량 = 0.0
    평가손익 = None
    수익률 = None
    매입평균단가 = None
    if 포트폴리오행 is not None and len(포트폴리오행) > 0:
        행 = 포트폴리오행.iloc[0]
        보유수량 = float(행.get("보유수량", 0) or 0)
        평가손익 = 행.get("평가손익")
        수익률 = 행.get("수익률")
        매입평균단가 = 행.get("매입평균단가")

    if 수익률 is not None and not pd.isna(수익률):
        if 수익률 >= 10 and 위치백분율 >= 70:
            총점 -= 0.6
        elif 수익률 <= -8 and 위치백분율 <= 30 and rsi is not None and rsi <= 38:
            총점 += 0.4

    판정 = "관망"
    실행 = "방향 확인"
    핵심문구 = f"{종목명}은 현재 추세·가격 위치·모멘텀이 혼재되어 있어 성급한 진입보다 다음 확인 신호가 더 중요합니다."

    if 총점 >= 3.2 and 위치백분율 <= 35 and (rsi is None or rsi <= 55):
        판정 = "강매수"
        실행 = "2~3회 공격적 분할매수"
        핵심문구 = f"{종목명}은 복합 점수가 높고 가격도 고점권이 아니어서 **강매수 후보**로 해석됩니다."
    elif 총점 >= 1.8 and 위치백분율 <= 50 and (rsi is None or rsi <= 60):
        판정 = "분할매수"
        실행 = "1~3회 나눠 매수"
        핵심문구 = f"{종목명}은 추세 훼손이 크지 않은 가운데 가격 부담이 낮아 **분할매수**가 가능한 구간입니다."
    elif (추세점수 <= 2 and 위치백분율 <= 30 and rsi is not None and rsi <= 38) or (총점 >= 0.8 and 위치백분율 <= 25 and rsi is not None and rsi <= 35):
        판정 = "반등매수"
        실행 = "소액 시범매수"
        핵심문구 = f"{종목명}은 아직 추세형 매수보다 **기술적 반등 대응** 성격이 더 강한 구간입니다."
    elif 총점 <= -3.0 and 위치백분율 >= 75 and rsi is not None and rsi >= 68:
        판정 = "차익실현"
        실행 = "분할매도"
        핵심문구 = f"{종목명}은 최근 가격 위치와 RSI가 과열권에 가까워 **차익실현 우선 구간**으로 해석됩니다."
    elif 총점 <= -1.8 and 위치백분율 >= 60:
        판정 = "비중축소"
        실행 = "반등 시 비중 축소"
        핵심문구 = f"{종목명}은 상승 탄력이 약한데 가격 부담이 남아 있어 **비중축소** 쪽이 더 유리합니다."
    elif 총점 >= 0.5 and 추세점수 >= 3 and 위치백분율 < 80:
        판정 = "보유"
        실행 = "기존 보유 유지"
        핵심문구 = f"{종목명}은 현재 복합 점수가 중립 이상이어서 신규 추격보다 **기존 보유 유지**가 자연스럽습니다."

    강도 = int(max(5, min(95, round((총점 + 4) / 8 * 100))))

    근거 = [
        f"추세 점수: {추세점수}/5 → {추세판정}",
        f"가격 위치: 최근 20거래일 범위의 {위치백분율:.1f}% → {위치판정}",
        f"RSI(14): {rsi:.2f} → {rsi판정}" if rsi is not None else "RSI(14): 데이터 부족",
        f"거래량: 20일 평균 대비 {거래량배수:.2f}배 → {거래량판정}" if 거래량배수 is not None else "거래량: 데이터 부족",
        f"복합 총점: {총점:.2f}점",
    ]

    세부코멘트 = [
        "이번 자동 판정은 한 가지 지표가 아니라 추세·위치·RSI·거래량을 합산해 계산합니다.",
        f"현재는 추세가 **{추세판정}**, 가격 위치는 **{위치판정}**, RSI는 **{rsi판정}**으로 해석됩니다.",
    ]

    if 판정 in ["강매수", "분할매수"]:
        세부코멘트.append("추세가 완전히 무너지지 않은 상태에서 가격 부담이 낮아 한 번에 몰아 사기보다 2~3회 분할 접근이 적절합니다.")
        if ma20 is not None:
            세부코멘트.append(f"단기 확인선은 20일선 부근({ma20:,.0f}원 전후)이며, 이 구간을 지키는지 함께 보시는 것이 좋습니다.")
    elif 판정 == "반등매수":
        세부코멘트.append("추세 자체는 아직 약하므로 중장기 추세 매수보다 기술적 반등 대응 관점으로 보는 편이 안전합니다.")
        세부코멘트.append("직전 저점 이탈 시에는 빠르게 재점검하는 방식이 필요합니다.")
    elif 판정 in ["차익실현", "비중축소"]:
        세부코멘트.append("고점권에서는 조금만 흔들려도 변동성이 커질 수 있어 한 번에 전량보다 분할 대응이 더 안정적입니다.")
        if ma5 is not None:
            세부코멘트.append(f"단기 이탈 확인선은 5일선 부근({ma5:,.0f}원 전후)으로 보시면 됩니다.")
    elif 판정 == "보유":
        세부코멘트.append("신규 추격 매수보다 현재 보유분을 유지하면서 다음 돌파 또는 눌림 신호를 기다리는 편이 더 자연스럽습니다.")
    else:
        세부코멘트.append("추세와 가격 위치가 동시에 유리하지 않아 다음 방향이 정리될 때까지 관찰 비중을 높이는 편이 좋습니다.")
        if ma20 is not None and ma60 is not None:
            세부코멘트.append(f"20일선({ma20:,.0f}원)과 60일선({ma60:,.0f}원) 사이 관계가 더 분명해지는지를 확인해 보시면 좋습니다.")

    if 보유수량 > 0:
        세부코멘트.append(f"현재 보유수량은 {숫자표시(보유수량)}주입니다.")
        if 매입평균단가 is not None and not pd.isna(매입평균단가):
            세부코멘트.append(f"평균단가는 {금액표시(매입평균단가)}입니다.")
        if 평가손익 is not None and not pd.isna(평가손익):
            세부코멘트.append(f"현재 평가손익은 {손익문자열(평가손익)}원입니다.")
        if 수익률 is not None and not pd.isna(수익률):
            세부코멘트.append(f"현재 수익률은 {수익률:.2f}%입니다.")

    위험문구 = "자동 판정은 규칙 기반 참고 의견입니다. 공시, 실적, 업황, 거시 변수 같은 비가격 정보는 반드시 별도로 확인하셔야 합니다."
    if abs(전일등락률) >= 4:
        위험문구 = f"당일 변동률이 {전일등락률:+.2f}%로 커서 하루 움직임이 총점에 과하게 반영될 수 있습니다. 단일 일봉만 보고 추격 대응하는 것은 피하는 편이 좋습니다."

    근거표 = pd.DataFrame([
        {"항목": "추세", "현재": 추세판정, "기준": "5개 조건 충족 수", "판정": f"{추세점수}/5"},
        {"항목": "가격 위치", "현재": f"{위치백분율:.1f}%", "기준": "20일 범위 내 위치", "판정": 위치판정},
        {"항목": "RSI(14)", "현재": f"{rsi:.2f}" if rsi is not None else "-", "기준": "28↓ 강한 과매도 / 68↑ 과열 경계", "판정": rsi판정},
        {"항목": "거래량 배수", "현재": f"{거래량배수:.2f}배" if 거래량배수 is not None else "-", "기준": "1.2배↑ 유효 / 0.7배↓ 약함", "판정": 거래량판정},
        {"항목": "당일 등락률", "현재": f"{전일등락률:+.2f}%", "기준": "+2% 이상 가점 / -2.5% 이하 감점", "판정": f"{당일흐름점수:+.1f}점"},
        {"항목": "복합 총점", "현재": f"{총점:.2f}점", "기준": "추세+위치+RSI+거래량+당일 흐름", "판정": 판정},
        {"항목": "실행 방향", "현재": 실행, "기준": "고급형 7단계", "판정": f"강도 {강도}%"},
    ])

    return {
        "판정": 판정,
        "실행": 실행,
        "강도": 강도,
        "핵심문구": 핵심문구,
        "세부코멘트": 세부코멘트,
        "근거": 근거,
        "근거표": 근거표,
        "기준표": 기준표,
        "위험문구": 위험문구,
        "추세판정": 추세판정,
        "위치판정": 위치판정,
        "RSI판정": rsi판정,
        "거래량판정": 거래량판정,
        "총점": 총점,
    }


def 자동판정배지HTML(판정, 실행, 강도):
    색상맵 = {
        "강매수": "#15803d",
        "분할매수": "#16a34a",
        "반등매수": "#65a30d",
        "보유": "#2563eb",
        "관망": "#6b7280",
        "비중축소": "#d97706",
        "차익실현": "#dc2626",
        "판정 보류": "#6b7280",
    }
    배경 = 색상맵.get(판정, "#334155")
    return f"""
    <div style="background:{배경}; border-radius:20px; padding:16px 18px; color:white; margin:8px 0 14px 0; box-shadow:0 10px 24px rgba(15,23,42,0.18);">
        <div style="font-size:0.95rem; opacity:0.9;">자동 매수·매도 판단</div>
        <div style="font-size:1.6rem; font-weight:800; margin-top:4px;">{판정}</div>
        <div style="font-size:1rem; margin-top:6px;">실행 방향: {실행}</div>
        <div style="font-size:0.92rem; margin-top:6px; opacity:0.95;">신호 강도: {강도}/100</div>
    </div>
    """

def 현재테마기본값():
    try:
        return st.get_option("theme.base") or "dark"
    except Exception:
        return "dark"


def 대시보드스타일적용():
    테마 = 현재테마기본값()
    if 테마 == "light":
        카드배경 = "#ffffff"
        카드테두리 = "#e5e7eb"
        카드그림자 = "0 3px 10px rgba(15, 23, 42, 0.05)"
        라벨색 = "#475569"
        제목색 = "#111827"
        메타색 = "#64748b"
        보유행배경 = "#f8fafc"
        보유행테두리 = "#e2e8f0"
    else:
        카드배경 = "#111827"
        카드테두리 = "#334155"
        카드그림자 = "0 8px 18px rgba(2, 6, 23, 0.28)"
        라벨색 = "#cbd5e1"
        제목색 = "#f8fafc"
        메타색 = "#94a3b8"
        보유행배경 = "#0f172a"
        보유행테두리 = "#1e293b"

    st.markdown(f"""
    <style>
    .simple-market-card {{
        border: 1px solid {카드테두리};
        border-left-width: 6px;
        border-radius: 16px;
        padding: 14px 14px 12px 14px;
        background: linear-gradient(180deg, {카드배경} 0%, rgba(15,23,42,0.98) 100%);
        box-shadow: {카드그림자};
        margin-bottom: 10px;
        min-height: 154px;
        display: flex;
        flex-direction: column;
        justify-content: flex-start;
        gap: 2px;
    }}
    .simple-market-card.up {{border-left-color: #dc2626;}}
    .simple-market-card.down {{border-left-color: #2563eb;}}
    .simple-market-card.flat {{border-left-color: #94a3b8;}}
    .simple-market-label {{
        display: inline-flex;
        align-items: center;
        width: fit-content;
        font-size: 0.74rem;
        font-weight: 700;
        color: {라벨색};
        margin-bottom: 4px;
        padding: 3px 8px;
        border-radius: 999px;
        background: rgba(148,163,184,0.12);
    }}
    .simple-market-title {{font-size: 0.98rem; font-weight: 800; color: {제목색}; margin-bottom: 8px; line-height: 1.25; min-height: 2.3em;}}
    .simple-market-price {{font-size: 1.78rem; font-weight: 800; color: {제목색}; line-height: 1.1; letter-spacing: -0.02em;}}
    .simple-market-delta {{font-size: 0.95rem; font-weight: 700; margin-top: 8px; min-height: 1.4em;}}
    .simple-market-delta.up {{color: #dc2626;}}
    .simple-market-delta.down {{color: #2563eb;}}
    .simple-market-delta.flat {{color: {메타색};}}
    .simple-market-meta {{font-size: 0.82rem; color: {메타색}; margin-top: auto; padding-top: 10px;}}
    .simple-market-holdings {{
        margin-top: 6px;
        font-size: 0.74rem;
        color: {메타색};
        background: {보유행배경};
        border: 1px solid {보유행테두리};
        border-radius: 8px;
        padding: 3px 7px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    .signal-box {{
        border-radius: 18px;
        padding: 14px 16px;
        color: white;
        box-shadow: 0 6px 16px rgba(15, 23, 42, 0.12);
        margin-bottom: 10px;
    }}
    .signal-title {{font-size: 0.9rem; opacity: 0.9;}}
    .signal-main {{font-size: 1.35rem; font-weight: 800; margin-top: 4px;}}
    .trade-action-row {{margin-top: 0.25rem; margin-bottom: 0.45rem;}}
    .trade-action-row [data-testid="stButton"] > button,
    .trade-action-row [data-testid="stDownloadButton"] > button {{
        min-height: 52px;
        border-radius: 14px;
        font-weight: 700;
        width: 100%;
        white-space: normal;
        line-height: 1.25;
    }}
    .trade-upload-note {{
        margin-top: 0.15rem;
        margin-bottom: 0.7rem;
        color: #94a3b8;
        font-size: 0.93rem;
    }}
    .ratio-summary-card {{
        border: 1px solid #1f2937;
        border-radius: 16px;
        padding: 14px 16px;
        background: #020817;
        margin-bottom: 10px;
    }}
    .ratio-summary-title {{
        font-size: 0.93rem;
        color: #cbd5e1;
        font-weight: 700;
        margin-bottom: 8px;
    }}
    .ratio-summary-main {{
        font-size: 1.55rem;
        color: #f8fafc;
        font-weight: 800;
        line-height: 1.15;
    }}
    .ratio-summary-sub {{
        margin-top: 6px;
        font-size: 0.92rem;
        color: #94a3b8;
    }}
    div[role="radiogroup"] label {{cursor: pointer !important;}}
    div[role="radiogroup"] p {{font-weight: 600;}}
    div[data-baseweb="select"] * {{cursor: pointer !important;}}
    button[role="tab"] {{cursor: pointer !important;}}
    .stTabs [data-baseweb="tab"] {{cursor: pointer !important;}}
    </style>
    """, unsafe_allow_html=True)


def 대시보드변화방향(등락률):
    if 등락률 is None or pd.isna(등락률):
        return "flat"
    if 등락률 > 0:
        return "up"
    if 등락률 < 0:
        return "down"
    return "flat"


def 심플카드HTML(이름, 현재가, 전일대비, 등락률, 보조라벨="", 하단메모="", 보유정보문자=""):
    import html

    방향 = 대시보드변화방향(등락률)
    if 현재가 is None or pd.isna(현재가):
        현재가문자 = "데이터 확인 필요"
    else:
        현재가문자 = 숫자표시(현재가, 2)

    변화문자 = "전일 비교값 없음"
    if 등락률 is not None and not pd.isna(등락률):
        if 전일대비 is not None and not pd.isna(전일대비):
            변화문자 = f"{전일대비:+,.2f} ({등락률:+.2f}%)"
        else:
            변화문자 = f"{등락률:+.2f}%"
    elif 전일대비 is not None and not pd.isna(전일대비):
        변화문자 = f"{전일대비:+,.2f}"

    이름 = html.escape(str(이름))
    보조라벨 = html.escape(str(보조라벨)) if 보조라벨 else ""
    하단메모 = html.escape(str(하단메모)) if 하단메모 else ""
    보유정보문자 = html.escape(str(보유정보문자)) if 보유정보문자 else ""

    parts = [f'<div class="simple-market-card {방향}">']
    if 보조라벨:
        parts.append(f'<div class="simple-market-label">{보조라벨}</div>')
    parts.append(f'<div class="simple-market-title">{이름}</div>')
    parts.append(f'<div class="simple-market-price">{현재가문자}</div>')
    parts.append(f'<div class="simple-market-delta {방향}">{변화문자}</div>')
    if 보유정보문자:
        parts.append(f'<div class="simple-market-holdings">{보유정보문자}</div>')
    if 하단메모:
        parts.append(f'<div class="simple-market-meta">{하단메모}</div>')
    parts.append('</div>')
    return ''.join(parts)


def 대시보드보유정보사전(거래df):
    try:
        계산결과 = 포트폴리오계산(거래df)
        if 계산결과 is None or 계산결과.empty:
            return {}
        작업 = 계산결과.copy()
        작업["보유수량"] = pd.to_numeric(작업.get("보유수량"), errors="coerce").fillna(0)
        작업["평가금액"] = pd.to_numeric(작업.get("평가금액"), errors="coerce").fillna(0)
        작업 = 작업[작업["보유수량"] > 0].copy()
        if 작업.empty:
            return {}
        결과 = {}
        for _, 행 in 작업.iterrows():
            코드 = str(행.get("종목코드", "")).zfill(6)
            수량 = pd.to_numeric(pd.Series([행.get("보유수량")]), errors="coerce").fillna(0).iloc[0]
            평가금액 = pd.to_numeric(pd.Series([행.get("평가금액")]), errors="coerce").fillna(0).iloc[0]
            결과[코드] = f"보유 {숫자표시(수량, 0)}주 · 평가 {금액표시(평가금액)}"
        return 결과
    except Exception:
        return {}


def 현재보유종목코드목록(거래df):
    try:
        계산결과 = 포트폴리오계산(거래df)
        if 계산결과 is None or 계산결과.empty:
            return []
        작업 = 계산결과.copy()
        작업["보유수량"] = pd.to_numeric(작업.get("보유수량"), errors="coerce").fillna(0)
        작업 = 작업[작업["보유수량"] > 0].copy()
        if 작업.empty:
            return []
        if "평가금액" in 작업.columns:
            작업["평가금액"] = pd.to_numeric(작업.get("평가금액"), errors="coerce").fillna(0)
            작업 = 작업.sort_values(["평가금액", "종목명"], ascending=[False, True])
        return [str(x).zfill(6) for x in 작업["종목코드"].tolist() if str(x).strip()]
    except Exception:
        return []


def 주요모니터자산구성(거래df):
    동적종목매핑갱신(거래df)
    구성 = [("코스피", 주요자산["코스피"], "주요 지수"), ("코스닥", 주요자산["코스닥"], "주요 지수")]
    보유코드목록 = 현재보유종목코드목록(거래df)
    추가된코드 = set()
    for 코드 in 보유코드목록:
        이름 = 코드명매핑.get(코드)
        if not 이름 or 이름 in ["코스피", "코스닥"] or 코드 in 추가된코드:
            continue
        자산정보 = 주요자산.get(이름)
        if not 자산정보:
            자산정보 = {"구분": 종목구분추정(이름, 코드), "코드": 코드}
            주요자산[이름] = 자산정보
        구성.append((이름, 자산정보, "보유 종목"))
        추가된코드.add(코드)
        if len(구성) >= 8:
            break
    return 구성


def 세션선택초기화():
    사용가능주요자산 = list(주요자산.keys())
    사용가능관심종목 = list(관심종목.values())

    if "main_asset_choice_v44" not in st.session_state or st.session_state["main_asset_choice_v44"] not in 사용가능주요자산:
        st.session_state["main_asset_choice_v44"] = 사용가능주요자산[0] if 사용가능주요자산 else ""
    if "holding_asset_choice_v44" not in st.session_state or st.session_state["holding_asset_choice_v44"] not in 사용가능관심종목:
        st.session_state["holding_asset_choice_v44"] = 사용가능관심종목[0] if 사용가능관심종목 else ""


def 현재거래이력가져오기():
    """세션 상태가 아직 없더라도 자동저장/기본 거래이력을 즉시 반영해 초기 화면과 이후 화면이 동일 기준을 쓰도록 보장합니다."""
    if "trade_history_df_v22" not in st.session_state:
        자동저장df = 자동저장불러오기(거래이력자동저장파일)
        if 자동저장df is not None and not 자동저장df.empty:
            st.session_state["trade_history_df_v22"] = 거래이력정규화(자동저장df)
        else:
            st.session_state["trade_history_df_v22"] = 거래이력정규화(기본포트폴리오.copy())
    else:
        st.session_state["trade_history_df_v22"] = 거래이력정규화(st.session_state["trade_history_df_v22"])
    동적종목매핑갱신(st.session_state["trade_history_df_v22"])
    return st.session_state["trade_history_df_v22"]


def 포트폴리오요약지표생성(계산포트폴리오, 표시대상포트폴리오=None):
    if 계산포트폴리오 is None or 계산포트폴리오.empty:
        return {
            "총투자원금": 0.0,
            "총평가금액": 0.0,
            "총평가손익": 0.0,
            "총실현손익": 0.0,
            "총수익률": 0.0,
            "보유종목수": 0,
            "조회실패건수": 0,
            "최대비중종목명": "-",
            "최대비중": 0.0,
        }

    표시대상 = 표시대상포트폴리오.copy() if 표시대상포트폴리오 is not None else 계산포트폴리오.copy()
    정상평가행 = 표시대상[표시대상["데이터상태"] == "정상"].copy() if "데이터상태" in 표시대상.columns else 표시대상.copy()

    총투자원금 = pd.to_numeric(정상평가행.get("투자원금"), errors="coerce").fillna(0).sum() if not 정상평가행.empty else 0.0
    총평가금액 = pd.to_numeric(정상평가행.get("평가금액"), errors="coerce").fillna(0).sum() if not 정상평가행.empty else 0.0
    총평가손익 = pd.to_numeric(정상평가행.get("평가손익"), errors="coerce").fillna(0).sum() if not 정상평가행.empty else 0.0
    총실현손익 = pd.to_numeric(계산포트폴리오.get("실현손익"), errors="coerce").fillna(0).sum()
    총수익률 = (총평가손익 / 총투자원금 * 100) if 총투자원금 not in [0, None] else 0.0
    보유종목수 = int((pd.to_numeric(표시대상.get("보유수량"), errors="coerce").fillna(0) > 0).sum()) if not 표시대상.empty else 0
    조회실패건수 = int((표시대상.get("데이터상태") != "정상").sum()) if "데이터상태" in 표시대상.columns else 0

    최대비중종목명 = "-"
    최대비중 = 0.0
    if not 표시대상.empty and "현재비중" in 표시대상.columns:
        비중작업 = 표시대상.copy()
        비중작업["현재비중"] = pd.to_numeric(비중작업.get("현재비중"), errors="coerce").fillna(0)
        비중작업 = 비중작업.sort_values(["현재비중", "종목명"], ascending=[False, True])
        if not 비중작업.empty and float(비중작업.iloc[0]["현재비중"]) > 0:
            최대비중종목명 = str(비중작업.iloc[0].get("종목명", "-"))
            최대비중 = float(비중작업.iloc[0]["현재비중"])

    return {
        "총투자원금": 총투자원금,
        "총평가금액": 총평가금액,
        "총평가손익": 총평가손익,
        "총실현손익": 총실현손익,
        "총수익률": 총수익률,
        "보유종목수": 보유종목수,
        "조회실패건수": 조회실패건수,
        "최대비중종목명": 최대비중종목명,
        "최대비중": 최대비중,
    }


def 포트폴리오요약카드표시(요약정보):
    if 모바일여부():
        카드1, 카드2 = st.columns(2)
        카드1.metric("총 투자원금", 금액표시(요약정보["총투자원금"]))
        카드2.metric("총 평가금액", 금액표시(요약정보["총평가금액"]))
        카드3, 카드4 = st.columns(2)
        카드3.metric("미실현 손익", 손익문자열(요약정보["총평가손익"]) + "원")
        카드4.metric("보유 수익률", 수익률문자열(요약정보["총수익률"]))
        카드5, 카드6 = st.columns(2)
        카드5.metric("보유 종목 수", f"{요약정보['보유종목수']}개")
        카드6.metric("최대 비중 종목", 요약정보["최대비중종목명"])
    else:
        카드1, 카드2, 카드3, 카드4 = st.columns(4)
        카드1.metric("총 투자원금", 금액표시(요약정보["총투자원금"]))
        카드2.metric("총 평가금액", 금액표시(요약정보["총평가금액"]))
        카드3.metric("미실현 손익", 손익문자열(요약정보["총평가손익"]) + "원")
        카드4.metric("보유 수익률", 수익률문자열(요약정보["총수익률"]))

        카드5, 카드6, 카드7, 카드8 = st.columns(4)
        카드5.metric("실현 손익", 손익문자열(요약정보["총실현손익"]) + "원")
        카드6.metric("보유 종목 수", f"{요약정보['보유종목수']}개")
        카드7.metric("최대 비중 종목", 요약정보["최대비중종목명"], f"{요약정보['최대비중']:.2f}%")
        카드8.metric("조회 실패 종목", f"{요약정보['조회실패건수']}개")


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

    빈도맵 = {"주": "W", "월": "M", "년": "Y"}
    규칙 = 빈도맵.get(구간)
    if 규칙 is None:
        return 데이터.copy()

    변환 = 데이터.copy()
    변환.index = pd.to_datetime(변환.index)
    변환 = 변환.sort_index()
    기간키 = 변환.index.to_period(규칙)

    집계 = pd.DataFrame({
        "시가": 변환.groupby(기간키)["시가"].first(),
        "고가": 변환.groupby(기간키)["고가"].max(),
        "저가": 변환.groupby(기간키)["저가"].min(),
        "종가": 변환.groupby(기간키)["종가"].last(),
        "거래량": 변환.groupby(기간키)["거래량"].sum(),
        "실제말일": 변환.groupby(기간키).apply(lambda x: pd.to_datetime(x.index).max()),
    }).dropna(subset=["시가", "고가", "저가", "종가"])

    if 집계.empty:
        return pd.DataFrame()

    집계.index = pd.to_datetime(집계["실제말일"])
    집계 = 집계.drop(columns=["실제말일"]).sort_index()

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
st.caption("코스피·코스닥과 현재 보유 종목을 간단한 카드형으로 확인합니다. 왼쪽 실선과 수치 색상은 상승은 빨간색, 하락은 파란색으로 표시합니다.")

if st.button("시세 새로고침"):
    st.cache_data.clear()
    st.rerun()

대시보드기준거래 = 현재거래이력가져오기().copy()
모니터자산목록 = 주요모니터자산구성(대시보드기준거래)
보유정보사전 = 대시보드보유정보사전(대시보드기준거래)

if len(모니터자산목록) <= 3:
    카드열수 = len(모니터자산목록)
elif 모바일여부():
    카드열수 = 2
else:
    카드열수 = min(6, len(모니터자산목록))

for row_start in range(0, len(모니터자산목록), 카드열수):
    cols = st.columns(카드열수)
    for col, (자산명, 자산정보, 구분라벨) in zip(cols, 모니터자산목록[row_start:row_start + 카드열수]):
        정보 = 자산현재가정보(자산명, 자산정보)
        종목코드 = str(자산정보['코드']).zfill(6)
        기준일 = 정보.get("기준일")
        if 기준일 is not None:
            하단메모 = f"코드 {자산정보['코드']} · 기준 {기준일}"
        else:
            하단메모 = f"코드 {자산정보['코드']} · 조회 실패"
        보유정보문자 = 보유정보사전.get(종목코드, "") if 구분라벨 == "보유 종목" else ""
        with col:
            st.markdown(
                심플카드HTML(
                    자산명,
                    정보.get("현재가"),
                    정보.get("전일대비"),
                    정보.get("등락률"),
                    보조라벨=구분라벨,
                    하단메모=하단메모,
                    보유정보문자=보유정보문자,
                ),
                unsafe_allow_html=True,
            )

보유코드목록 = 현재보유종목코드목록(대시보드기준거래)
if 보유코드목록:
    보유종목명문자 = ", ".join([코드명매핑.get(코드) or 종목코드기준종목명(코드) or 코드 for 코드 in 보유코드목록])
    st.caption(f"자동 연결된 보유 종목: {보유종목명문자}")
else:
    st.caption("현재 보유 종목이 없어 코스피와 코스닥만 표시합니다.")

모니터실패건수 = sum(1 for 자산명, 자산정보, _ in 모니터자산목록 if 자산현재가정보(자산명, 자산정보).get("현재가") is None)
if 모니터실패건수 > 0:
    st.warning(f"상단 모니터에서 시세를 불러오지 못한 자산이 {모니터실패건수}개 있습니다. 카드 하단의 기준일 또는 조회 실패 문구를 확인해 주세요.")
else:
    st.caption("상단 모니터는 최근 확보된 종가 기준으로 표시됩니다.")

# -----------------------------------
# 주요 경제지표
# -----------------------------------
st.markdown("---")
st.subheader("주요 지표")
st.caption("환율·금·원유 지표를 카드형으로 정리했습니다. 두바이유는 네이버 원본 시세를 우선 조회하고, 실패 시에만 프록시 값으로 대체합니다.")

시장지표df = 네이버시장지표목록가져오기()

if 시장지표df.empty:
    st.warning("시장지표 데이터를 불러오지 못했습니다.")
else:
    지표행목록 = list(시장지표df.iterrows())
    지표열수 = 2 if 모바일여부() else min(6, len(지표행목록))
    for row_start in range(0, len(지표행목록), 지표열수):
        cols = st.columns(지표열수)
        for col, (_, row) in zip(cols, 지표행목록[row_start:row_start + 지표열수]):
            with col:
                st.markdown(
                    심플카드HTML(
                        row["지표"],
                        row.get("현재값"),
                        row.get("전일대비"),
                        row.get("등락률"),
                        보조라벨="",
                        하단메모=f"출처 {row.get('출처', '-')}" if row.get("출처", "-") != "Proxy(Brent/WTI)" else "출처 Proxy(브렌트/WTI 대체값)",
                    ),
                    unsafe_allow_html=True,
                )

    if not BS4_AVAILABLE:
        st.info("bs4가 없어도 Yahoo 기반 대체값으로 주요 지표를 표시합니다.")


# -----------------------------------
# 포트폴리오 입력/수정
# -----------------------------------
st.markdown("---")
st.subheader("포트폴리오 거래 이력 입력")
st.caption(
    "업로드한 거래이력 파일 내용을 기본 거래내역에 반영했습니다. 거래일자·거래구분·거래수량·거래단가·운용사를 입력하면 아래 포트폴리오 현황이 자동 집계됩니다. "
    "종목코드 또는 종목명 중 하나를 입력하면 다른 값은 가능한 범위에서 자동 보정됩니다. 거래단가는 숫자로 입력해 주세요. CSV뿐 아니라 엑셀(xlsx, xls) 업로드도 지원합니다."
)

현재거래이력가져오기()

저장파일명 = f"거래이력_저장_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

if 모바일여부():
    업로드파일 = st.file_uploader(
        "거래이력 파일 불러오기",
        type=["csv", "json", "xlsx", "xls"],
        key="trade_history_file_uploader_v24"
    )
    st.caption("파일 업로드 후 반영 버튼을 누르면 아래 거래 원장에 즉시 적용됩니다.")

    버튼칸1, 버튼칸2, 버튼칸3 = st.columns(3, gap="small")
    with 버튼칸1:
        if st.button("업로드 파일 반영", disabled=업로드파일 is None, key="apply_upload_btn_v26", use_container_width=True):
            try:
                불러온df = 업로드파일에서거래이력읽기(업로드파일)
                보정df = 거래이력자동보정(불러온df.copy())
                st.session_state["trade_history_df_v22"] = 거래이력정규화(보정df)

                거래이력자동저장실행(st.session_state["trade_history_df_v22"])
                st.success(f"거래이력을 불러왔습니다. ({len(보정df)}건)")
                with st.expander("업로드 진단 정보", expanded=False):
                    st.write("업로드 파일명:", 업로드파일.name)
                    st.write("인식된 컬럼:", list(불러온df.columns))
                    if not 보정df.empty:
                        st.dataframe(보정df.head(10), use_container_width=True)
                st.rerun()
            except Exception as e:
                st.error(f"불러오기 중 오류가 발생했습니다: {e}")
    with 버튼칸2:
        st.download_button(
            "현재 거래내역 저장",
            data=현재거래내역엑셀저장바이트(st.session_state["trade_history_df_v22"]),
            file_name=저장파일명,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="save_trade_history_btn_v26",
            use_container_width=True,
        )
    with 버튼칸3:
        st.download_button(
            "JSON 백업",
            data=json.dumps(거래이력JSON변환(st.session_state["trade_history_df_v22"]), ensure_ascii=False, indent=2),
            file_name=f"거래이력_백업_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json",
            key="save_trade_history_json_btn_v26",
            use_container_width=True,
        )
else:
    업로드칸, 버튼칸1, 버튼칸2, 버튼칸3 = st.columns([4.8, 1.15, 1.2, 1.15], gap="small")
    with 업로드칸:
        업로드파일 = st.file_uploader(
            "파일 불러오기",
            type=["csv", "json", "xlsx", "xls"],
            key="trade_history_file_uploader_v24",
            label_visibility="visible"
        )
    with 버튼칸1:
        st.caption("&nbsp;")
        if st.button("업로드파일 반영", disabled=업로드파일 is None, key="apply_upload_btn_v26", use_container_width=True):
            try:
                불러온df = 업로드파일에서거래이력읽기(업로드파일)
                보정df = 거래이력자동보정(불러온df.copy())
                st.session_state["trade_history_df_v22"] = 거래이력정규화(보정df)

                거래이력자동저장실행(st.session_state["trade_history_df_v22"])
                st.success(f"거래이력을 불러왔습니다. ({len(보정df)}건)")
                with st.expander("업로드 진단 정보", expanded=False):
                    st.write("업로드 파일명:", 업로드파일.name)
                    st.write("인식된 컬럼:", list(불러온df.columns))
                    if not 보정df.empty:
                        st.dataframe(보정df.head(10), use_container_width=True)
                st.rerun()
            except Exception as e:
                st.error(f"불러오기 중 오류가 발생했습니다: {e}")
    with 버튼칸2:
        st.caption("&nbsp;")
        st.download_button(
            "현재거래내역 저장",
            data=현재거래내역엑셀저장바이트(st.session_state["trade_history_df_v22"]),
            file_name=저장파일명,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="save_trade_history_btn_v26",
            use_container_width=True,
        )
    with 버튼칸3:
        st.caption("&nbsp;")
        st.download_button(
            "JSON 백업",
            data=json.dumps(거래이력JSON변환(st.session_state["trade_history_df_v22"]), ensure_ascii=False, indent=2),
            file_name=f"거래이력_백업_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json",
            key="save_trade_history_json_btn_v26",
            use_container_width=True,
        )
    st.caption("파일 업로드 후 반영 버튼을 누르면 아래 거래 원장에 즉시 적용됩니다.")

st.info("거래이력 파일은 CSV, JSON, Excel(xlsx/xls) 형식을 불러올 수 있습니다. 거래구분은 매수/매도만 사용해 주세요.")

편집대상거래이력 = 거래이력정규화(st.session_state["trade_history_df_v22"])
수정포트폴리오 = st.data_editor(
    편집대상거래이력,
    num_rows="dynamic",
    use_container_width=True,
    hide_index=True,
    disabled=[],
    column_order=["종목코드", "종목명", "거래일자", "거래구분", "거래수량", "거래단가", "운용사", "비고"],
    column_config={
        "종목코드": st.column_config.TextColumn("종목코드", help="6자리 종목코드를 입력하면 종목명이 자동 보정됩니다."),
        "종목명": st.column_config.TextColumn("종목명", help="종목명을 입력하면 가능한 경우 종목코드가 자동 보정됩니다."),
        "거래일자": st.column_config.DateColumn("거래일자", format="YYYY-MM-DD"),
        "거래구분": st.column_config.SelectboxColumn("거래구분", options=["매수", "매도"], required=True),
        "거래수량": st.column_config.NumberColumn("거래수량", min_value=0, step=1, format="%d"),
        "거래단가": st.column_config.NumberColumn("거래단가", min_value=0, step=1, format="%d"),
        "운용사": st.column_config.TextColumn("운용사", help="예: 신한은행 IRP, 미래에셋증권"),
        "비고": st.column_config.TextColumn("비고"),
    },
    key="trade_editor_v25",
)
수정포트폴리오 = 수정포트폴리오.dropna(how="all")
수정포트폴리오 = 거래이력정규화(수정포트폴리오.reset_index(drop=True))
st.session_state["trade_history_df_v22"] = 수정포트폴리오.copy()

자동저장성공, 자동저장메시지 = 거래이력자동저장실행(st.session_state["trade_history_df_v22"])
if 자동저장성공:
    st.caption("거래이력이 자동저장되었습니다.")
else:
    st.warning(f"자동저장 실패: {자동저장메시지}")

입력검증표 = 거래이력검증표생성(수정포트폴리오)
이상치점검표 = 거래이력이상치점검표생성(수정포트폴리오)
통합점검표 = pd.concat([입력검증표, 이상치점검표], ignore_index=True) if not 이상치점검표.empty else 입력검증표.copy()
if not 통합점검표.empty:
    통합점검표 = 통합점검표.drop_duplicates().reset_index(drop=True)
불일치검증표 = 통합점검표[통합점검표["점검항목"] == "종목코드-종목명 불일치"] if not 통합점검표.empty else pd.DataFrame()
if not 불일치검증표.empty:
    st.error(f'종목코드와 종목명이 서로 맞지 않는 입력이 {len(불일치검증표)}건 있습니다. 자동으로 다른 종목으로 바꾸지 않고 그대로 표시했습니다.')
if 통합점검표.empty:
    st.success("거래이력 입력 점검 결과: 현재 확인된 형식 오류가 없습니다.")
else:
    st.warning(f"거래이력 입력 점검 결과: {len(통합점검표)}건의 확인 사항이 있습니다.")
    with st.expander("입력 검증 상세 보기", expanded=False):
        st.dataframe(index_1부터(통합점검표), use_container_width=True)


# -----------------------------------
# 포트폴리오 계산 결과
# -----------------------------------
계산포트폴리오 = 포트폴리오계산(수정포트폴리오)
보유계산포트폴리오 = 보유포트폴리오필터(계산포트폴리오)
보유종목옵션 = 보유종목선택옵션생성(계산포트폴리오)

과잉매도종목 = 계산포트폴리오[계산포트폴리오["과잉매도수량"] > 0]
if not 과잉매도종목.empty:
    st.warning("보유수량보다 많이 매도한 거래가 있습니다. 거래이력을 확인해 주세요.")

st.markdown("---")
st.subheader("포트폴리오 현황")

if 계산포트폴리오.empty:
    st.warning("포트폴리오 데이터를 계산할 수 없습니다.")
else:
    표시대상포트폴리오 = 보유계산포트폴리오.copy()
    정상평가행 = 표시대상포트폴리오[표시대상포트폴리오["데이터상태"] == "정상"].copy()

    총투자원금 = 정상평가행["투자원금"].sum()
    총평가금액 = 정상평가행["평가금액"].sum()
    총평가손익 = 정상평가행["평가손익"].sum()
    총실현손익 = 계산포트폴리오["실현손익"].sum()
    총수익률 = (총평가손익 / 총투자원금 * 100) if 총투자원금 != 0 else 0

    조회실패건수 = (표시대상포트폴리오["데이터상태"] != "정상").sum()
    if 표시대상포트폴리오.empty:
        st.info("현재 보유수량이 0보다 큰 종목이 없습니다. 아래 거래이력을 확인해 주세요.")

    if 조회실패건수 > 0:
        st.warning(f"현재가 조회 실패 종목 {조회실패건수}건은 평가금액/비중 계산에서 제외했습니다.")

    청산종목표 = 계산포트폴리오[계산포트폴리오["보유수량"] <= 0].copy()
    if not 청산종목표.empty:
        with st.expander(f"청산 또는 보유 0주 종목 보기 ({len(청산종목표)}건)", expanded=False):
            청산표시 = 청산종목표[["종목코드", "종목명", "총매수수량", "총매도수량", "보유수량", "실현손익", "최근거래일자"]].copy()
            청산표시 = 청산표시.rename(columns={"총매수수량": "총 매수수량", "총매도수량": "총 매도수량", "최근거래일자": "최근 거래일자"})
            st.dataframe(index_1부터(청산표시), use_container_width=True)

    요약정보 = 포트폴리오요약지표생성(계산포트폴리오, 표시대상포트폴리오)
    포트폴리오요약카드표시(요약정보)

    st.caption("포트폴리오 요약은 현재 보유 종목 기준으로 자동 계산되며, 현재가 조회 실패 종목은 평가금액·비중 계산에서 제외됩니다.")

    포트폴리오표시 = 표시대상포트폴리오[["종목코드", "종목명", "최초매수일자", "최근거래일자", "총매수수량", "총매도수량", "보유수량", "매입평균단가", "현재가", "투자원금", "평가금액", "평가손익", "실현손익", "수익률", "현재비중", "과잉매도수량", "데이터상태"]].copy()
    포트폴리오표시 = 포트폴리오표시.rename(columns={"매입평균단가": "매입 평균단가", "총매수수량": "총 매수수량", "총매도수량": "총 매도수량", "최초매수일자": "최초 매수일자", "최근거래일자": "최근 거래일자", "과잉매도수량": "과잉 매도수량"})
    포트폴리오표시 = 포트폴리오표_컬럼선택(포트폴리오표시)
    포트폴리오표시 = index_1부터(포트폴리오표시)

    if 모바일여부():
        모바일형식사전 = {}
        if "보유수량" in 포트폴리오표시.columns:
            모바일형식사전["보유수량"] = 안전정수포맷
        if "현재가" in 포트폴리오표시.columns:
            모바일형식사전["현재가"] = 안전정수포맷
        if "평가금액" in 포트폴리오표시.columns:
            모바일형식사전["평가금액"] = 안전정수포맷
        if "수익률" in 포트폴리오표시.columns:
            모바일형식사전["수익률"] = 수익률문자열
        모바일스타일 = 포트폴리오표시.style.format(모바일형식사전)
        if "수익률" in 포트폴리오표시.columns:
            모바일스타일 = 모바일스타일.map(수익률색상, subset=["수익률"])
        st.dataframe(모바일스타일, use_container_width=True)
    else:
        st.dataframe(
            포트폴리오표시.style.format({
                "총 매수수량": 안전정수포맷,
                "총 매도수량": 안전정수포맷,
                "보유수량": 안전정수포맷,
                "과잉 매도수량": 안전정수포맷,
                "매입 평균단가": 안전정수포맷,
                "현재가": 안전정수포맷,
                "투자원금": 안전정수포맷,
                "평가금액": 안전정수포맷,
                "평가손익": 손익문자열,
                "실현손익": 손익문자열,
                "수익률": 수익률문자열,
                "현재비중": lambda x: 안전소수포맷(x, 2),
            }).map(손익색상, subset=["평가손익", "실현손익"]).map(수익률색상, subset=["수익률"]),
            use_container_width=True,
        )
    st.markdown("### 포트폴리오 거래 원장 조회")
    st.caption("입력 원장과 같은 데이터를 누적보유수량 기준으로 정렬·필터해서 보는 조회용 표입니다. 직접 수정은 위 거래 이력 입력 표에서 진행해 주세요.")
    전체거래표 = 종목거래이력표생성(수정포트폴리오)
    if 전체거래표.empty:
        st.info("표시할 거래기록이 없습니다.")
    else:
        with st.expander("거래 원장 조회 열기", expanded=False):
            조회대상거래표 = 전체거래표.copy()
            필터칸1, 필터칸2, 필터칸3 = st.columns(3)
            with 필터칸1:
                종목옵션 = ["전체"] + sorted([x for x in 조회대상거래표["종목명"].dropna().astype(str).unique().tolist() if x])
                선택종목명 = st.selectbox("종목 필터", 종목옵션, index=0, key="ledger_filter_asset_v1")
            with 필터칸2:
                거래구분옵션 = ["전체"] + sorted([x for x in 조회대상거래표["거래구분"].dropna().astype(str).unique().tolist() if x])
                선택거래구분 = st.selectbox("거래구분 필터", 거래구분옵션, index=0, key="ledger_filter_type_v1")
            with 필터칸3:
                운용사옵션 = ["전체"] + sorted([x for x in 조회대상거래표["운용사"].dropna().astype(str).unique().tolist() if x])
                선택운용사 = st.selectbox("운용사 필터", 운용사옵션, index=0, key="ledger_filter_account_v1")

            if 선택종목명 != "전체":
                조회대상거래표 = 조회대상거래표[조회대상거래표["종목명"] == 선택종목명].copy()
            if 선택거래구분 != "전체":
                조회대상거래표 = 조회대상거래표[조회대상거래표["거래구분"] == 선택거래구분].copy()
            if 선택운용사 != "전체":
                조회대상거래표 = 조회대상거래표[조회대상거래표["운용사"] == 선택운용사].copy()

            st.caption(f"조회 결과 {len(조회대상거래표)}건")
            st.dataframe(거래기록표시용서식(index_1부터(조회대상거래표)), use_container_width=True)

    오류행 = 계산포트폴리오[(계산포트폴리오["과잉매도수량"] > 0) | (계산포트폴리오["데이터상태"] != "정상")]
    if not 오류행.empty:
        st.warning("일부 종목에 과잉 매도 입력 또는 현재가 조회 실패가 있습니다. 아래 현황표의 '과잉 매도수량', '데이터상태'를 확인해 주세요.")

    현재가실패표 = 계산포트폴리오[계산포트폴리오["데이터상태"] != "정상"][ ["종목코드", "종목명", "데이터상태"] ].copy()
    if not 현재가실패표.empty:
        st.error(f"현재가 조회 실패 종목이 {len(현재가실패표)}개 있습니다. 종목코드와 장중/휴장 여부, 네트워크 상태를 확인해 주세요.")
        with st.expander("현재가 조회 실패 종목 보기", expanded=False):
            st.dataframe(index_1부터(현재가실패표), use_container_width=True)

    비중그래프칸, 비중요약칸 = st.columns([1.45, 0.85], gap="large")
    with 비중그래프칸:
        st.plotly_chart(
            비중그래프(계산포트폴리오),
            use_container_width=True,
            config={"displaylogo": False, "responsive": True},
        )
    with 비중요약칸:
        비중요약표 = 계산포트폴리오.copy()
        비중요약표 = 비중요약표[["종목명", "현재비중", "평가금액"]].copy()
        비중요약표["현재비중"] = pd.to_numeric(비중요약표["현재비중"], errors="coerce").fillna(0)
        비중요약표["평가금액"] = pd.to_numeric(비중요약표["평가금액"], errors="coerce").fillna(0)
        비중요약표 = 비중요약표[비중요약표["평가금액"] > 0].sort_values(["현재비중", "평가금액"], ascending=[False, False]).reset_index(drop=True)

        if not 비중요약표.empty:
            최대행 = 비중요약표.iloc[0]
            st.markdown(
                f"""
                <div class="ratio-summary-card">
                    <div class="ratio-summary-title">최대 비중 종목</div>
                    <div class="ratio-summary-main">{최대행['종목명']}</div>
                    <div class="ratio-summary-sub">비중 {최대행['현재비중']:.2f}% · 평가금액 {금액표시(최대행['평가금액'])}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            표시용비중요약표 = 비중요약표.copy()
            표시용비중요약표["현재비중"] = 표시용비중요약표["현재비중"].map(lambda x: f"{x:.2f}%")
            표시용비중요약표["평가금액"] = 표시용비중요약표["평가금액"].map(금액표시)
            st.dataframe(index_1부터(표시용비중요약표), use_container_width=True, hide_index=False)
        else:
            st.info("비중 요약을 표시할 보유 종목이 없습니다.")


# -----------------------------------
# 보유 종목 개별 분석
# -----------------------------------
st.markdown("---")
st.subheader("보유 종목 개별 분석")
st.caption("아래 분석 종목 목록은 현재 거래이력으로 계산된 보유수량 기준으로 자동 생성됩니다. 따라서 거래이력·포트폴리오 현황·개별분석이 항상 같은 기준을 사용합니다.")

try:
    if not 보유종목옵션:
        st.info("현재 보유 중인 종목이 없어 개별 분석 항목을 표시하지 않습니다.")
    else:
        기본선택코드 = st.session_state.get("holding_asset_choice_code_v45", 보유종목옵션[0]["종목코드"])
        옵션코드목록 = [항목["종목코드"] for 항목 in 보유종목옵션]
        if 기본선택코드 not in 옵션코드목록:
            기본선택코드 = 옵션코드목록[0]
        기본선택인덱스 = 옵션코드목록.index(기본선택코드)

        선택종목코드 = st.selectbox(
            "분석할 보유 종목 선택",
            옵션코드목록,
            index=기본선택인덱스,
            format_func=lambda 코드: next((항목["표시"] for 항목 in 보유종목옵션 if 항목["종목코드"] == 코드), 코드),
            key="holding_asset_choice_v45",
        )
        st.session_state["holding_asset_choice_code_v45"] = 선택종목코드

        선택행 = next((항목 for 항목 in 보유종목옵션 if 항목["종목코드"] == 선택종목코드), None)
        선택종목명 = 선택행["종목명"] if 선택행 else 종목코드기준종목명(선택종목코드)
        선택종목구분 = "etf" if 선택종목코드 in ["069500", "229200", "471990"] else "stock"
        가격데이터 = 자산과거가격가져오기(선택종목구분, 선택종목코드, 개월수=6)
        선택포트폴리오행 = 보유계산포트폴리오[보유계산포트폴리오["종목코드"] == 선택종목코드]

        st.caption(f"현재 선택 종목: {선택종목명} · 구분: {선택종목구분} · 코드: {선택종목코드}")

        if not 선택포트폴리오행.empty:
            선택행데이터 = 선택포트폴리오행.iloc[0]
            정보칸1, 정보칸2, 정보칸3 = st.columns(3)
            정보칸1.metric("보유수량", 숫자표시(선택행데이터.get("보유수량")))
            정보칸2.metric("매입 평균단가", 금액표시(선택행데이터.get("매입평균단가")))
            정보칸3.metric("현재 비중", 비율표시(선택행데이터.get("현재비중")))

        if 가격데이터.empty:
            st.warning("가격 데이터를 불러오지 못했습니다.")
        else:
            최신값 = 가격데이터.iloc[-1]
            이전값 = 가격데이터.iloc[-2] if len(가격데이터) >= 2 else 최신값
            가격변화 = 최신값["종가"] - 이전값["종가"]
            등락률 = (가격변화 / 이전값["종가"] * 100) if 이전값["종가"] != 0 else 0

            신호결과 = 신호판정계산(가격데이터)
            자동코멘트결과 = 고급매매코멘트생성(선택종목명, 가격데이터, 선택포트폴리오행)
            모델분석 = 차트분석문구(선택종목명, 가격데이터)
            종목거래표 = 종목거래이력표생성(수정포트폴리오, 선택종목코드)
            상세해설문장 = 보유종목상세해설생성(선택종목명, 가격데이터, 선택포트폴리오행)

            보유분석탭1, 보유분석탭2, 보유분석탭3, 보유분석탭4 = st.tabs(["개요", "상세 해설", "기술적 분석", "거래기록"])

            with 보유분석탭1:
                if 모바일여부():
                    행1_1, 행1_2 = st.columns(2)
                    행1_1.metric("현재가", 금액표시(최신값["종가"]), f"{가격변화:,.0f}원")
                    행1_2.metric("등락률", 비율표시(등락률))
                    행2_1, 행2_2 = st.columns(2)
                    행2_1.metric("거래량", 숫자표시(최신값["거래량"]))
                    행2_2.metric("RSI(14)", f"{최신값['RSI(14)']:.2f}" if pd.notna(최신값["RSI(14)"]) else "-")
                else:
                    칸1, 칸2, 칸3, 칸4 = st.columns(4)
                    칸1.metric("현재가", 금액표시(최신값["종가"]), f"{가격변화:,.0f}원")
                    칸2.metric("등락률", 비율표시(등락률))
                    칸3.metric("거래량", 숫자표시(최신값["거래량"]))
                    칸4.metric("RSI(14)", f"{최신값['RSI(14)']:.2f}" if pd.notna(최신값["RSI(14)"]) else "-")

                st.markdown(자동판정배지HTML(자동코멘트결과["판정"], 자동코멘트결과["실행"], 자동코멘트결과["강도"]), unsafe_allow_html=True)
                st.info(자동코멘트결과["핵심문구"])

                요약칸1, 요약칸2, 요약칸3, 요약칸4 = st.columns(4)
                요약칸1.metric("자동 판정", 자동코멘트결과["판정"])
                요약칸2.metric("추세 판정", 자동코멘트결과["추세판정"])
                요약칸3.metric("실행 방향", 자동코멘트결과["실행"])
                요약칸4.metric("복합 총점", f"{자동코멘트결과['총점']:.2f}점" if 자동코멘트결과.get("총점") is not None else "-")

                st.markdown("#### 핵심 판독 요약")
                판독칸1, 판독칸2, 판독칸3, 판독칸4 = st.columns(4)
                판독칸1.metric("가격 위치", 자동코멘트결과.get("위치판정", "-"))
                판독칸2.metric("RSI 판정", 자동코멘트결과.get("RSI판정", "-"))
                판독칸3.metric("거래량 판정", 자동코멘트결과.get("거래량판정", "-"))
                판독칸4.metric("단순 신호", 신호결과.get("종합신호", "-"))
                st.caption(f"간단 실행 의견: {신호결과.get('실행의견', '-')}")

                if not 선택포트폴리오행.empty:
                    포지션행 = 선택포트폴리오행.iloc[0]
                    추가칸1, 추가칸2, 추가칸3, 추가칸4 = st.columns(4)
                    추가칸1.metric("평가금액", 금액표시(포지션행.get("평가금액")))
                    추가칸2.metric("평가손익", 금액표시(포지션행.get("평가손익")))
                    추가칸3.metric("보유 수익률", 비율표시(포지션행.get("수익률")))
                    최근거래일 = "-"
                    if not 종목거래표.empty and "거래일자" in 종목거래표.columns:
                        try:
                            최근거래일 = str(pd.to_datetime(종목거래표["거래일자"], errors="coerce").max().date())
                        except Exception:
                            최근거래일 = "-"
                    추가칸4.metric("최근 거래일", 최근거래일)

                st.plotly_chart(가격그래프(가격데이터, f"{선택종목명} 주가 추이"), use_container_width=True, config={"displaylogo": False, "responsive": True})

                최근거래요약 = pd.DataFrame()
                if not 종목거래표.empty:
                    최근거래요약 = 종목거래표.copy().tail(5)
                    표시컬럼 = [c for c in ["거래일자", "거래구분", "거래수량", "거래단가", "거래금액", "누적보유수량"] if c in 최근거래요약.columns]
                    최근거래요약 = 최근거래요약[표시컬럼].copy()
                    if "거래단가" in 최근거래요약.columns:
                        최근거래요약["거래단가"] = 최근거래요약["거래단가"].map(금액표시)
                    if "거래금액" in 최근거래요약.columns:
                        최근거래요약["거래금액"] = 최근거래요약["거래금액"].map(금액표시)
                    if "거래수량" in 최근거래요약.columns:
                        최근거래요약["거래수량"] = 최근거래요약["거래수량"].map(숫자표시)
                    if "누적보유수량" in 최근거래요약.columns:
                        최근거래요약["누적보유수량"] = 최근거래요약["누적보유수량"].map(숫자표시)

                개요왼쪽, 개요오른쪽 = st.columns([1.5, 1.1])
                with 개요왼쪽:
                    st.markdown("#### 최근 거래 흐름")
                    if 최근거래요약.empty:
                        st.info("이 종목의 최근 거래 요약을 표시할 데이터가 없습니다.")
                    else:
                        st.dataframe(index_1부터(최근거래요약.reset_index(drop=True)), use_container_width=True)
                with 개요오른쪽:
                    st.markdown("#### 빠른 해석")
                    st.markdown(f"- 현재 자동 판정은 **{자동코멘트결과['판정']}**입니다.")
                    st.markdown(f"- 추세는 **{자동코멘트결과.get('추세판정', '-')}**, 가격 위치는 **{자동코멘트결과.get('위치판정', '-')}**입니다.")
                    st.markdown(f"- RSI는 **{자동코멘트결과.get('RSI판정', '-')}**, 거래량은 **{자동코멘트결과.get('거래량판정', '-')}**입니다.")
                    st.markdown(f"- 현재 실행 방향은 **{자동코멘트결과['실행']}** 쪽으로 해석됩니다.")

                개요체크표 = pd.concat([
                    자동코멘트결과["근거표"],
                    신호결과["체크표"]
                ], axis=0, ignore_index=True)
                with st.expander("세부 체크표 보기", expanded=False):
                    st.dataframe(index_1부터(개요체크표), use_container_width=True)

            with 보유분석탭2:
                st.markdown("#### 자동 매수·매도 코멘트")
                st.success(f"**{자동코멘트결과['판정']} / {자동코멘트결과['실행']}**")
                st.write(자동코멘트결과["핵심문구"])
                for 문장 in 자동코멘트결과["세부코멘트"]:
                    st.markdown(f"- {문장}")
                with st.expander("자동 판단 근거 보기", expanded=True):
                    for 항목 in 자동코멘트결과["근거"]:
                        st.markdown(f"- {항목}")
                    st.dataframe(index_1부터(자동코멘트결과["근거표"]), use_container_width=True)
                    st.warning(자동코멘트결과["위험문구"])

                with st.expander("자동 판정 기준 설명", expanded=True):
                    st.markdown("- 자동 판정은 **추세·가격 위치·RSI·거래량·당일 흐름**을 함께 점수화해 계산합니다.")
                    st.markdown("- 따라서 한 항목이 같아도 다른 항목이 변하면 최종 판정이 바뀔 수 있습니다.")
                    st.dataframe(index_1부터(자동코멘트결과["기준표"]), use_container_width=True)

                st.markdown("#### 차트 해설")
                for 문장 in 상세해설문장:
                    st.markdown(f"- {문장}")

                st.markdown("#### 모델별 해석 비교")
                모델탭1, 모델탭2, 모델탭3 = st.tabs(["ChatGPT", "Gemini 스타일", "Claude"])
                with 모델탭1:
                    분석카드표시(모델분석["ChatGPT"])
                with 모델탭2:
                    분석카드표시(모델분석["Gemini"])
                with 모델탭3:
                    분석카드표시(모델분석["Claude"])

            with 보유분석탭3:
                기술진단 = 기술분석진단계산(가격데이터)
                기술차트탭0, 기술차트탭1 = st.tabs(["실전 요약", "체크표"])

                with 기술차트탭0:
                    실전칸1, 실전칸2, 실전칸3, 실전칸4 = st.columns(4)
                    실전칸1.metric("추세 배열", 기술진단.get("추세배열", "-"))
                    실전칸2.metric("20일 지지", 금액표시(기술진단.get("지지")))
                    실전칸3.metric("20일 저항", 금액표시(기술진단.get("저항")))
                    실전칸4.metric("단순 신호", 신호결과.get("종합신호", "-"))

                    st.markdown("#### 실전 해석 요약")
                    for 문장 in 기술진단.get("요약문장", []):
                        st.markdown(f"- {문장}")

                    요약왼쪽, 요약오른쪽 = st.columns([1.05, 1.15])
                    with 요약왼쪽:
                        st.markdown("#### 핵심 체크")
                        st.dataframe(index_1부터(기술진단["핵심표"]), use_container_width=True, hide_index=False)
                    with 요약오른쪽:
                        st.markdown("#### 지지·저항 및 기준선")
                        레벨표표시 = 기술진단["레벨표"].copy()
                        if not 레벨표표시.empty:
                            레벨표표시["가격"] = 레벨표표시["가격"].map(금액표시)
                        st.dataframe(index_1부터(레벨표표시), use_container_width=True, hide_index=False)

                    with st.expander("실전 체크포인트", expanded=True):
                        st.markdown("1. **상승 배열**이면 20일선 이탈 여부를 먼저 확인합니다.")
                        st.markdown("2. **지지선 근처**에서는 분할 접근, **저항선 근처**에서는 추격 매수보다 확인이 우선입니다.")
                        st.markdown("3. RSI가 과열인데 거래량까지 급증하면 단기 과열 가능성을 함께 봅니다.")
                        st.markdown("4. 가격이 20일선과 60일선을 동시에 하회하면 단기보다 중기 흐름 훼손 여부를 더 중요하게 봅니다.")
                    st.info("캔들차트는 반복 오류로 인해 이번 버전에서 제외했습니다. 개요 탭의 주가 추이와 아래 체크표를 함께 참고해 주세요.")

                with 기술차트탭1:
                    기술체크 = pd.DataFrame([
                        {"항목": "최근 기준일", "값": str(pd.to_datetime(가격데이터.index[-1]).date())},
                        {"항목": "5일 이동평균", "값": 숫자표시(최신값.get("5일평균"), 2)},
                        {"항목": "20일 이동평균", "값": 숫자표시(최신값.get("20일평균"), 2)},
                        {"항목": "60일 이동평균", "값": 숫자표시(최신값.get("60일평균"), 2)},
                        {"항목": "120일 이동평균", "값": 숫자표시(최신값.get("120일평균"), 2)},
                        {"항목": "RSI(14)", "값": 숫자표시(최신값.get("RSI(14)"), 2)},
                        {"항목": "최근 거래량", "값": 숫자표시(최신값.get("거래량"))},
                        {"항목": "20일 고가", "값": 숫자표시(가격데이터.tail(20)["고가"].max(), 2)},
                        {"항목": "20일 저가", "값": 숫자표시(가격데이터.tail(20)["저가"].min(), 2)},
                        {"항목": "60일 고가", "값": 숫자표시(가격데이터.tail(60)["고가"].max(), 2)},
                        {"항목": "60일 저가", "값": 숫자표시(가격데이터.tail(60)["저가"].min(), 2)},
                    ])
                    st.dataframe(index_1부터(기술체크), use_container_width=True)
                    with st.expander("신호 체크표 함께 보기", expanded=False):
                        st.dataframe(index_1부터(신호결과["체크표"]), use_container_width=True)

            with 보유분석탭4:
                st.markdown("#### 선택 종목 매수·매도 전체 기록")
                if 종목거래표.empty:
                    st.info("선택 종목의 거래기록이 없습니다.")
                else:
                    st.dataframe(거래기록표시용서식(index_1부터(종목거래표)), use_container_width=True)
except Exception as e:
    st.error(f"보유 종목 개별 분석 영역 오류: {e}")
st.write("※ 주요 지수와 종목 차트는 Yahoo 기반 경로를 사용하도록 구성했습니다.")
st.write("※ 네이버 금융 시장지표 HTML 구조가 바뀌면 Yahoo 및 파생 계산값으로 가능한 범위까지 대체하도록 구성했습니다.")


st.markdown("---")
st.markdown('개발자 조현웅 <a href="mailto:hwcho@me.com">hwcho@me.com</a>', unsafe_allow_html=True)

@st.cache_data(ttl=300)
def 네이버시장지표목록가져오기():
    결과 = []
    표시순서 = ["USD/KRW", "국제 금", "국내 금", "WTI", "브렌트유", "두바이유"]
    for 이름 in 표시순서:
        url = 시장지표네이버URL.get(이름)
        if not url:
            continue
        행 = 시장지표단건가져오기(이름, url)
        if 행 is None:
            행 = {"지표": 이름, "현재값": None, "전일대비": None, "등락률": None, "링크": url, "출처": "-"}
        결과.append({
            "지표": 행.get("지표", 이름),
            "현재값": 행.get("현재값"),
            "전일대비": 행.get("전일대비"),
            "등락률": 행.get("등락률"),
            "링크": 행.get("링크", url),
            "출처": 행.get("출처", "-"),
        })

    결과df = pd.DataFrame(결과)
    if 결과df.empty:
        return 결과df
    for col in ["현재값", "전일대비", "등락률"]:
        결과df[col] = pd.to_numeric(결과df[col], errors="coerce")
    return 결과df
    for col in ["현재값", "전일대비", "등락률"]:
        결과df[col] = pd.to_numeric(결과df[col], errors="coerce")
    return 결과df

