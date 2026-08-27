"""
통합자산관리 시스템 v2.0
- 신한은행 IRP (TDF + ETF)
- 미래에셋증권 (국내주식 + ETF)
- Google Sheets 실시간 연동
- yfinance 실시간 시세
- Google OAuth 로그인 (v2.0: 아이디/비밀번호 방식 폐지, 개인 소유 시트로 전환)
"""

import streamlit as st
import pandas as pd
import re
import numpy as np
import gspread
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.oauth2.credentials import Credentials as UserOAuthCredentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from cryptography.fernet import Fernet, InvalidToken
from datetime import datetime, date, timedelta
from pykrx import stock as krx_stock
from zoneinfo import ZoneInfo
import logging
import hmac
import hashlib
import base64
import time
import secrets as pysecrets
import requests  # DART corpCode.xml 다운로드용 (2026-08-12 추가)
import zipfile
import xml.etree.ElementTree as ET
from io import BytesIO

# ============================================================
# 기본 설정
# ============================================================
logging.basicConfig(level=logging.WARNING)
KST = ZoneInfo("Asia/Seoul")

# 모든 Plotly 차트에 공통 적용하는 설정: 오른쪽 위 모드바(카메라·확대·이동 등 아이콘 묶음)를
# 완전히 숨긴다. 범례·주석(최고가·최저가 라벨 등)이 차트 오른쪽 위와 겹쳐 보이던 문제의
# 근본 원인이 이 모드바였는데, 이 앱은 이미지 다운로드·확대 등 모드바 기능을 실제로 쓸 일이
# 거의 없으므로 마진을 조정하는 대신 아예 숨겨서 겹침 문제를 근본적으로 없앤다.
PLOTLY_CONFIG = {
    "displayModeBar": False,
    # [2026-08-11 추가] 모바일에서 차트를 손가락으로 확대하려고 핀치(꼬집기) 제스처를 하면,
    # 기본값(scrollZoom=False)에서는 이 제스처를 차트가 아니라 브라우저가 페이지 전체 확대로
    # 가로채간다. scrollZoom을 켜면 Plotly가 그 영역의 터치 제스처를 직접 가져가서
    # '차트 자체'를 확대/축소하게 되고, 페이지 전체가 커지는 문제가 사라진다.
    "scrollZoom": True,
}
APP_VERSION = "v2.1.4"
# [2026-08-19] v2.1.3 → v2.1.4: 장 마감 후(15:30~20:00, NXT 애프터마켓) 안내 배너 추가.
# 기존엔 장 시작 전(09:00 이전)만 안내했는데, 같은 원인(NXT 미반영)이 장 마감 후에도
# 재현되는 게 Jone 실측(16:52, ETF는 일치·개별주식만 벌어짐)으로 확인되어 확장함.

st.set_page_config(
    page_title=f"통합자산관리 시스템 {APP_VERSION}",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# 사용자마다 브라우저 메뉴에서 라이트/다크를 각자 다르게 선택할 수 있으므로(로컬 저장이라
# 서버에서 강제로 통일할 수 없음), 접속한 사용자 본인의 현재 테마를 실시간으로 확인해
# 카드·배지·글씨 색상을 그에 맞게 자동으로 전환한다.
try:
    IS_LIGHT_THEME = (st.context.theme.type == "light")
except Exception:
    IS_LIGHT_THEME = False

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
    "292150": {"name": "TIGER 코리아TOP10",     "ticker": "292150.KS", "type": "ETF",  "market": "KS"},
    "005930": {"name": "삼성전자",            "ticker": "005930.KS", "type": "주식", "market": "KS"},
    "000660": {"name": "SK하이닉스",          "ticker": "000660.KS", "type": "주식", "market": "KS"},
    "278470": {"name": "에이피알",            "ticker": "278470.KS", "type": "주식", "market": "KS"},
    "009150": {"name": "삼성전기",            "ticker": "009150.KS", "type": "주식", "market": "KS"},
    "005380": {"name": "현대차",             "ticker": "005380.KS", "type": "주식", "market": "KS"},
    "042660": {"name": "한화오션",            "ticker": "042660.KS", "type": "주식", "market": "KS"},
    "071970": {"name": "HD현대마린엔진",       "ticker": "071970.KS", "type": "주식", "market": "KS"},
    "034020": {"name": "두산에너빌리티",       "ticker": "034020.KS", "type": "주식", "market": "KS"},
}

# 국내 상장 ETF는 대부분 이 브랜드명으로 시작 — ASSET_MASTER에 등록되지 않은 새 종목이 들어와도
# 종목명만으로 ETF/주식을 자동 구분하기 위한 보조 목록 (신규 사용자의 미등록 종목 대응)
ETF_BRAND_PREFIXES = (
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "SOL", "ACE", "HANARO",
    "KOSEF", "PLUS", "RISE", "WOORI", "마이다스", "히어로즈", "TIMEFOLIO",
)

def get_asset_market(code: str) -> str:
    """종목코드로 국내(KR)/해외(US) 시장을 판별.
    ASSET_MASTER에 등록된 종목은 등록된 market 값을 따르고, 미등록 종목은 코드 형태로
    추정한다: 국내 종목코드는 숫자 6자리이거나 숫자+영문 혼합 6자리(예: 0148J0) 규칙을
    따르므로, 이 규칙에 안 맞고 영문자로만 이루어진 코드(예: AAPL, MSFT)는 해외(미국)
    종목으로 판단한다. (해외 주식 지원 — 2026-08 추가)"""
    code = str(code).strip().upper()
    meta = ASSET_MASTER.get(code)
    if meta:
        return "US" if meta.get("market") == "US" else "KR"
    if re.fullmatch(r"[A-Z]{1,5}", code):
        return "US"
    return "KR"

def get_asset_ticker(code: str) -> str:
    """ASSET_MASTER에 등록된 종목은 등록된 티커를, 미등록 종목코드는 시장 판별 결과에 따라
    국내는 KRX 6자리 코드 규칙으로(코드.KS), 해외(미국)는 코드 그대로(접미사 없음) 티커를
    생성해 반환한다. 신규 사용자가 보유한 임의의 종목코드도 별도 등록 없이 실시간 시세
    조회가 되도록 하기 위함."""
    code = str(code).strip()
    if not code:
        return ""
    meta = ASSET_MASTER.get(code)
    if meta:
        return meta["ticker"]
    if get_asset_market(code) == "US":
        return code.upper()
    return f"{code}.KS"

def get_asset_type(code: str, name: str = "") -> str:
    """ASSET_MASTER에 등록된 종목은 등록된 유형을, 미등록 종목은 종목명 앞부분(ETF 브랜드명)으로
    ETF 여부를 추정해 반환. 등록되지 않은 종목이라도 '구분' 표시가 항상 채워지도록 한다."""
    code = str(code).strip()
    meta = ASSET_MASTER.get(code)
    if meta:
        return meta["type"]
    name_str = str(name).strip().upper()
    if any(name_str.startswith(p.upper()) for p in ETF_BRAND_PREFIXES):
        return "ETF"
    return "주식"

# ============================================================
# DART 공시 고유번호(corp_code) 자동 조회 — [2026-08-12 추가]
# ============================================================
# DART는 증권시장 종목코드(예: 278470)만으로는 공시를 조회할 수 없고, 별도의 8자리
# "고유번호(corp_code)"가 있어야 한다. 이 매핑은 DART 웹페이지 어디에도 노출되지 않고
# corpCode.xml이라는 전용 API(대용량 zip 파일)로만 받을 수 있다. ASSET_MASTER에 새
# 개별기업이 추가될 때마다 매번 손으로 고유번호를 찾아 하드코딩하면 놓치기 쉽고
# (2026-08-12 실제로 3개 종목이 누락된 채로 발견됨), 그래서 정적 딕셔너리 대신 여기서는
# 전체 매핑을 1회 다운로드해 24시간 캐시해두고 종목코드로 바로 조회하는 방식을 쓴다.
# ETF는 DART가 아니라 KRX의 KIND 시스템에서 공시를 관리하므로 이 매핑에 원천적으로
# 존재하지 않는다 (정상 동작 — None이 반환되면 ETF이거나 상장기업이 아닌 것으로 처리).
#
# [필요 설정] secrets.toml에 아래 섹션 추가 필요 (없으면 조회가 항상 실패로 처리됨):
#   [dart]
#   api_key = "발급받은 DART Open API 인증키"

@st.cache_data(ttl=86400)
def _get_dart_corp_code_map() -> dict[str, str]:
    """DART 고유번호 전체 목록을 다운로드해 {종목코드: 고유번호} 딕셔너리로 변환
    (24시간 캐시 — 파일 크기가 크고 자주 바뀌는 정보가 아니므로 매 요청마다 받을 필요 없음).
    상장사가 아니라 종목코드가 없는 회사는 애초에 매핑에서 제외한다.
    실패 시 빈 딕셔너리를 반환 — 호출부(get_dart_corp_code)에서 자연스럽게 '조회 불가'로
    처리되게 해서, 이 기능 하나 때문에 앱 전체나 다른 화면이 죽는 일이 없도록 한다."""
    try:
        api_key = st.secrets["dart"]["api_key"]
        resp = requests.get(
            "https://opendart.fss.or.kr/api/corpCode.xml",
            params={"crtfc_key": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            xml_bytes = zf.read("CORPCODE.xml")
        root = ET.fromstring(xml_bytes)
        mapping: dict[str, str] = {}
        for item in root.iter("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code = (item.findtext("corp_code") or "").strip()
            if stock_code and corp_code:
                mapping[stock_code] = corp_code
        return mapping
    except Exception as e:
        logging.warning("DART 고유번호 목록 다운로드 실패: %s", e)
        return {}

def get_dart_corp_code(stock_code: str) -> str | None:
    """종목코드(예: '278470')로 DART 고유번호(8자리)를 조회해 반환한다. 상장기업이
    아니거나(ETF 등) DART 목록 다운로드 자체가 실패한 경우 None을 반환한다.
    사용 예: corp_code = get_dart_corp_code("005930")  # 삼성전자 -> '00126380'"""
    stock_code = str(stock_code).strip()
    return _get_dart_corp_code_map().get(stock_code)

# 계좌별 카드 배지 색상 팔레트 — 계좌 수가 몇 개든(신한/미래에셋뿐 아니라 향후 추가 계좌도)
# 순서대로 돌려가며 배정하기 위함. (bg, fg) 튜플.
ACCOUNT_COLOR_PALETTE = [
    ("rgba(59,130,246,0.16)",  "#7fb2f5"),   # 파랑
    ("rgba(29,158,117,0.16)",  "#4ecb9a"),   # 초록
    ("rgba(234,179,8,0.18)",   "#f5cf6b"),   # 호박색
    ("rgba(168,85,247,0.16)",  "#c79bf0"),   # 보라
    ("rgba(236,72,153,0.16)",  "#f2a0c6"),   # 핑크
    ("rgba(20,184,166,0.16)",  "#7fe3d4"),   # 청록
]

def get_account_color(acct_name: str, acct_order: list) -> tuple:
    """계좌 이름을 acct_order 내 순번에 따라 팔레트 색상에 매핑 (bg, fg) 반환."""
    try:
        idx = acct_order.index(acct_name)
    except ValueError:
        idx = 0
    return ACCOUNT_COLOR_PALETTE[idx % len(ACCOUNT_COLOR_PALETTE)]


# Google Sheets 연결 (서비스 계정 — 화이트리스트 '사용자계정' 시트 전용)
# ============================================================
SHEET_NAMES = {
    "거래이력":        "거래이력",
    "비주식자산":      "비주식자산",
    "월별자산스냅샷":  "월별자산스냅샷",
    "현금출납내역":    "현금출납내역",
}

@st.cache_resource(ttl=60)
def get_gspread_client():
    """서비스 계정으로 인증하는 클라이언트.
    v2.0부터는 개인 자산 시트가 아니라, 관리자용 '사용자계정'(화이트리스트) 시트에만 사용된다.
    개인 자산 시트는 이제 각자 본인 소유이므로 아래 get_user_gspread_client()로 접근한다."""
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_service_account_info(
            creds_dict,
            scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"],
        )
        return gspread.authorize(creds)
    except Exception as e:
        logging.warning("gspread 연결 실패: %s", e)
        return None

# ============================================================
# Google OAuth (개인별 로그인 + 개인 소유 시트)
# ============================================================
OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]
APP_TAG_KEY = "owner_app"
APP_TAG_VALUE = "jone_asset_manager"

# 신규 사용자의 개인 시트를 처음 생성할 때 자동으로 만들 탭과 헤더.
# stock_app_main.py의 load_sheet()/load_sheet_optional() 호출부와 이름이 반드시 일치해야 한다.
# [2026-08-11] '현금성자산' 탭 제거 — 코드 어디에서도 읽지 않는 죽은 시트였음(비주식자산 탭의
# '현금성자산' 자산군 행이 실제로 쓰이는 데이터). 신규 사용자 시트에도 더 이상 자동 생성하지 않는다.
# [2026-08-21] 모든 탭의 컬럼 순서를 Jone 개인 시트(asset_management)의 실제 순서에 맞춤.
# 기존 순서로 만들어진 신규 유저 시트와 관리자 본인 시트의 컬럼 순서가 서로 달라 혼동이 있었음 —
# 앱은 컬럼을 이름 기준으로 읽으므로 기능상 영향은 없었지만, 앞으로 생성되는 신규 유저 시트를
# 관리자 시트와 동일한 모양으로 통일한다. (거래이력/계좌간이체/월별자산스냅샷 순서 변경,
# 계좌간이체는 "금액"이 실제로는 "이체금액"으로 쓰이고 있고 출금자산군/입금자산군 컬럼이
# 추가로 있었음이 확인되어 헤더 구성 자체도 함께 맞춤. 비주식자산/현금출납내역은 기존과 동일)
REQUIRED_SHEET_HEADERS = {
    "거래이력":        ["종목코드", "종목명", "거래일자", "거래구분", "거래수량", "거래단가", "운용사", "비고"],
    "비주식자산":      ["계좌", "자산군", "상품명", "원금", "평가금액", "반영일자", "비고"],
    "월별자산스냅샷":  ["년월", "저장시각", "통합원금", "통합평가", "통합손익", "통합수익률", "메모"],
    "계좌간이체":      ["거래일자", "출금계좌", "출금자산군", "입금계좌", "입금자산군", "이체금액", "실현손익", "비고"],
    "현금출납내역":    ["날짜", "계좌", "구분", "금액", "사유"],
}

# 시트 서식 통일 규칙: 시트별 컬럼명 -> 'money'(천단위 콤마+오른쪽정렬) / 'number'(오른쪽정렬)
# / 'percent'(%+오른쪽정렬) / 'date'(날짜·시각 형식+가운데정렬) / 'text'(가운데정렬만, 숫자서식은 손대지 않음)
# / 'text_left'(왼쪽정렬만, 숫자서식은 손대지 않음 — 비고/사유/메모 등 자유서술 칸 전용).
# 규칙에 없는 컬럼(사용자가 시트에 직접 추가한 열 등)은 'text'로 처리되어 가운데 정렬만 적용된다.
COLUMN_FORMAT_RULES = {
    "거래이력": {
        "거래일자": "date", "운용사": "text", "종목코드": "text", "종목명": "text",
        "거래구분": "text", "거래수량": "number", "거래단가": "money", "비고": "text_left",
    },
    "비주식자산": {
        "계좌": "text", "자산군": "text", "상품명": "text", "원금": "money",
        "평가금액": "money", "반영일자": "date", "비고": "text_left",
    },
    "월별자산스냅샷": {
        "년월": "yearmonth", "저장시각": "datetime", "통합원금": "money", "통합평가": "money",
        "통합손익": "money", "통합수익률": "percent", "메모": "text_left",
    },
    "계좌간이체": {
        "거래일자": "date", "출금계좌": "text", "출금자산군": "text", "입금계좌": "text",
        "입금자산군": "text", "이체금액": "money", "실현손익": "money", "금액": "money", "비고": "text_left",
    },
    "현금출납내역": {
        "날짜": "date", "계좌": "text", "구분": "text", "금액": "money", "사유": "text_left",
    },
}

def _normalize_date_values_in_sheet(sh) -> list:
    """구글시트의 날짜 계열 컬럼(COLUMN_FORMAT_RULES에서 kind가 date/datetime/yearmonth인 컬럼)에서
    셀 '값' 자체가 텍스트("2026-03-05")와 실제 날짜(직접 입력한 날짜선택기 등)로 섞여 들어간
    경우를 통일한다. _apply_column_formatting은 화면에 보이는 표시형식(서식)만 바꾸고 값 자체는
    건드리지 않으므로, 이미 텍스트로 저장된 셀은 서식을 새로 입혀도 실제 날짜로 바뀌지 않는다.
    이 함수는 각 셀 값을 pandas로 파싱해 'YYYY-MM-DD' 문자열로 다시 쓰되, USER_ENTERED 입력
    방식을 사용해 구글시트가 이를 실제 날짜값으로 재인식하도록 만든다.
    파싱에 실패하는 값(빈 칸, 손상된 값 등)은 원래 값 그대로 두고 건드리지 않는다."""
    DATE_KINDS = {"date": "%Y-%m-%d", "datetime": "%Y-%m-%d %H:%M:%S", "yearmonth": "%Y-%m"}
    touched = []
    for sheet_name, rules in COLUMN_FORMAT_RULES.items():
        date_cols = [col for col, kind in rules.items() if kind in DATE_KINDS]
        if not date_cols:
            continue
        try:
            ws = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            continue
        header = ws.row_values(1)
        if not header:
            continue
        all_values = _call_with_retry(ws.get_all_values)
        if len(all_values) < 2:
            continue  # 헤더 외에 데이터가 없음

        sheet_changed = False
        for col_name in date_cols:
            if col_name not in header:
                continue
            col_idx = header.index(col_name)  # 0-based
            strftime_fmt = DATE_KINDS[rules[col_name]]

            new_col_values = []
            col_changed = False
            for row in all_values[1:]:
                raw = row[col_idx] if col_idx < len(row) else ""
                raw_str = str(raw).strip()
                if raw_str == "":
                    new_col_values.append([raw])
                    continue
                parsed = pd.to_datetime(raw, errors="coerce")
                if pd.isna(parsed):
                    new_col_values.append([raw])  # 파싱 불가 값은 손대지 않음
                    continue
                normalized_text = parsed.strftime(strftime_fmt)
                if normalized_text != raw_str:
                    col_changed = True
                new_col_values.append([normalized_text])

            if col_changed:
                col_letter = gspread.utils.rowcol_to_a1(1, col_idx + 1).rstrip("0123456789")
                cell_range = f"{col_letter}2:{col_letter}{len(all_values)}"
                _call_with_retry(
                    ws.update, new_col_values, cell_range, value_input_option="USER_ENTERED"
                )
                sheet_changed = True

        if sheet_changed:
            touched.append(sheet_name)
    return touched

def _apply_column_formatting(sh) -> list:
    """이미 열려 있는 gspread Spreadsheet 객체(sh)에 금액 천단위 콤마·정렬·글꼴 서식을 일괄 적용.
    신규 사용자의 시트를 처음 만들 때(_initialize_sheet_structure)와, 기존 사용자가
    '데이터 관리' 탭에서 버튼으로 요청할 때(apply_sheet_formatting) 양쪽이 공유하는 실제 로직.
    셀 값(내용) 자체는 절대 건드리지 않고 표시 형식(글꼴·숫자 포맷·정렬)만 바꾼다.
    [주의] 시트·컬럼마다 별도로 API를 호출하면 호출 수가 많아져 429(할당량 초과) 위험이 커지므로,
    모든 서식 변경 요청을 한 번에 모아서 batch_update 단 1회 호출로 처리한다."""
    BASE_FONT = {"fontFamily": "Arial", "fontSize": 10}

    formatted = []
    requests = []
    for sheet_name, rules in COLUMN_FORMAT_RULES.items():
        try:
            ws = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            continue  # 이 사용자에게 없는(선택적) 탭은 건너뜀
        header = ws.row_values(1)
        if not header:
            continue

        sheet_id = ws.id
        ncols = len(header)
        # 이전에 손으로 서식을 만지다 보면 행마다 폰트가 제각각이거나, 뒤쪽에 추가된 행이
        # 시트의 공식 row_count보다 실제로 더 많을 수 있어 넉넉하게 여유를 둔다.
        last_row = max(int(ws.row_count), 3000)

        # 헤더 행: 굵게 + 가운데 정렬 + 글꼴 통일
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1,
                    "startColumnIndex": 0, "endColumnIndex": ncols,
                },
                "cell": {"userEnteredFormat": {
                    "textFormat": {**BASE_FONT, "bold": True},
                    "horizontalAlignment": "CENTER",
                }},
                "fields": "userEnteredFormat(textFormat,horizontalAlignment)",
            }
        })

        for i, col_name in enumerate(header):
            kind = rules.get(str(col_name).strip(), "text")

            # [수정] "거래이력" 시트의 "거래단가" 컬럼만 예외적으로 소수점이 있을 수 있다
            # (해외 종목 원화 미환산 원본 달러 가격, 예: 230.5). 그 외 모든 금액 컬럼은
            # 항상 원화 정수이므로, 구글시트 공식 문서에 나온 대로 "소수점을 서식에 넣으면
            # 정수여도 마침표(.)가 항상 그려진다"는 규칙(developers.google.com/sheets/guides/formats)
            # 때문에 전체 시트에 소수점 서식을 걸면 정수 칸에도 죄다 "320,000."처럼 마침표만
            # 남는 문제가 생긴다. 그래서 "거래단가" 컬럼만 종목코드로 국내/해외를 나눠 행 단위로
            # 서식을 다르게 걸고, 나머지 금액 컬럼은 전부 소수점 없는 정수 서식으로 되돌린다.
            # (2026-08 발견·수정)
            if sheet_name == "거래이력" and col_name == "거래단가":
                try:
                    code_col_idx = header.index("종목코드")
                except ValueError:
                    code_col_idx = None
                all_values = ws.get_all_values()
                data_rows = all_values[1:]

                groups = []  # (market, start_idx, end_idx) — data_rows 기준 0-based, 연속 구간 묶음
                cur_market, cur_start = None, None
                for idx, row in enumerate(data_rows):
                    code = row[code_col_idx] if code_col_idx is not None and code_col_idx < len(row) else ""
                    market = "US" if code and get_asset_market(code) == "US" else "KR"
                    if market != cur_market:
                        if cur_market is not None:
                            groups.append((cur_market, cur_start, idx - 1))
                        cur_market, cur_start = market, idx
                if cur_market is not None:
                    groups.append((cur_market, cur_start, len(data_rows) - 1))
                # 아직 값이 없는 여유 행(last_row까지)도 국내(정수) 서식을 미리 걸어둬서,
                # 새로 입력될 국내 거래도 계속 마침표 없이 표시되도록 한다
                if len(data_rows) < (last_row - 1):
                    groups.append(("KR", len(data_rows), last_row - 2))

                for market, s, e in groups:
                    pattern = "#,##0.##" if market == "US" else "#,##0"
                    requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 1 + s, "endRowIndex": 1 + e + 1,
                                "startColumnIndex": i, "endColumnIndex": i + 1,
                            },
                            "cell": {"userEnteredFormat": {
                                "numberFormat": {"type": "NUMBER", "pattern": pattern},
                                "horizontalAlignment": "RIGHT",
                                "textFormat": {**BASE_FONT, "bold": False},
                            }},
                            "fields": "userEnteredFormat(numberFormat,horizontalAlignment,textFormat)",
                        }
                    })
                continue  # 이 컬럼은 위에서 이미 처리했으므로 아래 공통 분기는 건너뜀

            if kind in ("money", "number"):
                # 이 컬럼들은 예외(위의 거래단가) 없이 전부 원화 정수라서, 소수점 자체를
                # 서식에 넣지 않는다 — "#,##0.##"처럼 소수점을 넣으면 값이 정수여도
                # 구글시트가 마침표(.)를 항상 그려버리는 문제(공식 문서에 명시된 동작)를
                # 원천적으로 피하기 위함.
                cell_format = {
                    "numberFormat": {"type": "NUMBER", "pattern": "#,##0"},
                    "horizontalAlignment": "RIGHT",
                    "textFormat": {**BASE_FONT, "bold": False},
                }
                fields = "userEnteredFormat(numberFormat,horizontalAlignment,textFormat)"
            elif kind == "percent":
                cell_format = {
                    "numberFormat": {"type": "NUMBER", "pattern": '0.00"%"'},
                    "horizontalAlignment": "RIGHT",
                    "textFormat": {**BASE_FONT, "bold": False},
                }
                fields = "userEnteredFormat(numberFormat,horizontalAlignment,textFormat)"
            elif kind == "date":
                cell_format = {
                    "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"},
                    "horizontalAlignment": "CENTER",
                    "textFormat": {**BASE_FONT, "bold": False},
                }
                fields = "userEnteredFormat(numberFormat,horizontalAlignment,textFormat)"
            elif kind == "datetime":
                cell_format = {
                    "numberFormat": {"type": "DATE_TIME", "pattern": "yyyy-mm-dd hh:mm:ss"},
                    "horizontalAlignment": "CENTER",
                    "textFormat": {**BASE_FONT, "bold": False},
                }
                fields = "userEnteredFormat(numberFormat,horizontalAlignment,textFormat)"
            elif kind == "yearmonth":
                cell_format = {
                    "numberFormat": {"type": "DATE", "pattern": "yyyy-mm"},
                    "horizontalAlignment": "CENTER",
                    "textFormat": {**BASE_FONT, "bold": False},
                }
                fields = "userEnteredFormat(numberFormat,horizontalAlignment,textFormat)"
            elif kind == "text_left":
                # 비고/사유/메모 등 자유서술 칸: 가운데 정렬 대신 왼쪽 정렬.
                # 마찬가지로 numberFormat 필드는 요청하지 않는다(아래 else와 동일한 이유).
                cell_format = {
                    "horizontalAlignment": "LEFT",
                    "textFormat": {**BASE_FONT, "bold": False},
                }
                fields = "userEnteredFormat(horizontalAlignment,textFormat)"
            else:
                # 순수 텍스트 칸(종목명 등)은 numberFormat 필드를 아예 요청하지 않는다.
                # fields 마스크에 numberFormat을 넣어놓고 값을 안 채우면 구글이 기존 서식을
                # 지워버려 날짜가 일련번호로 깨지는 버그가 있었다 — 그 원인을 제거한 부분.
                cell_format = {
                    "horizontalAlignment": "CENTER",
                    "textFormat": {**BASE_FONT, "bold": False},
                }
                fields = "userEnteredFormat(horizontalAlignment,textFormat)"

            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": last_row,
                        "startColumnIndex": i, "endColumnIndex": i + 1,
                    },
                    "cell": {"userEnteredFormat": cell_format},
                    "fields": fields,
                }
            })

        formatted.append(sheet_name)

    if requests:
        _call_with_retry(sh.batch_update, {"requests": requests})
    return formatted

def apply_sheet_formatting(spreadsheet_id: str) -> tuple[bool, str]:
    """'데이터 관리' 탭의 버튼에서 호출 — 이미 쓰고 있는 개인 시트에 서식 규칙을 소급 적용.
    1) 날짜 계열 컬럼의 셀 '값' 자체(텍스트/날짜 혼재)를 먼저 통일하고,
    2) 그 다음 표시형식(폰트·정렬·숫자서식)을 적용한다. 순서가 바뀌면 텍스트로 남아있던
    날짜 셀에는 서식만 입혀지고 실제 값은 그대로 텍스트로 남아 혼재가 해결되지 않는다."""
    try:
        spreadsheet = get_spreadsheet(spreadsheet_id)
        if spreadsheet is None:
            return False, "개인 시트를 열지 못했습니다."
        normalized = _normalize_date_values_in_sheet(spreadsheet)
        formatted = _apply_column_formatting(spreadsheet)
        if not formatted:
            return False, "서식을 적용할 시트를 찾지 못했습니다."
        msg = f"서식 정리 완료: {', '.join(formatted)}"
        if normalized:
            msg += f" (날짜 값 통일: {', '.join(normalized)})"
        return True, msg
    except Exception as e:
        logging.warning("시트 서식 적용 실패: %s", e)
        return False, f"서식 적용 중 오류가 발생했습니다: {type(e).__name__} - {e}"

def get_oauth_flow():
    client_config = {
        "web": {
            "client_id": st.secrets["google_oauth"]["client_id"],
            "client_secret": st.secrets["google_oauth"]["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [st.secrets["google_oauth"]["redirect_uri"]],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=OAUTH_SCOPES)
    flow.redirect_uri = st.secrets["google_oauth"]["redirect_uri"]
    return flow

def _generate_pkce_pair():
    code_verifier = base64.urlsafe_b64encode(pysecrets.token_bytes(64)).decode("utf-8").rstrip("=")
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return code_verifier, code_challenge

@st.cache_resource(ttl=60 * 60 * 12)
def _oauth_credential_store() -> dict:
    """서버 프로세스가 살아있는 동안 '이메일 → OAuth Credentials'를 보관하는 전역 저장소.
    탭 재연결 등으로 session_state가 초기화돼도(원래 아이디/비밀번호 버전에서 겪던 것과 동일한 문제),
    서버 프로세스 자체가 재시작되지 않았다면 재로그인 없이 세션을 복구하기 위함.
    [주의] 서버가 재배포/절전 복귀 등으로 재시작되면 이 저장소는 비워지므로, 그 경우에는
    다시 'Google 계정으로 로그인' 버튼을 한 번 눌러야 한다 (대부분 동의 화면 없이 바로 통과됨)."""
    return {}

def _save_credentials(email: str, credentials):
    _oauth_credential_store()[email] = credentials

def _restore_credentials(email: str):
    return _oauth_credential_store().get(email)

def get_user_gspread_client():
    """로그인한 사용자 본인의 OAuth 자격증명으로 gspread 클라이언트 생성.
    개인 자산 시트는 drive.file 스코프이므로 이 앱이 만들었거나 사용자가 직접 연 파일만 접근 가능하며,
    서비스 계정이 아니라 반드시 사용자 본인 권한으로 열어야 한다."""
    credentials = st.session_state.get("oauth_credentials")
    if credentials is None:
        return None
    try:
        return gspread.authorize(credentials)
    except Exception as e:
        logging.warning("사용자 OAuth gspread 인증 실패: %s", e)
        return None

def get_user_email(credentials) -> str:
    oauth2_service = build("oauth2", "v2", credentials=credentials)
    user_info = oauth2_service.userinfo().get().execute()
    return user_info.get("email", "")

def _initialize_sheet_structure(credentials, spreadsheet_id: str):
    """새로 생성된 개인 시트에 필수 탭과 헤더 행을 만든다.
    Drive API로 처음 생성된 스프레드시트에는 'Sheet1' 하나만 있으므로,
    그 시트 이름을 첫 번째 탭 이름으로 바꾸고 나머지는 새로 추가한다."""
    try:
        gc = gspread.authorize(credentials)
        sh = gc.open_by_key(spreadsheet_id)
        sheet_order = list(REQUIRED_SHEET_HEADERS.keys())
        first_name = sheet_order[0]

        default_ws = sh.sheet1
        default_ws.update_title(first_name)
        default_ws.update("A1", [REQUIRED_SHEET_HEADERS[first_name]])

        for name in sheet_order[1:]:
            headers = REQUIRED_SHEET_HEADERS[name]
            ws = sh.add_worksheet(title=name, rows=200, cols=max(10, len(headers)))
            ws.update("A1", [headers])

        # 처음 만든 시트부터 금액 콤마·정렬 서식이 통일되어 있도록 바로 적용
        _apply_column_formatting(sh)
    except Exception as e:
        logging.warning("신규 시트 초기 구조 세팅 실패: %s", e)

def find_or_create_user_spreadsheet(credentials, display_name: str):
    """로그인한 사용자의 드라이브에서 이 앱이 만든 자산관리 시트를 찾거나, 없으면 새로 생성.
    반환값: (spreadsheet_id, created 여부)"""
    drive_service = build("drive", "v3", credentials=credentials)
    query = (
        f"appProperties has {{ key='{APP_TAG_KEY}' and value='{APP_TAG_VALUE}' }} "
        "and trashed = false"
    )
    results = drive_service.files().list(q=query, spaces="drive", fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"], False

    file_metadata = {
        "name": f"{display_name}_통합자산관리",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "appProperties": {APP_TAG_KEY: APP_TAG_VALUE},
    }
    new_file = drive_service.files().create(body=file_metadata, fields="id").execute()
    new_id = new_file["id"]
    _initialize_sheet_structure(credentials, new_id)
    return new_id, True

def _is_quota_error(e: Exception) -> bool:
    """구글 시트 API의 분당 요청 한도(429) 초과 오류인지 판별."""
    msg = str(e)
    return "429" in msg or "Quota exceeded" in msg or "RESOURCE_EXHAUSTED" in msg

def _is_transient_error(e: Exception) -> bool:
    """재시도해볼 가치가 있는 '일시적' 오류인지 판별.
    할당량 초과(429)뿐 아니라, 서버가 막 깨어나는 콜드 스타트 순간에 흔히 나는
    연결 타임아웃·연결 재설정·구글 쪽 5xx 오류까지 포함한다. 이런 오류들은 보통
    몇 초 뒤 재시도하면 정상적으로 해결되는데, 예전에는 429만 재시도 대상이라
    콜드 스타트 때 화이트리스트 조회가 한 번 실패하면 바로 포기해버리는 문제가 있었다."""
    if _is_quota_error(e):
        return True
    msg = str(e)
    transient_markers = (
        "500", "502", "503", "504",
        "Timeout", "timed out", "Connection aborted", "Connection reset",
        "RemoteDisconnected", "ConnectionError", "Temporary failure",
    )
    return any(marker in msg for marker in transient_markers)

def _call_with_retry(func, *args, max_retries: int = 3, base_delay: float = 2.0, **kwargs):
    """구글 API 호출 중 일시적 오류(할당량 초과, 콜드 스타트 시 타임아웃/연결 오류 등)가 나면
    잠깐 기다렸다가 자동으로 재시도한다.
    '전체 캐시 초기화'나 '시세 새로고침'을 짧은 시간 안에 여러 번 누르는 경우,
    그리고 서버가 방금 깨어나 첫 요청이 불안정한 경우를 대비."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if _is_transient_error(e) and attempt < max_retries - 1:
                time.sleep(base_delay * (attempt + 1))
                continue
            raise
    raise last_exc

@st.cache_resource(ttl=60)
def get_spreadsheet(spreadsheet_id: str):
    """사용자 개인 자산 시트를 연다.
    v2.0부터는 서비스 계정이 아니라 로그인한 사용자 본인의 OAuth 자격증명으로 접근한다
    (drive.file 스코프 특성상 서비스 계정에는 애초에 접근 권한이 없음)."""
    client = get_user_gspread_client()
    if client is None:
        return None
    try:
        return _call_with_retry(client.open_by_key, spreadsheet_id)
    except Exception as e:
        logging.warning("스프레드시트 열기 실패: %s", e)
        if _is_quota_error(e):
            st.error("⚠️ 구글 API 요청이 잠시 몰렸습니다 (분당 읽기 한도 초과). 1분 정도 기다린 후 다시 시도해주세요.")
        else:
            st.error(f"⚠️ 구글시트 열기 실패 (spreadsheet_id: {spreadsheet_id[:8]}...): {type(e).__name__} - {e}")
        return None

# 여러 시트에 공통으로 등장하는 날짜/시각 계열 컬럼명 — 구글시트에서 읽어올 때 항상
# "YYYY-MM-DD" 문자열로 통일해, 정수(일련번호)·문자열이 섞여 들어와도 이후 정렬·표시가 깨지지 않게 한다.
DATE_LIKE_COLUMNS = ("거래일자", "반영일자", "날짜", "이체일자", "등록일", "저장시각", "기준일")

def _normalize_date_value(v):
    """구글시트 셀 값이 문자열 날짜("2026-03-05")든, 날짜 일련번호(정수/실수, 1899-12-30 기준)든
    상관없이 "YYYY-MM-DD" 문자열로 통일해서 반환. 변환할 수 없는 값은 원래 문자열 그대로 둔다."""
    if v is None or v == "":
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try:
            base = pd.Timestamp("1899-12-30")
            ts = base + pd.Timedelta(days=float(v))
            # 시각까지 포함된 값(저장시각 등)은 초 단위까지, 날짜만 있는 값은 날짜까지만 표시
            if ts.hour or ts.minute or ts.second:
                return ts.strftime("%Y-%m-%d %H:%M:%S")
            return ts.strftime("%Y-%m-%d")
        except Exception:
            return str(v)
    return str(v).strip()

def _normalize_date_columns(df: pd.DataFrame) -> pd.DataFrame:
    """DATE_LIKE_COLUMNS에 해당하는 컬럼이 있으면 값의 타입(정수/문자열 혼재)에 상관없이
    문자열로 통일한다. 거래일자 등이 int/str로 섞여 들어와 정렬 시 앱이 죽는 문제를 방지."""
    for col in DATE_LIKE_COLUMNS:
        if col in df.columns:
            df[col] = df[col].apply(_normalize_date_value)
    return df

@st.cache_data(ttl=30)
def load_sheet(sheet_name: str, spreadsheet_id: str) -> pd.DataFrame:
    try:
        spreadsheet = get_spreadsheet(spreadsheet_id)
        if spreadsheet is None:
            st.error(f"⚠️ '{sheet_name}' 시트 로드 실패: 스프레드시트 자체를 열지 못했습니다 (spreadsheet_id 또는 공유 권한을 확인하세요).")
            return pd.DataFrame()
        ws = _call_with_retry(spreadsheet.worksheet, sheet_name)
        records = _call_with_retry(ws.get_all_records)
        df = pd.DataFrame(records)
        # gspread가 숫자 셀을 int로 반환 → 종목코드 앞자리 0 유실 방지
        if "종목코드" in df.columns:
            df["종목코드"] = df["종목코드"].apply(
                lambda x: str(int(x)).zfill(6) if str(x).strip().isdigit() else str(x).strip()
            )
        df = _normalize_date_columns(df)
        return df
    except Exception as e:
        logging.warning("시트 로드 실패 [%s]: %s", sheet_name, e)
        if _is_quota_error(e):
            st.error(
                f"⚠️ '{sheet_name}' 시트 로드 실패: 구글 API 요청이 잠시 몰렸습니다 (분당 읽기 한도 초과). "
                f"1분 정도 기다린 후 다시 시도해주세요."
            )
        else:
            st.error(f"⚠️ '{sheet_name}' 시트 로드 중 오류: {type(e).__name__} - {e}")
        return pd.DataFrame()


def load_sheet_optional(sheet_name: str, spreadsheet_id: str) -> pd.DataFrame:
    """load_sheet과 동일하지만, 시트 자체가 아직 없어도 화면에 에러 배너를 띄우지 않고
    조용히 빈 DataFrame을 반환한다. '현금출납내역'처럼 기존 사용자는 아직 안 만들었을 수 있는
    선택적 보조 시트에 사용 (없다고 해서 다른 기능까지 막히면 안 되므로)."""
    try:
        spreadsheet = get_spreadsheet(spreadsheet_id)
        if spreadsheet is None:
            return pd.DataFrame()
        ws = _call_with_retry(spreadsheet.worksheet, sheet_name)
        df = pd.DataFrame(_call_with_retry(ws.get_all_records))
        return _normalize_date_columns(df)
    except gspread.exceptions.WorksheetNotFound:
        return pd.DataFrame()
    except Exception as e:
        logging.warning("선택적 시트 로드 실패 [%s]: %s", sheet_name, e)
        return pd.DataFrame()

# ============================================================
# 화이트리스트 (사용자계정 시트 — 서비스 계정으로 접근)
# 컬럼: 이메일 / 이름 / spreadsheet_id / 상태 / 등록일 / refresh_token_enc(v2.1, 최초 저장 시 자동 추가)
# ============================================================
@st.cache_resource(ttl=60)
def get_accounts_spreadsheet():
    """모든 사용자 계정 정보가 담긴 '관리자용 계정 시트'를 연다.
    이 시트의 ID는 secrets의 [accounts] spreadsheet_id 값으로 고정되어 있으며,
    사용자 개인 자산 시트와는 별개의 시트임. 여기는 계속 서비스 계정으로 접근한다
    (지인 본인이 이 화이트리스트 시트에 접근할 필요는 없으므로)."""
    client = get_gspread_client()
    if client is None:
        return None
    try:
        sheet_id = st.secrets["accounts"]["spreadsheet_id"]
        return _call_with_retry(client.open_by_key, sheet_id)
    except Exception as e:
        logging.warning("계정 시트 열기 실패: %s", e)
        return None

@st.cache_data(ttl=30)
def load_accounts_df() -> pd.DataFrame:
    """'사용자계정' 시트를 불러온다. 로그인·세션복구·관리자 메뉴 등 여러 곳에서 호출되므로
    캐시가 없으면 매 상호작용(재실행)마다 API를 새로 때려 429(할당량 초과) 위험이 커진다.
    계정 추가/수정/삭제/승인 등 쓰기 작업 직후에는 load_accounts_df.clear()로 즉시 무효화하여
    캐시 때문에 방금 한 변경이 화면에 안 보이는 일이 없도록 한다.

    [중요] 조회에 실패하면(네트워크 오류, 콜드 스타트 시 일시적 오류, API 할당량 초과 등)
    빈 DataFrame을 조용히 반환하지 않고 예외를 그대로 던진다. 예전에는 실패 시 빈 DataFrame을
    반환했는데, 그러면 호출부(get_whitelist_status)가 '조회 실패'와 '이메일이 정말 화이트리스트에
    없음'을 구분하지 못해 일시적 오류를 '완전히 새로운 사용자'로 오인하고 자동으로 승인대기
    등록을 해버리는 사고가 있었다 (실제 활성 계정인데도 콜드 스타트 타이밍에 걸리면 매번 새로
    등록되는 버그)."""
    spreadsheet = get_accounts_spreadsheet()
    if spreadsheet is None:
        raise RuntimeError("화이트리스트 시트에 연결하지 못했습니다 (서비스 계정 인증 실패).")
    ws = _call_with_retry(spreadsheet.worksheet, "사용자계정")
    return pd.DataFrame(_call_with_retry(ws.get_all_records))

def get_whitelist_status(email: str):
    """화이트리스트 시트에서 이메일로 계정 정보를 조회. 없으면 None, 있으면 해당 행(Series) 반환.
    조회 자체가 실패하면(load_accounts_df가 예외를 던지면) 그 예외를 그대로 위로 전파한다 —
    호출부가 이 예외와 '정상 조회했지만 없음(None)'을 반드시 구분해서 처리해야 하기 때문이다."""
    df = load_accounts_df()
    if df.empty or "이메일" not in df.columns or not email:
        return None
    row = df[df["이메일"].astype(str).str.strip().str.lower() == email.strip().lower()]
    if row.empty:
        return None
    return row.iloc[0]

def register_pending_request(email: str, name: str) -> bool:
    """화이트리스트에 없는 이메일이 처음 로그인 시도하면 '승인대기' 상태로 자동 등록.
    관리자가 '가입 승인' 탭에서 승인해야 실제로 앱을 사용할 수 있다.

    [이중 방어] 호출부에서 이미 '화이트리스트에 없음'을 확인하고 불렀더라도, 혹시 모를
    경합(같은 이메일로 거의 동시에 두 번 로그인 시도 등)에 대비해 실제로 쓰기 직전에
    캐시를 비우고 한 번 더 조회한다. 이미 어떤 상태로든 등록되어 있으면 중복으로
    추가하지 않고 그냥 False를 반환한다."""
    try:
        load_accounts_df.clear()
        if get_whitelist_status(email) is not None:
            logging.info("승인 대기 등록 건너뜀 - 이미 등록된 이메일: %s", email)
            return False
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        ws.append_row([email, name, "", "승인대기", str(date.today())])
        load_accounts_df.clear()
        return True
    except Exception as e:
        logging.warning("승인 대기 등록 실패: %s", e)
        return False

def approve_email(sheet_row_number: int) -> bool:
    """대기 중인 이메일을 승인 (상태만 '활성'으로 변경).
    v1.0과 달리 여기서 시트를 만들어 공유하는 과정이 없다 — 승인된 사용자가 다음에 로그인하면
    find_or_create_user_spreadsheet()가 본인 소유 드라이브에 시트를 자동으로 만들기 때문."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        header = ws.row_values(1)
        status_col = header.index("상태") + 1
        ws.update_cell(sheet_row_number, status_col, "활성")
        load_accounts_df.clear()
        return True
    except Exception as e:
        logging.warning("이메일 승인 실패: %s", e)
        return False

def reject_email(sheet_row_number: int) -> bool:
    """대기 중인 신청을 '거부' 상태로 변경 (바로 삭제하지 않아 이력이 남음)."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        header = ws.row_values(1)
        status_col = header.index("상태") + 1
        ws.update_cell(sheet_row_number, status_col, "거부")
        load_accounts_df.clear()
        return True
    except Exception as e:
        logging.warning("가입 거부 처리 실패: %s", e)
        return False

def add_account_email(email: str, name: str, spreadsheet_id: str = "") -> bool:
    """관리자가 화이트리스트에 이메일을 직접 추가 (사전 승인 · 긴급 등록용).
    spreadsheet_id는 비워두면 해당 사용자가 첫 로그인할 때 자동으로 채워진다.
    (기존 아이디/비밀번호 시절 계정을 이메일 기반으로 재등록할 때는 여기에
    기존 spreadsheet_id를 직접 입력해 그 사용자의 기존 시트를 그대로 이어서 쓰게 할 수 있다.)"""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        ws.append_row([email, name, spreadsheet_id, "활성", str(date.today())])
        load_accounts_df.clear()
        return True
    except Exception as e:
        logging.warning("계정 추가 실패: %s", e)
        return False

def save_user_spreadsheet_id(email: str, spreadsheet_id: str) -> bool:
    """사용자가 처음 로그인해 개인 시트가 새로 만들어지면, 화이트리스트 시트의 spreadsheet_id 칸을 채운다.
    관리자가 '사용자 현황' 탭에서 누가 어떤 시트를 쓰는지 확인할 수 있도록 함."""
    try:
        df = load_accounts_df()
        if df.empty or email not in df["이메일"].values:
            return False
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        row_idx = df.index[df["이메일"] == email][0] + 2  # 헤더 행 고려
        header = ws.row_values(1)
        sid_col = header.index("spreadsheet_id") + 1
        ws.update_cell(row_idx, sid_col, spreadsheet_id)
        load_accounts_df.clear()
        return True
    except Exception as e:
        logging.warning("spreadsheet_id 저장 실패: %s", e)
        return False

def _ensure_refresh_token_column(ws) -> int:
    """'사용자계정' 시트에 'refresh_token_enc' 헤더가 없으면 맨 뒤에 추가하고, 그 열 번호(1-based)를 반환.
    기존 사용자들의 시트에는 이 컬럼이 없을 수 있으므로, 최초 저장 시점에 자동으로 만들어준다."""
    header = ws.row_values(1)
    if "refresh_token_enc" in header:
        return header.index("refresh_token_enc") + 1
    new_col = len(header) + 1
    ws.update_cell(1, new_col, "refresh_token_enc")
    return new_col

def save_user_refresh_token(email: str, refresh_token: str) -> bool:
    """구글 OAuth에서 받은 refresh_token을 암호화해 '사용자계정' 시트에 영구 저장.
    이렇게 저장해두면, 서버가 재시작되어 메모리 캐시(_oauth_credential_store)가 비워져도
    이 값으로 브라우저 상호작용 없이 조용히 재로그인할 수 있다 (매번 동의화면이 뜨는 문제의 근본 해결).
    secret_key 미설정 등으로 암호화가 불가능하면 아무것도 하지 않고 False를 반환한다."""
    if not refresh_token:
        return False
    token_enc = _encrypt_refresh_token(refresh_token)
    if not token_enc:
        return False
    try:
        df = load_accounts_df()
        if df.empty or "이메일" not in df.columns or email not in df["이메일"].values:
            return False
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        row_idx = df.index[df["이메일"] == email][0] + 2  # 헤더 행 고려
        col_idx = _ensure_refresh_token_column(ws)
        ws.update_cell(row_idx, col_idx, token_enc)
        load_accounts_df.clear()
        return True
    except Exception as e:
        logging.warning("refresh_token 저장 실패: %s", e)
        return False

def load_user_refresh_token(email: str):
    """'사용자계정' 시트에 저장된 암호화된 refresh_token을 읽어 복호화해서 반환.
    컬럼 자체가 없거나, 값이 비어있거나, 복호화에 실패하면 None을 반환한다."""
    try:
        df = load_accounts_df()
        if df.empty or "refresh_token_enc" not in df.columns or "이메일" not in df.columns:
            return None
        row = df[df["이메일"].astype(str).str.strip().str.lower() == email.strip().lower()]
        if row.empty:
            return None
        token_enc = str(row.iloc[0].get("refresh_token_enc", "")).strip()
        if not token_enc:
            return None
        return _decrypt_refresh_token(token_enc)
    except Exception as e:
        logging.warning("refresh_token 조회 실패: %s", e)
        return None

def build_credentials_from_refresh_token(refresh_token: str):
    """저장해둔 refresh_token만으로 구글 로그인 화면을 거치지 않고 곧바로 유효한
    OAuth Credentials를 재구성한다. 마지막에 .refresh()로 실제 access_token을 한 번 발급받아
    바로 API 호출에 쓸 수 있는 상태로 반환한다. refresh_token이 취소/만료된 경우 예외가
    발생하며, 호출부에서 이를 잡아 '다시 로그인해주세요' 화면으로 넘어가도록 처리해야 한다."""
    credentials = UserOAuthCredentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=st.secrets["google_oauth"]["client_id"],
        client_secret=st.secrets["google_oauth"]["client_secret"],
        scopes=OAUTH_SCOPES,
    )
    credentials.refresh(GoogleAuthRequest())
    return credentials

def update_account_status(email: str, new_status: str) -> bool:
    """'사용자계정' 시트에서 특정 이메일의 상태(활성/비활성)를 변경."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        df = pd.DataFrame(ws.get_all_records())
        if df.empty or email not in df["이메일"].values:
            return False
        row_idx = df.index[df["이메일"] == email][0] + 2
        status_col = df.columns.get_loc("상태") + 1
        ws.update_cell(row_idx, status_col, new_status)
        load_accounts_df.clear()
        return True
    except Exception as e:
        logging.warning("계정 상태 변경 실패: %s", e)
        return False

def delete_account_by_row(sheet_row_number: int) -> bool:
    """'사용자계정' 시트에서 특정 행(구글시트 실제 행 번호, 헤더=1행)을 통째로 삭제."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        ws.delete_rows(sheet_row_number)
        load_accounts_df.clear()
        return True
    except Exception as e:
        logging.warning("계정 삭제 실패: %s", e)
        return False

def update_account_fields(sheet_row_number: int, name: str, spreadsheet_id: str) -> bool:
    """'사용자계정' 시트에서 특정 행의 이름/spreadsheet_id를 그대로 덮어씀.
    spreadsheet_id를 잘못 등록한 경우, 계정을 삭제·재생성하지 않고 바로 고칠 수 있도록 함."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        header = ws.row_values(1)
        name_col = header.index("이름") + 1
        sheet_id_col = header.index("spreadsheet_id") + 1
        ws.update_cell(sheet_row_number, name_col, name)
        ws.update_cell(sheet_row_number, sheet_id_col, spreadsheet_id.strip())
        load_accounts_df.clear()
        return True
    except Exception as e:
        logging.warning("계정 정보 수정 실패: %s", e)
        return False

# ============================================================
# 세션 유지 토큰 (탭 클릭 등으로 연결이 끊겼다 재연결돼도 로그인 유지)
# v2.0: user_id 대신 email을 서명해서 담는다.
# ============================================================
def _session_secret() -> bytes:
    """Secrets에 [auth] secret_key가 없으면 세션 유지 기능은 조용히 비활성화됨(로그인 자체는 정상 동작)."""
    key = st.secrets.get("auth", {}).get("secret_key", "")
    return key.encode("utf-8") if key else b""

def _refresh_token_cipher():
    """[auth] secret_key로부터 Fernet(대칭키 암호화) 키를 유도한다.
    같은 secret_key를 쓰는 한 서버가 재시작되어도 항상 같은 암호화 키가 나오므로,
    구글시트에 저장해둔 암호문을 나중에 다시 복호화할 수 있다.
    secret_key가 비어있으면(설정 안 한 경우) None을 반환해 이 기능 전체를 조용히 끈다."""
    secret = _session_secret()
    if not secret:
        return None
    fernet_key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(fernet_key)

def _encrypt_refresh_token(refresh_token: str) -> str:
    """refresh_token(사실상 '평생 로그인'급 민감한 값)을 구글시트에 평문으로 저장하지 않기 위해
    암호화한다. secret_key가 없으면 빈 문자열을 반환해 저장을 건너뛴다."""
    cipher = _refresh_token_cipher()
    if cipher is None or not refresh_token:
        return ""
    return cipher.encrypt(refresh_token.encode("utf-8")).decode("utf-8")

def _decrypt_refresh_token(token_enc: str):
    """저장된 암호문을 복호화. secret_key가 바뀌었거나 값이 손상된 경우 None을 반환."""
    cipher = _refresh_token_cipher()
    if cipher is None or not token_enc:
        return None
    try:
        return cipher.decrypt(token_enc.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception) as e:
        logging.warning("refresh_token 복호화 실패: %s", e)
        return None

def make_session_token(email: str, ttl_hours: int = 24) -> str | None:
    secret = _session_secret()
    if not secret:
        return None
    expires = int(time.time()) + ttl_hours * 3600
    payload = f"{email}:{expires}"
    sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:20]
    raw = f"{payload}:{sig}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")

def verify_session_token(token: str) -> str | None:
    """토큰이 유효하면 email을, 아니면 None을 반환."""
    secret = _session_secret()
    if not secret or not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        email, expires_str, sig = raw.rsplit(":", 2)
        expected_sig = hmac.new(secret, f"{email}:{expires_str}".encode("utf-8"), hashlib.sha256).hexdigest()[:20]
        if not hmac.compare_digest(sig, expected_sig):
            return None
        if int(expires_str) < int(time.time()):
            return None
        return email
    except Exception as e:
        logging.warning("세션 토큰 검증 실패: %s", e)
        return None

# ============================================================
# 로그인 화면 (Google OAuth)
# ============================================================
def show_login():
    """Google 계정으로 로그인. 사전에 화이트리스트에 등록·승인된 이메일만 앱을 사용할 수 있다."""
    st.markdown("## 📊 통합자산관리 시스템")
    st.caption("Google 계정으로 로그인해주세요. 사전에 승인된 이메일만 사용할 수 있습니다.")

    query_params = st.query_params

    if "code" in query_params:
        # code_verifier는 session_state가 아니라, 구글이 그대로 되돌려주는
        # state 파라미터에 실어 보냈으므로 여기서 그대로 꺼낸다.
        code_verifier = query_params.get("state")
        if not code_verifier:
            st.error("코드 검증값을 찾을 수 없습니다. 처음부터 다시 로그인해주세요.")
            st.query_params.clear()
            if st.button("처음으로 돌아가기"):
                st.rerun()
            return

        try:
            flow = get_oauth_flow()
            flow.fetch_token(code=query_params["code"], code_verifier=code_verifier)
            credentials = flow.credentials
            email = get_user_email(credentials)
            st.query_params.clear()
        except Exception as e:
            st.error(f"로그인 처리 중 오류가 발생했습니다: {e}")
            st.query_params.clear()
            return

        if not email:
            st.error("Google 계정 정보를 가져오지 못했습니다. 다시 시도해주세요.")
            return

        try:
            status_row = get_whitelist_status(email)
        except Exception as e:
            logging.warning("화이트리스트 조회 실패(로그인 중): %s", e)
            st.error(
                "계정 확인 중 일시적인 오류가 발생했습니다. "
                "잠시 후 'Google 계정으로 로그인' 버튼을 다시 눌러주세요."
            )
            return

        if status_row is None:
            # 조회는 정상적으로 됐지만(예외 없음) 화이트리스트에 정말 없는 경우에만
            # 신규 사용자로 간주해 승인대기로 자동 등록한다.
            register_pending_request(email, email.split("@")[0])
            st.warning(
                "처음 로그인하시는 계정이라 승인 대기 등록을 완료했습니다. "
                "관리자(Jone)의 승인이 완료되면 다시 이 페이지에서 로그인해주세요."
            )
            return

        상태 = str(status_row.get("상태", "")).strip()
        if 상태 == "승인대기":
            st.warning("관리자 승인 대기 중인 계정입니다. 승인이 완료되면 다시 로그인해주세요.")
            return
        if 상태 == "거부":
            st.error("가입이 거부된 계정입니다. 관리자에게 문의해주세요.")
            return
        if 상태 != "활성":
            st.error("계정 상태를 확인할 수 없습니다. 관리자에게 문의해주세요.")
            return

        # 승인된 사용자 → 개인 시트 연결
        # 화이트리스트에 spreadsheet_id가 이미 있으면(예: v1.0에서 마이그레이션된 기존 사용자)
        # 그 값을 그대로 사용한다 — Drive 검색/신규 생성 로직은 절대 타지 않는다.
        # spreadsheet_id가 비어있는 '완전히 새로운' 사용자만 find_or_create로 개인 시트를 만든다.
        display_name = str(status_row.get("이름", "")).strip() or email.split("@")[0]
        existing_sid = str(status_row.get("spreadsheet_id", "")).strip()
        created = False

        if existing_sid:
            sheet_id = existing_sid
        else:
            with st.spinner("개인 자산관리 시트를 새로 만드는 중..."):
                try:
                    sheet_id, created = find_or_create_user_spreadsheet(credentials, display_name)
                except Exception as e:
                    st.error(f"개인 시트를 확인/생성하는 중 오류가 발생했습니다: {e}")
                    return
                save_user_spreadsheet_id(email, sheet_id)

        st.session_state["logged_in"] = True
        st.session_state["user_name"] = display_name
        st.session_state["user_email"] = email
        st.session_state["spreadsheet_id"] = sheet_id
        st.session_state["oauth_credentials"] = credentials
        st.session_state["is_admin"] = (
            email.strip().lower() == str(st.secrets.get("admin", {}).get("email", "")).strip().lower()
        )
        _save_credentials(email, credentials)

        # refresh_token이 새로 발급됐다면(최초 동의 시, 혹은 드물게 재동의 시) 영구 저장.
        # 이렇게 저장해두면 서버가 재시작돼도 이 값 하나로 브라우저 상호작용 없이 재로그인 가능.
        if getattr(credentials, "refresh_token", None):
            save_user_refresh_token(email, credentials.refresh_token)

        # 세션 연결이 끊겼다 재연결돼도 자동으로 로그인 상태를 복구하기 위한 토큰
        token = make_session_token(email)
        if token:
            st.query_params["t"] = token

        if created:
            st.success("🆕 개인 자산관리 시트를 새로 만들었습니다.")
        st.rerun()

    else:
        code_verifier, code_challenge = _generate_pkce_pair()
        flow = get_oauth_flow()
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            # [재변경] "select_account"로 바꿨다가 다시 "consent"로 되돌림.
            # select_account는 이미 동의한 계정에게는 구글이 refresh_token을 아예 안 줄 수 있는데,
            # 이러면 access_token이 만료된 뒤(보통 1시간) 갱신할 방법이 없어 모든 구글시트
            # 접근이 한꺼번에 RefreshError로 끊기는 실제 장애가 발생했다. "동의 화면을 매번
            # 보여주더라도 refresh_token을 확실히 받는 것"이 세션이 끊기는 것보다 안전하다.
            # 대신 이미 저장된 refresh_token으로 조용히 재로그인되는 세션 복구 경로
            # (build_credentials_from_refresh_token)는 그대로 살아있어, 브라우저 세션이 유지되는
            # 동안이나 24시간 이내 재방문 시에는 이 동의 화면 자체를 볼 일이 없다.
            prompt="consent",
            code_challenge=code_challenge,
            code_challenge_method="S256",
            state=code_verifier,
        )
        # [참고] st.link_button은 Streamlit이 항상 새 탭/새 창으로 링크를 여는 고정 동작이다.
        # components.html + JS(window.top.location.href)로 "같은 창에서 이동"을 시도했으나,
        # Streamlit 커스텀 컴포넌트 iframe에는 allow-top-navigation 권한이 기본 제외되어 있어
        # 클릭해도 아무 반응이 없는(조용히 차단되는) 문제가 있었다. 게다가 구글 OAuth 로그인
        # 화면 자체가 iframe 안에서 열리는 것을 보안상 거부하므로, 최상위 창(새 탭)에서 여는
        # 이 방식이 현재로선 가장 안전하게 동작하는 방법이라 원래대로 되돌린다.
        st.link_button("🔐 Google 계정으로 로그인", auth_url, type="primary")
        st.caption("🔗 버튼을 누르면 새 탭에서 구글 로그인 화면이 열립니다. 로그인 후 이 페이지로 자동으로 돌아옵니다.")
        st.caption(
            "처음 로그인하는 경우 자동으로 '승인 대기' 등록되며, 관리자 승인이 완료된 뒤 "
            "다시 로그인하면 이용할 수 있습니다."
        )

# ============================================================
# 실시간 시세 조회
# ============================================================
def _fetch_krx_stock_price(krx_code: str):
    """개별 종목의 현재가를 pykrx로 조회 (adjusted=True 기본값 사용).
    [정정] 주석에 'KRX 원천 데이터로 직접 조회'라고 되어 있었는데 정확하지 않다. pykrx는
    adjusted=True(수정주가, 우리가 쓰는 기본값)일 때 실제로는 KRX가 아니라 네이버페이 증권
    데이터를 가져온다 (adjusted=False로 호출해야 KRX 원천에서 직접 가져온다). 네이버 시세도
    신뢰도가 높고 실시간성이 좋아 야후 대비 문제(배치성 갱신 지연)는 해결되지만, '한국거래소
    원천'이라는 표현 자체는 부정확했다."""
    try:
        today = datetime.now(KST).strftime("%Y%m%d")
        from_date = (datetime.now(KST) - timedelta(days=10)).strftime("%Y%m%d")
        df = krx_stock.get_market_ohlcv_by_date(from_date, today, krx_code)
        df = df[df["종가"] > 0]
        if not df.empty:
            return float(df["종가"].iloc[-1])
    except Exception as e:
        logging.warning("KRX 종목 시세 조회 실패 [%s]: %s", krx_code, e)
    return None

@st.cache_data(ttl=3600)
def get_usd_krw_rate() -> float | None:
    """1달러당 원화 환율 조회 (야후파이낸스 'KRW=X' 티커, 1시간 캐시).
    해외(미국) 종목의 시세·매입금액을 원화로 환산할 때 쓰는 단일 환율 소스 —
    이 앱 안 모든 해외 종목 관련 계산이 이 함수 하나만 거쳐가므로 화면마다 다른
    환율이 적용될 일이 없다. 조회 실패 시 None을 반환한다."""
    try:
        hist = yf.Ticker("KRW=X").history(period="5d")
        if not hist.empty:
            return float(hist["Close"].dropna().iloc[-1])
    except Exception as e:
        logging.warning("환율 조회 실패: %s", e)
    return None

# 환율 조회가 실패했을 때 쓰는 비상용 근사 환율. [중요] 예전에는 조회 실패 시 환산을 아예
# 안 하고 원래(달러) 값을 그대로 반환했는데, 그러면 화면에는 "1,555원"처럼 그럴듯한 원화
# 금액처럼 보이면서 실제로는 달러 숫자 그대로라 사용자가 알아채기 어려운 훨씬 더 위험한
# 오류였다(실제로 이렇게 발생해 확인됨 — 2026-08). 실시간 조회가 실패해도 최소한 자릿수
# (스케일)는 맞는 금액이 보이도록, 대략적인 환율로라도 반드시 환산한다. 실제 환율과 크게
# 벌어지면 이 상수를 갱신할 것.
FALLBACK_USD_KRW_RATE = 1380

def get_usd_krw_rate_safe() -> float:
    """get_usd_krw_rate()가 실패(None)해도 항상 쓸 수 있는 환율값을 반환한다
    (실시간 조회 성공 시 그 값, 실패 시 FALLBACK_USD_KRW_RATE)."""
    return get_usd_krw_rate() or FALLBACK_USD_KRW_RATE

def _to_krw_if_foreign(ticker: str, price: float) -> float:
    """국내 종목(.KS/.KQ) 티커는 그대로, 해외 종목은 환율로 원화 환산해서 반환
    (환율 실시간 조회 실패 시 비상용 근사 환율 사용 — 위 get_usd_krw_rate_safe 참고)."""
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return price
    return price * get_usd_krw_rate_safe()

def _yf_last_two_closes(ticker: str, period_days: int = 12) -> tuple[float | None, float | None]:
    """야후 파이낸스에서 최근 종가 2개(오늘, 전일)를 가져온다. 실패하면 (None, None).
    시황 브리핑용 지표(다우·S&P·환율·금리 등)에서 공통으로 쓰는 헬퍼 — 지표 하나 조회가
    실패해도 나머지 지표는 정상적으로 채워지도록, 이 함수 자체는 예외를 밖으로 던지지 않는다.
    [2026-08-12 추가]"""
    try:
        hist = yf.Ticker(ticker).history(period=f"{period_days}d")
        closes = hist["Close"].dropna()
        if len(closes) >= 2:
            return float(closes.iloc[-1]), float(closes.iloc[-2])
        if len(closes) == 1:
            return float(closes.iloc[-1]), None
    except Exception as e:
        logging.warning("시황 지표 조회 실패 [%s]: %s", ticker, e)
    return None, None

def _kr_index_last_two_closes(pykrx_code: str, yf_fallback_ticker: str, from_date: str, today: str) -> tuple[float | None, float | None, str | None]:
    """코스피·코스닥 같은 국내 지수의 최근 종가 2개를 가져온다. pykrx를 먼저 시도하고
    (원천 데이터라 더 신뢰도가 높음), 실패하면 야후 파이낸스로 자동 전환한다.
    [2026-08-12 추가] pykrx의 지수 조회 함수(get_index_ohlcv_by_date)가 KRX 웹사이트
    구조 변경으로 KeyError('지수명')를 내며 깨진 상태인 게 실제로 확인됐다(사용자 확인).
    나중에 pykrx 쪽이 고쳐지면 자동으로 다시 pykrx 값을 쓰게 되고, 그전까지는 야후의
    코스피/코스닥 지수 티커(^KS11/^KQ11)로 대체한다.
    반환값 세 번째 항목은 실패 시 진단용 오류 메시지(성공하면 None)."""
    try:
        df = krx_stock.get_index_ohlcv_by_date(from_date, today, pykrx_code)
        if len(df) >= 2:
            return float(df["종가"].iloc[-1]), float(df["종가"].iloc[-2]), None
        pykrx_error = f"pykrx 조회 결과 {len(df)}행 (2행 미만)"
    except Exception as e:
        pykrx_error = str(e)
        logging.warning("국내 지수 pykrx 조회 실패 [%s]: %s — 야후로 대체 시도", pykrx_code, e)

    cur, prev = _yf_last_two_closes(yf_fallback_ticker)
    if cur is not None:
        return cur, prev, f"pykrx 실패({pykrx_error})해서 야후 대체값 사용 중"
    return None, None, f"pykrx·야후 둘 다 실패 (pykrx: {pykrx_error})"

# ============================================================
# 일일 종목 리포트 — 공시·뉴스·애널리스트 리포트 수집 (2026-08-12 추가)
# ============================================================
# 공시는 DART 공식 API(문서화·안정적), 뉴스와 애널리스트 리포트/컨센서스는
# 네이버 증권의 비공식 내부 API를 사용한다. 후자는 stock.naver.com이 robots.txt로
# 전체 크롤링을 금지(Disallow: /)하고 있음을 Jone이 인지한 상태에서, 지인 50명
# 대상 비공개 서비스라는 점을 근거로 사용하기로 결정한 것(2026-08-12, Jone 확정).
# 네이버 쪽 응답 필드명은 Jone이 관리자 미리보기의 "원본 응답 보기"로 실제 응답을
# 캡처해줘서 확정함(2026-08-12) — 뉴스는 clusters[].items[] 구조, 리포트는
# opinionText/brokerName, 컨센서스는 date/opinion(1~5 점수)/targetPrice 3개뿐임.

_NAVER_STOCK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://stock.naver.com/",
}

@st.cache_data(ttl=1800)
def get_dart_disclosures(corp_code: str, days: int = 14, max_count: int = 15) -> list[dict]:
    """DART 공식 API(list.json)로 최근 공시 목록을 조회한다 (30분 캐시).
    corp_code는 get_dart_corp_code()로 얻은 8자리 고유번호. ETF 등 corp_code가
    없는 종목은 애초에 호출하지 않도록 호출부에서 걸러야 한다.
    실패 시 빈 리스트 반환 — 공시 하나 실패해도 나머지 종목·섹션은 정상 표시되게."""
    try:
        api_key = st.secrets["dart"]["api_key"]
        today = datetime.now(KST)
        resp = requests.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={
                "crtfc_key": api_key,
                "corp_code": corp_code,
                "bgn_de": (today - timedelta(days=days)).strftime("%Y%m%d"),
                "end_de": today.strftime("%Y%m%d"),
                "page_no": 1,
                "page_count": max_count,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "000":  # DART: "000"이 정상, "013"은 검색결과 없음 등
            if data.get("status") == "013":
                return []  # 검색결과 없음은 정상 케이스(오류 아님)
            logging.warning("DART 공시 조회 오류 [%s]: %s", corp_code, data.get("message"))
            return []
        results = []
        for item in data.get("list", []):
            rcept_no = item.get("rcept_no", "")
            results.append({
                "제목": item.get("report_nm", ""),
                "제출인": item.get("flr_nm", ""),
                "날짜": item.get("rcept_dt", ""),
                "링크": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else "",
            })
        return results
    except Exception as e:
        logging.warning("DART 공시 조회 실패 [%s]: %s", corp_code, e)
        return []

@st.cache_data(ttl=1800)
def get_naver_news(item_code: str, max_count: int = 10) -> list[dict]:
    """네이버 증권 종목 뉴스 목록 조회 (30분 캐시, 비공식 API).
    [2026-08-12] 실제 응답을 Jone이 확인해준 결과로 필드명 확정함. 응답은
    items/list 같은 평면 구조가 아니라 같은 이슈끼리 묶인 clusters[].items[]
    구조였음(예: 같은 사건을 다룬 여러 매체 기사가 한 클러스터에 묶임).
    또한 응답에 기사 링크 필드가 없어서 officeId/articleId로 네이버 뉴스
    표준 URL 패턴(n.news.naver.com/mnews/article/{officeId}/{articleId})을
    직접 구성함 — 이 패턴 자체는 네이버 뉴스 전반에서 널리 쓰이는 형태."""
    try:
        resp = requests.get(
            "https://stock.naver.com/api/domestic/detail/news",
            params={"itemCode": item_code, "page": 1, "pageSize": max_count},
            headers=_NAVER_STOCK_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for cluster in data.get("clusters", []):
            for it in cluster.get("items", []):
                dt_raw = it.get("datetime", "")  # "202608130938" (YYYYMMDDHHMM)
                날짜 = f"{dt_raw[:4]}-{dt_raw[4:6]}-{dt_raw[6:8]} {dt_raw[8:10]}:{dt_raw[10:12]}" if len(dt_raw) == 12 else dt_raw
                office_id, article_id = it.get("officeId", ""), it.get("articleId", "")
                results.append({
                    "제목": it.get("title", ""),
                    "언론사": it.get("officeName", ""),
                    "날짜": 날짜,
                    "링크": f"https://n.news.naver.com/mnews/article/{office_id}/{article_id}" if office_id and article_id else "",
                })
                if len(results) >= max_count:
                    return results
        return results
    except Exception as e:
        logging.warning("네이버 증권 뉴스 조회 실패 [%s]: %s", item_code, e)
        return []

@st.cache_data(ttl=3600)
def get_naver_research(item_code: str, max_count: int = 5) -> list[dict]:
    """네이버 증권 종목별 애널리스트 리포트 목록 조회 (1시간 캐시, 비공식 API).
    [2026-08-12] 실제 응답 필드명 확정(Jone 확인). 애널리스트 실명(작성자) 필드는
    이 API에 애초에 존재하지 않아 제공하지 않음 — 증권사명(brokerName)까지만 제공됨."""
    try:
        resp = requests.get(
            "https://stock.naver.com/api/stockSecurity/researches/v2/company",
            params={"itemCodes": item_code, "index": 0, "size": max_count},
            headers=_NAVER_STOCK_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items") or []
        results = []
        for it in items[:max_count]:
            results.append({
                "제목": it.get("title", ""),
                "증권사": it.get("brokerName", ""),
                "날짜": it.get("writeDate", ""),
                "목표주가": _safe_float_or_none(it.get("goalPrice")),
                "투자의견": it.get("opinionText", ""),
            })
        return results
    except Exception as e:
        logging.warning("네이버 증권 리서치 조회 실패 [%s]: %s", item_code, e)
        return []

# [2026-08-13 추가] 네이버 증권 API는 숫자 값도 문자열로 주는 경우가 많다(예: "491875.0").
# 이 값을 다루는 곳마다 매번 변환 코드를 반복하지 않도록 공용 헬퍼로 뺌.
def _safe_float_or_none(v):
    """문자열/숫자/None 어떤 형태로 와도 float 또는 None으로 안전하게 변환."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

# [2026-08-12] 컨센서스 investOpinion 점수(1~5) 해석 기준. 네이버가 공식 문서화한
# 임계값이 아니라 국내 증권사 리서치에서 통상 쓰이는 5단계 척도를 참고한 추정치이니
# 정확한 경계값이 필요하면 실제 서비스 화면의 문구와 대조해서 조정할 것.
def _investor_opinion_label(score) -> str:
    try:
        score = float(score)
    except (TypeError, ValueError):
        return ""
    if score >= 4.5:
        return "적극매수"
    if score >= 3.5:
        return "매수"
    if score >= 2.5:
        return "중립"
    if score >= 1.5:
        return "비중축소"
    return "매도"

@st.cache_data(ttl=3600)
def get_naver_consensus(item_code: str) -> dict:
    """네이버 증권 종목 컨센서스(투자의견 점수·목표주가) 조회 (1시간 캐시, 비공식 API).
    [2026-08-12] 실제 응답 필드명 확정(Jone 확인) — date/opinion/targetPrice 3개뿐이고,
    당초 기대했던 현재가대비·애널리스트수 필드는 이 API에 존재하지 않음(제공 안 됨).
    opinion은 텍스트가 아니라 1~5 점수(높을수록 매수강도 강함) — _investor_opinion_label()로
    참고용 라벨을 붙여서 같이 반환.
    [2026-08-13 수정] 네이버 API가 opinion·targetPrice를 숫자가 아니라 문자열로 준다
    (예: "491875.0")는 걸 놓쳐서, 화면에서 "{target:,.0f}" 같은 숫자 서식을 바로 적용하다
    ValueError로 앱이 죽는 실제 장애가 발생함. 여기(데이터를 만드는 지점)에서 미리 float로
    변환해두면, 이 값을 쓰는 화면 쪽에서는 서식 지정 걱정 없이 바로 쓸 수 있다."""
    try:
        resp = requests.get(
            f"https://stock.naver.com/api/domestic/detail/{item_code}/consensus",
            headers=_NAVER_STOCK_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        score = _safe_float_or_none(data.get("opinion"))
        return {
            "투자의견점수": score,
            "투자의견_참고라벨": _investor_opinion_label(score),
            "목표주가": _safe_float_or_none(data.get("targetPrice")),
            "기준일": data.get("date", ""),
        }
    except Exception as e:
        logging.warning("네이버 증권 컨센서스 조회 실패 [%s]: %s", item_code, e)
        return {}

def get_daily_stock_report(code: str, name: str = "") -> dict:
    """한 종목의 공시(DART)+뉴스+리포트+컨센서스를 한 번에 모아서 반환한다.
    ETF는 DART 고유번호가 없으므로 공시는 자동으로 빈 리스트가 되고, 뉴스만
    의미 있게 채워진다(기존 세션 정리에서 합의된 처리 방식과 동일)."""
    corp_code = get_dart_corp_code(code)
    return {
        "종목코드": code,
        "종목명": name,
        "공시": get_dart_disclosures(corp_code) if corp_code else [],
        "뉴스": get_naver_news(code),
        "리포트": get_naver_research(code),
        "컨센서스": get_naver_consensus(code),
    }

@st.cache_data(ttl=3600)
def generate_stock_daily_summary(code: str, name: str, report: dict) -> str:
    """수집된 공시·뉴스·애널리스트 리포트·컨센서스를 Claude(Haiku)로 종합해 오늘자
    브리핑 문단을 만든다 (1시간 캐시).
    [2026-08-13 추가, 원래 설계 의도 보완] 애초 세션 정리 문서에 "종목별 섹션: 공시+뉴스+
    애널리스트리포트+기타이벤트... 실제 특이사항 적을 것"이라고 되어 있었고, Anthropic API
    키도 이 목적으로 별도 발급해뒀던 것인데(예상 월비용 $1~1.5), 정작 이 요약 단계 없이
    원본 데이터를 표로만 나열하고 있었던 것을 뒤늦게 보완함.
    실패 시(시크릿 미설정, API 오류 등) 빈 문자열 반환 — 호출부가 원본 표로 자연스럽게
    폴백하게 되어 있어 이 기능 하나 때문에 화면 전체가 죽지 않는다."""
    try:
        api_key = st.secrets["anthropic"]["api_key"]
    except Exception:
        return ""

    news_lines = "\n".join(
        f"- [{n.get('날짜', '')}] {n.get('언론사', '')}: {n.get('제목', '')}"
        for n in report.get("뉴스", [])[:10]
    ) or "없음"
    disclosure_lines = "\n".join(
        f"- [{d.get('날짜', '')}] {d.get('제목', '')}"
        for d in report.get("공시", [])[:10]
    ) or "없음"
    research_lines = "\n".join(
        f"- [{r.get('날짜', '')}] {r.get('증권사', '')} "
        f"(목표주가 {r.get('목표주가')}원, {r.get('투자의견', '')}): {r.get('제목', '')}"
        for r in report.get("리포트", [])[:5]
    ) or "없음"
    consensus = report.get("컨센서스") or {}
    consensus_line = (
        f"투자의견 {consensus.get('투자의견_참고라벨', '-')}, 목표주가 {consensus.get('목표주가', '-')}원"
        if consensus else "없음"
    )

    prompt = f"""아래는 {name}({code})에 대해 오늘 수집된 원본 데이터입니다. 이를 바탕으로
개인 투자자가 30초 안에 읽을 수 있는 "오늘의 종합 브리핑"을 한국어로 작성해주세요.

[최근 뉴스]
{news_lines}

[최근 공시]
{disclosure_lines}

[최근 애널리스트 리포트]
{research_lines}

[컨센서스]
{consensus_line}

작성 규칙:
- 3~5개의 짧은 불릿 포인트로 작성
- 오늘/최근 특이사항(주가에 영향 줄 만한 뉴스·공시·리포트 톤 변화)을 우선순위로 다룰 것
- 위 데이터에 없는 내용은 추측해서 언급하지 말 것
- 투자 조언이나 매수/매도 권유는 하지 말고 사실 전달에 집중할 것
- 마크다운 불릿(-) 형식으로만 출력하고, 다른 설명이나 인사말은 붙이지 말 것"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return text.strip()
    except Exception as e:
        logging.warning("일일 리포트 AI 요약 실패 [%s]: %s", code, e)
        return ""

@st.cache_data(ttl=1800)
def _get_naver_raw(kind: str, item_code: str) -> dict:
    """[2026-08-12, 관리자 미리보기 전용] 네이버 증권 API 원본 응답을 그대로 반환한다.
    파싱 함수(get_naver_news 등)의 필드명 추정이 맞는지 확인하기 위한 디버그용 —
    실제 필드명이 확인되면 이 함수는 정리해도 된다."""
    try:
        if kind == "news":
            url = "https://stock.naver.com/api/domestic/detail/news"
            params = {"itemCode": item_code, "page": 1, "pageSize": 10}
        elif kind == "research":
            url = "https://stock.naver.com/api/stockSecurity/researches/v2/company"
            params = {"itemCodes": item_code, "index": 0, "size": 5}
        else:
            return {"_오류": f"알 수 없는 kind: {kind}"}
        resp = requests.get(url, params=params, headers=_NAVER_STOCK_HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"_오류": str(e)}


# Daum 금융이 실제로 쓰는 내부 API 헤더 — Referer가 없으면 403 Forbidden을 반환한다
# (2026-08-12, Chrome 네트워크 탭으로 실측 확인. crawling 방지용으로 추정).
_DAUM_INVESTOR_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.daum.net/domestic/investors/KOSPI",
}

@st.cache_data(ttl=3600)
def get_investor_trend(market: str) -> dict:
    """코스피/코스닥 시장 전체의 외국인·기관·개인 순매수 금액(원)을 다음 금융에서
    조회한다 (1시간 캐시). market은 'KOSPI' 또는 'KOSDAQ'.
    [배경, 2026-08-12] 애초 KRX 공식 Open API로 이 데이터를 받아오려 했으나, 무료
    Open API 서비스 목록에는 '일별매매정보'(시세·거래량)만 있고 투자자별(외국인/기관)
    매매동향 API는 아예 없음을 확인함. 이 데이터는 다음 금융이 자체적으로 쓰는 내부
    API(finance.daum.net/api/charts/investors/{market}/days)에서 가져온다 — Chrome
    개발자도구 네트워크 탭으로 실제 요청을 확인해 알아낸 엔드포인트.
    반환값의 금액 단위는 원(KRW), 양수=순매수, 음수=순매도."""
    try:
        url = f"https://finance.daum.net/api/charts/investors/{market}/days"
        resp = requests.get(
            url,
            params={"limit": 2, "adjusted": "true"},
            headers=_DAUM_INVESTOR_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json().get("data", [])
        if not rows:
            return {}
        latest = rows[-1]
        return {
            "날짜": latest.get("date", "")[:10],
            "외국인": latest.get("foreignStraightPurchasePrice"),
            "기관": latest.get("institutionStraightPurchasePrice"),
            "개인": latest.get("individualStraightPurchasePrice"),
        }
    except Exception as e:
        logging.warning("다음 금융 투자자별 매매동향 조회 실패 [%s]: %s", market, e)
        return {}

@st.cache_data(ttl=3600)
def get_market_overview() -> dict:
    """일일 시황 브리핑에 쓸 지표를 한 번에 모아서 반환한다 (1시간 캐시).
    [설계 원칙] 지표 하나하나를 독립적으로 조회해서, 특정 지표(예: 상하이종합) 하나가
    실패해도 나머지 지표는 정상적으로 채워지게 한다. 실패한 지표는 값이 None으로 남고,
    화면에서는 그 항목만 자연스럽게 생략된다 — 지표 하나 때문에 시황 섹션 전체가
    안 뜨는 일이 없도록 하는 게 핵심.
    실패 시 "_오류" 키에 예외 메시지를 함께 담아서, 화면(관리자 미리보기)에서 실패 원인을
    바로 확인할 수 있게 한다.
    [2026-08-12] 코리아밸류업지수·코스피200야간선물·미국채2년·미국채스프레드·국내금(ETF대용)은
    Jone 요청으로 범위에서 제외(삭제)함 — 안정적인 무료 소스 미확보 또는 정확도 문제.
    """
    m: dict[str, dict] = {}

    def _add(key: str, ticker: str, unit_scale: float = 1.0):
        cur, prev = _yf_last_two_closes(ticker)
        if cur is not None:
            cur *= unit_scale
        if prev is not None:
            prev *= unit_scale
        m[key] = {
            "값": cur,
            "등락률": ((cur - prev) / prev * 100) if (cur is not None and prev) else None,
        }

    today = datetime.now(KST).strftime("%Y%m%d")
    from_date = (datetime.now(KST) - timedelta(days=10)).strftime("%Y%m%d")

    # ── 국내증시 (전일 종가 기준) ──
    cur_k, prev_k, err_k = _kr_index_last_two_closes("1001", "^KS11", from_date, today)
    if cur_k is not None:
        m["코스피"] = {
            "값": cur_k,
            "등락률": ((cur_k - prev_k) / prev_k * 100) if prev_k else None,
        }
        if err_k:
            m["코스피"]["_오류"] = err_k  # 대체 소스 사용 중이라는 안내(값은 정상적으로 있음)
    else:
        m["코스피"] = {"값": None, "등락률": None, "_오류": err_k}

    cur_q, prev_q, err_q = _kr_index_last_two_closes("2001", "^KQ11", from_date, today)
    if cur_q is not None:
        m["코스닥"] = {
            "값": cur_q,
            "등락률": ((cur_q - prev_q) / prev_q * 100) if prev_q else None,
        }
        if err_q:
            m["코스닥"]["_오류"] = err_q
    else:
        m["코스닥"] = {"값": None, "등락률": None, "_오류": err_q}

    # ── 외국인·기관 수급(코스피/코스닥 시장 전체) ──
    # [2026-08-12] pykrx의 투자자별 수급 함수 2가지 모두 KRX 웹사이트 구조 변경으로
    # 빈 결과만 반환함을 확인. KRX 공식 Open API도 이 데이터 자체를 제공하지 않음을
    # 서비스 목록에서 확인(주식 카테고리는 일별매매정보·종목기본정보뿐). 다음 금융의
    # 내부 API로 대체 구현함 — get_investor_trend() 참고.
    kospi_flow = get_investor_trend("KOSPI")
    if kospi_flow:
        m["코스피_수급"] = kospi_flow
    kosdaq_flow = get_investor_trend("KOSDAQ")
    if kosdaq_flow:
        m["코스닥_수급"] = kosdaq_flow

    # ── 해외증시 (전일 종가 기준) ──
    _add("다우존스", "^DJI")
    _add("S&P500", "^GSPC")
    _add("나스닥", "^IXIC")
    _add("필라델피아반도체", "^SOX")
    _add("니케이225", "^N225")
    _add("상하이종합", "000001.SS")
    _add("항셍지수", "^HSI")
    _add("VIX", "^VIX")

    # ── 환율·원자재·금리 ──
    _add("원달러환율", "KRW=X")
    _add("달러인덱스", "DX-Y.NYB")
    _add("WTI", "CL=F")
    _add("브렌트유", "BZ=F")
    _add("국제금", "GC=F")
    _add("미국채10년", "^TNX")  # 야후 ^TNX는 수익률(%) 값을 그대로 줌 (예: 4.67 = 4.67%). 검증 완료(2026-08-12).

    m["기준시각"] = now_kst()
    return m

# ============================================================
# 시황 카드 스파크라인 — 코스피/코스닥 전용 (2026-08-27 추가, 같은 날 방식 전환)
# ============================================================
# [배경] Jone이 미래에셋증권 앱처럼 시황 카드 오른쪽 여백에 최근 추세를 보여주는 미니
# 차트를 넣고 싶어함(2026-08-27). 처음엔 네이버 비공식 분봉(1분 단위) API로 "오늘 하루
# 장중 흐름"을 그리려 했으나, Jone이 Chrome 개발자도구로 직접 확인한 결과 코스피 페이지의
# "1일" 차트가 네이버 자체 API가 아니라 ChartIQ라는 외부 유료 차트 라이브러리로 그려지고
# 있었고, 정황상 REST로 "오늘 하루치 분봉"을 한 번에 내려주는 주소를 찾지 못함(WebSocket
# 실시간 틱 누적 방식으로 추정). 같은 날 리버스엔지니어링을 접고, 처음 비교했던 두 방식
# 중 ②번(일별 데이터로 재현)으로 전환함 — 새 비공식 API 없이 이미 앱이 안정적으로 쓰고
# 있는 pykrx·야후 소스만으로 완성해서 안정성 리스크를 없앤다. "오늘 하루 장중 흐름"이
# 아니라 "최근 며칠간 추세 속의 오늘"을 보여주는 형태로, 비교 목업에서 보여드렸던 옵션②와
# 동일한 느낌이다.

def get_index_recent_closes(pykrx_code: str, yf_fallback_ticker: str, days: int = 10) -> list[float]:
    """최근 N거래일 종가를 오래된→최신 순으로 반환한다 (스파크라인용). pykrx를 먼저
    시도하고(원천 데이터라 더 신뢰도가 높음), 실패하면 야후로 대체한다 — 시황 브리핑의
    다른 국내 지수 조회(_kr_index_last_two_closes)와 동일한 우선순위 방침을 그대로 따름.
    실패해도 예외를 던지지 않고 빈 리스트를 반환 — 스파크라인만 생략되고 카드 자체(값·
    등락률)는 정상 표시된다."""
    today = datetime.now(KST).strftime("%Y%m%d")
    from_date = (datetime.now(KST) - timedelta(days=days * 3)).strftime("%Y%m%d")  # 주말·공휴일 감안해 넉넉히
    try:
        df = krx_stock.get_index_ohlcv_by_date(from_date, today, pykrx_code)
        if len(df) >= 2:
            return [float(v) for v in df["종가"].tail(days).tolist()]
    except Exception as e:
        logging.warning("스파크라인용 pykrx 조회 실패 [%s]: %s — 야후로 대체 시도", pykrx_code, e)
    try:
        hist = yf.Ticker(yf_fallback_ticker).history(period=f"{days + 5}d")
        closes = hist["Close"].dropna()
        if len(closes) >= 2:
            return [float(v) for v in closes.tail(days).tolist()]
    except Exception as e:
        logging.warning("스파크라인용 야후 조회 실패 [%s]: %s", yf_fallback_ticker, e)
    return []

@st.cache_data(ttl=60)
def get_prices(tickers: tuple) -> tuple[dict[str, float], str | None]:
    """현재가 조회. 국내 종목(.KS/.KQ)은 KRX 원천 데이터(pykrx)를 우선 쓰고, 실패하거나
    국내 종목이 아닌 티커만 야후 파이낸스로 조회한다. 해외(미국) 종목은 야후에서 받은
    달러 가격을 현재 환율로 원화 환산해서 저장한다 — 이 함수를 거쳐 나온 값은 항상 원화
    기준이므로, 이 값을 쓰는 다른 모든 화면(대시보드·보유종목 등)은 통화를 신경 쓸 필요가
    없다 (해외 주식 지원 — 2026-08 추가).
    st.cache_data는 list를 해시할 수 없으므로 tuple로 받음.

    반환값은 (시세 딕셔너리, 조회 시각) 튜플이다.
    [2026-08-11 수정] 조회 시각을 st.cache_data 캐시 밖의 st.session_state에 별도로 기록하는
    방식은, st.cache_data가 '앱 전체에서 공유되는' 캐시인 반면 st.session_state는 '그 브라우저
    세션에서만' 유효하다는 범위 불일치 문제가 있었다. 새로고침 등으로 session_state가 초기화된
    직후 캐시가 아직 살아있으면(60초 이내) 이 함수 본문이 아예 실행되지 않아 조회 시각이
    세션에 한 번도 기록되지 않는 버그가 있었다. 캐시되는 반환값 자체에 조회 시각을 담아두면,
    캐시가 적중하든 새로 조회하든 — 그리고 어느 세션에서 읽든 — 항상 "그 시세가 실제로
    언제 조회됐는지"가 데이터와 함께 정확히 전달된다.
    """
    if not tickers:
        return {}, None
    prices = {}
    ticker_list = list(tickers)

    # 1차: 국내 종목은 KRX 원천 데이터로 직접 조회 (가장 신뢰도 높음)
    yf_fallback_needed = []
    for t in ticker_list:
        krx_code = None
        if t.endswith(".KS") or t.endswith(".KQ"):
            krx_code = t.rsplit(".", 1)[0]
        if krx_code:
            price = _fetch_krx_stock_price(krx_code)
            if price is not None:
                prices[t] = price
                continue
        yf_fallback_needed.append(t)

    if not yf_fallback_needed:
        return prices, now_kst()

    # 2차: KRX 조회에 실패했거나 국내 종목이 아닌 티커는 야후에서 일괄 조회
    try:
        ticker_str = " ".join(yf_fallback_needed)
        data = yf.download(ticker_str, period="5d", progress=False, auto_adjust=True, threads=False)
        if "Close" in data.columns:
            close = data["Close"].dropna(how="all")
            if not close.empty:
                latest = close.iloc[-1]
                if hasattr(latest, "items"):
                    for t, p in latest.items():
                        if pd.notna(p):
                            prices[t] = _to_krw_if_foreign(t, float(p))
                elif len(yf_fallback_needed) == 1 and pd.notna(latest):
                    prices[yf_fallback_needed[0]] = _to_krw_if_foreign(yf_fallback_needed[0], float(latest))
    except Exception as e:
        logging.warning("일괄 시세 조회 실패: %s", e)

    # 3차: 그래도 누락된 종목은 야후에서 개별 재시도
    missing = [t for t in yf_fallback_needed if t not in prices]
    for t in missing:
        try:
            hist = yf.Ticker(t).history(period="5d")
            if not hist.empty:
                prices[t] = _to_krw_if_foreign(t, float(hist["Close"].dropna().iloc[-1]))
        except Exception as e:
            logging.warning("개별 시세 조회 실패 [%s]: %s", t, e)

    return prices, now_kst()

def get_current_price(code: str, prices: dict) -> float | None:
    ticker = get_asset_ticker(code)
    if not ticker:
        return None
    return prices.get(ticker)

def _fetch_current_and_prev_close(ticker_list: list) -> dict[str, dict]:
    """여러 티커의 (현재가, 전일종가) 기준 등락률을 일괄 조회하고, 실패한 티커만 개별 재시도.
    get_day_change()가 사용하는 핵심 로직.

    [중요] 여러 티커를 한 번에 yf.download()로 묶어 받으면, 하나의 공유된 날짜 인덱스로
    합쳐진다. 그런데 비트코인처럼 주말에도 거래되는 종목과 코스피·코스닥처럼 주말에 거래되지
    않는 종목이 섞여 있으면, 공유 인덱스의 '마지막 행(iloc[-1])'과 '마지막에서 두 번째 행
    (iloc[-2])'이 종목마다 서로 다른 실제 거래일을 가리키게 된다. 예를 들어 비트코인은
    일요일에도 값이 있어 그 행이 안 지워지지만, 코스피는 그 행이 비어있다(NaN) — 이렇게 되면
    코스피의 '전일 종가'가 실제로는 이틀 전 종가가 되어버리는 식으로 등락률이 완전히 틀어진다.
    그래서 반드시 티커(컬럼)별로 각자 결측치를 제거한 뒤, 그 티커 자신의 마지막 2개 값만
    사용해야 한다 — 공유 인덱스에서 같은 위치(iloc)를 그대로 믿으면 안 된다."""
    result = {}
    try:
        ticker_str = " ".join(ticker_list)
        data = yf.download(ticker_str, period="5d", progress=False, auto_adjust=True, threads=True)
        if "Close" in data.columns:
            close = data["Close"]
            for t in ticker_list:
                try:
                    # 단일 티커만 요청한 경우 close 자체가 Series라 컬럼 선택이 불가능하므로 분기.
                    col_series = close[t] if (hasattr(close, "columns") and t in close.columns) else close
                    col_series = col_series.dropna()
                    if len(col_series) >= 2:
                        cur = float(col_series.iloc[-1])
                        prev = float(col_series.iloc[-2])
                        if prev != 0:
                            result[t] = {"current": cur, "change_pct": (cur - prev) / prev * 100}
                except Exception:
                    continue
    except Exception as e:
        logging.warning("일괄 등락률 조회 실패: %s", e)

    missing = [t for t in ticker_list if t not in result]
    for t in missing:
        try:
            hist = yf.Ticker(t).history(period="5d")
            closes = hist["Close"].dropna()
            if len(closes) >= 2:
                cur, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
                if prev != 0:
                    result[t] = {"current": cur, "change_pct": (cur - prev) / prev * 100}
        except Exception as e:
            logging.warning("개별 등락률 조회 실패 [%s]: %s", t, e)
    return result

@st.cache_data(ttl=300)
def get_day_change(tickers: tuple) -> dict[str, dict]:
    """보유종목 트리맵용 — 임의의 티커 목록에 대해 당일(전일 종가 대비) 등락률을 조회.
    st.cache_data는 list를 해시할 수 없으므로 tuple로 받음."""
    if not tickers:
        return {}
    return _fetch_current_and_prev_close(list(tickers))


# ============================================================
# 숫자 안전 변환 (빈 셀·하이픈·쉼표 등으로 인한 크래시 방지)
# ============================================================
def _safe_num(val, default=0.0):
    """거래수량/거래단가 등 숫자 필드를 안전하게 변환.
    빈 값, '-', 쉼표 포함 문자열 등 비정상 입력이 와도 앱이 죽지 않고 default를 반환."""
    try:
        s = str(val).strip().replace(",", "")
        if s in ("", "-", "nan", "None"):
            return default
        return float(s)
    except (ValueError, TypeError):
        return default

# ============================================================
# 거래이력 사전 점검 (계산 전 잠재적 데이터 오류를 사용자에게 안내)
# ============================================================
def validate_trade_df(trade_df: pd.DataFrame) -> list:
    """거래이력 데이터의 흔한 입력 오류를 사전 점검하여 경고 메시지 목록으로 반환.
    실제 계산 로직(calc_holdings/calc_realized_pnl)은 이 함수와 무관하게 항상 안전하게 동작하며,
    이 함수는 사용자에게 '어느 행에 문제가 있는지' 알려주는 용도."""
    warnings_list = []
    if trade_df.empty:
        return warnings_list

    # 1) 거래수량/거래단가가 비어있거나 숫자로 변환 안 되는 행
    blank_count = 0
    for _, row in trade_df.iterrows():
        qty_raw = str(row.get("거래수량", "")).strip()
        price_raw = str(row.get("거래단가", "")).strip()
        if qty_raw in ("", "-", "nan", "None") or price_raw in ("", "-", "nan", "None"):
            blank_count += 1
    if blank_count:
        warnings_list.append(
            f"⚠ 거래이력 시트에 거래수량 또는 거래단가가 비어있는 행이 {blank_count}건 있습니다. "
            f"해당 행은 계산에서 자동으로 제외되니, 구글시트에서 값을 채워주세요."
        )

    # 2) 거래구분이 '매수'/'매도'가 아닌 행 (오타 등)
    if "거래구분" in trade_df.columns:
        invalid_mask = ~trade_df["거래구분"].astype(str).str.strip().isin(["매수", "매도"])
        invalid_count = int(invalid_mask.sum())
        if invalid_count:
            warnings_list.append(
                f"⚠ 거래구분 값이 '매수'/'매도'가 아닌 행이 {invalid_count}건 있습니다 (오타 가능성). "
                f"해당 거래는 계산에서 통째로 빠지니 확인해주세요."
            )

    # 3) 보유수량을 초과하는 매도 (계좌+종목코드 기준, 시간순)
    df_sorted = trade_df.copy()
    if "거래일자" in df_sorted.columns:
        df_sorted["_정렬일자"] = pd.to_datetime(df_sorted["거래일자"], errors="coerce")
        df_sorted = df_sorted.sort_values("_정렬일자")
    holdings_qty = {}
    overdraw_names = []
    for _, row in df_sorted.iterrows():
        code = str(row.get("종목코드", "")).strip()
        account = str(row.get("운용사", "")).strip()
        구분 = str(row.get("거래구분", "")).strip()
        qty = _safe_num(row.get("거래수량", 0))
        key = (account, code)
        if 구분 == "매수":
            holdings_qty[key] = holdings_qty.get(key, 0) + qty
        elif 구분 == "매도":
            if qty > holdings_qty.get(key, 0):
                overdraw_names.append(f"{row.get('종목명', '')}({account})")
            holdings_qty[key] = max(0, holdings_qty.get(key, 0) - qty)
    if overdraw_names:
        uniq = ", ".join(sorted(set(overdraw_names)))
        warnings_list.append(
            f"⚠ 보유수량보다 많은 매도 이력이 있는 종목: {uniq}. "
            f"실현손익 계산 시 초과분은 자동으로 제외되지만, 입력값을 다시 확인해보세요."
        )

    return warnings_list

# ============================================================
# 평균매입가 재생 (단일 로직 — calc_holdings·calc_realized_pnl이 공유)
# ============================================================
def _replay_trade_ledger(trade_df: pd.DataFrame):
    """거래이력을 시간순으로 한 번 재생하며 계좌+종목코드별 평균매입단가·보유수량을 계산.

    이전에는 calc_holdings()와 calc_realized_pnl()이 각각 독립적으로 평균매입가
    로직을 구현하고 있었음(같은 계산이 두 곳에 나뉘어 있어, 한쪽만 고치면 두 화면의
    숫자가 어긋날 수 있는 구조였음). 이제 이 함수 하나로 합쳐서, 평균매입가 계산은
    이 함수만 거쳐가도록 통일함.

    Returns:
        (sell_events, final_state)
        - sell_events: 매도 발생 시점마다의 상세 정보 리스트 (calc_realized_pnl이 사용)
        - final_state: {(계좌, 종목코드): {"종목명","보유수량","평균단가"}} 최종 보유 현황 (calc_holdings가 사용)
    """
    if trade_df.empty:
        return [], {}

    df = trade_df.copy()
    df["_거래일자_dt"] = pd.to_datetime(df["거래일자"], errors="coerce")
    df = df.sort_values("_거래일자_dt").reset_index(drop=True)

    qty_held = {}
    avg_cost = {}
    names = {}
    sell_events = []

    for _, row in df.iterrows():
        code = str(row.get("종목코드", "")).strip()
        name = str(row.get("종목명", "")).strip()
        account = str(row.get("운용사", "")).strip()
        qty = int(_safe_num(row.get("거래수량", 0)))
        price = _safe_num(row.get("거래단가", 0))
        구분 = str(row.get("거래구분", "")).strip()
        date_ = row["_거래일자_dt"]
        key = (account, code)  # 계좌+종목코드 단위로 평균단가 분리 관리 (동일 종목이 여러 계좌에 있을 수 있음)
        names[key] = name

        if 구분 == "매수":
            prev_qty = qty_held.get(key, 0)
            prev_avg = avg_cost.get(key, 0.0)
            new_qty = prev_qty + qty
            new_avg = (prev_avg * prev_qty + price * qty) / new_qty if new_qty else price
            qty_held[key] = new_qty
            avg_cost[key] = new_avg
        elif 구분 == "매도":
            prev_qty = qty_held.get(key, 0)
            prev_avg = avg_cost.get(key, price)
            # 보유수량을 초과하는 매도는 실현손익 계산에서 초과분을 제외 (데이터 입력 오류로 인한 손익 부풀림 방지)
            effective_qty = min(qty, prev_qty) if prev_qty > 0 else 0
            sell_events.append({
                "거래일자": date_, "계좌": account, "종목코드": code, "종목명": name,
                "매도수량": qty, "매도단가": price, "평균매입단가": prev_avg,
                "effective_qty": effective_qty,
            })
            qty_held[key] = max(0, prev_qty - qty)
            # 매도 후에도 남은 수량의 평균단가 자체는 변하지 않음 (평균매입가법 원칙)

    final_state = {}
    for key, qty in qty_held.items():
        if qty > 0:
            final_state[key] = {
                "종목명": names.get(key, ""),
                "보유수량": qty,
                "평균단가": avg_cost.get(key, 0.0),
            }

    return sell_events, final_state


# ============================================================
# 보유 종목 계산
# ============================================================
def calc_holdings(trade_df: pd.DataFrame) -> pd.DataFrame:
    """거래이력으로 현재 보유 종목과 평균단가 계산. (_replay_trade_ledger 공유 로직 사용)
    해외(미국) 종목은 거래이력에 입력된 원래 통화(달러) 그대로 평균단가를 계산한 뒤,
    이 시점에 현재 환율로 원화 환산한다 — 그래야 이후 모든 계산(투자원금 합계, 평가금액,
    수익률 등)이 국내 종목과 완전히 동일한 방식(전부 원화)으로 흘러간다. (해외 주식 지원
    — 2026-08 추가)"""
    _, final_state = _replay_trade_ledger(trade_df)

    rows = []
    for (account, code), h in final_state.items():
        avg = h["평균단가"]
        qty = h["보유수량"]
        if avg and get_asset_market(code) == "US":
            avg = avg * get_usd_krw_rate_safe()
        rows.append({
            "종목코드": code,
            "종목명": h["종목명"],
            "계좌": account,
            "보유수량": qty,
            "평균단가": round(avg),
            "매입금액": round(avg * qty),
        })

    return pd.DataFrame(rows)

# ============================================================
# 실현손익 계산 (단일 함수 — 모든 화면이 이것 하나만 참조)
# ============================================================
def calc_realized_pnl(trade_df: pd.DataFrame) -> pd.DataFrame:
    """매도 건별 실현손익을 평균매입가법으로 계산하는 단일 함수.
    주식/ETF 전체가 이 함수 하나만 거쳐가므로 화면마다 다른 숫자가 나올 수 없다.
    (_replay_trade_ledger 공유 로직 사용)
    해외(미국) 종목은 매도단가·평균매입단가 모두 원래 통화(달러) 그대로 계산한 뒤, 그
    결과값(매도금액·매입금액)을 현재 환율로 원화 환산한다. 정확히는 매도 시점 환율을
    써야 하지만, 이 앱은 과거 환율 데이터를 따로 저장하지 않으므로 현재 환율로 일괄
    환산하는 방식을 쓴다 — 보유 종목 평가와 같은 기준이라 적어도 화면끼리는 일관된다.
    (해외 주식 지원 — 2026-08 추가)
    """
    sell_events, _ = _replay_trade_ledger(trade_df)

    realized_rows = []
    for ev in sell_events:
        매도금액 = ev["effective_qty"] * ev["매도단가"]
        매입금액 = ev["effective_qty"] * ev["평균매입단가"]
        평균매입단가 = ev["평균매입단가"]
        매도단가 = ev["매도단가"]
        if get_asset_market(ev["종목코드"]) == "US":
            rate = get_usd_krw_rate_safe()
            매도금액 *= rate
            매입금액 *= rate
            평균매입단가 *= rate
            매도단가 *= rate
        실현손익 = 매도금액 - 매입금액
        realized_rows.append({
            "거래일자": ev["거래일자"], "계좌": ev["계좌"], "종목코드": ev["종목코드"], "종목명": ev["종목명"],
            "매도수량": ev["매도수량"], "매도단가": round(매도단가), "평균매입단가": round(평균매입단가),
            "매도금액": round(매도금액), "매입금액": round(매입금액),
            "실현손익": round(실현손익),
        })

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

def style_pnl_cell(v) -> str:
    """st.dataframe의 Styler.map에 넘기는 손익 셀 색상 문자열 (양수=빨강, 음수=파랑).
    거래이력·보유종목·매도이력·TDF펀드 표 등 여러 곳에 똑같은 함수가 각각 복사돼 있던 것을
    하나로 통합했다 (동작은 기존과 동일, 코드만 정리)."""
    try:
        f = float(v)
    except Exception:
        return ""
    color = "#e0635e" if f > 0 else "#5b9bd8" if f < 0 else "inherit"
    return f"color: {color}; font-weight: 600"

def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")

# [2026-08-19 추가] 장 시작 전(09:00 이전) 안내 배너 판단용.
# 반드시 서버 시간이 아니라 서울시간(KST, ZoneInfo("Asia/Seoul"))을 기준으로 판단해야
# 한다 — Streamlit Cloud 서버가 다른 시간대에서 돌아가고 있어도 여기서 오판하지 않도록,
# 이미 앱 전체에서 쓰고 있는 KST 타임존 객체를 그대로 재사용한다.
def is_before_krx_open() -> bool:
    """지금이 KRX 정규장 개장(09:00 KST) 이전인지 여부. 주말·공휴일 여부는 따로 판단하지
    않는다 — 어차피 그런 날은 09시가 지나도 시세가 그대로일 뿐이라, 오전 시간대에만 뜨는
    이 배너가 굳이 필요하지 않은 부작용은 있어도 잘못된 정보를 주진 않는다."""
    now = datetime.now(KST)
    return now.hour < 9

# [2026-08-19 추가] 장 마감 후(15:30~20:00, NXT 애프터마켓) 안내 배너 판단용.
# Jone 실측 확인(16:52, ETF 3종목은 정확히 일치·개별주식만 크게 벌어짐)으로 원인이
# 확정됨: 이 앱의 가격 소스는 KRX 정규장 15:30 종가에서 더 이상 안 바뀌는데, NXT
# 거래가능 종목은 애프터마켓(15:30~20:00)에서 계속 실시간으로 움직이기 때문. 아침의
# 프리마켓(is_before_krx_open)과 근본 원인이 같음 — NXT 시세 자체를 이 앱이 못 가져옴.
def is_after_krx_close() -> bool:
    """지금이 KRX 정규장 마감(15:30 KST) 이후이면서 NXT 애프터마켓 종료(20:00) 이전인지
    여부. 20:00 이후는 NXT도 종료되어 다시 다음날 프리마켓까지 가격이 고정되므로(정규장
    종가 = NXT 마지막가), 굳이 안내가 필요하지 않다."""
    now = datetime.now(KST)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    nxt_after_end = now.replace(hour=20, minute=0, second=0, microsecond=0)
    return market_close <= now < nxt_after_end

def _safe_date_str(v, fmt: str = "%Y-%m-%d") -> str:
    """pandas Timestamp를 안전하게 문자열로 변환. NaT(날짜 파싱 실패)이면 '-'를 반환해
    strftime 호출 시 앱이 죽는 것을 방지한다."""
    try:
        if pd.isna(v):
            return "-"
        return v.strftime(fmt)
    except Exception:
        return "-"

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
# 라이트/다크 테마별 색상 변수는 별도의 작은 CSS 블록으로 주입 (아래 큰 CSS 블록은
# 일반 문자열이라 중괄호를 이스케이프할 필요 없이 그대로 유지 가능)
st.markdown(f"""
<style>
:root {{
    --color-up:    #e0635e;
    --color-down:  #5b9bd8;
    --color-flat:  #9e9e9e;
    --card-bg:     {"rgba(0,0,0,0.035)"  if IS_LIGHT_THEME else "rgba(255,255,255,0.035)"};
    --card-border: {"rgba(0,0,0,0.12)"   if IS_LIGHT_THEME else "rgba(255,255,255,0.08)"};
    --text-dim:    {"rgba(0,0,0,0.62)"   if IS_LIGHT_THEME else "rgba(255,255,255,0.55)"};
    --text-dim2:   {"rgba(0,0,0,0.48)"   if IS_LIGHT_THEME else "rgba(255,255,255,0.4)"};
    --text-strong:  {"rgba(0,0,0,0.88)"  if IS_LIGHT_THEME else "rgba(255,255,255,0.88)"};
    --text-strong2: {"rgba(0,0,0,0.85)"  if IS_LIGHT_THEME else "rgba(255,255,255,0.85)"};
    --text-strong3: {"rgba(0,0,0,0.75)"  if IS_LIGHT_THEME else "rgba(255,255,255,0.75)"};
    --overlay-02: {"rgba(0,0,0,0.035)" if IS_LIGHT_THEME else "rgba(255,255,255,0.02)"};
    --overlay-03: {"rgba(0,0,0,0.05)"  if IS_LIGHT_THEME else "rgba(255,255,255,0.03)"};
    --overlay-05: {"rgba(0,0,0,0.08)"  if IS_LIGHT_THEME else "rgba(255,255,255,0.05)"};
    --overlay-06: {"rgba(0,0,0,0.1)"   if IS_LIGHT_THEME else "rgba(255,255,255,0.06)"};
    --overlay-08: {"rgba(0,0,0,0.12)"  if IS_LIGHT_THEME else "rgba(255,255,255,0.08)"};
    --overlay-10: {"rgba(0,0,0,0.15)"  if IS_LIGHT_THEME else "rgba(255,255,255,0.1)"};
}}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<style>

/* ── 한글 줄바꿈 전역 규칙 ──
   기본 CSS 줄바꿈은 한글을 '음절 단위'로 아무 데서나 끊기 때문에(예: "보이거나"가 "보"/
   "이거나"로 쪼개짐), 캐시 초기화 안내문처럼 폭이 좁은 박스에서 특히 부자연스러웠다.
   word-break: keep-all로 '어절(띄어쓰기) 단위'로만 줄바꿈되게 하고, overflow-wrap:
   break-word로 URL처럼 정말 끊어야 하는 긴 텍스트는 예외적으로 처리한다. 숫자 칸처럼
   이미 white-space: nowrap이 걸린 곳은 이 규칙과 무관하게 계속 한 줄로 유지된다. */
* {
    word-break: keep-all;
    overflow-wrap: break-word;
}

/* ── 가로모드(랜드스케이프)에서 좌우에 여백이 남던 문제 ──
   layout="wide"로 설정해도 Streamlit 버전에 따라 콘텐츠 영역에 자체적인 최대 폭 제한이
   남아있는 경우가 있어서, 가로로 눕히면 화면 좌우로 빈 공간이 생겼다. block-container만
   넓혀서는 안 없어져서, 그 바깥의 상위 레이어(html/body, stApp, AppViewContainer)까지
   전부 폭 100%로 강제한다. 클래스명이 버전마다 달라질 수 있어 여러 선택자를 동시에 지정. */
html, body,
[data-testid="stApp"],
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
.block-container {
    max-width: 100% !important;
    width: 100% !important;
    margin-left: 0 !important;
    margin-right: 0 !important;
}
[data-testid="stMainBlockContainer"], .block-container {
    padding-left: 1.4rem !important;
    padding-right: 1.4rem !important;
}
@media (max-width: 700px) {
    [data-testid="stMainBlockContainer"], .block-container {
        padding-left: 0.8rem !important;
        padding-right: 0.8rem !important;
    }
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
.hero-label .cost-emph {
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--text-main, #f0f0f0);
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
    background: var(--overlay-06);
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

/* ── 자산 구성 요약 카드: 컬러 스트라이프 + 아이콘 배지 (제안 3 확정판) ── */
.asset-breakdown-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.7rem;
    margin-top: 1rem;
}
@media (max-width: 700px) {
    .asset-breakdown-grid { grid-template-columns: 1fr; }
}
.asset-breakdown-item {
    position: relative;
    background: var(--tint);
    border: 1px solid var(--stripe-border, var(--card-border));
    border-radius: 14px;
    padding: 1rem 1.1rem 1rem 1.25rem;
    overflow: hidden;
}
.asset-breakdown-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.7rem;
}
.asset-breakdown-name {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.92rem;
    font-weight: 700;
    color: var(--text-strong);
}
.asset-breakdown-icon {
    width: 26px;
    height: 26px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.95rem;
    background: var(--tint-strong);
    flex-shrink: 0;
}
.asset-breakdown-badge {
    font-size: 0.74rem;
    font-weight: 700;
    padding: 0.12rem 0.5rem;
    border-radius: 6px;
    background: var(--tint-strong);
    color: var(--text-strong3);
}
.asset-breakdown-value {
    font-size: 1.55rem;
    font-weight: 800;
    line-height: 1.15;
    letter-spacing: -0.01em;
    font-variant-numeric: tabular-nums;
}
.asset-breakdown-sub {
    margin-top: 0.45rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.82rem;
    color: var(--text-dim);
    font-variant-numeric: tabular-nums;
}
.asset-breakdown-pct {
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 0.15rem;
}
.asset-breakdown-bar {
    margin-top: 0.7rem;
    height: 4px;
    border-radius: 3px;
    background: var(--overlay-08);
    overflow: hidden;
}
.asset-breakdown-bar > div {
    height: 100%;
    border-radius: 3px;
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
    border-top: 1px dashed var(--overlay-06);
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

/* ── 보유 종목 화면: 계좌 필터 카드 / 보기 방식 토글 사이 여백 ── */
.ui-gap-md { height: 1.1rem; }

.section-title {
    font-size: 1.25rem;
    font-weight: 700;
    margin: 1.4rem 0 0.7rem 0;
    padding-bottom: 0.35rem;
    border-bottom: 2px solid var(--overlay-10);
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

/* ── 보유종목 카드형 리스트 (토스 스타일 참고) ── */
.holding-card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 14px;
    padding: 1rem 1.2rem;
    margin-bottom: 0.6rem;
}
.holding-top-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 0.6rem;
    flex-wrap: wrap;
}
.holding-name-block {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
}
.holding-type-badge {
    font-size: 0.72rem;
    font-weight: 600;
    padding: 0.12rem 0.5rem;
    border-radius: 6px;
    background: var(--overlay-08);
    color: var(--text-dim);
}
.holding-acct-badge {
    font-size: 0.72rem;
    font-weight: 600;
    padding: 0.12rem 0.5rem;
    border-radius: 6px;
}
.holding-name {
    font-size: 1.08rem;
    font-weight: 700;
}
.holding-pct-badge {
    font-size: 1.15rem;
    font-weight: 700;
    text-align: right;
}
.holding-main-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-top: 0.5rem;
    flex-wrap: wrap;
    gap: 0.4rem;
}
.holding-eval {
    font-size: 1.4rem;
    font-weight: 700;
}
.holding-pnl {
    font-size: 0.98rem;
    font-weight: 600;
}
.holding-sub {
    margin-top: 0.55rem;
    padding-top: 0.55rem;
    border-top: 1px solid var(--card-border);
    display: flex;
    flex-wrap: wrap;
    gap: 0.9rem;
    font-size: 0.82rem;
    color: var(--text-dim);
}
.holding-sub span b {
    color: var(--text-dim2);
    font-weight: 500;
}

/* ── 데이터 관리 탭 ── */
.mgmt-summary-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 0.7rem;
    margin: 0.9rem 0 1.6rem;
}
@media (max-width: 700px) {
    .mgmt-summary-grid { grid-template-columns: 1fr; }
}
.mgmt-summary-item {
    background: var(--overlay-03);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 0.85rem 1.05rem;
    display: flex;
    align-items: center;
    gap: 0.8rem;
}
.mgmt-summary-icon {
    width: 34px;
    height: 34px;
    border-radius: 9px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 1.05rem;
    flex-shrink: 0;
}
.mgmt-summary-label {
    font-size: 0.8rem;
    color: var(--text-dim);
    margin-bottom: 0.15rem;
}
.mgmt-summary-value {
    font-size: 1.18rem;
    font-weight: 700;
}
.mgmt-section-head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 1rem;
    font-weight: 700;
    margin: 1.7rem 0 0.7rem;
}
.mgmt-warn-card {
    background: var(--overlay-03);
    border: 1px solid var(--card-border);
    border-radius: 14px;
    padding: 1.1rem 1.3rem;
    margin-top: 0.7rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
}
.mgmt-warn-text { font-size: 0.88rem; color: var(--text-dim); max-width: 480px; }
.mgmt-warn-text b { color: var(--text-strong2); }

/* ── 관리자 메뉴: 일반 화면과 구분되도록 골드 톤 강조 ── */
div[class*="st-key-admin_panel_wrap"] div[data-testid="stExpander"] {
    border: 1px solid rgba(240,180,41,0.35);
    border-radius: 14px;
    background: rgba(240,180,41,0.045);
}
div[class*="st-key-admin_panel_wrap"] div[data-testid="stExpander"] summary {
    font-weight: 600;
    color: #f0b429;
}
div[class*="st-key-admin_panel_wrap"] div[data-testid="stExpander"] summary:hover {
    color: #ffce54;
}

/* ── 기술적 분석: 종목 선택을 HTS 하단 탭처럼(동그라미 없이, 선택된 것만 밑줄) ── */
div[class*="st-key-ta_ticker_tabs"] {
    margin-bottom: 1.1rem;  /* [2026-08-11] 탭 밑줄과 바로 아래 현재가 카드가 너무 붙어보여 여백 추가 */
}
div[class*="st-key-ta_ticker_tabs"] div[role="radiogroup"] {
    gap: 0;
    border-bottom: 1px solid var(--card-border);
    flex-wrap: wrap;
}
div[class*="st-key-ta_ticker_tabs"] label {
    margin: 0 !important;
    padding: 0.4rem 0.85rem;
    border-radius: 0;
    cursor: pointer;
}
div[class*="st-key-ta_ticker_tabs"] label > div:first-child {
    display: none;  /* 라디오 동그라미 숨김 — 탭처럼 보이도록 */
}
div[class*="st-key-ta_ticker_tabs"] label:has(input:checked) {
    border-bottom: 2px solid #e35b5b;
}
div[class*="st-key-ta_ticker_tabs"] label:has(input:checked) p {
    color: #e35b5b;
    font-weight: 700;
}

/* ── 메인 메뉴도 같은 탭 스타일로 통일 (전체 앱 최상단 네비게이션이라 살짝 더 크게) ── */
div[class*="st-key-main_menu_tabs"] div[role="radiogroup"] {
    gap: 0;
    border-bottom: 1px solid var(--card-border);
    flex-wrap: wrap;
    margin-bottom: 0.6rem;
}
div[class*="st-key-main_menu_tabs"] label {
    margin: 0 !important;
    padding: 0.55rem 1.1rem;
    border-radius: 0;
    cursor: pointer;
}
div[class*="st-key-main_menu_tabs"] label > div:first-child {
    display: none;
}
div[class*="st-key-main_menu_tabs"] label p {
    font-size: 1rem;
}
div[class*="st-key-main_menu_tabs"] label:has(input:checked) {
    border-bottom: 2px solid #e35b5b;
}
div[class*="st-key-main_menu_tabs"] label:has(input:checked) p {
    color: #e35b5b;
    font-weight: 700;
}

/* ── 메인 메뉴: 좁은 화면(모바일)에서는 각 항목 너비가 텍스트 길이에 따라 제각각이라
   두 번째 줄로 넘어갈 때 줄(행)이 안 맞아 보였다. 640px 이하에서는 2열 격자로 고정해
   모든 항목의 좌우 경계가 줄마다 나란히 맞도록 한다. ── */
@media (max-width: 640px) {
    div[class*="st-key-main_menu_tabs"] div[role="radiogroup"] {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 0;
    }
    div[class*="st-key-main_menu_tabs"] label {
        display: flex;
        align-items: center;
        justify-content: center;
        text-align: center;
        padding: 0.6rem 0.4rem;
    }
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# 데이터 로드
# ============================================================
@st.cache_data(ttl=30)
def load_all_data(spreadsheet_id: str):
    trade_df     = load_sheet("거래이력", spreadsheet_id)
    nonstock_df  = load_sheet("비주식자산", spreadsheet_id)
    monthly_df   = load_sheet("월별자산스냅샷", spreadsheet_id)
    transfer_df  = load_sheet("계좌간이체", spreadsheet_id)  # TDF 환매 등 계좌간 자금 이동 이력 (실현손익 포함)
    cashlog_df   = load_sheet_optional("현금출납내역", spreadsheet_id)  # 생활비 인출 등 현금 입출금 이력 (아직 없는 사용자도 있어 선택적 로더 사용)
    # [2026-08-11] '현금성자산' 탭 로드 제거 — 실제 화면 어디에서도 참조하지 않던 죽은 시트였다.
    # 대시보드/데이터관리에 표시되는 현금성자산 금액은 항상 nonstock_df(비주식자산 탭)의
    # "자산군"=="현금성자산" 행에서 온다.
    return trade_df, nonstock_df, monthly_df, transfer_df, cashlog_df

def save_monthly_snapshot(spreadsheet_id: str, yearmonth: str, principal, eval_amount) -> tuple[bool, str]:
    """'월별자산스냅샷' 시트에 이번 달(또는 지정 월) 스냅샷을 저장.
    같은 년월(yearmonth, 'YYYY-MM' 형식) 행이 이미 있으면 그 값을 덮어쓰고, 없으면 새 줄로 추가한다.
    거래이력을 기반으로 자동 계산해서 넣는 게 아니라, 호출된 시점의 통합원금/통합평가금액을
    그대로 한 줄의 '기록'으로 남기는 방식이다 (원래 이 시트의 성격 — 월말 스냅샷 — 을 그대로 유지)."""
    try:
        spreadsheet = get_spreadsheet(spreadsheet_id)
        if spreadsheet is None:
            return False, "개인 시트를 열지 못했습니다."

        try:
            ws = spreadsheet.worksheet("월별자산스냅샷")
        except gspread.exceptions.WorksheetNotFound:
            # [2026-08-21] 신규 생성 시 헤더를 REQUIRED_SHEET_HEADERS와 동일하게 맞춤
            # (기존에는 년월/통합원금/통합평가 3개 컬럼만 만들었으나, 신규 유저 템플릿과 일치시킴)
            ws = spreadsheet.add_worksheet(
                title="월별자산스냅샷", rows=200, cols=len(REQUIRED_SHEET_HEADERS["월별자산스냅샷"])
            )
            ws.update("A1", [REQUIRED_SHEET_HEADERS["월별자산스냅샷"]])

        records = ws.get_all_values()
        if not records:
            ws.update("A1", [REQUIRED_SHEET_HEADERS["월별자산스냅샷"]])
            records = [REQUIRED_SHEET_HEADERS["월별자산스냅샷"]]

        header = records[0]
        try:
            ym_col = header.index("년월")
            cost_col = header.index("통합원금")
            eval_col = header.index("통합평가")
        except ValueError:
            return False, "'월별자산스냅샷' 시트의 헤더(년월/통합원금/통합평가)를 찾을 수 없습니다."

        # 저장시각/통합손익/통합수익률은 있으면 채우고, 없으면 건드리지 않는다 (하위 호환)
        time_col = header.index("저장시각") if "저장시각" in header else None
        pnl_col = header.index("통합손익") if "통합손익" in header else None
        pct_col = header.index("통합수익률") if "통합수익률" in header else None

        principal_int = int(principal)
        eval_int = int(eval_amount)
        pnl_val = eval_int - principal_int
        pct_val = round(pnl_val / principal_int * 100, 2) if principal_int else 0
        saved_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

        # 기존에 같은 년월 행이 있는지 찾기 (셀 값이 "2026-07-01 00:00:00"처럼 길게 들어있어도
        # 앞 7글자만 비교해 같은 달로 인식)
        target_row = None
        for i, row in enumerate(records[1:], start=2):  # 실제 시트 행번호 (헤더=1행)
            cell_val = row[ym_col] if ym_col < len(row) else ""
            if str(cell_val).strip()[:7] == yearmonth:
                target_row = i
                break

        if target_row:
            ws.update_cell(target_row, cost_col + 1, principal_int)
            ws.update_cell(target_row, eval_col + 1, eval_int)
            if time_col is not None:
                ws.update_cell(target_row, time_col + 1, saved_at)
            if pnl_col is not None:
                ws.update_cell(target_row, pnl_col + 1, pnl_val)
            if pct_col is not None:
                ws.update_cell(target_row, pct_col + 1, pct_val)
            msg = f"{yearmonth} 스냅샷 값을 갱신했습니다."
        else:
            new_row = [""] * len(header)
            new_row[ym_col] = yearmonth
            new_row[cost_col] = principal_int
            new_row[eval_col] = eval_int
            if time_col is not None:
                new_row[time_col] = saved_at
            if pnl_col is not None:
                new_row[pnl_col] = pnl_val
            if pct_col is not None:
                new_row[pct_col] = pct_val
            ws.append_row(new_row)
            msg = f"{yearmonth} 스냅샷을 새로 추가했습니다."

        load_sheet.clear()
        load_all_data.clear()
        return True, msg
    except Exception as e:
        logging.warning("월별 스냅샷 저장 실패: %s", e)
        return False, f"저장 중 오류가 발생했습니다: {type(e).__name__} - {e}"

# ============================================================
# 메인 앱
# ============================================================
# ============================================================
# 관리자 메뉴 (본인 계정으로 로그인 후에만 노출)
# ============================================================
def render_admin_panel():
    col_admin, _ = st.columns([2, 1])  # 화면 전체 폭을 다 쓰지 않도록 2:1로 제한
    with col_admin:
        with st.container(key="admin_panel_wrap"):
            with st.expander("🔧 관리자 메뉴", expanded=True):
                st.caption("🔒 관리자 전용 · 지인 계정을 관리합니다")

                # 이 메뉴가 열려있는 동안 모든 탭이 계정 목록을 공유해서 씀
                # (탭마다 따로 불러오면 구글시트 API 호출이 3배로 늘어나 429 오류 위험이 커짐)
                try:
                    df_acc = load_accounts_df()
                except Exception as e:
                    logging.warning("화이트리스트 조회 실패(관리자 메뉴): %s", e)
                    st.error("계정 목록을 불러오지 못했습니다. 잠시 후 새로고침해서 다시 시도해주세요.")
                    return

                # [2026-08-27 수정] 여기도 상단 메인 메뉴와 똑같은 st.tabs() 튕김 버그가 있었음
                # (Jone 제보: "분봉 차트 미리보기" 버튼을 누르면 "시스템" 탭에서 "계정 관리" 탭으로
                # 튕겨나감 — 화면 구성이 크게 바뀌는 위젯 클릭 시 발생하는 Streamlit 자체의 알려진
                # 미해결 이슈, 위 3685번 줄 주석 참고). 상단 메인 메뉴를 st.radio로 바꿔서 고쳤던
                # 것과 동일한 방식으로, 이 안쪽 탭들도 session_state에 선택값이 저장되는 라디오
                # 버튼으로 바꿔 원천 차단한다.
                ADMIN_SUBTABS = ["계정 관리", "🆕 가입 승인", "사용자 현황", "시스템"]
                selected_admin_tab = st.radio(
                    "관리자 메뉴 세부 탭", ADMIN_SUBTABS, horizontal=True,
                    key="active_admin_subtab", label_visibility="collapsed",
                )

                # ---------- 계정 관리 ----------
                if selected_admin_tab == "계정 관리":
                    st.caption("이메일 직접 추가 (사전 승인 · 긴급 등록용)")
                    with st.form("admin_add_account_form"):
                        new_email = st.text_input("이메일 (구글 계정)")
                        new_name = st.text_input("이름")
                        new_sheet_id = st.text_input(
                            "연결할 구글시트 spreadsheet_id (선택 — 비워두면 첫 로그인 시 자동 생성)"
                        )
                        add_submitted = st.form_submit_button("계정 추가", type="primary", width="stretch")
                    if add_submitted:
                        if new_email and new_name:
                            if add_account_email(new_email, new_name, new_sheet_id):
                                st.success(f"'{new_email}' 계정이 추가되었습니다.")
                                st.rerun()
                            else:
                                st.error("계정 추가에 실패했습니다. 로그를 확인하세요.")
                        else:
                            st.warning("이메일과 이름을 입력해주세요.")

                    st.markdown("---")

                    if df_acc.empty:
                        st.info("등록된 계정이 없습니다.")
                    else:
                        # 계정을 한 번만 선택하면 아래 상태변경·정보수정·삭제가 전부 그 계정 기준으로 동작
                        st.caption("👤 관리할 계정 선택")
                        acc_options = {}
                        for i, row in df_acc.iterrows():
                            sheet_row = i + 2  # 헤더가 1행이므로 +2
                            label = f"{row.get('이메일','')} / {row.get('이름','')}"
                            acc_options[label] = (sheet_row, row)
                        selected_label = st.selectbox(
                            "대상 계정", list(acc_options.keys()), key="admin_selected_account",
                        )
                        target_row_num, target_row_data = acc_options[selected_label]
                        target_email = str(target_row_data.get("이메일", ""))
                        # key에 행번호를 포함시켜, 계정을 바꿔 선택하면 입력창도 그 계정의 값으로 새로 그려지도록 함
                        # (key가 그대로면 Streamlit이 이전 입력값을 계속 기억해 다른 계정으로 착각하게 됨)

                        st.markdown("---")
                        st.caption(f"'{target_email}' 계정 상태 변경")
                        new_status = st.selectbox(
                            "상태 변경", ["활성", "비활성"], key=f"admin_new_status_{target_row_num}",
                        )
                        if st.button("상태 적용", key=f"admin_status_btn_{target_row_num}", width="stretch"):
                            if update_account_status(target_email, new_status):
                                st.success(f"'{target_email}' 계정 상태가 '{new_status}'로 변경되었습니다.")
                                st.rerun()
                            else:
                                st.error("상태 변경에 실패했습니다.")

                        st.markdown("---")
                        st.caption("✏️ 계정 정보 수정 (이름 · 연결된 구글시트 ID)")
                        edit_name = st.text_input(
                            "이름", value=str(target_row_data.get("이름", "")),
                            key=f"admin_edit_name_{target_row_num}",
                        )
                        edit_sheet_id = st.text_input(
                            "연결된 구글시트 spreadsheet_id",
                            value=str(target_row_data.get("spreadsheet_id", "")),
                            key=f"admin_edit_sheet_id_{target_row_num}",
                        )
                        if st.button("💾 정보 저장", key="admin_edit_save_btn", width="stretch"):
                            if update_account_fields(target_row_num, edit_name, edit_sheet_id):
                                st.success(f"'{target_email}' 계정 정보가 수정되었습니다.")
                                st.rerun()
                            else:
                                st.error("정보 수정에 실패했습니다.")

                        st.markdown("---")
                        st.caption("🗑 계정 삭제 (되돌릴 수 없음)")
                        st.warning(f"'{target_email}' 계정을 삭제합니다. 이 작업은 되돌릴 수 없습니다.")
                        if st.button(f"🗑 '{target_email}' 계정 삭제", key=f"admin_delete_btn_{target_row_num}", width="stretch"):
                            if delete_account_by_row(target_row_num):
                                st.success(f"'{target_email}' 계정이 삭제되었습니다.")
                                st.rerun()
                            else:
                                st.error("삭제에 실패했습니다.")

                # ---------- 가입 승인 ----------
                elif selected_admin_tab == "🆕 가입 승인":
                    st.caption("지인이 Google 로그인을 처음 시도하면 자동으로 여기에 접수됩니다. 확인 후 승인/거부하세요.")
                    pending_df = (
                        df_acc[df_acc["상태"] == "승인대기"]
                        if not df_acc.empty else pd.DataFrame()
                    )
                    if pending_df.empty:
                        st.info("승인 대기 중인 신청이 없습니다.")
                    else:
                        st.caption(f"승인 대기 {len(pending_df)}건")
                        for i, prow in pending_df.iterrows():
                            sheet_row = i + 2  # 헤더가 1행이므로 +2
                            with st.container(border=True):
                                st.markdown(f"**{prow.get('이름','')}**")
                                st.caption(f"이메일: {prow.get('이메일','')} · 신청일: {prow.get('등록일','')}")
                                c1, c2 = st.columns(2)
                                with c1:
                                    if st.button("✅ 승인", key=f"approve_{sheet_row}",
                                                 width="stretch", type="primary"):
                                        if approve_email(sheet_row):
                                            st.success("승인 완료: 다음에 이 이메일로 로그인하면 개인 시트가 자동으로 만들어집니다.")
                                            st.rerun()
                                        else:
                                            st.error("승인 처리에 실패했습니다.")
                                with c2:
                                    if st.button("❌ 거부", key=f"reject_{sheet_row}", width="stretch"):
                                        if reject_email(sheet_row):
                                            st.warning("거부 처리되었습니다.")
                                            st.rerun()
                                        else:
                                            st.error("거부 처리에 실패했습니다.")

                # ---------- 사용자 현황 ----------
                elif selected_admin_tab == "사용자 현황":
                    if not df_acc.empty:
                        display_cols = [c for c in ["이메일", "이름", "상태", "등록일"] if c in df_acc.columns]
                        st.dataframe(df_acc[display_cols], width="stretch", hide_index=True)
                        st.caption(f"총 {len(df_acc)}개 계정 · 활성 {sum(df_acc['상태'] == '활성')}개 · "
                                   f"승인대기 {sum(df_acc['상태'] == '승인대기')}개")
                    else:
                        st.info("등록된 계정이 없습니다.")

                # ---------- 시스템 ----------
                elif selected_admin_tab == "시스템":
                    st.caption("캐시된 데이터를 지우고 구글시트/시세를 다시 불러옵니다.")
                    if st.button("🔄 전체 캐시 새로고침", key="admin_cache_clear", width="stretch"):
                        st.cache_data.clear()
                        st.cache_resource.clear()
                        st.success("캐시가 초기화되었습니다. 페이지를 새로고침 해주세요.")
                        st.rerun()

                    st.divider()
                    # [2026-08-12 추가] 일일 시황 브리핑 기능 개발 중 — 실제 값이 제대로
                    # 나오는지 배포 환경에서 직접 확인할 수 있도록 임시로 넣어둔 미리보기.
                    # 나중에 시황 섹션이 정식 화면(오늘의 브리핑 탭)으로 옮겨가면 이 버튼은
                    # 정리할 예정.
                    st.caption("일일 시황 브리핑에 쓸 지표들을 실제로 가져와서 값이 맞는지 확인합니다 (개발 중 임시 기능).")
                    if st.button("🌐 시황 데이터 미리보기(테스트)", key="admin_market_overview_preview", width="stretch"):
                        with st.spinner("시황 지표 조회 중..."):
                            mo = get_market_overview()
                        # [2026-08-12] 수급 데이터(코스피_수급/코스닥_수급)는 값/등락률 구조가
                        # 아니라 날짜+외국인/기관/개인 순매수금액 구조라 별도 표로 분리해서 보여준다.
                        FLOW_KEYS = ("코스피_수급", "코스닥_수급")
                        rows = []
                        for key, v in mo.items():
                            if key in ("기준시각",) or key in FLOW_KEYS:
                                continue
                            rows.append({
                                "지표": key,
                                "값": v.get("값"),
                                "등락률(%)": round(v["등락률"], 2) if v.get("등락률") is not None else None,
                                "오류": v.get("_오류", ""),
                            })
                        df_mo = pd.DataFrame(rows)
                        # [2026-08-12] 값은 천 단위 콤마, 등락률은 기존 손익 색상 규칙(양수=빨강,
                        # 음수=파랑)을 그대로 적용 — style_pnl_cell()이 이미 앱 전체에서 쓰는
                        # 손익 색상 헬퍼라 그대로 재사용.
                        styled = (
                            df_mo.style
                            .format({"값": "{:,.2f}", "등락률(%)": "{:+.2f}"}, na_rep="-")
                            .map(style_pnl_cell, subset=["등락률(%)"])
                        )
                        st.dataframe(styled, width="stretch", hide_index=True)
                        st.caption(f"기준시각: {mo.get('기준시각')}")
                        st.caption("⚠ '값'이 None(-)으로 나온 항목은 '오류' 칸에 실패 사유가 표시됩니다.")

                        # [2026-08-12 추가] 코스피/코스닥 수급(외국인·기관·개인 순매수) 별도 표
                        flow_rows = []
                        for key in FLOW_KEYS:
                            flow = mo.get(key)
                            if not flow:
                                continue
                            flow_rows.append({
                                "시장": key.replace("_수급", ""),
                                "날짜": flow.get("날짜"),
                                "외국인(억원)": (flow.get("외국인") or 0) / 1e8,
                                "기관(억원)": (flow.get("기관") or 0) / 1e8,
                                "개인(억원)": (flow.get("개인") or 0) / 1e8,
                            })
                        if flow_rows:
                            df_flow = pd.DataFrame(flow_rows)
                            styled_flow = (
                                df_flow.style
                                .format({"외국인(억원)": "{:+,.0f}", "기관(억원)": "{:+,.0f}", "개인(억원)": "{:+,.0f}"})
                                .map(style_pnl_cell, subset=["외국인(억원)", "기관(억원)", "개인(억원)"])
                            )
                            st.dataframe(styled_flow, width="stretch", hide_index=True)
                        else:
                            st.caption("⚠ 코스피/코스닥 수급 데이터를 가져오지 못했습니다 (다음 금융 API 응답 실패).")

                    st.divider()
                    # [2026-08-12] 종목별 일일 리포트(공시+뉴스+애널리스트 리포트+컨센서스) 미리보기.
                    # 네이버 쪽 필드명은 Jone이 실제 응답을 확인해줘서 확정됨(get_naver_news 등 docstring
                    # 참고). "원본 응답 보기"는 이후 네이버가 API 구조를 바꿨을 때 재진단용으로 남겨둠.
                    st.caption("종목 하나를 골라 공시(DART)·뉴스·애널리스트 리포트·컨센서스가 잘 나오는지 확인합니다 (개발 중 임시 기능).")
                    preview_code = st.text_input(
                        "종목코드(6자리)", value="005930", key="admin_daily_report_code",
                        help="예: 삼성전자 005930, SK하이닉스 000660",
                    )
                    show_raw = st.checkbox("원본 응답 보기 (문제 발생 시 진단용)", key="admin_daily_report_raw")
                    if st.button("📰 일일 리포트 미리보기(테스트)", key="admin_daily_report_preview", width="stretch"):
                        with st.spinner("공시·뉴스·리포트 조회 중..."):
                            report = get_daily_stock_report(preview_code.strip())

                        st.markdown("**📢 공시 (DART)**")
                        if report["공시"]:
                            st.dataframe(pd.DataFrame(report["공시"]), width="stretch", hide_index=True)
                        else:
                            st.caption("공시 없음 또는 조회 실패 (ETF는 DART 고유번호가 없어 정상적으로 비어있을 수 있음)")

                        st.markdown("**📰 뉴스 (네이버 증권)**")
                        if report["뉴스"]:
                            st.dataframe(pd.DataFrame(report["뉴스"]), width="stretch", hide_index=True)
                        else:
                            st.caption("뉴스 없음 또는 조회 실패")
                        if show_raw:
                            st.json(_get_naver_raw("news", preview_code.strip()))

                        st.markdown("**📊 애널리스트 리포트 (네이버 증권)**")
                        if report["리포트"]:
                            st.dataframe(pd.DataFrame(report["리포트"]), width="stretch", hide_index=True)
                        else:
                            st.caption("리포트 없음 또는 조회 실패")
                        if show_raw:
                            st.json(_get_naver_raw("research", preview_code.strip()))

                        st.markdown("**🎯 컨센서스 (네이버 증권)**")
                        consensus = report["컨센서스"]
                        if consensus:
                            st.json(consensus)
                        else:
                            st.caption("컨센서스 없음 또는 조회 실패")

                    st.divider()
                    # [2026-08-27 추가, 같은 날 방식 전환] 시황 카드 추세 미니 차트 기능 —
                    # 네이버 비공식 분봉 API 대신 이미 검증된 pykrx/야후 소스로 전환했으므로
                    # (위 get_index_recent_closes 주석 참고), 원본 응답 보기 같은 진단은
                    # 필요 없고 값이 잘 나오는지만 간단히 확인하면 된다.
                    st.caption("코스피·코스닥 카드에 들어갈 최근 거래일 종가 추세가 잘 나오는지 확인합니다 (개발 중 임시 기능).")
                    spark_index = st.selectbox("지수", ["코스피", "코스닥"], key="admin_spark_index")
                    if st.button("📈 추세 차트 미리보기(테스트)", key="admin_spark_preview", width="stretch"):
                        code_map = {"코스피": ("1001", "^KS11"), "코스닥": ("2001", "^KQ11")}
                        with st.spinner("최근 거래일 종가 조회 중..."):
                            closes = get_index_recent_closes(*code_map[spark_index])
                        if closes:
                            st.dataframe(pd.DataFrame({"종가": closes}), width="stretch", hide_index=True)
                            st.caption(f"총 {len(closes)}개 거래일 (오래된 순, 마지막=오늘)")
                        else:
                            st.warning("종가 데이터를 가져오지 못했습니다 (pykrx·야후 둘 다 실패).")

                    st.divider()
                    # [2026-08-19 추가] 시세 지연 진단 패널.
                    # Jone 제보: 보유종목 8개 전부(국내주식+ETF)에서 앱 등락률이 실제(증권사 앱)보다
                    # 일관되게 덜 하락하게 나옴(예: 삼성전자 앱 -7.45% vs 실제 -8.01%), 시세 새로고침을
                    # 눌러도 그대로. 8개 전부 같은 방향으로 벌어진다는 건 종목별로 들쭉날쭉한 NXT
                    # 미반영 문제와는 다른 패턴이라, 데이터 소스 자체(_fetch_krx_stock_price가 실제로는
                    # 네이버 경유임 — 위 주석 참고)의 지연 가능성이 의심됨. 정확한 원인을 추측이 아니라
                    # 실측으로 확인하기 위해, 같은 종목을 pykrx(네이버 경유)와 야후 파이낸스 두 소스로
                    # 동시에 조회해서 값과 조회 시각을 나란히 보여준다. 원인이 확인되면 이 패널은
                    # 정리해도 된다.
                    st.caption("같은 종목을 두 소스로 동시에 조회해서 값을 비교합니다 (시세 지연 원인 진단용, 개발 중 임시 기능).")
                    diag_code = st.text_input(
                        "종목코드(6자리)", value="005930", key="admin_price_diag_code",
                        help="예: 삼성전자 005930, SK하이닉스 000660",
                    )
                    if st.button("⏱️ 시세 지연 진단 실행", key="admin_price_diag_run", width="stretch"):
                        code = diag_code.strip()
                        with st.spinner("두 소스에서 동시 조회 중..."):
                            krx_price = _fetch_krx_stock_price(code)
                            t1 = now_kst()
                            yf_price = None
                            try:
                                hist = yf.Ticker(f"{code}.KS").history(period="5d")
                                if hist.empty:
                                    hist = yf.Ticker(f"{code}.KQ").history(period="5d")
                                if not hist.empty:
                                    yf_price = float(hist["Close"].dropna().iloc[-1])
                            except Exception as e:
                                logging.warning("진단용 야후 조회 실패 [%s]: %s", code, e)
                            t2 = now_kst()

                        d1, d2 = st.columns(2)
                        with d1:
                            st.metric("pykrx (네이버 경유)", f"{krx_price:,.0f}원" if krx_price else "조회 실패")
                            st.caption(f"조회 완료 시각: {t1}")
                        with d2:
                            st.metric("야후 파이낸스", f"{yf_price:,.0f}원" if yf_price else "조회 실패")
                            st.caption(f"조회 완료 시각: {t2}")
                        if krx_price and yf_price:
                            diff = krx_price - yf_price
                            st.caption(f"두 소스 차이: {diff:+,.0f}원 ({diff/yf_price*100:+.2f}%)")
                        st.caption(
                            "⚠ 이 화면의 값도 지금 이 순간의 스냅샷일 뿐입니다. 증권사 앱에 뜬 실제가와 "
                            "비교해서 어느 소스가 더 가까운지, 시간이 지나도 계속 벌어지는지 확인해주세요."
                        )


# ============================================================
# 개발자 정보 (모달 팝업)
# ============================================================
@st.dialog("앱 정보")
def show_developer_info():
    st.markdown("**개발: H.W Jone**")
    st.markdown(f"**버전: {APP_VERSION}**")
    st.markdown("**문의: hwcho@me.com**")
    st.caption("버그 제보나 기능 제안은 위 이메일로 보내주세요.")


# [2026-08-19] 오늘의 리포트 화면 전용 배지(등락률) HTML 생성 헬퍼.
# Jone 제보: 기존엔 st.metric의 delta_color="inverse"를 썼는데, 이건 빨강/초록만 지원해서
# 국내 관례인 "하락=파랑"이 안 나왔음(초록으로 표시됨). 앱의 다른 화면(보유종목 등)에서
# 이미 손익 표시에 쓰고 있는 style_pnl_cell과 정확히 같은 색(상승 #e0635e, 하락 #5b9bd8)으로
# 통일해서 커스텀 배지를 직접 그린다. Jone이 승인한 시안(네이버 증권 스타일 참고) 반영.
_UP_COLOR, _UP_BG = "#e0635e", "rgba(224,99,94,0.12)"
_DOWN_COLOR, _DOWN_BG = "#5b9bd8", "rgba(91,155,216,0.12)"

def _change_badge_html(pct) -> str:
    """등락률(%) 값을 받아 상승=빨강/하락=파랑 배지 HTML을 반환. None이면 빈 문자열."""
    if pct is None:
        return ""
    color, bg = (_UP_COLOR, _UP_BG) if pct > 0 else (_DOWN_COLOR, _DOWN_BG) if pct < 0 else ("var(--text-secondary,#888)", "rgba(136,136,136,0.12)")
    arrow = "▲" if pct > 0 else "▼" if pct < 0 else "-"
    return (
        f"<span style='display:inline-flex;align-items:center;gap:2px;margin-top:4px;"
        f"font-size:12px;font-weight:600;color:{color};background:{bg};"
        f"padding:2px 6px;border-radius:6px;'>{arrow} {abs(pct):.2f}%</span>"
    )

def _metric_card_html(label: str, value: str, pct=None, spark_svg: str = "") -> str:
    """시황 카드 하나(라벨 + 값 + 등락 배지, 선택적으로 오른쪽에 분봉 스파크라인) HTML.
    [2026-08-27 추가] spark_svg가 있으면 카드를 좌우 flex 레이아웃으로 바꿔 오른쪽 빈
    여백에 미니 차트를 넣는다. 빈 문자열이면(기본값) 기존과 완전히 동일하게 보인다 —
    코스피·코스닥 외 나머지 지표 카드들은 이번 변경의 영향을 받지 않는다."""
    left = (
        "<div style='min-width:0;'>"
        f"<div style='font-size:12px;color:var(--text-secondary,#888);margin-bottom:4px;'>{label}</div>"
        f"<div style='font-size:20px;font-weight:600;white-space:nowrap;'>{value}</div>"
        f"{_change_badge_html(pct)}"
        "</div>"
    )
    if spark_svg:
        return (
            "<div style='background:rgba(128,128,128,0.08);border-radius:10px;padding:12px;"
            "display:flex;align-items:center;justify-content:space-between;gap:8px;'>"
            f"{left}{spark_svg}"
            "</div>"
        )
    return (
        "<div style='background:rgba(128,128,128,0.08);border-radius:10px;padding:12px;'>"
        f"{left}"
        "</div>"
    )

# [2026-08-27 추가, 같은 날 방식 전환] 코스피/코스닥 카드 오른쪽에 넣을 추세 미니 차트 SVG.
# 데이터 소스는 위 get_index_recent_closes() — pykrx/야후 기반이라 실패 위험이 낮다.
# 마지막 구간(전일→오늘)만 등락 색으로 강조하고 나머지는 회색으로 그려서, "오늘 하루
# 장중 흐름"이 아니라 "최근 며칠 추세 속의 오늘"이라는 느낌을 준다(Jone에게 보여드린
# 비교 목업의 옵션② 방식과 동일한 구성 — 점선은 전일/오늘 경계).
def _daily_trend_svg(values: list[float], up: bool, width: int = 72, height: int = 32) -> str:
    """최근 N거래일 종가 리스트(오래된→최신, 마지막=오늘)로 미니 추세선 SVG를 만든다.
    2개 미만이면 빈 문자열 반환. up: 등락 방향(등락률 ≥ 0이면 True) — 마지막 구간과 오늘
    점의 색상을 기존 등락 배지와 통일(상승 #e0635e/하락 #5b9bd8, _UP_COLOR/_DOWN_COLOR 재사용)."""
    if not values or len(values) < 2:
        return ""
    color = _UP_COLOR if up else _DOWN_COLOR
    n = len(values)
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pad = 3

    def _x(i):
        return pad + i * (width - 2 * pad) / (n - 1)

    def _y(v):
        return height - pad - (v - lo) / span * (height - 2 * pad)

    pts_past = " ".join(f"{_x(i):.1f},{_y(v):.1f}" for i, v in enumerate(values[:-1]))
    last_seg = f"{_x(n-2):.1f},{_y(values[-2]):.1f} {_x(n-1):.1f},{_y(values[-1]):.1f}"
    boundary_x = _x(n - 2)
    dots = "".join(
        f'<circle cx="{_x(i):.1f}" cy="{_y(v):.1f}" r="1.4" fill="#b7bac1"/>'
        for i, v in enumerate(values[:-1])
    )
    dots += f'<circle cx="{_x(n-1):.1f}" cy="{_y(values[-1]):.1f}" r="2.2" fill="{color}"/>'
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'style="flex-shrink:0;">'
        f'<line x1="{boundary_x:.1f}" y1="0" x2="{boundary_x:.1f}" y2="{height}" '
        f'stroke="#d9dbe0" stroke-width="1" stroke-dasharray="2,2"/>'
        f'<polyline points="{pts_past}" fill="none" stroke="#b7bac1" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<polyline points="{last_seg}" fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'{dots}'
        f'</svg>'
    )

def _get_index_spark_svg(key: str, mo: dict) -> str:
    """코스피/코스닥 카드용 추세 미니 차트 SVG를 만든다. 실패하면 빈 문자열을 반환해서
    카드는 지금처럼(차트 없이) 정상 표시된다 — 이 함수 자체가 예외를 밖으로 던지지 않는다.
    코드(pykrx 지수코드/야후 티커)는 get_market_overview()의 _kr_index_last_two_closes()
    호출과 동일한 값을 그대로 재사용한다."""
    code_map = {"코스피": ("1001", "^KS11"), "코스닥": ("2001", "^KQ11")}
    codes = code_map.get(key)
    if not codes:
        return ""
    try:
        values = get_index_recent_closes(*codes)
        if len(values) < 2:
            return ""
        v = mo.get(key) or {}
        pct = v.get("등락률")
        up = (pct or 0) >= 0
        return _daily_trend_svg(values, up)
    except Exception as e:
        logging.warning("시황 카드 추세 차트 생성 실패 [%s]: %s", key, e)
        return ""

# [2026-08-19 추가] AI(Claude)가 만든 요약은 마크다운 문법(**굵게**, "- " 목록)을 그대로
# 텍스트로 반환하는데, 이걸 그냥 <br>로만 줄바꿈 처리해서 커스텀 HTML 카드에 넣었더니
# 마크다운이 해석되지 않고 별표(**)가 그대로 화면에 노출되는 버그가 실제로 발견됨
# (Jone 제보, 스크린샷 확인). st.markdown의 표준 마크다운 파서에 맡기는 대신, 이 카드는
# 색깔 있는 배경이 필요해서 raw HTML을 써야 하므로, 필요한 마크다운 문법 2가지(굵게·목록)만
# 직접 HTML로 변환하는 경량 헬퍼를 추가함. 복잡한 마크다운 전체를 지원하진 않지만,
# 프롬프트에서 요청하는 형식(불릿 포인트 3~5개)에는 이 정도로 충분하다.
def _brief_markdown_to_html(text: str) -> str:
    """AI 브리핑 텍스트의 **굵게**와 "- " 목록만 HTML로 변환. 그 외 텍스트는 그대로 두고
    줄바꿈은 <br>로 처리."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    lines = text.split("\n")
    html_parts = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("• "):
            if not in_list:
                html_parts.append("<ul style='margin:4px 0;padding-left:20px;'>")
                in_list = True
            html_parts.append(f"<li style='margin-bottom:4px;'>{stripped[2:]}</li>")
        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            if stripped:
                html_parts.append(f"{stripped}<br>")
    if in_list:
        html_parts.append("</ul>")
    return "".join(html_parts)

def render_daily_report(holdings_df: pd.DataFrame):
    """일일 시황 + 보유 종목별 공시·뉴스·애널리스트 리포트 화면.
    [2026-08-13 추가] 데이터 수집 함수(get_market_overview, get_daily_stock_report 등)는
    2026-08-12에 이미 완성돼 관리자 메뉴 "미리보기"로만 쓰이고 있었는데, 이번에 실제
    사용자 화면으로 옮겼다.
    [2026-08-19 재설계] Jone 피드백(등락색이 실제 증권앱과 다름, 전체 UI 가독성 개선 요청)
    반영 — 네이버 증권 스타일을 참고한 시안을 먼저 승인받고, 카드 기반 레이아웃으로
    전면 재설계함. st.metric 대신 커스텀 HTML 카드/배지를 써서 상승=빨강/하락=파랑을
    앱 전체와 통일했다."""
    st.markdown('<div class="section-title">오늘의 시황 · 종목 리포트</div>', unsafe_allow_html=True)
    st.caption("공시(DART)·뉴스·애널리스트 리포트는 참고용 정보이며, 투자 판단의 근거로 쓰기엔 부족할 수 있습니다.")

    # ── 시황 ──
    with st.spinner("시황 데이터 불러오는 중..."):
        mo = get_market_overview()

    st.markdown("##### 🌐 국내·해외 시황")

    def _card_grid(keys, cols_per_row, spark=False):
        cols = st.columns(cols_per_row)
        i = 0
        for key in keys:
            v = mo.get(key)
            if not v or v.get("값") is None:
                continue
            # [2026-08-27 추가] spark=True인 카드(코스피/코스닥)만 오른쪽에 분봉
            # 미니 차트를 붙인다. 나머지 지표는 spark_svg=""라 기존과 완전히 동일.
            spark_svg = _get_index_spark_svg(key, mo) if spark else ""
            with cols[i % cols_per_row]:
                st.markdown(
                    _metric_card_html(key, f"{v['값']:,.2f}", v.get("등락률"), spark_svg),
                    unsafe_allow_html=True,
                )
            i += 1
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    _card_grid(["코스피", "코스닥"], 2, spark=True)
    _card_grid(["다우존스", "S&P500", "나스닥", "필라델피아반도체"], 4)
    _card_grid(["니케이225", "상하이종합", "항셍지수", "VIX"], 4)
    _card_grid(["원달러환율", "달러인덱스", "WTI"], 3)
    _card_grid(["브렌트유", "국제금", "미국채10년"], 3)

    # ── 코스피/코스닥 수급 ──
    flow_rows = []
    for key in ("코스피_수급", "코스닥_수급"):
        flow = mo.get(key)
        if flow:
            flow_rows.append({
                "시장": key.replace("_수급", ""),
                "외국인": (flow.get("외국인") or 0) / 1e8,
                "기관": (flow.get("기관") or 0) / 1e8,
                "개인": (flow.get("개인") or 0) / 1e8,
            })
    if flow_rows:
        st.markdown("##### 💹 코스피·코스닥 수급 (억원)")

        def _flow_cell(v: float) -> str:
            color = _UP_COLOR if v > 0 else _DOWN_COLOR if v < 0 else "inherit"
            return f"<div style='text-align:right;color:{color};font-weight:600;'>{v:+,.0f}</div>"

        rows_html = "".join(
            "<div style='display:grid;grid-template-columns:1fr 1fr 1fr 1fr;padding:8px 0;"
            "border-top:1px solid rgba(128,128,128,0.15);font-size:14px;align-items:center;'>"
            f"<div style='font-weight:600;'>{r['시장']}</div>"
            f"{_flow_cell(r['외국인'])}{_flow_cell(r['기관'])}{_flow_cell(r['개인'])}"
            "</div>"
            for r in flow_rows
        )
        st.markdown(
            "<div style='background:rgba(128,128,128,0.08);border-radius:10px;padding:4px 12px;'>"
            "<div style='display:grid;grid-template-columns:1fr 1fr 1fr 1fr;padding:8px 0;"
            "font-size:12px;color:var(--text-secondary,#888);'>"
            "<div>시장</div><div style='text-align:right;'>외국인</div>"
            "<div style='text-align:right;'>기관</div><div style='text-align:right;'>개인</div></div>"
            f"{rows_html}</div>",
            unsafe_allow_html=True,
        )

    st.caption(f"기준시각: {mo.get('기준시각', '-')}")
    st.divider()

    # ── 종목별 리포트 ──
    st.markdown("##### 📰 보유 종목 리포트")
    if holdings_df.empty:
        st.info("보유 중인 종목이 없어 종목별 리포트를 표시할 수 없습니다.")
        return

    stock_options = holdings_df[["종목코드", "종목명"]].drop_duplicates().sort_values("종목명")
    code_by_label = {
        f"{row['종목명']} ({row['종목코드']})": (row["종목코드"], row["종목명"])
        for _, row in stock_options.iterrows()
    }
    selected_label = st.selectbox("리포트를 볼 종목 선택", list(code_by_label.keys()), key="daily_report_stock_select")

    if not selected_label:
        return
    code, name = code_by_label[selected_label]
    with st.spinner(f"{name} 리포트 불러오는 중..."):
        report = get_daily_stock_report(code, name)

    # ── AI 종합 브리핑 (Anthropic API) ──
    with st.spinner("AI 종합 브리핑 작성 중..."):
        summary = generate_stock_daily_summary(code, name, report)

    if summary:
        summary_html = _brief_markdown_to_html(summary)
        st.markdown(
            "<div style='background:rgba(46,116,181,0.10);border-radius:12px;padding:16px;margin-bottom:12px;'>"
            "<div style='display:flex;align-items:center;gap:6px;font-size:13px;font-weight:600;"
            "color:#2E74B5;margin-bottom:8px;'>✨ 오늘의 종합 브리핑</div>"
            f"<div style='font-size:14px;line-height:1.7;'>{summary_html}</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.caption("AI(Claude)가 아래 원본 데이터를 바탕으로 요약한 내용입니다. 투자 판단은 반드시 원본을 직접 확인 후 하세요.")
    else:
        st.info(
            "AI 종합 브리핑을 만들지 못했습니다 (Anthropic API 키 미설정 또는 일시적 오류). "
            "아래 원본 데이터를 직접 확인해주세요."
        )

    # ── 컨센서스 요약 카드 ──
    consensus = report["컨센서스"]
    if consensus:
        target = consensus.get("목표주가")
        score = consensus.get("투자의견점수")
        opinion = consensus.get("투자의견_참고라벨") or "-"
        opinion_color = _UP_COLOR if opinion in ("적극매수", "매수") else _DOWN_COLOR if opinion in ("매도", "비중축소") else "inherit"
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(
                "<div style='background:rgba(128,128,128,0.08);border-radius:10px;padding:10px;'>"
                "<div style='font-size:12px;color:var(--text-secondary,#888);'>투자의견</div>"
                f"<div style='font-size:16px;font-weight:600;color:{opinion_color};'>{opinion}</div></div>",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                _metric_card_html("목표주가", f"{target:,.0f}원" if target is not None else "-"),
                unsafe_allow_html=True,
            )
        with c3:
            st.markdown(
                _metric_card_html("의견 점수", f"{score:.2f} / 5" if score is not None else "-"),
                unsafe_allow_html=True,
            )
        st.caption(f"컨센서스 기준일: {consensus.get('기준일', '-')} (네이버 증권 제공, 참고용)")

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.markdown("**📰 최근 뉴스**")
    if report["뉴스"]:
        news_html = "".join(
            "<div style='display:flex;justify-content:space-between;gap:8px;padding:8px 0;"
            "border-top:1px solid rgba(128,128,128,0.15);font-size:13px;'>"
            f"<a href='{n['링크']}' target='_blank' style='color:inherit;text-decoration:none;'>{n['제목']}</a>"
            f"<span style='color:var(--text-secondary,#888);white-space:nowrap;'>{n['언론사']}</span></div>"
            for n in report["뉴스"][:10]
        )
        st.markdown(news_html, unsafe_allow_html=True)
    else:
        st.caption("뉴스 없음")

    st.divider()

    # ── 원본 데이터 (필요할 때만 펼쳐보기) ──
    with st.expander("📋 원본 데이터 보기 (공시·전체 리포트)"):
        st.markdown("**📢 공시 (DART)**")
        if report["공시"]:
            df_disc = pd.DataFrame(report["공시"])
            st.dataframe(
                df_disc, width="stretch", hide_index=True,
                column_config={
                    "링크": st.column_config.LinkColumn("링크", display_text="바로가기"),
                },
            )
        else:
            st.caption("공시 없음 (ETF는 DART 고유번호가 없어 항상 비어있는 게 정상입니다)")

        st.markdown("**📊 애널리스트 리포트**")
        if report["리포트"]:
            df_report = pd.DataFrame(report["리포트"])
            st.dataframe(
                df_report, width="stretch", hide_index=True,
                column_config=build_number_column_config(df_report, money_cols=["목표주가"]),
            )
        else:
            st.caption("애널리스트 리포트 없음")


def main(spreadsheet_id: str):
    # 헤더
    col_title, col_time = st.columns([4, 1])
    with col_title:
        user_name = st.session_state.get("user_name", "")
        st.markdown(f"## 📊 통합자산관리 시스템 <span style='font-size:0.9rem;color:gray'>({user_name})</span>",
                    unsafe_allow_html=True)
    with col_time:
        st.markdown(f"<div style='text-align:right;color:gray;font-size:0.8rem;padding-top:1rem'>{now_kst()} 기준</div>",
                    unsafe_allow_html=True)
        if st.button("로그아웃", key="logout_btn"):
            for k in ("logged_in", "user_name", "user_email", "spreadsheet_id", "oauth_credentials", "is_admin"):
                st.session_state.pop(k, None)
            st.query_params.clear()
            st.rerun()

    # 관리자 메뉴는 상단 메인 탭의 "🔧 관리자 메뉴"(데이터 관리 바로 옆)에서 진입한다
    # (관리자 계정으로 로그인했을 때만 이 탭 자체가 목록에 추가됨 — MAIN_TABS 구성 부분 참고).

    # 데이터 로드
    with st.spinner("데이터 불러오는 중..."):
        trade_df, nonstock_df, monthly_df, transfer_df, cashlog_df = load_all_data(spreadsheet_id)

    # 거래이력 사전 점검 (빈 셀, 거래구분 오타, 초과매도 등 흔한 입력 오류 안내)
    for _msg in validate_trade_df(trade_df):
        st.warning(_msg)

    # 보유 종목 계산
    holdings_df = calc_holdings(trade_df)

    # 시세 조회 (ASSET_MASTER 미등록 종목도 종목코드 기반으로 티커를 자동 생성해 조회)
    tickers = []
    for code in holdings_df["종목코드"].tolist() if not holdings_df.empty else []:
        ticker = get_asset_ticker(code)
        if ticker:
            tickers.append(ticker)

    col_refresh, _ = st.columns([1, 5])
    with col_refresh:
        if st.button("🔄 시세 새로고침", key="refresh_btn"):
            st.cache_data.clear()
            # 버튼 클릭 자체가 이미 화면을 자동으로 다시 그리므로, 여기서 st.rerun()을
            # 추가로 호출하지 않는다. 중복 rerun이 현재 선택된 탭(active_main_tab) 상태를
            # 놓쳐 통합 대시보드로 튕기는 원인이 되었었다.

    prices, 시세기준시각 = get_prices(tuple(tickers)) if tickers else ({}, None)
    holdings_df = enrich_with_prices(holdings_df, prices)

    # 시세 반영 현황 표시 (조회 실패 시 경고)
    if tickers and not prices:
        st.warning("⚠ 실시간 시세 조회에 실패했습니다. 잠시 후 '시세 새로고침'을 눌러주세요. (yfinance 서버 응답 없음)")

    # ──────────────────────────────────────────────
    # 탭 구성
    # ──────────────────────────────────────────────
    # st.tabs()는 "지금 어느 탭이 선택되어 있는지"를 브라우저 쪽에서만 기억하고
    # session_state에는 저장하지 않는다. 그래서 탭 안에서 '카드형→표'처럼 화면 구성이
    # 크게 바뀌는 위젯을 클릭하면, Streamlit이 화면을 완전히 새로 그리는 걸로 착각해
    # 첫 번째 탭으로 튕겨나가는 버그가 있다 (Streamlit 자체의 알려진 미해결 이슈).
    # session_state에 직접 선택된 탭을 저장하는 라디오 버튼으로 대체해 이 문제를 원천 차단한다.
    MAIN_TABS = ["📈 통합 대시보드", "💼 보유 종목", "📐 기술적 분석", "🗞️ 오늘의 리포트", "📋 거래이력", "💵 현금흐름", "⚙️ 데이터 관리"]
    # [2026-08-19] 관리자 메뉴를 대시보드 맨 아래 토글 버튼 방식에서, 다른 메뉴들과 동등하게
    # 상단 탭 목록의 "데이터 관리" 바로 옆으로 옮김 (Jone 요청 — 아래에 있어서 매번 스크롤해야
    # 하고 클릭도 한 번 더 필요해 불편했음). 관리자 계정으로 로그인했을 때만 이 탭 자체가
    # 목록에 추가되어, 다른 사용자 화면에는 존재하지 않는다(코드상 조건부로 append됨).
    is_admin_user = st.session_state.get("is_admin", False)
    if is_admin_user:
        MAIN_TABS = MAIN_TABS + ["🔧 관리자 메뉴"]

    with st.container(key="main_menu_tabs"):
        selected_main_tab = st.radio(
            "메인 메뉴", MAIN_TABS, horizontal=True,
            key="active_main_tab", label_visibility="collapsed",
        )
    st.markdown("<div style='margin-top:-0.5rem'></div>", unsafe_allow_html=True)

    if selected_main_tab == "📈 통합 대시보드":
        render_dashboard(holdings_df, nonstock_df, monthly_df, prices, trade_df=trade_df, transfer_df=transfer_df)
    elif selected_main_tab == "💼 보유 종목":
        render_holdings(holdings_df, prices, nonstock_df, 시세기준시각=시세기준시각)
    elif selected_main_tab == "📐 기술적 분석":
        render_technical_analysis(holdings_df, trade_df)
    elif selected_main_tab == "🗞️ 오늘의 리포트":
        render_daily_report(holdings_df)
    elif selected_main_tab == "📋 거래이력":
        render_trades(trade_df)
    elif selected_main_tab == "💵 현금흐름":
        render_cashflow(trade_df, cashlog_df)
    elif selected_main_tab == "⚙️ 데이터 관리":
        render_data_mgmt(nonstock_df)
    elif selected_main_tab == "🔧 관리자 메뉴" and is_admin_user:
        render_admin_panel()


# ============================================================
# 탭1: 통합 대시보드
# ============================================================
def render_holdings_treemap(holdings_df: pd.DataFrame):
    """보유종목 트리맵 — 사각형 크기=평가금액 비중, 색상=당일(전일 종가 대비) 등락률.
    같은 종목이 여러 계좌에 나뉘어 있으면 종목코드 기준으로 평가금액을 합산해서 하나로 표시.
    '트리맵' / '순위' 두 가지 보기 방식을 토글로 전환할 수 있고, 하단에는 항상
    상승·보합·하락 종목 수 요약 바를 표시한다 (네이버페이 증권 업종 트리맵 UI 참고)."""
    st.markdown('<div class="section-title">보유종목 현황판 (당일 등락률)</div>', unsafe_allow_html=True)

    if holdings_df.empty:
        st.info("보유 중인 종목이 없습니다.")
        return

    grouped = holdings_df.groupby(["종목코드", "종목명"], as_index=False)["평가금액"].sum()
    grouped = grouped[grouped["평가금액"] > 0].reset_index(drop=True)
    if grouped.empty:
        st.info("표시할 보유종목이 없습니다.")
        return

    total_value = grouped["평가금액"].sum()

    tickers = tuple(sorted({
        get_asset_ticker(c) for c in grouped["종목코드"] if get_asset_ticker(c)
    }))
    day_change = get_day_change(tickers)

    def _change_pct(code):
        ticker = get_asset_ticker(code)
        if not ticker:
            return None
        info = day_change.get(ticker)
        return info["change_pct"] if info else None

    def _current_price(code):
        ticker = get_asset_ticker(code)
        if not ticker:
            return None
        info = day_change.get(ticker)
        return info["current"] if info else None

    grouped["당일등락률"] = grouped["종목코드"].apply(_change_pct)
    grouped["현재가"] = grouped["종목코드"].apply(_current_price)
    grouped["등락표시"] = grouped["당일등락률"].apply(
        lambda v: f"{v:+.2f}%" if v is not None else "조회 실패"
    )
    color_values = grouped["당일등락률"].fillna(0).tolist()

    # 평가금액 가중 당일 등락률(포트폴리오 전체 관점의 하루 성적 요약) — 조회 성공한 종목만으로 계산
    valid = grouped.dropna(subset=["당일등락률"])
    if not valid.empty:
        weighted_pct = (valid["당일등락률"] * valid["평가금액"]).sum() / valid["평가금액"].sum()
        weighted_color = color_pnl(weighted_pct)
        st.markdown(
            f'<div style="display:flex;gap:0.7rem;margin-bottom:0.9rem;flex-wrap:wrap">'
            f'<div style="background:var(--card-bg);border:1px solid var(--card-border);'
            f'border-radius:12px;padding:0.8rem 1.1rem;flex:1;min-width:180px">'
            f'<div style="font-size:0.82rem;color:var(--text-dim)">보유종목 평가금액 합계</div>'
            f'<div style="font-size:1.3rem;font-weight:700;margin-top:0.15rem">{total_value:,.0f}원</div>'
            f'</div>'
            f'<div style="background:var(--card-bg);border:1px solid var(--card-border);'
            f'border-radius:12px;padding:0.8rem 1.1rem;flex:1;min-width:180px">'
            f'<div style="font-size:0.82rem;color:var(--text-dim)">평가금액 가중 당일 등락률</div>'
            f'<div style="font-size:1.3rem;font-weight:700;margin-top:0.15rem;color:{weighted_color}">{weighted_pct:+.2f}%</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    view_mode = st.radio(
        "보기 방식", ["트리맵", "순위"], horizontal=True,
        key="holdings_view_mode", label_visibility="collapsed",
    )

    if view_mode == "트리맵":
        fig = go.Figure(go.Treemap(
            labels=grouped["종목명"],
            parents=[""] * len(grouped),
            values=grouped["평가금액"],
            customdata=grouped["등락표시"],
            texttemplate="%{label}<br>%{customdata}",
            textfont=dict(size=14),
            pathbar=dict(visible=False),
            marker=dict(
                colors=color_values,
                colorscale=[
                    [0, "#1a4d8f"], [0.25, "#5b9bd8"], [0.5, "#2b2f3a"],
                    [0.75, "#e0635e"], [1, "#8f1f1a"],
                ],  # 하락일수록 진한 파랑, 상승일수록 진한 빨강 (국내 관례)
                cmid=0,
                line=dict(width=1, color="#1a1d24"),
            ),
            hovertemplate="<b>%{label}</b><br>평가금액: %{value:,.0f}원<br>당일등락률: %{customdata}<extra></extra>",
        ))
        fig.update_layout(
            margin=dict(t=10, l=4, r=4, b=4),
            height=360,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)
    else:
        # 순위 리스트: 당일등락률 내림차순 정렬 (네이버페이 증권 '외국인 매매 상위' 랭킹 UI 참고)
        ranked = grouped.dropna(subset=["당일등락률"]).sort_values("당일등락률", ascending=False).reset_index(drop=True)
        no_data = grouped[grouped["당일등락률"].isna()]

        rows_html = ""
        for i, row in ranked.iterrows():
            pct = row["당일등락률"]
            price = row["현재가"]
            price_html = f"{int(price):,}" if price is not None else "-"
            rows_html += (
                '<div style="display:flex;align-items:center;padding:9px 10px;'
                'border-top:1px solid var(--card-border)">'
                f'<span style="width:22px;font-size:0.78rem;color:var(--text-dim2)">{i + 1}</span>'
                f'<span style="flex:1;font-size:0.86rem;color:var(--text-strong)">{row["종목명"]}</span>'
                f'<span style="font-size:0.86rem;font-weight:600;margin-right:10px;'
                f'font-variant-numeric:tabular-nums">{price_html}</span>'
                f'<span style="font-size:0.82rem;font-weight:700;min-width:62px;text-align:right;'
                f'color:{color_pnl(pct)};font-variant-numeric:tabular-nums">{pct:+.2f}%</span>'
                '</div>'
            )
        if no_data.empty:
            no_data_html = ""
        else:
            names = ", ".join(no_data["종목명"].tolist())
            no_data_html = (
                '<div style="padding:9px 10px;border-top:1px solid var(--card-border);'
                f'font-size:0.8rem;color:var(--text-dim2)">조회 실패: {names}</div>'
            )
        st.markdown(
            f'<div style="background:var(--card-bg);border:1px solid var(--card-border);'
            f'border-radius:12px;padding:4px 4px 4px">{rows_html}{no_data_html}</div>',
            unsafe_allow_html=True,
        )

    # 상승/보합/하락 종목 수 요약 바 — 트리맵/순위 두 보기 모두에서 하단에 공통 표시
    up = int((grouped["당일등락률"] > 0).sum())
    flat = int((grouped["당일등락률"] == 0).sum())
    down = int((grouped["당일등락률"] < 0).sum())
    failed = int(grouped["당일등락률"].isna().sum())
    summary = f"🔴 상승 {up} · ⚪ 보합 {flat} · 🔵 하락 {down}"
    if failed:
        summary += f" · 조회실패 {failed}"
    st.caption(summary)
    st.caption("사각형 크기 = 보유종목 평가금액 비중 · 색상 = 당일(전일 종가 대비) 등락률")


def calc_asset_summary(holdings_df, nonstock_df, trade_df=None, transfer_df=None):
    """전체 자산(주식/ETF + TDF/펀드 + 현금성자산) 합산 요약을 계산.
    render_dashboard, render_holdings 등 여러 탭에서 공통으로 사용.
    trade_df/transfer_df를 주면 실현손익(이미 매도·이체로 확정된 손익)까지 함께 계산."""
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

    # 통합 합산 (미실현 손익 — 현재 보유 중인 자산 기준)
    total_eval = stock_eval + nonstock_eval
    total_cost = stock_cost + tdf_cost + cash_eval  # 현금은 원금=평가

    total_pnl  = total_eval - total_cost
    total_pct  = total_pnl / total_cost * 100 if total_cost else 0
    stock_pnl  = stock_eval - stock_cost
    stock_pct  = stock_pnl / stock_cost * 100 if stock_cost else 0

    tdf_pnl = tdf_eval - tdf_cost
    tdf_pct = tdf_pnl / tdf_cost * 100 if tdf_cost else 0
    cash_pct_of_total = cash_eval / total_eval * 100 if total_eval else 0

    # 4) 실현손익 (이미 매도·이체로 확정된 손익 — 대시보드 총 손익에서 빠져있던 부분)
    realized_stock = 0
    if trade_df is not None and not trade_df.empty:
        _realized_df = calc_realized_pnl(trade_df)
        if not _realized_df.empty:
            realized_stock = int(_realized_df["실현손익"].sum())

    realized_transfer = 0
    if transfer_df is not None and not transfer_df.empty and "실현손익" in transfer_df.columns:
        realized_transfer = int(transfer_df["실현손익"].apply(_safe_num).sum())

    realized_total = realized_stock + realized_transfer
    grand_total_pnl = total_pnl + realized_total  # 미실현 + 실현 = 누적 총손익

    return {
        "stock_eval": stock_eval, "stock_cost": stock_cost, "stock_pnl": stock_pnl, "stock_pct": stock_pct,
        "tdf_eval": tdf_eval, "tdf_cost": tdf_cost, "tdf_pnl": tdf_pnl, "tdf_pct": tdf_pct,
        "cash_eval": cash_eval, "cash_pct_of_total": cash_pct_of_total,
        "nonstock_eval": nonstock_eval,
        "total_eval": total_eval, "total_cost": total_cost, "total_pnl": total_pnl, "total_pct": total_pct,
        "realized_stock": realized_stock, "realized_transfer": realized_transfer,
        "realized_total": realized_total, "grand_total_pnl": grand_total_pnl,
    }


def render_dashboard(holdings_df, nonstock_df, monthly_df, prices, trade_df=None, transfer_df=None):

    # [2026-08-19 추가] 장 시작 전(09:00 KST 이전)/장 마감 후(15:30~20:00) 안내 배너.
    # Jone 제보 + 실측(16:52, ETF 3종목은 정규장 종가와 정확히 일치·NXT 거래가능 개별주식만
    # 크게 벌어짐)으로 원인이 확정됨: 이 앱의 시세 소스(pykrx→네이버, KRX 정규장 데이터)는
    # 정규장(09:00~15:30) 밖의 시간에는 "정규장 종가"에서 더 이상 안 바뀌는데, NXT
    # 거래가능 종목은 프리마켓(08:00~08:50)·애프터마켓(15:30~20:00)에도 실시간으로 계속
    # 움직인다. 처음엔 프리마켓만 반영했었는데, 애프터마켓에서도 똑같은 문제가 실제로
    # 재현되는 걸 놓쳤음 — 두 시간대 모두 안내하도록 확장.
    # NXT 실시간가 자체를 가져오는 건 별도 조사가 필요해 아직 미착수(관리자와 논의 후 보류).
    if is_before_krx_open():
        st.warning(
            "⏰ 지금은 KRX 정규장 개장(09:00) 전입니다. 화면의 시세는 **전일 종가**이며, "
            "NXT 프리마켓 등 실시간 시세는 아직 반영되지 않습니다. 실시간 가격은 증권사 앱을 참고해주세요."
        )
    elif is_after_krx_close():
        st.warning(
            "⏰ 지금은 KRX 정규장 마감(15:30) 이후입니다. 화면의 시세는 **정규장 종가**이며, "
            "NXT 애프터마켓(15:30~20:00) 실시간 시세는 반영되지 않습니다. "
            "(참고: ETF는 NXT 거래 대상이 아니라 이 시간대에도 실제가와 대체로 일치합니다.) "
            "실시간 가격은 증권사 앱을 참고해주세요."
        )

    render_holdings_treemap(holdings_df)

    s = calc_asset_summary(holdings_df, nonstock_df, trade_df=trade_df, transfer_df=transfer_df)
    stock_eval, stock_cost, stock_pct = s["stock_eval"], s["stock_cost"], s["stock_pct"]
    tdf_eval, tdf_cost, tdf_pct = s["tdf_eval"], s["tdf_cost"], s["tdf_pct"]
    cash_eval = s["cash_eval"]
    total_eval, total_cost, total_pnl, total_pct = s["total_eval"], s["total_cost"], s["total_pnl"], s["total_pct"]
    realized_total, grand_total_pnl = s["realized_total"], s["grand_total_pnl"]

    # ── 히어로 카드: 원금 → 평가금액 → 손익 한눈에 + 비중 바 ──
    st.markdown('<div class="section-title">통합 자산 현황</div>', unsafe_allow_html=True)

    stock_pct_w = stock_eval / total_eval * 100 if total_eval else 0
    tdf_pct_w   = tdf_eval / total_eval * 100 if total_eval else 0
    cash_pct_w  = cash_eval / total_eval * 100 if total_eval else 0

    def _asset_tile(icon, name, stripe, value, pct_w, sub_label, sub_value, pct_val=None):
        """자산 구성 카드(아이콘 배지 + 좌측 컬러 스트라이프) 1개 생성 (한 줄 문자열로 반환 — 마크다운 코드블록 오인 방지)."""
        if pct_val is None:
            pct_html = f'<span style="color:var(--text-dim2)">{sub_value}</span>'
        else:
            arrow = "▲" if pct_val > 0 else "▼" if pct_val < 0 else "―"
            pct_html = f'<span class="asset-breakdown-pct" style="color:{color_pnl(pct_val)}">{arrow} {fmt_pct(pct_val)}</span>'
        return (
            f'<div class="asset-breakdown-item" style="--stripe:{stripe};--tint:{stripe}1a;--tint-strong:{stripe}33;--stripe-border:{stripe}40">'
            f'<div class="asset-breakdown-head">'
            f'<span class="asset-breakdown-name"><span class="asset-breakdown-icon">{icon}</span>{name}</span>'
            f'<span class="asset-breakdown-badge">{pct_w:.0f}%</span>'
            f'</div>'
            f'<div class="asset-breakdown-value">{fmt_money_full(value)}</div>'
            f'<div class="asset-breakdown-sub"><span>{sub_label}</span>{pct_html}</div>'
            f'<div class="asset-breakdown-bar"><div style="width:{pct_w:.1f}%;background:{stripe}"></div></div>'
            f'</div>'
        )

    stock_tile = _asset_tile("📈", "주식/ETF", "#7C6CF0", stock_eval, stock_pct_w, f"원금 {fmt_money_full(stock_cost)}", "", stock_pct)
    tdf_tile   = _asset_tile("🏦", "TDF/펀드", "#1FBFA0", tdf_eval, tdf_pct_w, f"원금 {fmt_money_full(tdf_cost)}", "", tdf_pct)
    cash_tile  = _asset_tile("💰", "현금성자산", "#6B6F7A", cash_eval, cash_pct_w, "예수금 등", "변동 없음", None)
    breakdown_grid_html = f'<div class="asset-breakdown-grid">{stock_tile}{tdf_tile}{cash_tile}</div>'

    hero_html = (
        f'<div class="hero-card">'
        f'<div class="hero-label">총 투자원금 <span class="cost-emph">{fmt_money_full(total_cost)}</span> → 통합 평가금액</div>'
        f'<div class="hero-row">'
        f'<div class="hero-value">{fmt_money_full(total_eval)}</div>'
        f'<div class="hero-pnl" style="color:{color_pnl(total_pnl)}">{fmt_money_full(total_pnl)} ({fmt_pct(total_pct)})</div>'
        f'</div>'
        f'<div class="hero-bar">'
        f'<div style="width:{stock_pct_w:.1f}%;background:#7C6CF0"></div>'
        f'<div style="width:{tdf_pct_w:.1f}%;background:#1FBFA0"></div>'
        f'<div style="width:{cash_pct_w:.1f}%;background:#6B6F7A"></div>'
        f'</div>'
        f'<div style="font-size:0.78rem;color:var(--text-dim2);margin-top:0.3rem">자산 구성 비율 (주식·TDF·현금 비중, 손익과는 무관)</div>'
        f'{breakdown_grid_html}'
        f'</div>'
    )
    st.markdown(hero_html, unsafe_allow_html=True)

    # 미실현 + 실현(매도·이체로 이미 확정된) 손익을 함께 보여줘서 "체감 수익"과 화면 숫자 차이를 없앰
    if realized_total != 0:
        st.caption(
            f"미실현 {fmt_money_full(total_pnl)} + 실현손익(매도·이체) {fmt_money_full(realized_total)} "
            f"= 누적 총손익 {fmt_money_full(grand_total_pnl)}"
        )

    # ── 계좌별 현황 ──
    st.markdown('<div class="section-title">계좌별 현황</div>', unsafe_allow_html=True)

    # 신한/미래에셋으로 고정하지 않고, 거래이력·비주식자산 시트에 실제 등장하는 계좌명만 동적으로 집계.
    # 사용자마다 보유 계좌 구성이 다르므로, 데이터가 없는 계좌는 카드 자체가 생성되지 않는다.
    def _num(x):
        try:
            v = str(x).strip().replace(",", "")
            return float(v) if v and v not in ("-", "") else 0.0
        except (ValueError, TypeError):
            return 0.0

    stock_accounts = set(holdings_df["계좌"].unique()) if not holdings_df.empty else set()
    nonstock_accounts = set(nonstock_df["계좌"].unique()) if not nonstock_df.empty else set()
    all_accounts = sorted(stock_accounts | nonstock_accounts)

    account_cards = []
    for acct in all_accounts:
        acct_holdings = holdings_df[holdings_df["계좌"] == acct] if not holdings_df.empty else pd.DataFrame()
        stock_eval_a = int(acct_holdings["평가금액"].sum()) if not acct_holdings.empty else 0
        stock_cost_a = int(acct_holdings["매입금액"].sum()) if not acct_holdings.empty else 0

        acct_nonstock = nonstock_df[nonstock_df["계좌"] == acct] if not nonstock_df.empty else pd.DataFrame()
        tdf_eval_a, tdf_cost_a, cash_eval_a = 0.0, 0.0, 0.0
        if not acct_nonstock.empty:
            for _, row in acct_nonstock.iterrows():
                유형 = str(row.get("자산군", ""))
                eva = _num(row.get("평가금액", 0))
                pri = _num(row.get("원금", 0))
                if 유형 in ("TDF", "펀드", "채권"):
                    tdf_eval_a += eva
                    tdf_cost_a += pri
                elif 유형 == "현금성자산":
                    cash_eval_a += eva
        tdf_eval_a, tdf_cost_a, cash_eval_a = int(tdf_eval_a), int(tdf_cost_a), int(cash_eval_a)

        total_a = stock_eval_a + tdf_eval_a + cash_eval_a
        cost_a  = stock_cost_a + tdf_cost_a + cash_eval_a  # 현금은 원금=평가
        pnl_a   = total_a - cost_a
        pct_a   = pnl_a / cost_a * 100 if cost_a else 0

        bg, fg = get_account_color(acct, all_accounts)
        card_html = f"""
        <div class="acct-card">
            <span class="acct-badge" style="background:{bg};color:{fg}">{acct}</span>
            <div class="acct-main-row">
                <div class="acct-value">{fmt_money_full(total_a)}</div>
                <div class="acct-pnl" style="color:{color_pnl(pnl_a)}">{fmt_money_full(pnl_a)} ({fmt_pct(pct_a)})</div>
            </div>
            <div class="acct-divider"></div>
            <div class="acct-row">
                <span class="acct-row-label">투자원금</span>
                <span class="acct-row-val">{fmt_money_full(cost_a)}</span>
            </div>
            <div class="acct-divider-light"></div>
            <div class="acct-row">
                <span class="acct-row-label acct-row-sub">├ 보유종목 평가</span>
                <span class="acct-row-val">{fmt_money_full(stock_eval_a)}</span>
            </div>
            <div class="acct-row">
                <span class="acct-row-label acct-row-sub">├ TDF/펀드 평가</span>
                <span class="acct-row-val">{fmt_money_full(tdf_eval_a)}</span>
            </div>
            <div class="acct-row">
                <span class="acct-row-label acct-row-sub">└ 현금</span>
                <span class="acct-row-val">{fmt_money_full(cash_eval_a)}</span>
            </div>
        </div>"""
        account_cards.append(
            {"acct": acct, "total": total_a, "pnl": pnl_a, "tdf_cost": tdf_cost_a, "tdf_eval": tdf_eval_a, "html": card_html}
        )

    # 평가금액이 큰 계좌부터 좌 → 우 순서로 2열 배치
    account_cards.sort(key=lambda c: c["total"], reverse=True)

    if not account_cards:
        st.info("등록된 계좌 데이터가 없습니다.")
    else:
        cols = st.columns(2)
        for i, c in enumerate(account_cards):
            with cols[i % 2]:
                st.markdown(c["html"], unsafe_allow_html=True)

        # TDF 환매 후 원금 중 일부만 재투자되어 특정 계좌 손익이 큰 폭의 마이너스로 보이는 경우,
        # 실제 손실로 오해하지 않도록 계좌별로 안내 문구 표시
        for c in account_cards:
            _tdf_gap = c["tdf_cost"] - c["tdf_eval"]
            if _tdf_gap > 0 and c["pnl"] < 0 and _tdf_gap >= abs(c["pnl"]) * 0.5:
                st.caption(
                    f"💡 **{c['acct']}** 계좌의 평가손익이 큰 폭의 마이너스로 보이는 건 실제 손실이 아닐 수 있습니다. "
                    "TDF 환매 후 원금 중 일부만 재투자되고 나머지는 다른 계좌(예수금)로 이동한 경우 이렇게 표시됩니다. "
                    "자세한 내역은 '데이터 관리' 탭을 확인해주세요."
                )

    # ── 자산 구성 (도넛 + 표 병행) ──
    st.markdown('<div class="section-title">자산 구성</div>', unsafe_allow_html=True)

    # 계좌가 아닌 실제 자산 유형(ETF/주식/TDF/현금) 기준으로 집계 — 한 계좌 안에 ETF와 주식이
    # 함께 있어도(예: 미래에셋 계좌에 ETF+주식 혼재) 정확히 분리되도록 get_asset_type()으로 분류.
    # 주식은 다시 get_asset_market()으로 국내/해외를 나눠, 해외 종목이 "국내주식"에
    # 잘못 합산되지 않도록 한다 (해외 주식 지원 이후 발견된 분류 누락 수정 — 2026-08).
    if not holdings_df.empty:
        _type_series = holdings_df.apply(lambda r: get_asset_type(r["종목코드"], r["종목명"]), axis=1)
        _market_series = holdings_df["종목코드"].apply(get_asset_market)
        etf_eval = int(holdings_df.loc[_type_series == "ETF", "평가금액"].sum())
        _stock_mask = _type_series == "주식"
        domestic_stock_eval = int(holdings_df.loc[_stock_mask & (_market_series == "KR"), "평가금액"].sum())
        overseas_stock_eval = int(holdings_df.loc[_stock_mask & (_market_series == "US"), "평가금액"].sum())
    else:
        etf_eval = 0
        domestic_stock_eval = 0
        overseas_stock_eval = 0

    # 큰 조각과 작은 조각이 번갈아 나오도록 순서 배열 — 작은 조각(TDF·현금성자산)끼리
    # 붙어있으면 바깥으로 뽑은 라벨 선이 서로 겹치기 쉬워서, 양옆에 항상 큰 조각이 오도록 배치
    _colors = ["#5c6bc0", "#78909c", "#7b1fa2", "#0288d1", "#ff7043"]
    _labels = ["국내주식", "현금성자산", "ETF", "TDF/펀드", "해외주식"]
    _values = [max(0, v) for v in [domestic_stock_eval, cash_eval, etf_eval, tdf_eval, overseas_stock_eval]]
    _total_for_pct = sum(_values) or 1

    col_donut, col_table = st.columns([1, 1])
    with col_donut:
        fig_type = go.Figure(go.Pie(
            labels=_labels, values=_values,
            hole=0.55, textinfo="percent", textposition="auto", insidetextorientation="radial",
            marker_colors=_colors, automargin=True,
            hovertemplate="%{label}: %{value:,.0f}원 (%{percent})<extra></extra>",
        ))
        fig_type.update_layout(
            title="자산군별 비중", height=340,
            margin=dict(t=40, b=30, l=40, r=40),
            showlegend=False,
            font=dict(size=14),
        )
        st.plotly_chart(fig_type, width="stretch", config=PLOTLY_CONFIG)

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
        st.caption("📌 빨간 막대 = 그 달 말 기준 통합 평가금액 · 파란 선·점 = 그 달 말 기준 통합 투자원금. 정확한 금액은 막대 위에 마우스를 올리거나 아래 표를 확인하세요.")
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
            # [수정] 시트에 저장된 순서 그대로 쓰다 보니(수동 스냅샷을 나중에 끼워넣는 등) 차트가
            # 7월-6월-8월처럼 뒤섞여 보이는 문제가 있었다. 추이 차트는 시간순(오름차순)으로
            # 왼쪽→오른쪽이 과거→최근이 되어야 자연스러우므로 명시적으로 정렬한다.
            mdf = mdf.sort_values("년월_표시")

            # ── 차트: 막대·선에는 텍스트 라벨을 넣지 않는다 (정확한 금액은 마우스오버 툴팁 또는 아래 표로 확인).
            # 이전 버전은 막대(평가금액)와 선(원금) 라벨이 서로 겹쳐 보이는 문제가 있었는데,
            # 라벨을 아예 없애고 표로 역할을 분리해 근본적으로 해결했다.
            fig_trend = go.Figure()
            fig_trend.add_trace(go.Bar(
                x=mdf["년월_표시"], y=mdf["통합평가"],
                name="평가금액", marker_color="#e0635e", opacity=0.85,
                hovertemplate="%{x}<br>평가금액 %{y:,.0f}원<extra></extra>",
            ))
            fig_trend.add_trace(go.Scatter(
                x=mdf["년월_표시"], y=mdf["통합원금"],
                name="원금", mode="lines+markers",
                line=dict(color="#8ecaff", width=2),
                marker=dict(size=8, color="#8ecaff"),
                hovertemplate="%{x}<br>원금 %{y:,.0f}원<extra></extra>",
            ))
            _max_val = max(mdf["통합평가"].max(), mdf["통합원금"].max())
            fig_trend.update_layout(
                height=280,
                margin=dict(t=20, b=30, l=10, r=10),
                legend=dict(orientation="h", y=1.1),
                yaxis=dict(tickformat=",", range=[0, _max_val * 1.15]),
                xaxis=dict(type="category", tickangle=0),
                bargap=0.5,
                hovermode="x unified",
            )
            st.plotly_chart(fig_trend, width="stretch", config=PLOTLY_CONFIG)

            # ── 상세 표: 월별 정확한 금액 · 손익 · 수익률 ──
            table_df = mdf[["년월_표시", "통합원금", "통합평가"]].copy()
            table_df["손익"] = table_df["통합평가"] - table_df["통합원금"]
            table_df["수익률"] = table_df.apply(
                lambda r: round(r["손익"] / r["통합원금"] * 100, 2) if r["통합원금"] else 0.0, axis=1
            )
            table_df = table_df.rename(columns={"년월_표시": "년월"}).sort_values("년월", ascending=False)

            styled_trend = table_df.style.map(style_pnl_cell, subset=["손익", "수익률"])
            col_config = build_number_column_config(
                table_df, money_cols=["통합원금", "통합평가", "손익"], pct_cols=["수익률"]
            )
            with st.expander("📋 월별 상세 표 보기 (정확한 금액·손익·수익률)"):
                st.dataframe(
                    styled_trend, width="stretch", hide_index=True,
                    column_config=col_config,
                    height=min(320, 50 + 40 * len(table_df)),
                )
        except Exception as e:
            st.caption(f"추이 차트 오류: {e}")

    # ── 월별 스냅샷 자동 저장 ──
    # '월별자산스냅샷' 시트는 거래이력에서 자동 계산되지 않고, 그때그때 스냅샷을 남기는 방식이라
    # 여기서 버튼 한 번으로 '지금 이 순간'의 통합원금/통합평가금액을 이번 달 기록으로 저장한다.
    st.markdown('<div class="section-title">이번 달 스냅샷 저장</div>', unsafe_allow_html=True)
    this_yearmonth = datetime.now(KST).strftime("%Y-%m")
    already_exists = (
        not monthly_df.empty
        and "년월" in monthly_df.columns
        and monthly_df["년월"].astype(str).str.strip().str.slice(0, 7).eq(this_yearmonth).any()
    )
    st.caption(
        f"{this_yearmonth} 기준 통합원금 {fmt_money_full(total_cost)} · 통합평가금액 {fmt_money_full(total_eval)}"
        f"{'을(를) 이미 있는 이번 달 기록에 덮어씁니다.' if already_exists else '을(를) 새 줄로 추가합니다.'}"
    )
    if st.button("📸 이번 달 스냅샷 저장", key="save_monthly_snapshot_btn"):
        spreadsheet_id_for_snapshot = st.session_state.get("spreadsheet_id", "")
        with st.spinner("스냅샷 저장 중..."):
            ok, msg = save_monthly_snapshot(
                spreadsheet_id_for_snapshot, this_yearmonth, total_cost, total_eval
            )
        (st.success if ok else st.error)(msg)
        if ok:
            st.rerun()

    # ── 개발자 정보 (통합 대시보드 맨 아래에만 표시) ──
    # [2026-08-19] 관리자 메뉴 진입 버튼은 상단 "🔧 관리자 메뉴" 탭으로 옮겨서 여기서는 제거함.
    st.markdown("---")
    col_dev, col_btn = st.columns([5, 1])
    with col_dev:
        st.caption(f"개발자: H.W Jone · {APP_VERSION}")
    with col_btn:
        if st.button("ℹ️ 앱 정보", key="dev_info_btn"):
            show_developer_info()


# ============================================================
# 탭2: 보유 종목 상세
# ============================================================
def render_holdings(holdings_df, prices, nonstock_df=None, 시세기준시각=None):
    st.markdown('<div class="section-title">보유 종목 상세</div>', unsafe_allow_html=True)

    if holdings_df.empty:
        st.info("보유 종목이 없습니다.")
        return

    # ── 보유 주식/ETF 평가금액 요약 (이 탭은 주식/ETF만 다루므로, 카드 주인공도 주식/ETF로 통일) ──
    if nonstock_df is not None:
        s = calc_asset_summary(holdings_df, nonstock_df)
        stock_pct_w = s["stock_eval"] / s["total_eval"] * 100 if s["total_eval"] else 0
        st.markdown(f"""
        <div class="hero-card">
            <div class="hero-label">투자원금 {fmt_money_full(s['stock_cost'])} → 보유 주식/ETF 평가금액</div>
            <div class="hero-row">
                <div class="hero-value">{fmt_money_full(s['stock_eval'])}</div>
                <div class="hero-pnl" style="color:{color_pnl(s['stock_pnl'])}">{fmt_money_full(s['stock_pnl'])} ({fmt_pct(s['stock_pct'])})</div>
            </div>
            <div class="hero-legend">
                <span>TDF/펀드·현금성자산을 더한 전체 자산 {fmt_money_full(s['total_eval'])} 중 {stock_pct_w:.0f}%를 차지합니다</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

    시세반영수 = holdings_df["시세반영"].sum() if "시세반영" in holdings_df.columns else 0
    전체수 = len(holdings_df)
    # [2026-08-11 추가] 시세 조회는 60초 캐시(ttl=60)를 쓰기 때문에, "카드형 ↔ 표" 보기 방식을
    # 전환하는 것만으로도 그사이 캐시가 만료되면 현재가가 미세하게 달라질 수 있다. 이걸 버그로
    # 오해하지 않도록 "이 시세가 언제 조회된 값인지"를 화면에 명시한다.
    if 시세기준시각:
        st.caption(f"총 {전체수}종목 · 실시간 시세 반영 {시세반영수}종목 · 시세 기준 {시세기준시각}")
    else:
        st.caption(f"총 {전체수}종목 · 실시간 시세 반영 {시세반영수}종목")

    # 계좌별 필터
    계좌목록 = ["전체"] + sorted(holdings_df["계좌"].unique().tolist())
    선택계좌 = st.selectbox("계좌 필터", 계좌목록, key="holding_account_filter")
    st.markdown('<div class="ui-gap-md"></div>', unsafe_allow_html=True)
    if 선택계좌 != "전체":
        display_df = holdings_df[holdings_df["계좌"] == 선택계좌]
    else:
        display_df = holdings_df

    # ── 동일 종목명이 여러 계좌에 걸쳐 있을 때 구분용 표시명 생성 (차트 라벨 겹침 방지) ──
    # 계좌명을 신한/미래에셋으로 한정 짓지 않고, 사용자가 입력한 실제 계좌명을 그대로 사용
    display_df = display_df.copy()
    _name_counts = display_df["종목명"].value_counts()
    display_df["표시명"] = display_df.apply(
        lambda r: f"{r['종목명']}({r['계좌']})" if _name_counts[r["종목명"]] > 1 else r["종목명"],
        axis=1,
    )

    # ── 필터 적용된 보유종목 합계 (계좌 필터 반영) ──
    filt_eval = int(display_df["평가금액"].sum()) if not display_df.empty else 0
    filt_cost = int(display_df["매입금액"].sum()) if not display_df.empty else 0
    filt_pnl = filt_eval - filt_cost
    filt_pct = filt_pnl / filt_cost * 100 if filt_cost else 0
    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown(f"""
        <div class="metric-card" style="background:var(--card-bg);border:1px solid var(--card-border)">
            <div class="metric-label">보유종목 투자원금 ({선택계좌})</div>
            <div class="metric-value">{fmt_money_full(filt_cost)}</div>
        </div>""", unsafe_allow_html=True)
    with m2:
        st.markdown(f"""
        <div class="metric-card" style="background:var(--card-bg);border:1px solid var(--card-border)">
            <div class="metric-label">보유종목 평가금액 ({선택계좌})</div>
            <div class="metric-value">{fmt_money_full(filt_eval)}</div>
        </div>""", unsafe_allow_html=True)
    with m3:
        st.markdown(f"""
        <div class="metric-card" style="background:var(--card-bg);border:1px solid var(--card-border)">
            <div class="metric-label">평가손익 ({선택계좌})</div>
            <div class="metric-value" style="color:{color_pnl(filt_pnl)}">{fmt_money_full(filt_pnl)} ({fmt_pct(filt_pct)})</div>
        </div>""", unsafe_allow_html=True)

    # ETF 먼저, 그 다음 주식 — 각 그룹 내에서는 투자원금(매입금액) 큰 순서
    def _type_rank(row):
        return 0 if get_asset_type(row["종목코드"], row["종목명"]) == "ETF" else 1

    display_df = display_df.copy()
    display_df["_type_rank"] = display_df.apply(_type_rank, axis=1)
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
        type_label = get_asset_type(code, row["종목명"])
        if get_asset_market(code) == "US":
            type_label += " 🌐"
        cur_val = current_price if has_price else row["평균단가"]

        table_rows.append({
            "구분": type_label,
            "종목명": row["종목명"],
            "계좌": 계좌,
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

    st.markdown('<div class="ui-gap-md"></div>', unsafe_allow_html=True)
    보기방식 = st.radio("보기 방식", ["카드형", "표"], horizontal=True, key="holding_view_mode")

    _acct_order = sorted(holdings_df["계좌"].unique().tolist()) if not holdings_df.empty else []

    if 보기방식 == "카드형":
        for _, r in table_df.iterrows():
            _bg, _fg = get_account_color(r["계좌"], _acct_order)
            현재가_str = f"{r['현재가']:,}" if r["현재가"] is not None else "-"
            시세표시 = "" if r["시세반영"] else ' <span style="color:#c9a227">(매입가 기준)</span>'
            st.markdown(f"""
            <div class="holding-card">
                <div class="holding-top-row">
                    <div class="holding-name-block">
                        <span class="holding-type-badge">{r['구분']}</span>
                        <span class="holding-acct-badge" style="background:{_bg};color:{_fg}">{r['계좌']}</span>
                        <span class="holding-name">{r['종목명']}</span>
                    </div>
                    <div class="holding-pct-badge" style="color:{color_pnl(r['수익률'])}">{fmt_pct(r['수익률'])}</div>
                </div>
                <div class="holding-main-row">
                    <div class="holding-eval">{fmt_money_full(r['평가금액'])}</div>
                    <div class="holding-pnl" style="color:{color_pnl(r['손익'])}">{fmt_money_full(r['손익'])}</div>
                </div>
                <div class="holding-sub">
                    <span><b>현재가</b> {현재가_str}원{시세표시}</span>
                    <span><b>평단</b> {r['평단']:,}원</span>
                    <span><b>수량</b> {r['수량']:,}주</span>
                    <span><b>투자원금</b> {r['투자원금']:,}원</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        show_cols = ["구분", "종목명", "계좌", "수량", "평단", "현재가", "투자원금", "평가금액", "손익", "수익률"]
        styled_table = table_df[show_cols].style.map(style_pnl_cell, subset=["손익", "수익률"])

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
            width="stretch",
            hide_index=True,
            column_config=col_config,
            height=min(560, 50 + 45 * len(table_df)),
        )

    미반영수 = (~display_df["시세반영"]).sum() if "시세반영" in display_df.columns else 0
    if 미반영수 > 0:
        st.caption(f"⚠ {미반영수}종목은 실시간 시세 조회에 실패해 매입가로 표시 중입니다. '시세 새로고침'을 눌러 다시 시도하세요.")

    # ── 종목별 보유 비중 도넛 + 수익률 바 차트 ──
    if len(display_df) > 0:
        ch1, ch2 = st.columns([5, 7])

        with ch1:
            st.markdown('<div class="section-title">종목별 보유 비중</div>', unsafe_allow_html=True)
            donut_labels = display_df["표시명"].tolist()
            donut_values = display_df["평가금액"].tolist()
            # 색상군(파랑/빨강/주황/초록/보라 등)이 최대한 겹치지 않도록 배치한 12색 팔레트
            palette = [
                "#4C6FFF", "#FF6B6B", "#FFB84C", "#2EC4B6",
                "#9B5DE5", "#F15BB5", "#00BBF9", "#43AA8B",
                "#E76F51", "#8338EC", "#06D6A0", "#FEE440",
            ]
            donut_colors = [palette[i % len(palette)] for i in range(len(donut_labels))]
            total_val = sum(donut_values) or 1

            fig_donut = go.Figure(go.Pie(
                labels=donut_labels,
                values=donut_values,
                hole=0.52,
                textinfo="percent",          # 슬라이스 안에는 %만 표시
                textposition="inside",
                marker_colors=donut_colors,
                textfont=dict(size=12),
                insidetextorientation="horizontal",
                # 비중 3% 미만 슬라이스는 텍스트를 숨겨 겹침 방지 (범례로 확인 가능)
                texttemplate=["%{percent:.1%}" if v / total_val >= 0.03 else "" for v in donut_values],
                pull=[0.03 if v / total_val < 0.06 else 0 for v in donut_values],
            ))
            fig_donut.update_layout(
                height=380,
                margin=dict(t=10, b=10, l=10, r=10),
                showlegend=True,
                legend=dict(
                    orientation="v",
                    x=1.01, y=0.5,
                    font=dict(size=12),
                    itemsizing="constant",
                ),
                font=dict(size=12),
            )
            st.plotly_chart(fig_donut, width="stretch", config=PLOTLY_CONFIG)

        with ch2:
            st.markdown('<div class="section-title">종목별 수익률</div>', unsafe_allow_html=True)
            chart_df = display_df.sort_values("수익률")
            colors = ["#e0635e" if v >= 0 else "#5b9bd8" for v in chart_df["수익률"]]

            # x축 범위: 최대/최소값 기준 20% 여유 확보 (% 텍스트 잘림 방지)
            max_v = max(chart_df["수익률"].max(), 0)
            min_v = min(chart_df["수익률"].min(), 0)
            x_range = [min_v * 1.35 if min_v < 0 else -5,
                       max_v * 1.35 if max_v > 0 else 5]

            fig = go.Figure(go.Bar(
                x=chart_df["수익률"],
                y=chart_df["표시명"],
                orientation="h",
                marker_color=colors,
                text=[fmt_pct(v) for v in chart_df["수익률"]],
                textposition="outside",
                cliponaxis=False,           # 축 범위 밖 텍스트 잘림 방지
            ))
            fig.update_layout(
                height=max(320, len(chart_df) * 44),
                margin=dict(t=10, b=30, l=10, r=10),
                xaxis=dict(
                    title="수익률(%)",
                    range=x_range,
                    zeroline=True,
                    tickfont=dict(size=12),
                ),
                yaxis=dict(tickfont=dict(size=12)),
                font=dict(size=12),
            )
            st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)


# ============================================================
# 탭3: 기술적 분석
# ============================================================
@st.cache_data(ttl=86400)  # 일봉 기준 지표라 하루 한 번만 갱신해도 충분 (KRX 서버 부담도 줄임)
def _fetch_price_history(ticker: str, months_back: int | None) -> pd.DataFrame:
    """기술적 분석용 과거 시세 조회. pykrx로 조회하며, adjusted=True(기본값)로 수정주가를
    사용한다 (실제 데이터 출처는 네이버페이 증권 — 위 _fetch_krx_stock_price 주석 참고).
    [중요 수정] 예전에는 야후 파이낸스를 auto_adjust=False(수정 안 한 원래 가격)로 조회했다.
    실시간 시세(get_prices)는 이미 야후의 국내 종목 갱신 지연 문제 때문에 pykrx로 전환했지만,
    이 기술적 분석용 과거 시세 조회 함수는 그 전환에서 빠져 있었다. 그 결과 (1) 야후 시세 자체가
    실제 거래소 가격과 달라지는 문제에 더해 (2) 액면분할·배당 등으로 수정주가가 필요한 구간에서
    '수정 안 한' 야후 가격을 쓰다 보니, 캔들차트·이동평균·RSI·이격도·기간 최고가/최저가 등
    이 화면의 모든 수치가 HTS(수정주가 기준으로 연속된 차트를 보여줌)와 크게 달라 보이는
    근본 원인이었다. pykrx의 기본값인 수정주가(adjusted=True)로 통일해 HTS와 같은 기준으로 맞춘다.
    [해외 주식 지원 — 2026-08 추가] pykrx는 국내(KRX) 종목만 다루므로, .KS/.KQ가 아닌 해외
    티커는 별도 함수(_fetch_price_history_foreign)로 분기한다."""
    if not (ticker.endswith(".KS") or ticker.endswith(".KQ")):
        return _fetch_price_history_foreign(ticker, months_back)
    krx_code = ticker.split(".")[0]
    try:
        today = datetime.now(KST).strftime("%Y%m%d")
        if months_back is None:
            from_date = "19900101"  # 상장 이후 전체 (실제 상장일 이전 날짜를 넣어도 pykrx가 있는 만큼만 반환)
        else:
            from_date = (datetime.now(KST) - pd.DateOffset(months=months_back)).strftime("%Y%m%d")
        df = krx_stock.get_market_ohlcv_by_date(from_date, today, krx_code)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df[df["종가"] > 0].copy()
        df.index.name = "Date"
        df = df.reset_index().rename(columns={
            "시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume",
        })
        return df[["Date", "Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        logging.warning("과거 시세 조회 실패(KRX) [%s]: %s", krx_code, e)
        return pd.DataFrame()


def _fetch_price_history_foreign(ticker: str, months_back: int | None) -> pd.DataFrame:
    """해외(미국) 종목의 과거 시세 조회. pykrx는 국내 거래소만 다루므로 이 경우엔 야후
    파이낸스에서 달러 기준 OHLC를 받아, 현재 환율로 원화 환산해서 반환한다. 정확히는
    그날그날의 환율을 적용해야 맞지만, 이 앱은 과거 환율을 별도로 저장하지 않으므로 조회
    시점의 환율을 차트 전체 구간에 일괄 적용하는 방식을 쓴다 — 그래프의 등락 모양(추세)은
    달러 기준 원본과 동일하고, 세로축의 절대 원화 스케일만 '오늘 환율 기준'이라는 점을
    참고할 것. (해외 주식 지원 — 2026-08 추가)"""
    try:
        if months_back is None:
            hist = yf.Ticker(ticker).history(period="max", auto_adjust=True)
        else:
            start_date = (datetime.now(KST) - pd.DateOffset(months=months_back)).strftime("%Y-%m-%d")
            hist = yf.Ticker(ticker).history(start=start_date, auto_adjust=True)
        if hist.empty:
            return pd.DataFrame()
        hist = hist.reset_index()
        # 야후파이낸스가 반환하는 날짜는 미국 동부 시간대 정보(tz-aware)가 붙어 있는데,
        # 구글시트에서 읽어온 거래일자(tz 정보 없음)나 국내 종목의 pykrx 날짜(역시 tz 없음)와
        # 형(dtype)이 달라 merge_asof에서 병합 오류가 났었다. 시간대 정보를 제거해 다른 모든
        # 날짜 컬럼과 같은 형식(tz-naive)으로 맞춘다.
        if isinstance(hist["Date"].dtype, pd.DatetimeTZDtype):
            hist["Date"] = hist["Date"].dt.tz_localize(None)
        rate = get_usd_krw_rate_safe()
        for col in ("Open", "High", "Low", "Close"):
            hist[col] = hist[col] * rate
        return hist[["Date", "Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        logging.warning("과거 시세 조회 실패(해외) [%s]: %s", ticker, e)
        return pd.DataFrame()


def _calc_technical_indicators(hist: pd.DataFrame) -> dict:
    """이동평균·이격도·RSI(14)·볼린저밴드·거래량 배율을 계산해서 반환. 매수/매도 신호는
    만들지 않고, 수치와 통상적인 해석 기준만 함께 제공한다 — 최종 판단은 사용자 몫이라는 원칙."""
    close = hist["Close"]
    result = {"현재가": float(close.iloc[-1])}

    for window in (5, 20, 60, 120):
        if len(close) >= window:
            ma = close.rolling(window).mean().iloc[-1]
            result[f"MA{window}"] = float(ma)
            result[f"이격도{window}"] = (result["현재가"] - ma) / ma * 100 if ma else None
        else:
            result[f"MA{window}"] = None
            result[f"이격도{window}"] = None

    # RSI(14) — 표준 공식(평균 상승폭/평균 하락폭 비율)
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, pd.NA)
        rsi = 100 - (100 / (1 + rs))
        result["RSI14"] = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else None
    else:
        result["RSI14"] = None

    # 볼린저밴드(20, ±2표준편차) — 가장 널리 쓰이는 표준 설정. 표준편차는 "가격이 평균에서
    # 얼마나 들쭉날쭉했는지"를 나타내는 값으로, 변동성이 커지면 밴드 폭도 함께 넓어진다.
    # [2026-08-11 추가]
    if len(close) >= 20:
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        result["BB중심"] = float(bb_mid.iloc[-1])
        result["BB상단"] = float((bb_mid + bb_std * 2).iloc[-1])
        result["BB하단"] = float((bb_mid - bb_std * 2).iloc[-1])
        band_width = result["BB상단"] - result["BB하단"]
        result["BB위치"] = ((result["현재가"] - result["BB하단"]) / band_width * 100) if band_width else None
    else:
        result["BB중심"] = result["BB상단"] = result["BB하단"] = result["BB위치"] = None

    # 거래량 배율 — 최근 거래량이 최근 20구간 평균 거래량 대비 몇 배인지. [2026-08-11 추가]
    if "Volume" in hist.columns and len(hist) >= 20:
        recent_volume = float(hist["Volume"].iloc[-1])
        avg_volume = float(hist["Volume"].iloc[-20:].mean())
        result["거래량배율"] = (recent_volume / avg_volume) if avg_volume else None
    else:
        result["거래량배율"] = None

    return result


def _bb_interpretation(bb_pos) -> str:
    """볼린저밴드 내 위치(0~100%)의 통상적인 해석 기준만 담백하게 서술."""
    if bb_pos is None:
        return "데이터가 부족해 계산할 수 없습니다."
    if bb_pos >= 100:
        return "통상 상단선을 벗어난(뚫은) 상태로, 평소 변동 범위 위쪽을 넘어섰습니다."
    if bb_pos <= 0:
        return "통상 하단선을 벗어난(뚫은) 상태로, 평소 변동 범위 아래쪽을 넘어섰습니다."
    if bb_pos >= 80:
        return "통상 상단선에 가까운 위치입니다."
    if bb_pos <= 20:
        return "통상 하단선에 가까운 위치입니다."
    return "밴드 중심 부근입니다."


def _volume_ratio_interpretation(ratio) -> str:
    """거래량 배율의 통상적인 해석 기준만 담백하게 서술."""
    if ratio is None:
        return "데이터가 부족해 계산할 수 없습니다."
    if ratio >= 2:
        return "최근 20구간 평균 대비 거래가 크게 활발한 편입니다."
    if ratio <= 0.5:
        return "최근 20구간 평균 대비 거래가 뜸한 편입니다."
    return "최근 20구간 평균과 비슷한 수준입니다."


def _rsi_interpretation(rsi) -> str:
    """RSI 수치의 통상적인 해석 기준만 담백하게 서술 (매수/매도 결론 없음)."""
    if rsi is None:
        return "데이터가 부족해 계산할 수 없습니다."
    if rsi >= 70:
        return "통상 70 이상은 과매수 구간으로 해석됩니다."
    if rsi <= 30:
        return "통상 30 이하는 과매도 구간으로 해석됩니다."
    return "통상 30~70 사이는 중립 구간으로 해석됩니다."


def _gap_interpretation(gap) -> str:
    """이격도 수치의 통상적인 해석 기준만 담백하게 서술."""
    if gap is None:
        return "데이터가 부족해 계산할 수 없습니다."
    if gap > 5:
        return "이동평균보다 다소 높은 위치입니다."
    if gap < -5:
        return "이동평균보다 다소 낮은 위치입니다."
    return "이동평균과 비슷한 수준입니다."


def _generate_ta_comment(hist: pd.DataFrame, ind: dict, hi_lo: dict, unit: str) -> list[str]:
    """수치를 있는 그대로 서술하는 객관적 코멘트 목록을 만든다.
    원칙: (1) '사라'/'팔아라' 같은 매수·매도 결론은 절대 내리지 않는다.
    (2) 존재하는 수치(이동평균 배열, 이격도, RSI, 거래량, 고점·저점 대비 위치)만 사실 그대로
    서술한다. (3) 데이터가 부족한 지표는 문장 자체를 생략한다(억지로 채우지 않음)."""
    comments = []
    price = ind.get("현재가")

    # 1) 이동평균 배열 상태 — '정배열/역배열'은 시장에서 통용되는 객관적 용어(추세의 방향성을
    #    나타내는 관찰 사실)이며, 매수/매도 판단이 아니라 현재 배열 상태에 대한 서술이다.
    mas = [ind.get(f"MA{w}") for w in (5, 20, 60, 120)]
    if all(m is not None for m in mas):
        if mas[0] > mas[1] > mas[2] > mas[3]:
            comments.append(f"{5}{unit}선부터 {120}{unit}선까지 순서대로 배열된 '정배열' 상태입니다(단기 이동평균이 장기 이동평균보다 위).")
        elif mas[0] < mas[1] < mas[2] < mas[3]:
            comments.append(f"{5}{unit}선부터 {120}{unit}선까지 순서대로 배열된 '역배열' 상태입니다(단기 이동평균이 장기 이동평균보다 아래).")
        else:
            comments.append("이동평균선들이 뒤섞여 있어 뚜렷한 정배열·역배열 상태는 아닙니다.")

    # 2) RSI 구간
    rsi = ind.get("RSI14")
    if rsi is not None:
        zone = "과매수" if rsi >= 70 else "과매도" if rsi <= 30 else "중립"
        comments.append(f"RSI(14)는 {rsi:.1f}로 통상적 기준상 {zone} 구간에 해당합니다.")

    # 3) 최고가·최저가 대비 현재 위치
    if price is not None and hi_lo.get("최고가") and hi_lo.get("최저가"):
        from_high = (price - hi_lo["최고가"]) / hi_lo["최고가"] * 100
        from_low = (price - hi_lo["최저가"]) / hi_lo["최저가"] * 100
        comments.append(f"현재가는 조회 기간 내 최고가 대비 {from_high:+.1f}%, 최저가 대비 {from_low:+.1f}% 지점입니다.")

    # 4) 볼린저밴드 내 위치 — 20구간 이동평균 ± 2표준편차로 그린 밴드의 어디쯤에 있는지.
    #    [2026-08-11 추가]
    bb_pos = ind.get("BB위치")
    if bb_pos is not None:
        zone = "상단선 위" if bb_pos >= 100 else "하단선 아래" if bb_pos <= 0 else "중심 부근" if 20 < bb_pos < 80 else ("상단선 인근" if bb_pos >= 80 else "하단선 인근")
        comments.append(f"볼린저밴드({20}{unit}, ±2표준편차) 내에서 {zone}에 위치합니다(밴드 내 위치 {bb_pos:.0f}%).")

        # 4-1) 밴드 폭 변화 안내 — [2026-08-12 추가]
        # 차트에 그려진 밴드는 조회 기간 "전체"를 다 그린 것이라, 과거에 변동성이 크게 튀었던
        # 구간(밴드가 확 넓어진 지점)이 있으면 그 폭이 시각적으로 눈에 띄어서, 지금(맨 오른쪽)
        # 밴드가 이미 좁아진 상태인데도 마치 하단/상단에 몰려 있는 것처럼 착각하기 쉽다.
        # 이걸 코멘트로 미리 짚어줘서, 카드 수치(밴드 내 위치)와 차트 인상이 다르게 느껴질 때
        # 원인을 바로 알 수 있게 한다. 실제로 눈에 띄게 좁아졌을 때만(과거 최대폭 대비 70%
        # 미만이고, 그 최대폭 시점이 최근 10구간 이내가 아닐 때만) 코멘트를 추가한다 — 항상
        # 뜨면 오히려 코멘트가 지저분해지고 정보 가치가 떨어지기 때문.
        bb_width_series = hist["Close"].rolling(20).std() * 4
        valid_width = bb_width_series.dropna()
        if len(valid_width) >= 20:
            current_width = float(bb_width_series.iloc[-1])
            max_width = float(valid_width.max())
            max_pos = int(valid_width.values.argmax())
            max_abs_pos = hist.index.get_indexer([valid_width.index[max_pos]])[0]
            bars_since_peak = (len(hist) - 1) - max_abs_pos
            if current_width and max_width and current_width < max_width * 0.7 and bars_since_peak > 10:
                peak_date = hist["Date"].iloc[max_abs_pos]
                comments.append(
                    f"참고: 차트의 밴드는 조회 기간 전체를 그린 것입니다. 변동성이 가장 컸던 "
                    f"{peak_date.strftime('%Y-%m-%d')} 무렵 밴드 폭이 {max_width:,.0f}원으로 가장 넓었고, "
                    f"현재 밴드 폭은 {current_width:,.0f}원으로 그때보다 좁아진 상태입니다. "
                    f"화면 전체에서는 그 넓었던 구간에 시선이 쏠려 최근 위치가 실제와 다르게 보일 수 있습니다."
                )

    # 5) 거래량 배율 — 최근 1구간 거래량이 최근 20구간 평균 대비 몇 배인지. [2026-08-11 추가]
    vol_ratio = ind.get("거래량배율")
    if vol_ratio is not None:
        comments.append(f"최근 거래량은 20{unit} 평균 대비 {vol_ratio:.1f}배 수준입니다.")

    # 6) 최근 거래량 추세 — 최근 5구간 평균과 그 직전 20구간 평균을 비교 (데이터가 충분할 때만)
    if "Volume" in hist.columns and len(hist) >= 25:
        recent_vol = hist["Volume"].iloc[-5:].mean()
        prior_vol = hist["Volume"].iloc[-25:-5].mean()
        if prior_vol:
            vol_chg = (recent_vol - prior_vol) / prior_vol * 100
            if abs(vol_chg) >= 20:
                direction = "증가" if vol_chg > 0 else "감소"
                comments.append(f"최근 거래량은 직전 대비 {direction}했습니다({vol_chg:+.0f}%).")
            else:
                comments.append("최근 거래량은 직전과 비슷한 수준입니다.")

    # 7) 최근 등락률 (최대 20구간, 데이터가 그보다 짧으면 있는 만큼만)
    n = min(20, len(hist) - 1)
    if n >= 1:
        past_price = float(hist["Close"].iloc[-1 - n])
        if past_price:
            chg = (price - past_price) / past_price * 100
            comments.append(f"최근 {n}{unit} 동안 {chg:+.1f}% 변동했습니다.")

    return comments


def _calc_period_high_low(hist: pd.DataFrame) -> dict:
    """조회 기간(현재 1년) 중 최고가·최저가와 그 날짜를 계산."""
    idx_high = hist["High"].idxmax()
    idx_low = hist["Low"].idxmin()
    return {
        "최고가": float(hist.loc[idx_high, "High"]),
        "최고가_날짜": hist.loc[idx_high, "Date"],
        "최저가": float(hist.loc[idx_low, "Low"]),
        "최저가_날짜": hist.loc[idx_low, "Date"],
    }


CANDLE_PERIOD_OPTIONS = {
    # months_back: HTS 화면을 실측해서 맞춘 값 (None=상장 이후 전체)
    "일봉": {"months_back": 7,   "resample": None, "unit": "일"},
    "주봉": {"months_back": 38,  "resample": "W",  "unit": "주"},   # 약 3년 2개월
    "월봉": {"months_back": 144, "resample": "ME", "unit": "개월"},  # 약 12년
    "년봉": {"months_back": None, "resample": "YE", "unit": "년"},  # 상장 이후 전체
}

def _avg_cost_series(trade_df: pd.DataFrame, code: str) -> pd.DataFrame:
    """특정 종목코드의 '평균매입단가가 매매 시점마다 어떻게 바뀌었는지' 시계열로 반환.
    여러 계좌에 나눠 보유해도 전체를 합산한 하나의 평균단가로 계산한다(_replay_trade_ledger는
    계좌별로 따로 관리하는데, 기술적분석 화면은 종목 단위 차트라 계좌 구분 없이 합산이 맞다).
    전량 매도로 보유수량이 0이 되면 그 시점부터는 평균단가를 표시하지 않는다(청산 후 다시
    매수하면 그 시점의 매수가부터 새로 시작 — 옛 평균단가와 섞이면 안 되므로)."""
    if trade_df.empty:
        return pd.DataFrame(columns=["Date", "평균단가"])
    df = trade_df[trade_df["종목코드"].astype(str).str.strip() == str(code).strip()].copy()
    if df.empty:
        return pd.DataFrame(columns=["Date", "평균단가"])
    df["_dt"] = pd.to_datetime(df["거래일자"], errors="coerce")
    df = df.dropna(subset=["_dt"]).sort_values("_dt")

    qty_held = 0
    avg_cost = 0.0
    points = []
    for _, row in df.iterrows():
        qty = int(_safe_num(row.get("거래수량", 0)))
        price = _safe_num(row.get("거래단가", 0))
        구분 = str(row.get("거래구분", "")).strip()
        if 구분 == "매수":
            new_qty = qty_held + qty
            avg_cost = (avg_cost * qty_held + price * qty) / new_qty if new_qty else price
            qty_held = new_qty
        elif 구분 == "매도":
            qty_held = max(0, qty_held - qty)
            if qty_held == 0:
                avg_cost = 0.0
        points.append({"Date": row["_dt"], "평균단가": avg_cost if qty_held > 0 else None})

    result = pd.DataFrame(points)
    # 해외(미국) 종목은 거래단가가 달러로 입력되므로, 캔들차트(이미 원화 환산됨)와 같은
    # 기준으로 맞추기 위해 평균단가도 현재 환율로 원화 환산한다. (해외 주식 지원 — 2026-08 추가)
    if not result.empty and get_asset_market(code) == "US":
        result["평균단가"] = result["평균단가"] * get_usd_krw_rate_safe()
    return result


def _resample_ohlcv(hist: pd.DataFrame, rule: str) -> pd.DataFrame:
    """일봉 데이터를 주봉/월봉/년봉으로 리샘플링. 시가는 기간의 첫 값, 고가/저가는 최대/최소,
    종가는 기간의 마지막 값, 거래량은 합계로 집계한다 (증권사 HTS와 동일한 표준 방식)."""
    df = hist.set_index("Date")
    agg = df.resample(rule).agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum",
    }).dropna(subset=["Close"])
    return agg.reset_index()


def render_technical_analysis(holdings_df: pd.DataFrame, trade_df: pd.DataFrame):
    st.markdown('<div class="section-title">기술적 분석</div>', unsafe_allow_html=True)
    st.caption(
        "⚠ 전일 종가 기준으로 계산합니다(실시간 아님). "
        "아래 수치는 매수·매도 신호가 아니라 통상적인 해석 기준을 참고용으로 제공하는 것이며, "
        "투자 판단과 책임은 본인에게 있습니다."
    )

    if holdings_df.empty:
        st.info("보유 종목이 없습니다.")
        return

    # 종목코드 기준으로 계좌 합산 (같은 종목을 여러 계좌에 나눠 갖고 있어도 하나로만 표시)
    # 정렬 순서는 '보유 종목 상세' 화면과 동일하게: ETF 먼저, 그다음 주식 — 각 그룹 내에서는
    # 계좌를 합산한 투자원금(매입금액) 총액이 큰 순서.
    unique_codes = holdings_df[["종목코드", "종목명"]].drop_duplicates(subset="종목코드").copy()
    _cost_by_code = holdings_df.groupby("종목코드")["매입금액"].sum()
    unique_codes["_매입금액총액"] = unique_codes["종목코드"].map(_cost_by_code)
    unique_codes["_type_rank"] = unique_codes.apply(
        lambda r: 0 if get_asset_type(r["종목코드"], r["종목명"]) == "ETF" else 1, axis=1
    )
    unique_codes = unique_codes.sort_values(
        ["_type_rank", "_매입금액총액"], ascending=[True, False]
    )
    options = {f"{row['종목명']} ({row['종목코드']})": row["종목코드"] for _, row in unique_codes.iterrows()}
    labels = list(options.keys())

    # 종목 선택 위젯 자체는 HTS처럼 거래량 차트 '아래'에 탭 형태로 배치할 것이므로,
    # 여기서는 위젯을 만들지 않고 이전 선택값만 안전하게 읽어와 차트를 그리는 데 사용한다.
    # (보유종목이 바뀌어 이전 선택값이 더 이상 없으면 첫 번째 종목으로 자동 대체)
    if st.session_state.get("ta_ticker_select") not in options:
        st.session_state["ta_ticker_select"] = labels[0]
    selected_label = st.session_state["ta_ticker_select"]
    code = options[selected_label]
    ticker = get_asset_ticker(code)

    if get_asset_market(code) == "US":
        st.caption("🌐 해외(미국) 종목입니다. 달러 시세를 오늘 환율로 원화 환산해서 보여줍니다 (실시간 환율 아님, 캐시 기준 최대 1시간 이내).")

    period_names = list(CANDLE_PERIOD_OPTIONS.keys())
    selected_period_name = st.radio(
        "기간", period_names, horizontal=True, key="ta_candle_period",
    )
    period_cfg = CANDLE_PERIOD_OPTIONS[selected_period_name]
    unit = period_cfg["unit"]

    hist = _fetch_price_history(ticker, months_back=period_cfg["months_back"])
    if hist.empty or len(hist) < 6:
        st.warning("이 종목은 과거 시세 데이터를 충분히 가져오지 못해 분석할 수 없습니다.")
        return

    if period_cfg["resample"]:
        hist = _resample_ohlcv(hist, period_cfg["resample"])
        if len(hist) < 6:
            st.warning(f"{selected_period_name} 기준으로는 데이터가 충분하지 않아 분석할 수 없습니다.")
            return

    ind = _calc_technical_indicators(hist)
    hi_lo = _calc_period_high_low(hist)

    # ── 상승/하락 판정 기준: '전일 종가 대비' (국내 HTS 관행) ──
    # [중요 수정] 기존에는 Plotly Candlestick 기본 방식대로 '당일 시가 대비 종가'로 양봉/음봉을
    # 정했는데, 국내 HTS·증권사 앱은 그 날 시가가 얼마였든 상관없이 '전일 종가보다 오늘 종가가
    # 높은가'로 빨간/파란 캔들을 정한다. 두 기준이 갈리는 날(예: 시가는 전일 종가보다 낮게
    # 출발했지만 종가는 전일 종가보다 높게 마감)에는 색이 반대로 보였다. 캔들 몸통(시가·고가·
    # 저가·종가 값 자체)은 그대로 두고, '어느 색으로 그릴지'만 전일 종가 대비 기준으로 바꾼다.
    # 거래량 막대도 같은 기준을 쓰는 국내 HTS 관행에 맞춰 동일하게 적용한다.
    prev_close = hist["Close"].shift(1)
    is_up = hist["Close"] >= prev_close
    if len(is_up) > 0:
        # 첫 캔들은 비교할 전일 종가가 없으므로, 그 날의 시가 대비 종가로 대신 판정한다.
        is_up.iloc[0] = hist["Close"].iloc[0] >= hist["Open"].iloc[0]

    def _masked(series):
        return series.where(is_up)

    def _masked_inv(series):
        return series.where(~is_up)

    # ── 차트: 캔들차트 + 이동평균선(위) + 거래량 막대(아래) ──
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.72, 0.28], vertical_spacing=0.03,
    )
    # 상승(빨강)·하락(파랑) 구간을 두 개의 트레이스로 나눠 그린다. Plotly Candlestick은
    # '시가 대비 종가'로만 색을 정하는 게 기본 동작이라, 전일 종가 대비 기준으로 바꾸려면
    # 이렇게 날짜별로 마스킹한 두 트레이스로 쪼개는 방법이 필요하다(값이 없는 날은 자동으로 빈 칸 처리).
    fig.add_trace(go.Candlestick(
        x=hist["Date"], open=_masked(hist["Open"]), high=_masked(hist["High"]),
        low=_masked(hist["Low"]), close=_masked(hist["Close"]),
        name=selected_period_name, increasing_line_color="#e35b5b", decreasing_line_color="#e35b5b",
    ), row=1, col=1)
    fig.add_trace(go.Candlestick(
        x=hist["Date"], open=_masked_inv(hist["Open"]), high=_masked_inv(hist["High"]),
        low=_masked_inv(hist["Low"]), close=_masked_inv(hist["Close"]),
        name=selected_period_name, increasing_line_color="#4a90d9", decreasing_line_color="#4a90d9",
        showlegend=False,
    ), row=1, col=1)
    for window, color in zip((5, 20, 60, 120), ("#f0a020", "#2ecc71", "#9b59b6", "#7f8c8d")):
        if len(hist) < window:
            continue  # 데이터가 그 기간만큼 없으면 빈 범례만 남기지 않고 아예 건너뜀
        ma_series = hist["Close"].rolling(window).mean()
        fig.add_trace(go.Scatter(
            x=hist["Date"], y=ma_series, name=f"{window}{unit} 이동평균",
            line=dict(width=1.2, color=color, dash="dot"),
        ), row=1, col=1)

    # ── 볼린저밴드(20구간, ±2표준편차) ── [2026-08-11 추가]
    # 이미 이동평균 4개 + 평단가선까지 겹쳐 그려서 차트가 붐비는 편이라, 기본은 꺼둔 채
    # 체크박스로 켤 수 있게 한다(원하는 사람만 추가 정보를 더 보는 방식).
    show_bb = st.checkbox("볼린저밴드 표시 (20구간, ±2표준편차)", value=False, key="ta_show_bb")
    if show_bb and len(hist) >= 20:
        bb_mid = hist["Close"].rolling(20).mean()
        bb_std = hist["Close"].rolling(20).std()
        bb_upper = bb_mid + bb_std * 2
        bb_lower = bb_mid - bb_std * 2
        fig.add_trace(go.Scatter(
            x=hist["Date"], y=bb_upper, name="볼린저밴드 상단",
            line=dict(width=1, color="#8ecaff", dash="dash"), showlegend=True,
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=hist["Date"], y=bb_lower, name="볼린저밴드 하단",
            line=dict(width=1, color="#8ecaff", dash="dash"),
            fill="tonexty", fillcolor="rgba(142,202,255,0.08)", showlegend=True,
        ), row=1, col=1)

    # ── 내 평균단가 추세선 (매매 시점마다 계단식으로 변동) ──
    # 매매 시마다 값이 바뀌는 계단형 선이라 line_shape='hv'(수평→수직)를 쓴다. 이동평균선보다
    # 굵고 뚜렷한 색으로 그려서 '내 매입 단가 대비 지금 시세가 얼마나 차이나는지'가 한눈에
    # 보이게 한다. 계좌를 나눠 보유해도 전체 합산 평균단가 하나로 표시한다.
    avg_series = _avg_cost_series(trade_df, code).dropna(subset=["평균단가"])
    if not avg_series.empty:
        avg_series = avg_series.sort_values("Date")
        # [수정] 기존에는 hist(캔들)의 날짜에만 평균단가를 끼워맞췄는데, 오늘 막 매수한 종목은
        # 매수일이 아직 캔들 데이터의 마지막 날짜보다 더 최근일 수 있다(예: 장 마감 반영 전).
        # 이 경우 hist 쪽엔 매수일과 같거나 그 이후인 날짜가 하나도 없어서 병합 결과가 전부
        # 빈 값이 되어 선 자체가 통째로 안 보였다. hist 날짜에 avg_series 자신의 날짜(매수일)도
        # 더해서 병합 기준으로 삼으면, 캔들이 아직 없는 시점이라도 매수 시점 자체는 반드시
        # 하나의 기준점으로 포함된다.
        merge_dates = pd.concat([hist[["Date"]], avg_series[["Date"]]]).drop_duplicates().sort_values("Date")
        merged_avg = pd.merge_asof(merge_dates, avg_series, on="Date", direction="backward")
        if merged_avg["평균단가"].notna().any():
            # [재수정] marker size를 0으로 줄이는 방식이 기대와 달리 점을 완전히 숨기지
            # 못했다(실제 배포에서 확인됨). size 조절 대신 mode 문자열 자체를 바꿔 아예
            # markers를 포함시키지 않는 방식으로 확실하게 처리한다. 유효 구간이 점 1개뿐일
            # 때(막 매수 직후, 선을 그을 다음 점이 없음)만 "lines+markers"를 쓰고, 거래가
            # 여러 번이라 이미 선으로 이어지는 종목은 "lines"만 써서 점이 아예 안 생기게 한다.
            valid_points = merged_avg["평균단가"].notna().sum()
            trace_mode = "lines+markers" if valid_points == 1 else "lines"
            fig.add_trace(go.Scatter(
                x=merged_avg["Date"], y=merged_avg["평균단가"], name="내 평균단가",
                mode=trace_mode,
                line=dict(width=3, color="#ffd166", shape="hv"),
                marker=dict(size=6, color="#ffd166"),
                hovertemplate="%{x|%Y-%m-%d}<br>내 평균단가: %{y:,.0f}원<extra></extra>",
            ), row=1, col=1)

    # 기간 내 최고가/최저가 라벨
    fig.add_annotation(
        x=hi_lo["최고가_날짜"], y=hi_lo["최고가"], text=f"최고 {hi_lo['최고가']:,.0f}",
        showarrow=True, arrowhead=1, yshift=12, font=dict(size=11, color="#e35b5b"), row=1, col=1,
    )
    fig.add_annotation(
        x=hi_lo["최저가_날짜"], y=hi_lo["최저가"], text=f"최저 {hi_lo['최저가']:,.0f}",
        showarrow=True, arrowhead=1, yshift=-12, font=dict(size=11, color="#4a90d9"), row=1, col=1,
    )

    volume_colors = ["#e35b5b" if up else "#4a90d9" for up in is_up]
    fig.add_trace(go.Bar(
        x=hist["Date"], y=hist["Volume"], name="거래량", marker_color=volume_colors, showlegend=False,
    ), row=2, col=1)

    fig.update_layout(
        height=520, margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        xaxis_rangeslider_visible=False,
    )
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)

    # ── 종목 선택 (HTS처럼 거래량 차트 바로 아래에 탭 형태로 배치) ──
    with st.container(key="ta_ticker_tabs"):
        st.radio(
            "종목 선택", labels, horizontal=True,
            key="ta_ticker_select", label_visibility="collapsed",
        )

    # ── 현재가/최고가/최저가 요약 (숫자 카드) ──
    # [2026-08-11 추가] holdings_df는 이미 enrich_with_prices()를 거쳐 "현재가"가 채워져 있고
    # (해외 종목도 원화 환산 완료 상태), 같은 종목코드가 여러 계좌에 나뉘어 있어도 시세는
    # 종목코드 하나에 하나뿐이므로 첫 유효값만 가져오면 된다.
    _cur_price_rows = holdings_df.loc[holdings_df["종목코드"] == code, "현재가"].dropna()
    현재가 = float(_cur_price_rows.iloc[0]) if not _cur_price_rows.empty else None

    # [2026-08-11 강조 개선] st.metric은 세 카드가 흰색 글씨로 똑같이 보여 한눈에 구분하기
    # 어려웠다. 차트 캔들·주석과 같은 배색 규칙(상승·최고=빨강, 하락·최저=파랑)을 그대로 가져와
    # 카드 왼쪽에 색 스트라이프 + 값 자체도 그 색으로 강조하고, 최고가/최저가는 기록일을
    # 툴팁이 아니라 카드 안에 바로 보이는 캡션으로 둬서 모바일에서도 바로 확인되게 한다.
    cur_col, hi_col, lo_col = st.columns(3)
    with cur_col:
        st.markdown(f"""
        <div class="metric-card" style="background:var(--card-bg);border:1px solid var(--card-border);border-left:4px solid #f0a020">
            <div class="metric-label">현재가</div>
            <div class="metric-value" style="color:#f0a020">{f"{현재가:,.0f}" if 현재가 is not None else "-"}</div>
        </div>
        """, unsafe_allow_html=True)
    with hi_col:
        st.markdown(f"""
        <div class="metric-card" style="background:var(--card-bg);border:1px solid var(--card-border);border-left:4px solid #e35b5b">
            <div class="metric-label">🔺 기간 내 최고가</div>
            <div class="metric-value" style="color:#e35b5b">{hi_lo['최고가']:,.0f}</div>
            <div style="font-size:0.78rem;color:var(--text-dim);margin-top:0.2rem">{hi_lo['최고가_날짜'].strftime('%Y-%m-%d')} 기록</div>
        </div>
        """, unsafe_allow_html=True)
    with lo_col:
        st.markdown(f"""
        <div class="metric-card" style="background:var(--card-bg);border:1px solid var(--card-border);border-left:4px solid #4a90d9">
            <div class="metric-label">🔻 기간 내 최저가</div>
            <div class="metric-value" style="color:#4a90d9">{hi_lo['최저가']:,.0f}</div>
            <div style="font-size:0.78rem;color:var(--text-dim);margin-top:0.2rem">{hi_lo['최저가_날짜'].strftime('%Y-%m-%d')} 기록</div>
        </div>
        """, unsafe_allow_html=True)

    # ── 이격도 요약 ──
    st.markdown("##### 이동평균 이격도")
    gap_cols = st.columns(4)
    for col, window in zip(gap_cols, (5, 20, 60, 120)):
        gap = ind.get(f"이격도{window}")
        with col:
            st.metric(f"{window}{unit}선 대비", f"{gap:+.1f}%" if gap is not None else "-")
            st.caption(_gap_interpretation(gap))

    # ── RSI · 볼린저밴드 위치 · 거래량 배율 (같은 4칸 폭 카드로 일관성 유지) ── [2026-08-11 확장]
    st.markdown(f"##### RSI (14{unit}) · 볼린저밴드 · 거래량 배율")
    rsi = ind.get("RSI14")
    bb_pos = ind.get("BB위치")
    vol_ratio = ind.get("거래량배율")
    rsi_col, bb_col, vol_col, _ = st.columns(4)
    with rsi_col:
        st.metric("RSI", f"{rsi:.1f}" if rsi is not None else "-")
        st.caption(_rsi_interpretation(rsi))
    with bb_col:
        st.metric("볼린저밴드 내 위치", f"{bb_pos:.0f}%" if bb_pos is not None else "-",
                   help="0%=하단선, 50%=중심(20구간 이동평균), 100%=상단선")
        st.caption(_bb_interpretation(bb_pos))
    with vol_col:
        st.metric("거래량 배율", f"{vol_ratio:.2f}배" if vol_ratio is not None else "-",
                   help="최근 1구간 거래량 ÷ 최근 20구간 평균 거래량")
        st.caption(_volume_ratio_interpretation(vol_ratio))

    # ── 분석 코멘트 (수치를 있는 그대로 서술, 매수·매도 결론 없음) ──
    st.markdown("##### 분석 코멘트")
    comments = _generate_ta_comment(hist, ind, hi_lo, unit)
    if comments:
        comment_html = "".join(f"<li>{c}</li>" for c in comments)
        st.markdown(
            f"""
            <div class="metric-card" style="background:var(--card-bg);border:1px solid var(--card-border)">
                <ul style="margin:0;padding-left:1.1rem;line-height:1.7;">{comment_html}</ul>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.caption("위 코멘트는 수치를 있는 그대로 서술한 것이며, 매수·매도 의견이 아닙니다.")
    else:
        st.caption("코멘트를 생성할 만큼 데이터가 충분하지 않습니다.")


# ============================================================
# 탭4: 거래이력
# ============================================================
def render_trades(trade_df):
    st.markdown('<div class="section-title">거래이력</div>', unsafe_allow_html=True)
    st.caption("🌐 해외(미국) 종목의 거래단가·거래금액은 구글시트에 입력하신 원래 통화(달러) 그대로 표시됩니다. 원화 환산 금액은 '보유 종목'·'통합 대시보드' 화면에서 확인하세요.")

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

    # 거래일자를 실제 날짜로 변환해 정렬 (문자열/숫자가 섞여 있어도 정렬이 깨지지 않도록
    # 원본 컬럼이 아니라 별도의 파싱된 컬럼 기준으로 정렬한다)
    df["_거래일자_정렬용"] = pd.to_datetime(df["거래일자"], errors="coerce")
    df = df.sort_values("_거래일자_정렬용", ascending=False).drop(columns=["_거래일자_정렬용"])
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
        width="stretch",
        hide_index=True,
        column_config=col_config,
        height=min(600, 50 + 45 * len(display_df)),
    )


# ============================================================
# 탭5: 현금흐름 (거래이력 기반 자동 계산 — 별도 시트 없음)
# ============================================================
def render_cashflow(trade_df, cashlog_df=None):
    st.markdown('<div class="section-title">자금흐름 추적</div>', unsafe_allow_html=True)
    st.caption("💡 거래이력 시트만으로 자동 계산됩니다. 매도 한 건이 발생하면 그 이후 같은 계좌에서 일어난 매수 내역을 시간순으로 보여줍니다.")
    st.caption("⚠ 참고용입니다. 매도금이 정확히 어느 매수에 쓰였는지는 계좌 잔액이 섞이기 때문에 100% 단정할 수 없고, 시간 순서로 정황만 보여줍니다.")

    if trade_df.empty:
        st.info("거래이력이 없습니다.")
        _render_cashlog_section(cashlog_df)
        return

    realized_df = calc_realized_pnl(trade_df)

    # ── 실현손익 요약 ──
    st.markdown('<div class="section-title">실현손익 (매도 건 기준)</div>', unsafe_allow_html=True)
    st.caption("📌 평균매입가법으로 계산하며, 이 화면과 다른 모든 화면이 동일한 계산 함수 하나를 공유합니다.")

    if realized_df.empty:
        st.info("매도 거래가 없어 실현손익이 없습니다.")
        _render_cashlog_section(cashlog_df)
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

    styled = display_realized.style
    if hasattr(styled, "map"):
        styled = styled.map(style_pnl_cell, subset=["실현손익"])
    else:
        styled = styled.applymap(style_pnl_cell, subset=["실현손익"])
    col_config = build_number_column_config(
        display_realized,
        money_cols=["매도단가", "평균매입단가", "매도금액", "매입금액", "실현손익"],
    )
    st.dataframe(
        styled, width="stretch", hide_index=True,
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

        # 매도일 이후 같은 계좌의 매수 내역 — 건수 제한 없이 전부 조회.
        # 예전에는 .head(5)로 5건까지만 표시해서, 6번째 이후 매수(예: 애플)가
        # 화면에서 통째로 사라지는 문제가 있었음 (2026-08 발견). 지금은 앞 5건만
        # 카드 안에 바로 보여주고, 나머지는 "더 보기" 접기(expander)로 전부 표시한다.
        후속매수_전체 = trade_sorted[
            (trade_sorted["거래일자_dt"] >= ev_date) &
            (trade_sorted["운용사"] == ev_account) &
            (trade_sorted["거래구분"] == "매수")
        ]
        표시건수 = 5
        후속매수_표시 = 후속매수_전체.head(표시건수)
        후속매수_나머지 = 후속매수_전체.iloc[표시건수:]

        def _buy_item_html(buy):
            buy_amt = int(buy["거래수량"]) * float(buy["거래단가"])
            return (
                f'<div class="sell-follow-item">↳ {_safe_date_str(buy["거래일자_dt"])} · '
                f'{buy["종목명"]} 매수 {int(buy["거래수량"])}주 · {fmt_money(buy_amt)}</div>'
            )

        if 후속매수_표시.empty:
            buy_html = '<div class="sell-follow-empty">↳ 이후 같은 계좌에서 추가 매수 없음 · 매도금은 예수금에 합산 보관 중</div>'
        else:
            buy_html = "".join(_buy_item_html(buy) for _, buy in 후속매수_표시.iterrows())

        _acct_order_ev = sorted(trade_sorted["운용사"].unique().tolist())
        _badge_bg, _badge_fg = get_account_color(ev_account, _acct_order_ev)
        pnl_html = f'<span class="sell-event-pnl" style="color:{color_pnl(pnl)}">실현손익 {fmt_money(pnl)}</span>'

        st.markdown(
            '<div class="acct-card sell-event-card">'
            '<div class="sell-event-header">'
            f'<span class="acct-badge" style="background:{_badge_bg};color:{_badge_fg}">{ev_account}</span>'
            f'<span class="sell-event-name">{ev["제목"]}</span>'
            f'<span class="sell-event-date">{_safe_date_str(ev_date)}</span>'
            '<span class="sell-event-spacer"></span>'
            f'<span class="sell-event-amount">{fmt_money(ev_amount)} 회수</span>'
            f'{pnl_html}'
            '</div>'
            f'{buy_html}'
            '</div>',
            unsafe_allow_html=True
        )

        # 5건을 넘는 매수 내역은 접기(expander)로 전부 표시 — 데이터 누락 없이 화면만 압축
        if not 후속매수_나머지.empty:
            with st.expander(f"↳ 이후 매수 {len(후속매수_나머지)}건 더 보기", expanded=False):
                more_html = "".join(_buy_item_html(buy) for _, buy in 후속매수_나머지.iterrows())
                st.markdown(more_html, unsafe_allow_html=True)

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

    _render_cashlog_section(cashlog_df)


def _render_cashlog_section(cashlog_df):
    """현금출납내역 (생활비 인출 등, 거래이력과 별개로 직접 기록하는 시트).
    거래이력 유무와 무관하게 항상 표시되어야 하므로 render_cashflow 상단에서 별도로 호출."""
    st.markdown('<div class="section-title">현금출납내역</div>', unsafe_allow_html=True)
    st.caption("💡 생활비 인출처럼 매매가 아닌 현금 입출금은 이 섹션에서 이력으로 관리합니다. "
               "비주식자산 시트의 예수금 평가금액은 '지금 잔액'만, 이 시트는 '왜 그렇게 바뀌었는지'의 흐름을 남깁니다.")

    if cashlog_df is None or cashlog_df.empty:
        st.info(
            "아직 '현금출납내역' 시트가 없거나 비어 있습니다. 구글시트에 아래 열로 새 탭을 만들어 기록해보세요:\n\n"
            "날짜 | 계좌 | 구분(입금/출금) | 금액 | 사유"
        )
        return

    show_cols = [c for c in ["날짜", "계좌", "구분", "금액", "사유"] if c in cashlog_df.columns]
    if not show_cols:
        st.warning("'현금출납내역' 시트의 열 이름이 예상과 달라 표시할 수 없습니다. "
                   "날짜 · 계좌 · 구분 · 금액 · 사유 열이 있는지 확인해주세요.")
        return

    log_df = cashlog_df[show_cols].copy()
    log_df["금액"] = pd.to_numeric(
        log_df["금액"].astype(str).str.replace(",", "").str.replace("원", ""), errors="coerce"
    ).fillna(0)

    # 이번 달 입금/출금 합계
    if "날짜" in log_df.columns:
        log_df["_dt"] = pd.to_datetime(log_df["날짜"], errors="coerce")
        this_month = log_df[log_df["_dt"].dt.strftime("%Y-%m") == datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m")]
    else:
        this_month = log_df

    deposit = this_month.loc[this_month["구분"] == "입금", "금액"].sum() if "구분" in this_month.columns else 0
    withdraw = this_month.loc[this_month["구분"] == "출금", "금액"].sum() if "구분" in this_month.columns else 0

    c1, c2 = st.columns(2)
    c1.metric("이번 달 입금 합계", fmt_money_full(deposit))
    c2.metric("이번 달 출금 합계", fmt_money_full(withdraw))

    display_log = log_df.drop(columns=["_dt"], errors="ignore").sort_values(
        "날짜", ascending=False
    ) if "날짜" in log_df.columns else log_df
    col_config = build_number_column_config(display_log, money_cols=["금액"])
    st.dataframe(display_log, width="stretch", hide_index=True, column_config=col_config)


# ============================================================
# 탭6: 데이터 관리
# ============================================================
def render_data_mgmt(nonstock_df):
    st.markdown('<div class="section-title">비주식자산 현황 (TDF · 현금성자산)</div>', unsafe_allow_html=True)
    st.caption("💡 대시보드의 현금성자산 금액은 이 시트 기준입니다. 잔액 변경 시 비주식자산 시트를 업데이트하세요.")

    def _safe_float(val):
        try:
            v = str(val).strip().replace(",", "")
            return float(v) if v and v not in ("-", "") else 0.0
        except (ValueError, TypeError):
            return 0.0

    tdf_rows  = nonstock_df[nonstock_df["자산군"] == "TDF"] if not nonstock_df.empty else pd.DataFrame()
    cash_rows = nonstock_df[nonstock_df["자산군"] == "현금성자산"] if not nonstock_df.empty else pd.DataFrame()
    tdf_total  = sum(_safe_float(r.get("평가금액", 0)) for _, r in tdf_rows.iterrows())
    cash_total = sum(_safe_float(r.get("평가금액", 0)) for _, r in cash_rows.iterrows())

    # ── 상단 요약 카드 (한눈에 보는 총액) ──
    summary_html = (
        '<div class="mgmt-summary-grid">'
        '<div class="mgmt-summary-item">'
        '<div class="mgmt-summary-icon" style="background:#1FBFA033;color:#1FBFA0">🏦</div>'
        '<div><div class="mgmt-summary-label">TDF·펀드 총 평가금액</div>'
        f'<div class="mgmt-summary-value">{fmt_money_full(tdf_total)}</div></div>'
        '</div>'
        '<div class="mgmt-summary-item">'
        '<div class="mgmt-summary-icon" style="background:#6B6F7A33;color:#6B6F7A">💰</div>'
        '<div><div class="mgmt-summary-label">현금성자산 총액</div>'
        f'<div class="mgmt-summary-value">{fmt_money_full(cash_total)}</div></div>'
        '</div>'
        '</div>'
    )
    st.markdown(summary_html, unsafe_allow_html=True)

    def _render_nonstock_table(rows, show_pnl=False, total_eval=0):
        """비주식자산 행을 표로 렌더링.
        [전면 개편] 이전에는 이 표만 직접 만든 HTML 테이블이라, 앱의 다른 표들(보유종목·
        거래이력·현금흐름 등)이 다 쓰는 st.dataframe과 다르게 동작했다 — 모바일에서 숫자가
        잘려 보이는 문제, 다운로드·검색·전체화면 버튼이 없는 문제가 전부 이 차이에서 비롯됐다.
        이제 다른 표들과 완전히 같은 방식(st.dataframe + column_config)으로 통일해서, 모바일
        가로 스크롤·숨은 컬럼 보기·다운로드·검색·전체화면이 다른 표들과 동일하게 제공된다."""
        acct_rows = []
        total_cost = 0.0
        for _, row in rows.iterrows():
            acct = str(row.get("계좌", "")).strip()
            name = str(row.get("상품명", "")).strip()
            cost = _safe_float(row.get("원금", 0))
            eva = _safe_float(row.get("평가금액", 0))
            pnl = eva - cost
            total_cost += cost
            entry = {
                "계좌": acct, "상품명": name,
                "투자원금": int(cost), "평가금액": int(eva),
            }
            if show_pnl:
                entry["평가손익"] = int(pnl)
                entry["수익률"] = round(pnl / cost * 100, 2) if cost else 0.0
            entry["반영일자"] = str(row.get("반영일자", "")).strip()
            entry["비고"] = str(row.get("비고", "")).strip()
            acct_rows.append(entry)

        # 합계 행 (비고·반영일자는 비워둠)
        total_row = {"계좌": "합계", "상품명": "", "투자원금": int(total_cost), "평가금액": int(total_eval)}
        if show_pnl:
            total_pnl = total_eval - total_cost
            total_row["평가손익"] = int(total_pnl)
            total_row["수익률"] = round(total_pnl / total_cost * 100, 2) if total_cost else 0.0
        total_row["반영일자"] = ""
        total_row["비고"] = ""
        acct_rows.append(total_row)

        table_df = pd.DataFrame(acct_rows)
        money_cols = ["투자원금", "평가금액"] + (["평가손익"] if show_pnl else [])
        pct_cols = ["수익률"] if show_pnl else []
        col_config = build_number_column_config(table_df, money_cols=money_cols, pct_cols=pct_cols)

        styled = table_df.style.map(
            style_pnl_cell, subset=["평가손익", "수익률"] if show_pnl else []
        ) if show_pnl else table_df

        st.dataframe(
            styled,
            width="stretch",
            hide_index=True,
            column_config=col_config,
            height=min(400, 50 + 45 * len(table_df)),
        )

    if not nonstock_df.empty:
        if not tdf_rows.empty:
            st.markdown('<div class="mgmt-section-head">🏦 TDF / 펀드</div>', unsafe_allow_html=True)
            _render_nonstock_table(tdf_rows, show_pnl=True, total_eval=tdf_total)

            # 환매 후 원금 중 일부만 재투자되어 평가손익이 큰 폭의 마이너스(-50% 이하)로 보이는 경우,
            # 실제 손실로 오해하지 않도록 안내 문구 표시
            partial_reinvest_names = []
            for _, r in tdf_rows.iterrows():
                cost = _safe_float(r.get("원금", 0))
                eva = _safe_float(r.get("평가금액", 0))
                if cost > 0 and (eva - cost) / cost <= -0.5:
                    partial_reinvest_names.append(str(r.get("상품명", "")).strip())
            if partial_reinvest_names:
                st.caption(
                    "💡 " + ", ".join(sorted(set(partial_reinvest_names))) +
                    " 상품의 평가손익이 큰 폭의 마이너스로 보이는 건 실제 손실이 아닐 수 있습니다. "
                    "환매 후 원금 중 일부만 재투자되고 나머지는 예수금 등으로 이동한 경우 이렇게 표시됩니다. "
                    "정확한 내역은 비고란을 확인해주세요."
                )

        if not cash_rows.empty:
            st.markdown('<div class="mgmt-section-head">💰 현금성자산 (예수금 · 대기자금)</div>', unsafe_allow_html=True)
            _render_nonstock_table(cash_rows, show_pnl=False, total_eval=cash_total)
    else:
        st.info("비주식자산 데이터 없음")

    st.markdown('<div class="mgmt-section-head">🎨 시트 서식 정리</div>', unsafe_allow_html=True)
    st.caption(
        "금액 칸은 천 단위 콤마(,)와 오른쪽 정렬로, 텍스트·날짜 칸은 가운데 정렬로 구글시트 서식을 통일합니다. "
        "값(숫자 자체)은 바뀌지 않고, 보이는 형식만 정리됩니다."
    )
    if st.button("🎨 구글시트 서식 통일 적용", key="apply_format_btn", width="content"):
        spreadsheet_id_for_format = st.session_state.get("spreadsheet_id", "")
        with st.spinner("시트 서식을 정리하는 중..."):
            ok, msg = apply_sheet_formatting(spreadsheet_id_for_format)
        (st.success if ok else st.error)(msg)

    st.markdown('<div class="mgmt-section-head">🧹 캐시 초기화</div>', unsafe_allow_html=True)
    warn_html = (
        '<div class="mgmt-warn-card">'
        '<div class="mgmt-warn-text">'
        '<b>실시간 시세와 구글시트 데이터를 모두 지우고 새로 불러옵니다.</b> '
        '숫자가 이상하게 보이거나 방금 구글시트에 입력한 내용이 반영되지 않을 때 눌러주세요.'
        '</div></div>'
    )
    st.markdown(warn_html, unsafe_allow_html=True)
    if st.button("🔄 전체 캐시 초기화 (데이터 새로고침)", key="clear_cache_btn", width="content"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("캐시가 초기화되었습니다. 페이지를 새로고침하세요.")
        st.rerun()


# ============================================================
# 실행 (로그인 게이트)
# ============================================================
if __name__ == "__main__" or True:
    # 세션 연결이 끊겼다 재연결된 경우, 주소창의 서명된 토큰으로 자동 재로그인 시도
    # (탭 클릭 등으로 웹소켓이 재연결되면서 session_state가 초기화되는 경우에 대한 방어 코드)
    # v2.1: 1순위로 _oauth_credential_store()(서버 프로세스 전역 캐시)에서 복구를 시도하고,
    # 그마저 비어있으면(서버 재시작 등) 2순위로 화이트리스트 시트에 저장해둔 refresh_token으로
    # 브라우저 상호작용 없이 조용히 재로그인을 시도한다. 이 refresh_token 자체가 없거나
    # 만료/취소된 사용자만 결국 로그인 화면에서 버튼을 다시 눌러야 한다.
    if not st.session_state.get("logged_in"):
        token = st.query_params.get("t")
        if token:
            restored_email = verify_session_token(token)
            if restored_email:
                try:
                    status_row = get_whitelist_status(restored_email)
                except Exception as e:
                    logging.warning("화이트리스트 조회 실패(세션 복구): %s", e)
                    status_row = None  # 실패 시 자동 로그인만 건너뛰고, 아래에서 일반 로그인 화면으로 진행
                if status_row is not None and str(status_row.get("상태", "")).strip() == "활성":
                    cached_credentials = _restore_credentials(restored_email)
                    if cached_credentials is None:
                        # 메모리 캐시 미스 → 영구 저장된 refresh_token으로 조용히 재구성 시도
                        stored_refresh_token = load_user_refresh_token(restored_email)
                        if stored_refresh_token:
                            try:
                                cached_credentials = build_credentials_from_refresh_token(stored_refresh_token)
                                _save_credentials(restored_email, cached_credentials)
                            except Exception as e:
                                logging.warning("refresh_token으로 재로그인 실패: %s", e)
                                cached_credentials = None
                    if cached_credentials is not None:
                        st.session_state["logged_in"] = True
                        st.session_state["user_name"] = str(status_row.get("이름", "")).strip() or restored_email.split("@")[0]
                        st.session_state["user_email"] = restored_email
                        st.session_state["spreadsheet_id"] = str(status_row.get("spreadsheet_id", "")).strip()
                        st.session_state["oauth_credentials"] = cached_credentials
                        st.session_state["is_admin"] = (
                            restored_email.strip().lower() == str(st.secrets.get("admin", {}).get("email", "")).strip().lower()
                        )

    if not st.session_state.get("logged_in"):
        show_login()
        st.stop()
    else:
        main(st.session_state["spreadsheet_id"])
