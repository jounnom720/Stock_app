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
import numpy as np
import gspread
import yfinance as yf
import plotly.graph_objects as go
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime, date
from zoneinfo import ZoneInfo
import logging
import hmac
import hashlib
import base64
import time
import secrets as pysecrets

# ============================================================
# 기본 설정
# ============================================================
logging.basicConfig(level=logging.WARNING)
KST = ZoneInfo("Asia/Seoul")
APP_VERSION = "v2.0.0"

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

def get_asset_ticker(code: str) -> str:
    """ASSET_MASTER에 등록된 종목은 등록된 티커를, 미등록 종목코드는 KRX 6자리 코드 규칙에 따라
    자동으로 야후파이낸스 티커(코드.KS)를 생성해 반환. 신규 사용자가 보유한 임의의 종목코드도
    별도 등록 없이 실시간 시세 조회가 되도록 하기 위함."""
    code = str(code).strip()
    if not code:
        return ""
    meta = ASSET_MASTER.get(code)
    if meta:
        return meta["ticker"]
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

# ============================================================
# 시장 지표 마스터 (대시보드 상단 카드용, 성격별 4그룹)
# ============================================================
MARKET_INDICES = [
    {"group": "국내 증시",      "name": "코스피",     "ticker": "^KS11"},
    {"group": "국내 증시",      "name": "코스닥",     "ticker": "^KQ11"},
    {"group": "환율·원자재",    "name": "USD/KRW",    "ticker": "KRW=X"},
    {"group": "환율·원자재",    "name": "WTI 유가",   "ticker": "CL=F"},
    {"group": "환율·원자재",    "name": "국제 금",    "ticker": "GC=F"},
    {"group": "미국 증시",      "name": "S&P500",     "ticker": "^GSPC"},
    {"group": "미국 증시",      "name": "나스닥",     "ticker": "^IXIC"},
    {"group": "미국 증시",      "name": "다우존스",   "ticker": "^DJI"},
    {"group": "위험심리·금리",  "name": "VIX",        "ticker": "^VIX"},
    {"group": "위험심리·금리",  "name": "달러인덱스", "ticker": "DX-Y.NYB"},
    {"group": "위험심리·금리",  "name": "美 10년물",  "ticker": "^TNX"},
    {"group": "위험심리·금리",  "name": "비트코인",   "ticker": "BTC-USD"},
]

# ============================================================
# Google Sheets 연결 (서비스 계정 — 화이트리스트 '사용자계정' 시트 전용)
# ============================================================
SHEET_NAMES = {
    "거래이력":        "거래이력",
    "비주식자산":      "비주식자산",
    "현금성자산":      "현금성자산",
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
REQUIRED_SHEET_HEADERS = {
    "거래이력":        ["거래일자", "운용사", "종목코드", "종목명", "거래구분", "거래수량", "거래단가", "비고"],
    "비주식자산":      ["계좌", "자산군", "상품명", "원금", "평가금액", "반영일자", "비고"],
    "현금성자산":      ["기준일", "계좌", "유형", "원금", "평가금액", "메모"],
    "월별자산스냅샷":  ["년월", "통합원금", "통합평가"],
    "계좌간이체":      ["거래일자", "출금계좌", "입금계좌", "금액", "실현손익", "비고"],
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
    "현금성자산": {
        "기준일": "date", "계좌": "text", "유형": "text", "원금": "money",
        "평가금액": "money", "메모": "text_left",
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
            if kind in ("money", "number"):
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

def _call_with_retry(func, *args, max_retries: int = 3, base_delay: float = 2.0, **kwargs):
    """구글 API 호출 중 429(분당 요청 한도 초과) 오류가 나면 잠깐 기다렸다가 자동으로 재시도한다.
    '전체 캐시 초기화'나 '시세 새로고침'을 짧은 시간 안에 여러 번 누르는 경우를 대비."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if _is_quota_error(e) and attempt < max_retries - 1:
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
# 컬럼: 이메일 / 이름 / spreadsheet_id / 상태 / 등록일
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
    캐시 때문에 방금 한 변경이 화면에 안 보이는 일이 없도록 한다."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return pd.DataFrame()
        ws = _call_with_retry(spreadsheet.worksheet, "사용자계정")
        return pd.DataFrame(_call_with_retry(ws.get_all_records))
    except Exception as e:
        logging.warning("계정 목록 로드 실패: %s", e)
        return pd.DataFrame()

def get_whitelist_status(email: str):
    """화이트리스트 시트에서 이메일로 계정 정보를 조회. 없으면 None, 있으면 해당 행(Series) 반환."""
    df = load_accounts_df()
    if df.empty or "이메일" not in df.columns or not email:
        return None
    row = df[df["이메일"].astype(str).str.strip().str.lower() == email.strip().lower()]
    if row.empty:
        return None
    return row.iloc[0]

def register_pending_request(email: str, name: str) -> bool:
    """화이트리스트에 없는 이메일이 처음 로그인 시도하면 '승인대기' 상태로 자동 등록.
    관리자가 '가입 승인' 탭에서 승인해야 실제로 앱을 사용할 수 있다."""
    try:
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

        status_row = get_whitelist_status(email)

        if status_row is None:
            # 처음 시도하는 이메일 → 승인대기로 자동 등록
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
            prompt="consent",
            code_challenge=code_challenge,
            code_challenge_method="S256",
            state=code_verifier,
        )
        st.link_button("🔐 Google 계정으로 로그인", auth_url, type="primary")
        st.caption(
            "처음 로그인하는 경우 자동으로 '승인 대기' 등록되며, 관리자 승인이 완료된 뒤 "
            "다시 로그인하면 이용할 수 있습니다."
        )

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
    ticker = get_asset_ticker(code)
    if not ticker:
        return None
    return prices.get(ticker)

def _fetch_current_and_prev_close(ticker_list: list) -> dict[str, dict]:
    """여러 티커의 (현재가, 전일종가) 기준 등락률을 일괄 조회하고, 실패한 티커만 개별 재시도.
    get_market_index_data()와 get_day_change()가 공통으로 사용하는 핵심 로직."""
    result = {}
    try:
        ticker_str = " ".join(ticker_list)
        data = yf.download(ticker_str, period="5d", progress=False, auto_adjust=True, threads=True)
        if "Close" in data.columns:
            close = data["Close"].dropna(how="all")
            if len(close) >= 2:
                latest_row = close.iloc[-1]
                prev_row = close.iloc[-2]
                for t in ticker_list:
                    try:
                        cur = float(latest_row[t]) if hasattr(latest_row, "__getitem__") else float(latest_row)
                        prev = float(prev_row[t]) if hasattr(prev_row, "__getitem__") else float(prev_row)
                        if pd.notna(cur) and pd.notna(prev) and prev != 0:
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
def get_market_index_data() -> dict[str, dict]:
    """시장 지표(코스피·환율·VIX 등)의 현재가와 전일 대비 등락률을 조회."""
    tickers = [m["ticker"] for m in MARKET_INDICES]
    return _fetch_current_and_prev_close(tickers)


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
    """거래이력으로 현재 보유 종목과 평균단가 계산. (_replay_trade_ledger 공유 로직 사용)"""
    _, final_state = _replay_trade_ledger(trade_df)

    rows = []
    for (account, code), h in final_state.items():
        avg = h["평균단가"]
        qty = h["보유수량"]
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
    """
    sell_events, _ = _replay_trade_ledger(trade_df)

    realized_rows = []
    for ev in sell_events:
        매도금액 = ev["effective_qty"] * ev["매도단가"]
        매입금액 = ev["effective_qty"] * ev["평균매입단가"]
        실현손익 = 매도금액 - 매입금액
        realized_rows.append({
            "거래일자": ev["거래일자"], "계좌": ev["계좌"], "종목코드": ev["종목코드"], "종목명": ev["종목명"],
            "매도수량": ev["매도수량"], "매도단가": ev["매도단가"], "평균매입단가": round(ev["평균매입단가"]),
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

def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")

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
.hero-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 4px;
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

/* ── 시장 지표 카드 (9개 전체를 한 그리드에 가로로 펼침) ── */
.mkt-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 0.6rem;
}
.mkt-card {
    position: relative;
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 10px;
    padding: 0.9rem 1.05rem 0.9rem 1.15rem;
}
.mkt-card::before {
    content: "";
    position: absolute;
    left: 0; top: 0.7rem; bottom: 0.7rem;
    width: 3px;
    border-radius: 2px;
    background: var(--mkt-bar, var(--card-border));
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
.holding-acct-irp { background: rgba(59,130,246,0.16); color: #7fb2f5; }
.holding-acct-mira { background: rgba(29,158,117,0.16); color: #4ecb9a; }
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
.mgmt-table-card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 14px;
    overflow: hidden;
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
</style>
""", unsafe_allow_html=True)

# ============================================================
# 데이터 로드
# ============================================================
@st.cache_data(ttl=30)
def load_all_data(spreadsheet_id: str):
    trade_df     = load_sheet("거래이력", spreadsheet_id)
    nonstock_df  = load_sheet("비주식자산", spreadsheet_id)
    cash_df      = load_sheet("현금성자산", spreadsheet_id)
    monthly_df   = load_sheet("월별자산스냅샷", spreadsheet_id)
    transfer_df  = load_sheet("계좌간이체", spreadsheet_id)  # TDF 환매 등 계좌간 자금 이동 이력 (실현손익 포함)
    cashlog_df   = load_sheet_optional("현금출납내역", spreadsheet_id)  # 생활비 인출 등 현금 입출금 이력 (아직 없는 사용자도 있어 선택적 로더 사용)
    return trade_df, nonstock_df, cash_df, monthly_df, transfer_df, cashlog_df

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
            ws = spreadsheet.add_worksheet(title="월별자산스냅샷", rows=200, cols=5)
            ws.update("A1", [["년월", "통합원금", "통합평가"]])

        records = ws.get_all_values()
        if not records:
            ws.update("A1", [["년월", "통합원금", "통합평가"]])
            records = [["년월", "통합원금", "통합평가"]]

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
            with st.expander("🔧 관리자 메뉴", expanded=False):
                st.caption("🔒 관리자 전용 · 지인 계정을 관리합니다")

                # 이 메뉴가 열려있는 동안 모든 탭이 계정 목록을 공유해서 씀
                # (탭마다 따로 불러오면 구글시트 API 호출이 3배로 늘어나 429 오류 위험이 커짐)
                df_acc = load_accounts_df()

                tab_a, tab_pending, tab_b, tab_c = st.tabs(["계정 관리", "🆕 가입 승인", "사용자 현황", "시스템"])

                # ---------- 계정 관리 ----------
                with tab_a:
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
                with tab_pending:
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
                with tab_b:
                    if not df_acc.empty:
                        display_cols = [c for c in ["이메일", "이름", "상태", "등록일"] if c in df_acc.columns]
                        st.dataframe(df_acc[display_cols], width="stretch", hide_index=True)
                        st.caption(f"총 {len(df_acc)}개 계정 · 활성 {sum(df_acc['상태'] == '활성')}개 · "
                                   f"승인대기 {sum(df_acc['상태'] == '승인대기')}개")
                    else:
                        st.info("등록된 계정이 없습니다.")

                # ---------- 시스템 ----------
                with tab_c:
                    st.caption("캐시된 데이터를 지우고 구글시트/시세를 다시 불러옵니다.")
                    if st.button("🔄 전체 캐시 새로고침", key="admin_cache_clear", width="stretch"):
                        st.cache_data.clear()
                        st.cache_resource.clear()
                        st.success("캐시가 초기화되었습니다. 페이지를 새로고침 해주세요.")
                        st.rerun()


# ============================================================
# 개발자 정보 (모달 팝업)
# ============================================================
@st.dialog("앱 정보")
def show_developer_info():
    st.markdown("**개발: H.W Jone**")
    st.markdown(f"**버전: {APP_VERSION}**")
    st.markdown("**문의: hwcho@me.com**")
    st.caption("버그 제보나 기능 제안은 위 이메일로 보내주세요.")


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

    # 관리자 메뉴 (본인 계정으로 로그인했을 때만 노출)
    if st.session_state.get("is_admin"):
        render_admin_panel()

    # 데이터 로드
    with st.spinner("데이터 불러오는 중..."):
        trade_df, nonstock_df, cash_df, monthly_df, transfer_df, cashlog_df = load_all_data(spreadsheet_id)

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

    prices = get_prices(tuple(tickers)) if tickers else {}
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
    MAIN_TABS = ["📈 통합 대시보드", "💼 보유 종목", "📋 거래이력", "💵 현금흐름", "⚙️ 데이터 관리"]

    selected_main_tab = st.radio(
        "메인 메뉴", MAIN_TABS, horizontal=True,
        key="active_main_tab", label_visibility="collapsed",
    )
    st.markdown("<div style='margin-top:-0.5rem'></div>", unsafe_allow_html=True)

    if selected_main_tab == MAIN_TABS[0]:
        render_dashboard(holdings_df, nonstock_df, cash_df, monthly_df, prices, trade_df=trade_df, transfer_df=transfer_df)
    elif selected_main_tab == MAIN_TABS[1]:
        render_holdings(holdings_df, prices, nonstock_df)
    elif selected_main_tab == MAIN_TABS[2]:
        render_trades(trade_df)
    elif selected_main_tab == MAIN_TABS[3]:
        render_cashflow(trade_df, cashlog_df)
    elif selected_main_tab == MAIN_TABS[4]:
        render_data_mgmt(nonstock_df, cash_df)


# ============================================================
# 탭1: 통합 대시보드
# ============================================================
def render_market_indices():
    """대시보드 상단 시장 지표 카드 — 12개 지표를 한 그리드에 가로로 펼쳐 배치."""
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
            elif item["ticker"] == "BTC-USD":
                value_str = f"${cur:,.0f}"
            else:
                value_str = f"{cur:,.2f}" if abs(cur) < 1000 else f"{cur:,.0f}"
            change_str = f"{chg:+.2f}%"
            color = color_pnl(chg)
        cards.append(f"""
        <div class="mkt-card" style="--mkt-bar:{color}">
            <div class="mkt-group-tag">{item['group']}</div>
            <div class="mkt-name">{item['name']}</div>
            <div class="mkt-value">{value_str}</div>
            <div class="mkt-change" style="color:{color}">{change_str}</div>
        </div>""")
    st.markdown(f'<div class="mkt-row">{"".join(cards)}</div>', unsafe_allow_html=True)


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
            f'<div style="display:flex;gap:0.6rem;align-items:baseline;'
            f'margin-bottom:0.5rem">'
            f'<span style="font-size:0.85rem;color:var(--text-dim)">보유종목 평가금액 합계</span>'
            f'<span style="font-size:1.05rem;font-weight:700">{total_value:,.0f}원</span>'
            f'</div>'
            f'<div style="display:flex;gap:0.6rem;align-items:baseline;'
            f'margin-bottom:0.8rem">'
            f'<span style="font-size:0.85rem;color:var(--text-dim)">평가금액 가중 당일 등락률</span>'
            f'<span style="font-size:1.05rem;font-weight:700;color:{weighted_color}">{weighted_pct:+.2f}%</span>'
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
        st.plotly_chart(fig, width="stretch")
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


def render_dashboard(holdings_df, nonstock_df, cash_df, monthly_df, prices, trade_df=None, transfer_df=None):

    render_market_indices()
    render_holdings_treemap(holdings_df)

    s = calc_asset_summary(holdings_df, nonstock_df, trade_df=trade_df, transfer_df=transfer_df)
    stock_eval, stock_cost, stock_pnl, stock_pct = s["stock_eval"], s["stock_cost"], s["stock_pnl"], s["stock_pct"]
    tdf_eval, tdf_cost, tdf_pnl, tdf_pct = s["tdf_eval"], s["tdf_cost"], s["tdf_pnl"], s["tdf_pct"]
    cash_eval, cash_pct_of_total = s["cash_eval"], s["cash_pct_of_total"]
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
        f'<div class="hero-label">총 투자원금 {fmt_money_full(total_cost)} → 통합 평가금액</div>'
        f'<div class="hero-row">'
        f'<div class="hero-value">{fmt_money_full(total_eval)}</div>'
        f'<div class="hero-pnl" style="color:{color_pnl(total_pnl)}">{fmt_money_full(total_pnl)} ({fmt_pct(total_pct)})</div>'
        f'</div>'
        f'<div class="hero-bar">'
        f'<div style="width:{stock_pct_w:.1f}%;background:#7C6CF0"></div>'
        f'<div style="width:{tdf_pct_w:.1f}%;background:#1FBFA0"></div>'
        f'<div style="width:{cash_pct_w:.1f}%;background:#6B6F7A"></div>'
        f'</div>'
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
    # 함께 있어도(예: 미래에셋 계좌에 ETF+주식 혼재) 정확히 분리되도록 get_asset_type()으로 분류
    if not holdings_df.empty:
        _type_series = holdings_df.apply(lambda r: get_asset_type(r["종목코드"], r["종목명"]), axis=1)
        etf_eval = int(holdings_df.loc[_type_series == "ETF", "평가금액"].sum())
        stock_only_eval = int(holdings_df.loc[_type_series == "주식", "평가금액"].sum())
    else:
        etf_eval = 0
        stock_only_eval = 0

    _colors = ["#7b1fa2", "#5c6bc0", "#0288d1", "#78909c"]
    _labels = ["ETF", "국내주식", "TDF/펀드", "현금성자산"]
    _values = [max(0, v) for v in [etf_eval, stock_only_eval, tdf_eval, cash_eval]]
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
        st.plotly_chart(fig_type, width="stretch")

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
            st.plotly_chart(fig_trend, width="stretch")

            # ── 상세 표: 월별 정확한 금액 · 손익 · 수익률 ──
            table_df = mdf[["년월_표시", "통합원금", "통합평가"]].copy()
            table_df["손익"] = table_df["통합평가"] - table_df["통합원금"]
            table_df["수익률"] = table_df.apply(
                lambda r: round(r["손익"] / r["통합원금"] * 100, 2) if r["통합원금"] else 0.0, axis=1
            )
            table_df = table_df.rename(columns={"년월_표시": "년월"}).sort_values("년월", ascending=False)

            def _style_trend_pnl(v):
                try:
                    f = float(v)
                except Exception:
                    return ""
                color = "#e0635e" if f > 0 else "#5b9bd8" if f < 0 else "inherit"
                return f"color: {color}; font-weight: 600"

            styled_trend = table_df.style.map(_style_trend_pnl, subset=["손익", "수익률"])
            col_config = build_number_column_config(
                table_df, money_cols=["통합원금", "통합평가", "손익"], pct_cols=["수익률"]
            )
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
def render_holdings(holdings_df, prices, nonstock_df=None):
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
    st.caption(f"총 {전체수}종목 · 실시간 시세 반영 {시세반영수}종목")

    # 계좌별 필터
    계좌목록 = ["전체"] + sorted(holdings_df["계좌"].unique().tolist())
    선택계좌 = st.selectbox("계좌 필터", 계좌목록, key="holding_account_filter")
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

    def _style_holding_pnl(v):
        try:
            f = float(v)
        except Exception:
            return ""
        color = "#e0635e" if f > 0 else "#5b9bd8" if f < 0 else "inherit"
        return f"color: {color}; font-weight: 600"

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
            st.plotly_chart(fig_donut, width="stretch")

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
            st.plotly_chart(fig, width="stretch")


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
# 탭4: 현금흐름 (거래이력 기반 자동 계산 — 별도 시트 없음)
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

        # 매도일 이후 같은 계좌의 매수 내역
        후속매수 = trade_sorted[
            (trade_sorted["거래일자_dt"] >= ev_date) &
            (trade_sorted["운용사"] == ev_account) &
            (trade_sorted["거래구분"] == "매수")
        ].head(5)

        if 후속매수.empty:
            buy_html = '<div class="sell-follow-empty">↳ 이후 같은 계좌에서 추가 매수 없음 · 매도금은 예수금에 합산 보관 중</div>'
        else:
            items = []
            for _, buy in 후속매수.iterrows():
                buy_amt = int(buy["거래수량"]) * float(buy["거래단가"])
                items.append(
                    f'<div class="sell-follow-item">↳ {_safe_date_str(buy["거래일자_dt"])} · '
                    f'{buy["종목명"]} 매수 {int(buy["거래수량"])}주 · {fmt_money(buy_amt)}</div>'
                )
            buy_html = "".join(items)

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
# 탭4: 데이터 관리
# ============================================================
def render_data_mgmt(nonstock_df, cash_df):
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
        """비주식자산 행을 카드로 감싼 HTML 테이블로 렌더링. show_pnl=True면 평가손익 컬럼 추가, 하단에 합계행 표시."""
        p = "padding:0.65rem 1.1rem;"
        p_r = "padding:0.65rem 1.1rem;text-align:right;"
        th_style = "padding:0.6rem 1.1rem;text-align:left;font-weight:600;color:var(--text-dim);font-size:0.8rem;border-bottom:1px solid var(--card-border);background:var(--overlay-02);"
        th_r = "padding:0.6rem 1.1rem;text-align:right;font-weight:600;color:var(--text-dim);font-size:0.8rem;border-bottom:1px solid var(--card-border);background:var(--overlay-02);"
        row_sep = "border-bottom:1px solid var(--overlay-05);"

        pnl_th = f"<th style='{th_r}'>평가손익</th>" if show_pnl else ""
        _acct_order_ns = sorted(rows["계좌"].astype(str).str.strip().unique().tolist()) if not rows.empty else []
        html = (
            '<div class="mgmt-table-card">'
            "<table style='width:100%;border-collapse:collapse;font-size:0.92rem;'>"
            "<thead><tr>"
            f"<th style='{th_style}'>계좌</th>"
            f"<th style='{th_style}'>상품명</th>"
            f"<th style='{th_r}'>투자원금</th>"
            f"<th style='{th_r}'>평가금액</th>"
            f"{pnl_th}"
            f"<th style='{th_r}'>반영일자</th>"
            f"<th style='{th_style}'>비고</th>"
            "</tr></thead><tbody>"
        )

        for _, row in rows.iterrows():
            acct = str(row.get("계좌", "")).strip()
            name = str(row.get("상품명", "")).strip()
            cost = _safe_float(row.get("원금", 0))
            eva  = _safe_float(row.get("평가금액", 0))
            pnl  = eva - cost
            date_ = str(row.get("반영일자", "")).strip()
            note = str(row.get("비고", "")).strip()

            badge_bg, badge_color = get_account_color(acct, _acct_order_ns)
            badge_html = (
                f'<span style="background:{badge_bg};color:{badge_color};'
                f'font-size:0.75rem;padding:2px 8px;border-radius:4px;'
                f'margin-right:6px;white-space:nowrap;">{acct}</span>'
            )

            pnl_color = color_pnl(pnl)
            pnl_td = (
                f"<td style='{p_r}color:{pnl_color};font-weight:600;'>"
                f"{fmt_money_full(pnl)} ({fmt_pct(pnl / cost * 100 if cost else 0)})"
                f"</td>"
            ) if show_pnl else ""

            cost_str = fmt_money_full(cost) if cost else "-"
            eva_str  = fmt_money_full(eva)  if eva  else "-"

            html += (
                f"<tr style='{row_sep}'>"
                f"<td style='{p}'>{badge_html}</td>"
                f"<td style='{p};font-weight:600;'>{name}</td>"
                f"<td style='{p_r}'>{cost_str}</td>"
                f"<td style='{p_r};font-weight:600;'>{eva_str}</td>"
                f"{pnl_td}"
                f"<td style='{p_r};color:var(--text-dim);font-size:0.88rem;'>{date_}</td>"
                f"<td style='{p};color:var(--text-dim);font-size:0.88rem;'>{note}</td>"
                "</tr>"
            )

        # 합계행 (계좌+상품명+투자원금=3열 라벨, 평가금액 합계 1열, 나머지는 빈칸)
        trail_colspan = 3 if show_pnl else 2
        html += (
            "<tr style='background:var(--overlay-03);'>"
            f"<td colspan='3' style='{p};font-weight:700;color:var(--text-dim);'>합계</td>"
            f"<td style='{p_r};font-weight:700;'>{fmt_money_full(total_eval)}</td>"
            f"<td colspan='{trail_colspan}' style='{p};'></td>"
            "</tr>"
        )
        html += "</tbody></table></div>"
        st.markdown(html, unsafe_allow_html=True)

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
        '<b>실시간 시세와 구글시트 데이터를 모두 지우고 새로 불러옵니다.</b><br>'
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
    # v2.0: 토큰에는 email이 담겨 있고, 실제 API 접근에 필요한 OAuth Credentials는
    # _oauth_credential_store()(서버 프로세스 전역 캐시)에서 복구를 시도한다.
    # 서버가 재시작되어 캐시가 비어있다면 복구에 실패하며, 이 경우 로그인 화면에서
    # 'Google 계정으로 로그인' 버튼을 한 번 더 눌러야 한다.
    if not st.session_state.get("logged_in"):
        token = st.query_params.get("t")
        if token:
            restored_email = verify_session_token(token)
            if restored_email:
                cached_credentials = _restore_credentials(restored_email)
                status_row = get_whitelist_status(restored_email)
                if (
                    cached_credentials is not None
                    and status_row is not None
                    and str(status_row.get("상태", "")).strip() == "활성"
                ):
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
