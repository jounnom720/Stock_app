import io
import json
import math
import os
import re
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urljoin

import pandas as pd
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

def 헤더이미지경로찾기():
    base_dir = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
    후보목록 = [
        "header_photo.jpg",
        "header_photo.jpeg",
        "20260330_115937968.jpg",
    ]
    for 파일명 in 후보목록:
        후보경로 = base_dir / 파일명
        if 후보경로.exists():
            return 후보경로
    return None

헤더이미지경로 = 헤더이미지경로찾기()

if 모바일여부():
    st.title("📈 투자 분석 시스템")
    st.caption("모바일 조회용 간소화 화면")
    if 헤더이미지경로 is not None:
        st.image(str(헤더이미지경로), use_container_width=True)
else:
    헤더왼쪽, 헤더오른쪽 = st.columns([2.4, 1], vertical_alignment="center")
    with 헤더왼쪽:
        st.title("📈 투자 분석 시스템 v4.23 Stable Plus")
        st.caption("시장 점검, 종목 분석, 거래 이력 기반 포트폴리오 집계, 입력 검증, 현재가 조회 실패 점검, 리밸런싱과 위험도 분석을 한 화면에서 확인하는 안정화 버전입니다.")
        st.text("이 프로그램은 순수하게 개인용으로 제작한 것입니다. 문의 hwcho@me.com")
    with 헤더오른쪽:
        if 헤더이미지경로 is not None:
            st.image(str(헤더이미지경로), use_container_width=True)
        else:
            st.markdown(
                """
                <div style="height: 220px; border-radius: 18px;
                            background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
                            display:flex; align-items:center; justify-content:center;
                            color:#64748b; font-size:15px; text-align:center; padding:18px;">
                    header_photo.jpg 파일을 같은 폴더에 두면
                    이 영역에 상단 이미지가 표시됩니다.
                </div>
                """,
                unsafe_allow_html=True,
            )

if not PLOTLY_AVAILABLE:
    st.error("plotly가 설치되어 있지 않습니다. 터미널에서 'pip install plotly' 후 다시 실행해 주세요.")
    st.stop()


st.info("이 버전은 공유용입니다. 사이드바에서 본인 거래이력 파일을 업로드하거나 거래를 직접 입력해 주세요.")

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
    "삼성전자": {"구분": "stock", "코드": "005930"},
    "SK하이닉스": {"구분": "stock", "코드": "000660"},
}

관심종목 = {
    "069500": "KODEX 200",
    "229200": "KODEX 코스닥150",
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
    }
    return 별칭매핑.get(이름, 이름)


def 종목코드기준종목명(종목코드):
    코드 = "" if pd.isna(종목코드) else re.sub(r"[^0-9]", "", str(종목코드)).zfill(6)
    if 코드 in 코드명매핑:
        return 코드명매핑[코드]
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


기본포트폴리오 = pd.DataFrame([
    {"종목코드": "069500", "종목명": "KODEX 200", "거래일자": "2026-03-10", "거래구분": "매수", "거래수량": 10, "거래단가": 82000, "운용사": "샘플계좌", "비고": "예시 데이터"},
    {"종목코드": "005930", "종목명": "삼성전자", "거래일자": "2026-03-11", "거래구분": "매수", "거래수량": 5, "거래단가": 180000, "운용사": "샘플계좌", "비고": "예시 데이터"},
    {"종목코드": "000660", "종목명": "SK하이닉스", "거래일자": "2026-03-12", "거래구분": "매수", "거래수량": 1, "거래단가": 920000, "운용사": "샘플계좌", "비고": "예시 데이터"},
    {"종목코드": "229200", "종목명": "KODEX 코스닥150", "거래일자": "2026-03-13", "거래구분": "매수", "거래수량": 8, "거래단가": 20000, "운용사": "샘플계좌", "비고": "예시 데이터"},
])
기본포트폴리오["거래일자"] = pd.to_datetime(기본포트폴리오["거래일자"], errors="coerce").dt.date

시장지표네이버URL = {

    "USD/KRW": "https://finance.naver.com/marketindex/exchangeDetail.naver?marketindexCd=FX_USDKRW",
    "국제 금": "https://finance.naver.com/marketindex/worldGoldDetail.naver",
    "국내 금": "https://finance.naver.com/marketindex/goldDetail.naver",
    "WTI": "https://finance.naver.com/marketindex/worldDailyQuoteDetail.naver?marketindexCd=OIL_CL",
    "브렌트유": "https://finance.naver.com/marketindex/worldDailyQuoteDetail.naver?marketindexCd=OIL_BRT",
    "두바이유": "https://finance.naver.com/marketindex/worldDailyQuoteDetail.naver?marketindexCd=OIL_DUB",
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

    표준열 = ["종목코드", "종목명", "거래일자", "거래구분", "거래수량", "거래단가", "운용사", "비고"]
    for 열 in 표준열:
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

    for 열 in 표준열:
        if 열 not in 결과.columns:
            결과[열] = None if 열 in ["거래일자", "거래수량", "거래단가"] else ""

    return 결과[표준열]


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

    결과 = 결과.dropna(how="all")
    결과["_거래일자정렬"] = pd.to_datetime(결과["거래일자"], errors="coerce")
    결과["_입력순서"] = range(len(결과))
    결과 = 결과.sort_values(by=["_거래일자정렬", "_입력순서"], ascending=[False, False], na_position="last")
    결과 = 결과.drop(columns=["_거래일자정렬", "_입력순서"], errors="ignore").reset_index(drop=True)
    return 결과

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
    return 안전JSON저장(목표비중, 목표비중저장파일)



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
        return df.set_index("날짜").sort_index()
    except Exception:
        return pd.DataFrame()


def _야후종목ETF_OHLCV조회(코드, 시작문자열, 종료문자열):
    코드 = str(코드).zfill(6)
    후보심볼 = [f"{코드}.KS", f"{코드}.KQ"]
    for 심볼 in 후보심볼:
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
    return _야후인덱스OHLCV조회(시작문자열, 종료문자열, 코드)


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
    거래원본 = 거래원본.sort_values(["종목코드", "거래일자", "종목명"]).reset_index(drop=True)

    집계결과 = []

    for (종목코드, 종목명), 그룹 in 거래원본.groupby(["종목코드", "종목명"], sort=False):
        총매수수량 = 총매수금액 = 총매도수량 = 총매도금액 = 실현손익 = 0.0
        보유수량 = 보유원가 = 과잉매도수량 = 0.0
        최초매수일자 = pd.NaT
        최근거래일자 = pd.NaT

        for _, 행 in 그룹.iterrows():
            거래일자 = 행["거래일자"]
            거래구분 = str(행["거래구분"]).strip()
            수량 = float(행["거래수량"])
            단가 = float(행["거래단가"])
            최근거래일자 = 거래일자

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
            "종목코드": 종목코드,
            "종목명": 종목명자동보정(종목코드, 종목명),
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
        if code in ["069500", "229200"]:
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
st.caption("주요 지수와 대표 종목의 현재가·전일대비·등락률은 Yahoo 기반 최근 거래일 데이터로 계산합니다.")

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
        분석탭 = st.tabs(["ChatGPT 스타일", "Gemini 스타일", "Claude 스타일"])
        with 분석탭[0]:
            분석카드표시(분석문구["ChatGPT"])
        with 분석탭[1]:
            분석카드표시(분석문구["Gemini"])
        with 분석탭[2]:
            분석카드표시(분석문구["Claude"])

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
        use_container_width=True,
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
st.subheader("포트폴리오 거래 이력 입력")
st.caption(
    "업로드한 거래이력 파일 내용을 기본 거래내역에 반영했습니다. 거래일자·거래구분·거래수량·거래단가·운용사를 입력하면 아래 포트폴리오 현황이 자동 집계됩니다. "
    "종목코드 또는 종목명 중 하나를 입력하면 다른 값은 가능한 범위에서 자동 보정됩니다. 거래단가는 숫자로 입력해 주세요. CSV뿐 아니라 엑셀(xlsx, xls) 업로드도 지원합니다."
)

if "trade_history_df_v22" not in st.session_state:
    자동저장df = 자동저장불러오기(거래이력자동저장파일)

    if 자동저장df is not None and not 자동저장df.empty:
        st.session_state["trade_history_df_v22"] = 자동저장df
    else:
        st.session_state["trade_history_df_v22"] = 거래이력입력창정렬(거래이력자동보정(기본포트폴리오.copy()))

업로드파일 = st.file_uploader(
    "거래이력 파일 불러오기",
    type=["csv", "json", "xlsx", "xls"],
    key="trade_history_file_uploader_v24"
)

저장파일명 = f"거래이력_저장_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"

if 모바일여부():
    if st.button("업로드 파일 반영", disabled=업로드파일 is None, key="apply_upload_btn_v26"):
        try:
            불러온df = 업로드파일에서거래이력읽기(업로드파일)
            보정df = 거래이력자동보정(불러온df.copy())
            st.session_state["trade_history_df_v22"] = 거래이력입력창정렬(보정df)

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

    st.download_button(
        "현재 거래내역 저장",
        data=현재거래내역엑셀저장바이트(st.session_state["trade_history_df_v22"]),
        file_name=저장파일명,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="save_trade_history_btn_v26"
    )
else:
    버튼칸1, 버튼칸2, 버튼칸3 = st.columns([1.2, 1.2, 6])
    with 버튼칸1:
        if st.button("업로드 파일 반영", disabled=업로드파일 is None, key="apply_upload_btn_v26"):
            try:
                불러온df = 업로드파일에서거래이력읽기(업로드파일)
                보정df = 거래이력자동보정(불러온df.copy())
                st.session_state["trade_history_df_v22"] = 거래이력입력창정렬(보정df)

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
            key="save_trade_history_btn_v26"
        )

        st.download_button(
            "거래이력 JSON 백업 다운로드",
            data=json.dumps(거래이력JSON변환(st.session_state["trade_history_df_v22"]), ensure_ascii=False, indent=2),
            file_name=f"거래이력_백업_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json",
            key="save_trade_history_json_btn_v26"
        )

st.info("거래이력 파일은 CSV, JSON, Excel(xlsx/xls) 형식을 불러올 수 있습니다. 거래구분은 매수/매도만 사용해 주세요.")

편집대상거래이력 = 거래이력입력창정렬(st.session_state["trade_history_df_v22"])
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
수정포트폴리오 = 거래이력자동보정(수정포트폴리오.reset_index(drop=True))
수정포트폴리오 = 거래이력입력창정렬(수정포트폴리오)
st.session_state["trade_history_df_v22"] = 수정포트폴리오.copy()

자동저장성공, 자동저장메시지 = 거래이력자동저장실행(st.session_state["trade_history_df_v22"])
if 자동저장성공:
    st.caption("거래이력이 자동저장되었습니다.")
else:
    st.warning(f"자동저장 실패: {자동저장메시지}")

입력검증표 = 거래이력검증표생성(수정포트폴리오)
불일치검증표 = 입력검증표[입력검증표["점검항목"] == "종목코드-종목명 불일치"] if not 입력검증표.empty else pd.DataFrame()
if not 불일치검증표.empty:
    st.error(f'종목코드와 종목명이 서로 맞지 않는 입력이 {len(불일치검증표)}건 있습니다. 자동으로 다른 종목으로 바꾸지 않고 그대로 표시했습니다.')
if 입력검증표.empty:
    st.success("거래이력 입력 점검 결과: 현재 확인된 형식 오류가 없습니다.")
else:
    st.warning(f"거래이력 입력 점검 결과: {len(입력검증표)}건의 확인 사항이 있습니다.")
    with st.expander("입력 검증 상세 보기", expanded=False):
        st.dataframe(index_1부터(입력검증표), use_container_width=True)


# -----------------------------------
# 포트폴리오 계산 결과
# -----------------------------------
계산포트폴리오 = 포트폴리오계산(수정포트폴리오)

과잉매도종목 = 계산포트폴리오[계산포트폴리오["과잉매도수량"] > 0]
if not 과잉매도종목.empty:
    st.warning("보유수량보다 많이 매도한 거래가 있습니다. 거래이력을 확인해 주세요.")

st.markdown("---")
st.subheader("포트폴리오 현황")

if 계산포트폴리오.empty:
    st.warning("포트폴리오 데이터를 계산할 수 없습니다.")
else:
    정상평가행 = 계산포트폴리오[계산포트폴리오["데이터상태"] == "정상"].copy()

    총투자원금 = 정상평가행["투자원금"].sum()
    총평가금액 = 정상평가행["평가금액"].sum()
    총평가손익 = 정상평가행["평가손익"].sum()
    총실현손익 = 계산포트폴리오["실현손익"].sum()
    총수익률 = (총평가손익 / 총투자원금 * 100) if 총투자원금 != 0 else 0

    조회실패건수 = (계산포트폴리오["데이터상태"] != "정상").sum()
    if 조회실패건수 > 0:
        st.warning(f"현재가 조회 실패 종목 {조회실패건수}건은 평가금액/비중 계산에서 제외했습니다.")

    if 모바일여부():
        행1_1, 행1_2 = st.columns(2)
        행1_1.metric("총 투자원금", 금액표시(총투자원금))
        행1_2.metric("총 평가금액", 금액표시(총평가금액))

        행2_1, 행2_2 = st.columns(2)
        행2_1.metric("미실현 손익", 손익문자열(총평가손익) + "원")
        행2_2.metric("실현 손익", 손익문자열(총실현손익) + "원")

        st.metric("보유 수익률", 수익률문자열(총수익률))
    else:
        위칸1, 위칸2, 위칸3, 위칸4, 위칸5 = st.columns(5)
        위칸1.metric("총 투자원금", 금액표시(총투자원금))
        위칸2.metric("총 평가금액", 금액표시(총평가금액))
        위칸3.metric("미실현 손익", 손익문자열(총평가손익) + "원")
        위칸4.metric("실현 손익", 손익문자열(총실현손익) + "원")
        위칸5.metric("보유 수익률", 수익률문자열(총수익률))

    포트폴리오표시 = 계산포트폴리오[["종목코드", "종목명", "최초매수일자", "최근거래일자", "총매수수량", "총매도수량", "보유수량", "매입평균단가", "현재가", "투자원금", "평가금액", "평가손익", "실현손익", "수익률", "현재비중", "과잉매도수량", "데이터상태"]].copy()
    포트폴리오표시 = 포트폴리오표시.rename(columns={"매입평균단가": "매입 평균단가", "총매수수량": "총 매수수량", "총매도수량": "총 매도수량", "최초매수일자": "최초 매수일자", "최근거래일자": "최근 거래일자", "과잉매도수량": "과잉 매도수량"})
    포트폴리오표시 = 포트폴리오표_컬럼선택(포트폴리오표시)
    포트폴리오표시 = index_1부터(포트폴리오표시)

    if 모바일여부():
        모바일형식사전 = {}
        if "보유수량" in 포트폴리오표시.columns:
            모바일형식사전["보유수량"] = "{:,.0f}"
        if "현재가" in 포트폴리오표시.columns:
            모바일형식사전["현재가"] = "{:,.0f}"
        if "평가금액" in 포트폴리오표시.columns:
            모바일형식사전["평가금액"] = "{:,.0f}"
        if "수익률" in 포트폴리오표시.columns:
            모바일형식사전["수익률"] = 수익률문자열
        모바일스타일 = 포트폴리오표시.style.format(모바일형식사전)
        if "수익률" in 포트폴리오표시.columns:
            모바일스타일 = 모바일스타일.map(수익률색상, subset=["수익률"])
        st.dataframe(모바일스타일, use_container_width=True)
    else:
        st.dataframe(
            포트폴리오표시.style.format({
                "총 매수수량": "{:,.0f}",
                "총 매도수량": "{:,.0f}",
                "보유수량": "{:,.0f}",
                "과잉 매도수량": "{:,.0f}",
                "매입 평균단가": "{:,.0f}",
                "현재가": "{:,.0f}",
                "투자원금": "{:,.0f}",
                "평가금액": "{:,.0f}",
                "평가손익": 손익문자열,
                "실현손익": 손익문자열,
                "수익률": 수익률문자열,
                "현재비중": "{:.2f}",
            }).map(손익색상, subset=["평가손익", "실현손익"]).map(수익률색상, subset=["수익률"]),
            use_container_width=True,
        )
    오류행 = 계산포트폴리오[(계산포트폴리오["과잉매도수량"] > 0) | (계산포트폴리오["데이터상태"] != "정상")]
    if not 오류행.empty:
        st.warning("일부 종목에 과잉 매도 입력 또는 현재가 조회 실패가 있습니다. 아래 현황표의 '과잉 매도수량', '데이터상태'를 확인해 주세요.")

    현재가실패표 = 계산포트폴리오[계산포트폴리오["데이터상태"] != "정상"][ ["종목코드", "종목명", "데이터상태"] ].copy()
    if not 현재가실패표.empty:
        st.error(f"현재가 조회 실패 종목이 {len(현재가실패표)}개 있습니다. 종목코드와 장중/휴장 여부, 네트워크 상태를 확인해 주세요.")
        with st.expander("현재가 조회 실패 종목 보기", expanded=False):
            st.dataframe(index_1부터(현재가실패표), use_container_width=True)

    st.plotly_chart(비중그래프(계산포트폴리오), width="stretch")


# -----------------------------------
# 목표비중 설정
# -----------------------------------
st.markdown("---")
st.subheader("목표 비중 설정")

저장된목표비중 = 목표비중불러오기()
if 모바일여부():
    목표행1_1, 목표행1_2 = st.columns(2)
    with 목표행1_1:
        목표_KODEX200 = st.number_input("KODEX 200 목표 비중(%)", 0.0, 100.0, float(저장된목표비중["069500"]), 1.0)
    with 목표행1_2:
        목표_KODEX코스닥150 = st.number_input("KODEX 코스닥150 목표 비중(%)", 0.0, 100.0, float(저장된목표비중["229200"]), 1.0)

    목표행2_1, 목표행2_2 = st.columns(2)
    with 목표행2_1:
        목표_삼성전자 = st.number_input("삼성전자 목표 비중(%)", 0.0, 100.0, float(저장된목표비중["005930"]), 1.0)
    with 목표행2_2:
        목표_SK하이닉스 = st.number_input("SK하이닉스 목표 비중(%)", 0.0, 100.0, float(저장된목표비중["000660"]), 1.0)
else:
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
        성공, 메시지 = 목표비중저장(목표비중사전)
        if 성공:
            st.success("목표비중이 저장되었습니다.")
        else:
            st.error(메시지)
with 버튼칸2:
    st.write(f"현재 목표 비중 합계: {목표비중합계:.2f}%")

if abs(목표비중합계 - 100.0) > 0.001:
    st.error(f"목표 비중의 합계가 100%가 되어야 합니다. 현재 합계: {목표비중합계:.2f}%")
else:
    st.markdown("---")
    st.subheader("목표 비중 대비 리밸런싱")
    리밸런싱표, 현재총평가금액 = 리밸런싱계산(계산포트폴리오, 목표비중사전)

    if 모바일여부():
        st.metric("현재 총 평가금액", 금액표시(현재총평가금액))
        결과행1, 결과행2 = st.columns(2)
        결과행1.metric("목표 비중 합계", 비율표시(목표비중합계))
        결과행2.metric("리밸런싱 대상 종목 수", f"{len(리밸런싱표):,}개")
    else:
        결과칸1, 결과칸2, 결과칸3 = st.columns(3)
        결과칸1.metric("현재 총 평가금액", 금액표시(현재총평가금액))
        결과칸2.metric("목표 비중 합계", 비율표시(목표비중합계))
        결과칸3.metric("리밸런싱 대상 종목 수", f"{len(리밸런싱표):,}개")

    st.plotly_chart(목표비중비교그래프(리밸런싱표), width="stretch")

    리밸런싱표시 = 리밸런싱표[["종목명", "현재가", "현재비중", "목표비중", "비중차이", "평가금액", "목표평가금액", "리밸런싱금액", "정확계산수량", "주문참고수량", "권장방향"]].copy()
    리밸런싱표시 = 리밸런싱표_컬럼선택(리밸런싱표시)
    리밸런싱표시 = index_1부터(리밸런싱표시)

    if 모바일여부():
        모바일형식사전 = {}
        if "현재비중" in 리밸런싱표시.columns:
            모바일형식사전["현재비중"] = "{:.2f}"
        if "목표비중" in 리밸런싱표시.columns:
            모바일형식사전["목표비중"] = "{:.2f}"
        st.dataframe(리밸런싱표시.style.format(모바일형식사전), use_container_width=True)
    else:
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
            use_container_width=True,
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
        use_container_width=True,
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
st.write("※ 거래 이력표는 같은 종목을 여러 줄로 입력할 수 있으며, 포트폴리오 현황은 거래 이력을 이동평균법 기준으로 자동 집계합니다.")
st.write("※ 주요 지수와 종목 차트는 Yahoo 기반 경로를 사용하도록 구성했습니다.")
st.write("※ 네이버 금융 시장지표 HTML 구조가 바뀌면 Yahoo 및 파생 계산값으로 가능한 범위까지 대체하도록 구성했습니다.")
