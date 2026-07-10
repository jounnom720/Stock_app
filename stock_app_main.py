"""
통합자산관리 시스템 v1.0
- 신한은행 IRP (TDF + ETF)
- 미래에셋증권 (국내주식 + ETF)
- Google Sheets 실시간 연동
- yfinance 실시간 시세
"""""
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
import bcrypt
import hmac
import hashlib
import base64
import time

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
    "현금성자산":      "현금성자산",
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
def get_spreadsheet(spreadsheet_id: str):
    """사용자별 자산 데이터 시트를 연다.
    spreadsheet_id를 인자로 받아 캐시 키에 포함시킴으로써,
    사용자마다 다른 시트가 캐시에서 섞이지 않도록 함."""
    client = get_gspread_client()
    if client is None:
        return None
    try:
        return client.open_by_key(spreadsheet_id)
    except Exception as e:
        logging.warning("스프레드시트 열기 실패: %s", e)
        st.error(f"⚠️ 구글시트 열기 실패 (spreadsheet_id: {spreadsheet_id[:8]}...): {type(e).__name__} - {e}")
        return None

@st.cache_data(ttl=30)
def load_sheet(sheet_name: str, spreadsheet_id: str) -> pd.DataFrame:
    try:
        spreadsheet = get_spreadsheet(spreadsheet_id)
        if spreadsheet is None:
            st.error(f"⚠️ '{sheet_name}' 시트 로드 실패: 스프레드시트 자체를 열지 못했습니다 (spreadsheet_id 또는 공유 권한을 확인하세요).")
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
        st.error(f"⚠️ '{sheet_name}' 시트 로드 중 오류: {type(e).__name__} - {e}")
        return pd.DataFrame()

# ============================================================
# 계정 인증 (로그인)
# ============================================================
@st.cache_resource(ttl=60)
def get_accounts_spreadsheet():
    """모든 사용자 계정 정보가 담긴 '관리자용 계정 시트'를 연다.
    이 시트의 ID는 secrets의 [accounts] spreadsheet_id 값으로 고정되어 있으며,
    사용자 개인 자산 시트와는 별개의 시트임."""
    client = get_gspread_client()
    if client is None:
        return None
    try:
        sheet_id = st.secrets["accounts"]["spreadsheet_id"]
        return client.open_by_key(sheet_id)
    except Exception as e:
        logging.warning("계정 시트 열기 실패: %s", e)
        return None

def load_accounts_df() -> pd.DataFrame:
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return pd.DataFrame()
        ws = spreadsheet.worksheet("사용자계정")
        return pd.DataFrame(ws.get_all_records())
    except Exception as e:
        logging.warning("계정 목록 로드 실패: %s", e)
        return pd.DataFrame()

def authenticate(user_id: str, password: str):
    """아이디/비밀번호를 '사용자계정' 시트와 대조.
    성공 시 {'이름':..., 'spreadsheet_id':...} 딕셔너리 반환, 실패 시 None."""
    df = load_accounts_df()
    if df.empty:
        return None
    row = df[(df["아이디"] == user_id) & (df["상태"] == "활성")]
    if row.empty:
        return None
    row = row.iloc[0]
    stored_hash = str(row["비밀번호_해시"]).encode("utf-8")
    try:
        if bcrypt.checkpw(password.encode("utf-8"), stored_hash):
            return {"이름": row["이름"], "spreadsheet_id": row["spreadsheet_id"]}
    except Exception as e:
        logging.warning("비밀번호 검증 실패: %s", e)
    return None

def get_active_account(user_id: str):
    """아이디로 '활성' 상태 계정 정보 조회 (비밀번호 검증 없이). 자동 재로그인 토큰 검증용."""
    df = load_accounts_df()
    if df.empty:
        return None
    row = df[(df["아이디"] == user_id) & (df["상태"] == "활성")]
    if row.empty:
        return None
    row = row.iloc[0]
    return {"이름": row["이름"], "spreadsheet_id": row["spreadsheet_id"]}

# ============================================================
# 세션 유지 토큰 (탭 클릭 등으로 연결이 끊겼다 재연결돼도 로그인 유지)
# ============================================================
def _session_secret() -> bytes:
    """Secrets에 [auth] secret_key가 없으면 세션 유지 기능은 조용히 비활성화됨(로그인 자체는 정상 동작)."""
    key = st.secrets.get("auth", {}).get("secret_key", "")
    return key.encode("utf-8") if key else b""

def make_session_token(user_id: str, ttl_hours: int = 24) -> str | None:
    secret = _session_secret()
    if not secret:
        return None
    expires = int(time.time()) + ttl_hours * 3600
    payload = f"{user_id}:{expires}"
    sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:20]
    raw = f"{payload}:{sig}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")

def verify_session_token(token: str) -> str | None:
    """토큰이 유효하면 user_id를, 아니면 None을 반환."""
    secret = _session_secret()
    if not secret or not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        user_id, expires_str, sig = raw.rsplit(":", 2)
        expected_sig = hmac.new(secret, f"{user_id}:{expires_str}".encode("utf-8"), hashlib.sha256).hexdigest()[:20]
        if not hmac.compare_digest(sig, expected_sig):
            return None
        if int(expires_str) < int(time.time()):
            return None
        return user_id
    except Exception as e:
        logging.warning("세션 토큰 검증 실패: %s", e)
        return None

def add_account(user_id: str, password: str, name: str, spreadsheet_id: str, email: str = "") -> bool:
    """관리자가 신규 계정을 '사용자계정' 시트에 추가. bcrypt로 비밀번호를 해싱해서 저장.
    컬럼 순서: 아이디 / 비밀번호_해시 / 이름 / 이메일 / spreadsheet_id / 등록일 / 상태"""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        ws.append_row([user_id, hashed, name, email, spreadsheet_id, str(date.today()), "활성"])
        return True
    except Exception as e:
        logging.warning("계정 추가 실패: %s", e)
        return False


def add_signup_request(user_id: str, password: str, name: str, email: str) -> tuple[bool, str]:
    """지인이 회원가입을 신청하면 '사용자계정' 시트에 상태='승인대기'로 추가.
    승인 전에는 authenticate()가 '활성' 상태만 통과시키므로 자동으로 로그인이 차단된다."""
    try:
        df = load_accounts_df()
        if not df.empty and user_id in df["아이디"].values:
            return False, "이미 사용 중인 아이디입니다."
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False, "계정 시트 연결에 실패했습니다."
        ws = spreadsheet.worksheet("사용자계정")
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        # spreadsheet_id는 승인 시 템플릿을 복사해 자동으로 채워지므로 신청 단계에서는 빈 값
        ws.append_row([user_id, hashed, name, email, "", str(date.today()), "승인대기"])
        return True, "가입 신청이 접수되었습니다. 관리자 승인이 완료되면 입력하신 이메일로 자산관리 시트 공유 안내가 발송됩니다."
    except Exception as e:
        logging.warning("가입 신청 실패: %s", e)
        return False, f"가입 신청 중 오류가 발생했습니다: {e}"


def approve_signup(sheet_row_number: int, user_email: str, user_name: str) -> tuple[bool, str]:
    """대기 중인 가입 신청을 승인.
    [주의] 서비스 계정으로 새 구글시트를 '복사 생성'하면 서비스 계정 자체의 드라이브 저장공간이 0이라
    'storage quota exceeded' 오류가 남. 그래서 파일을 새로 만들지 않고, Jone이 미리 본인 드라이브에
    만들어 서비스 계정에 편집자로 공유해둔 '템플릿 풀' 중 아직 아무에게도 배정 안 된 것을 하나 골라
    1) 제목만 신청자 이름으로 바꾸고, 2) 신청자 이메일에 편집자로 공유(구글이 초대 메일 자동 발송)한다."""
    try:
        client = get_gspread_client()
        if client is None:
            return False, "구글 인증에 실패했습니다."

        pool = list(st.secrets.get("template", {}).get("pool", []))
        if not pool:
            return False, "secrets에 [template] pool(템플릿 spreadsheet_id 목록)이 설정되어 있지 않습니다."

        df = load_accounts_df()
        assigned_ids = set(df["spreadsheet_id"].astype(str).str.strip()) if not df.empty else set()
        available = [sid for sid in pool if sid not in assigned_ids]
        if not available:
            return False, "배정 가능한 여분 템플릿이 없습니다. 템플릿을 몇 개 더 만들어 secrets에 추가해주세요."

        new_id = available[0]
        sheet = client.open_by_key(new_id)
        sheet.update_title(f"{user_name}_자산관리")
        sheet.share(
            user_email, perm_type="user", role="writer", notify=True,
            email_message="자산관리 앱 계정이 승인되었습니다. 아래 링크의 구글시트에 본인 거래 데이터를 입력해주세요.",
        )

        spreadsheet = get_accounts_spreadsheet()
        ws = spreadsheet.worksheet("사용자계정")
        header = ws.row_values(1)
        sheet_id_col = header.index("spreadsheet_id") + 1
        status_col = header.index("상태") + 1
        ws.update_cell(sheet_row_number, sheet_id_col, new_id)
        ws.update_cell(sheet_row_number, status_col, "활성")
        return True, f"승인 완료: 준비된 템플릿을 배정하고 {user_email}로 편집 권한 공유 메일이 발송되었습니다. (남은 여분 템플릿 {len(available) - 1}개)"
    except Exception as e:
        logging.warning("가입 승인 실패: %s", e)
        return False, f"승인 처리 중 오류가 발생했습니다: {type(e).__name__} - {e}"


def reject_signup(sheet_row_number: int) -> bool:
    """대기 중인 가입 신청을 '거부' 상태로 변경 (바로 삭제하지 않아 이력이 남음)."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        header = ws.row_values(1)
        status_col = header.index("상태") + 1
        ws.update_cell(sheet_row_number, status_col, "거부")
        return True
    except Exception as e:
        logging.warning("가입 거부 처리 실패: %s", e)
        return False

def update_account_status(user_id: str, new_status: str) -> bool:
    """'사용자계정' 시트에서 특정 아이디의 상태(활성/비활성)를 변경."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        df = pd.DataFrame(ws.get_all_records())
        if df.empty or user_id not in df["아이디"].values:
            return False
        row_idx = df.index[df["아이디"] == user_id][0] + 2  # 헤더 행 고려
        status_col = df.columns.get_loc("상태") + 1
        ws.update_cell(row_idx, status_col, new_status)
        return True
    except Exception as e:
        logging.warning("계정 상태 변경 실패: %s", e)
        return False

def reset_account_password(user_id: str, new_password: str) -> bool:
    """'사용자계정' 시트에서 특정 아이디의 비밀번호 해시를 재설정."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        df = pd.DataFrame(ws.get_all_records())
        if df.empty or user_id not in df["아이디"].values:
            return False
        row_idx = df.index[df["아이디"] == user_id][0] + 2
        pw_col = df.columns.get_loc("비밀번호_해시") + 1
        new_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        ws.update_cell(row_idx, pw_col, new_hash)
        return True
    except Exception as e:
        logging.warning("비밀번호 초기화 실패: %s", e)
        return False

def delete_account_by_row(sheet_row_number: int) -> bool:
    """'사용자계정' 시트에서 특정 행(구글시트 실제 행 번호, 헤더=1행)을 통째로 삭제.
    아이디가 아닌 '행 번호'로 지정하는 이유: 등록 실수 등으로 동일 아이디가
    중복 등록된 경우에도 원하는 한 행만 정확히 삭제하기 위함."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        ws.delete_rows(sheet_row_number)
        return True
    except Exception as e:
        logging.warning("계정 삭제 실패: %s", e)
        return False

def update_account_fields(sheet_row_number: int, name: str, spreadsheet_id: str, email: str = None) -> bool:
    """'사용자계정' 시트에서 특정 행의 이름/spreadsheet_id(/이메일)를 그대로 덮어씀.
    spreadsheet_id나 이메일을 잘못 등록한 경우, 계정을 삭제·재생성하지 않고 바로 고칠 수 있도록 함.
    email이 None이면 이메일 컬럼은 건드리지 않는다(하위 호환)."""
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
        if email is not None and "이메일" in header:
            email_col = header.index("이메일") + 1
            ws.update_cell(sheet_row_number, email_col, email.strip())
        return True
    except Exception as e:
        logging.warning("계정 정보 수정 실패: %s", e)
        return False

def show_login():
    """로그인 화면. 성공 시 session_state에 사용자 정보를 저장하고 재실행."""
    st.markdown("## 📊 통합자산관리 시스템")

    tab_login, tab_signup = st.tabs(["로그인", "📝 회원가입 신청"])

    # ---------- 로그인 ----------
    with tab_login:
        with st.form("login_form"):
            user_id = st.text_input("아이디")
            password = st.text_input("비밀번호", type="password")
            submitted = st.form_submit_button("로그인")
        if submitted:
            result = authenticate(user_id, password)
            if result:
                st.session_state["logged_in"] = True
                st.session_state["user_name"] = result["이름"]
                st.session_state["spreadsheet_id"] = result["spreadsheet_id"]
                st.session_state["user_id"] = user_id
                # secrets에 [admin] user_id = "본인 로그인 아이디" 를 등록해두면,
                # 그 아이디로 로그인했을 때만 관리자 메뉴가 보이도록 함
                st.session_state["is_admin"] = (user_id == st.secrets.get("admin", {}).get("user_id"))
                # 세션 연결이 끊겼다 재연결돼도 자동으로 로그인 상태를 복구하기 위한 토큰
                token = make_session_token(user_id)
                if token:
                    st.query_params["t"] = token
                st.rerun()
            else:
                # 아이디는 있지만 아직 '활성'이 아닌 경우, 원인을 구체적으로 안내
                df = load_accounts_df()
                row = df[df["아이디"] == user_id] if (not df.empty and user_id) else pd.DataFrame()
                if not row.empty and row.iloc[0].get("상태") == "승인대기":
                    st.warning("관리자 승인 대기 중인 계정입니다. 승인이 완료되면 이메일로 안내드립니다.")
                elif not row.empty and row.iloc[0].get("상태") == "거부":
                    st.error("가입이 거부된 계정입니다. 관리자에게 문의해주세요.")
                else:
                    st.error("아이디 또는 비밀번호가 올바르지 않습니다.")

        # 관리자 전용 계정 직접 추가 패널 (긴급/소수 계정용. 평소엔 '회원가입 신청' 탭 사용을 권장)
        with st.expander("🔐 관리자 (계정 직접 추가)"):
            admin_pw = st.text_input("관리자 비밀번호", type="password", key="admin_pw")
            if admin_pw and admin_pw == st.secrets.get("admin", {}).get("password"):
                st.success("관리자 인증됨")
                with st.form("add_account_form"):
                    new_id = st.text_input("신규 아이디")
                    new_pw = st.text_input("신규 비밀번호", type="password")
                    new_name = st.text_input("이름")
                    new_email = st.text_input("이메일 (구글 계정)")
                    new_sheet_id = st.text_input("이 사용자의 구글시트 ID")
                    add_submitted = st.form_submit_button("계정 추가")
                if add_submitted:
                    if add_account(new_id, new_pw, new_name, new_sheet_id, new_email):
                        st.success(f"'{new_id}' 계정이 추가되었습니다.")
                    else:
                        st.error("계정 추가에 실패했습니다. 로그를 확인하세요.")

    # ---------- 회원가입 신청 (지인 누구나 접근 가능) ----------
    with tab_signup:
        st.caption(
            "아이디/비밀번호/이름/이메일을 입력해 신청하면, 관리자 승인 후 "
            "자산관리용 구글시트가 자동으로 만들어지고 입력하신 이메일로 편집 권한 공유 메일이 발송됩니다."
        )
        with st.form("signup_form"):
            su_id = st.text_input("사용할 아이디")
            su_pw = st.text_input("사용할 비밀번호", type="password")
            su_pw2 = st.text_input("비밀번호 확인", type="password")
            su_name = st.text_input("이름")
            su_email = st.text_input("이메일 (구글 계정 · 시트 공유용이므로 정확히 입력)")
            su_submitted = st.form_submit_button("가입 신청", type="primary")
        if su_submitted:
            if not (su_id and su_pw and su_name and su_email):
                st.warning("모든 항목을 입력해주세요.")
            elif su_pw != su_pw2:
                st.warning("비밀번호가 서로 일치하지 않습니다.")
            elif "@" not in su_email:
                st.warning("이메일 형식을 확인해주세요.")
            else:
                ok, msg = add_signup_request(su_id, su_pw, su_name, su_email)
                (st.success if ok else st.error)(msg)

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
        qty = int(_safe_num(row.get("거래수량", 0)))
        price = _safe_num(row.get("거래단가", 0))
        account = str(row.get("운용사", "")).strip()
        구분 = str(row.get("거래구분", "")).strip()

        key = (account, code)  # 계좌+종목코드 단위로 집계 (동일 종목이 여러 계좌에 있을 수 있음)
        if key not in holdings:
            holdings[key] = {"종목코드": code, "종목명": name, "계좌": account,
                              "보유수량": 0, "매수금액합계": 0.0}
        if 구분 == "매수":
            holdings[key]["보유수량"] += qty
            holdings[key]["매수금액합계"] += qty * price
        elif 구분 == "매도":
            if holdings[key]["보유수량"] > 0:
                avg = holdings[key]["매수금액합계"] / holdings[key]["보유수량"]
                holdings[key]["보유수량"] = max(0, holdings[key]["보유수량"] - qty)
                holdings[key]["매수금액합계"] = avg * holdings[key]["보유수량"]

    rows = []
    for key, h in holdings.items():
        if h["보유수량"] > 0:
            avg = h["매수금액합계"] / h["보유수량"] if h["보유수량"] else 0
            rows.append({
                "종목코드": h["종목코드"],
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
        qty = int(_safe_num(row["거래수량"]))
        price = _safe_num(row["거래단가"])
        account = row["운용사"]
        date = row["거래일자"]
        key = (str(account).strip(), code)  # 계좌+종목코드 단위로 평균단가 분리 관리

        if row["거래구분"] == "매수":
            prev_qty = qty_held.get(key, 0)
            prev_avg = avg_cost.get(key, 0.0)
            new_qty = prev_qty + qty
            new_avg = (prev_avg * prev_qty + price * qty) / new_qty if new_qty else price
            qty_held[key] = new_qty
            avg_cost[key] = new_avg
        elif row["거래구분"] == "매도":
            prev_avg = avg_cost.get(key, price)
            prev_qty = qty_held.get(key, 0)
            # 보유수량을 초과하는 매도는 실현손익 계산에서 초과분을 제외 (데이터 입력 오류로 인한 손익 부풀림 방지)
            effective_qty = min(qty, prev_qty) if prev_qty > 0 else 0
            매도금액 = effective_qty * price
            매입금액 = effective_qty * prev_avg
            실현손익 = 매도금액 - 매입금액
            realized_rows.append({
                "거래일자": date, "계좌": account, "종목코드": code, "종목명": name,
                "매도수량": qty, "매도단가": price, "평균매입단가": round(prev_avg),
                "매도금액": round(매도금액), "매입금액": round(매입금액),
                "실현손익": round(실현손익),
            })
            qty_held[key] = max(0, prev_qty - qty)

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
    background: linear-gradient(160deg, var(--tint) 0%, var(--overlay-03) 55%);
    border: 1px solid var(--card-border);
    border-radius: 14px;
    padding: 1rem 1.1rem 1rem 1.25rem;
    overflow: hidden;
}
.asset-breakdown-item::before {
    content: "";
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 4px;
    background: var(--stripe);
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
    return trade_df, nonstock_df, cash_df, monthly_df, transfer_df

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
                    st.caption("새 계정 추가")
                    with st.form("admin_add_account_form"):
                        new_id = st.text_input("신규 아이디")
                        new_pw = st.text_input("신규 비밀번호", type="password")
                        new_name = st.text_input("이름")
                        new_sheet_id = st.text_input("이 사용자의 구글시트 ID")
                        add_submitted = st.form_submit_button("계정 추가", type="primary", width="stretch")
                    if add_submitted:
                        if new_id and new_pw and new_name and new_sheet_id:
                            if add_account(new_id, new_pw, new_name, new_sheet_id):
                                st.success(f"'{new_id}' 계정이 추가되었습니다.")
                                st.rerun()
                            else:
                                st.error("계정 추가에 실패했습니다. 로그를 확인하세요.")
                        else:
                            st.warning("모든 항목을 입력해주세요.")

                    st.markdown("---")

                    if df_acc.empty:
                        st.info("등록된 계정이 없습니다.")
                    else:
                        # 계정을 한 번만 선택하면 아래 상태변경·정보수정·삭제가 전부 그 계정 기준으로 동작
                        st.caption("👤 관리할 계정 선택")
                        acc_options = {}
                        for i, row in df_acc.iterrows():
                            sheet_row = i + 2  # 헤더가 1행이므로 +2
                            label = f"{row.get('아이디','')} / {row.get('이름','')}"
                            acc_options[label] = (sheet_row, row)
                        selected_label = st.selectbox(
                            "대상 계정", list(acc_options.keys()), key="admin_selected_account",
                        )
                        target_row_num, target_row_data = acc_options[selected_label]
                        target_id = str(target_row_data.get("아이디", ""))
                        # key에 행번호를 포함시켜, 계정을 바꿔 선택하면 입력창도 그 계정의 값으로 새로 그려지도록 함
                        # (key가 그대로면 Streamlit이 이전 입력값을 계속 기억해 다른 계정으로 착각하게 됨)

                        st.markdown("---")
                        st.caption(f"'{target_id}' 계정 상태 변경 / 비밀번호 초기화")
                        col1, col2 = st.columns(2)
                        with col1:
                            new_status = st.selectbox(
                                "상태 변경", ["활성", "비활성"], key=f"admin_new_status_{target_row_num}",
                            )
                            if st.button("상태 적용", key=f"admin_status_btn_{target_row_num}", width="stretch"):
                                if update_account_status(target_id, new_status):
                                    st.success(f"'{target_id}' 계정 상태가 '{new_status}'로 변경되었습니다.")
                                    st.rerun()
                                else:
                                    st.error("상태 변경에 실패했습니다.")
                        with col2:
                            reset_pw = st.text_input(
                                "새 비밀번호", type="password", key=f"admin_reset_pw_{target_row_num}",
                            )
                            if st.button("비밀번호 초기화", key=f"admin_reset_btn_{target_row_num}", width="stretch"):
                                if reset_pw:
                                    if reset_account_password(target_id, reset_pw):
                                        st.success(f"'{target_id}' 비밀번호가 초기화되었습니다.")
                                    else:
                                        st.error("비밀번호 초기화에 실패했습니다.")
                                else:
                                    st.warning("새 비밀번호를 입력해주세요.")

                        st.markdown("---")
                        st.caption("✏️ 계정 정보 수정 (이름 · 연결된 구글시트 ID · 이메일)")
                        edit_name = st.text_input(
                            "이름", value=str(target_row_data.get("이름", "")),
                            key=f"admin_edit_name_{target_row_num}",
                        )
                        edit_sheet_id = st.text_input(
                            "연결된 구글시트 spreadsheet_id",
                            value=str(target_row_data.get("spreadsheet_id", "")),
                            key=f"admin_edit_sheet_id_{target_row_num}",
                        )
                        edit_email = st.text_input(
                            "이메일 (구글 계정 · 시트 공유용)",
                            value=str(target_row_data.get("이메일", "")),
                            key=f"admin_edit_email_{target_row_num}",
                        )
                        if st.button("💾 정보 저장", key="admin_edit_save_btn", width="stretch"):
                            if update_account_fields(target_row_num, edit_name, edit_sheet_id, edit_email):
                                st.success(f"'{target_id}' 계정 정보가 수정되었습니다.")
                                st.rerun()
                            else:
                                st.error("정보 수정에 실패했습니다.")

                        st.markdown("---")
                        st.caption("🗑 계정 삭제 (되돌릴 수 없음)")
                        st.warning(f"'{target_id}' 계정을 삭제합니다. 이 작업은 되돌릴 수 없습니다.")
                        if st.button(f"🗑 '{target_id}' 계정 삭제", key=f"admin_delete_btn_{target_row_num}", width="stretch"):
                            if delete_account_by_row(target_row_num):
                                st.success(f"'{target_id}' 계정이 삭제되었습니다.")
                                st.rerun()
                            else:
                                st.error("삭제에 실패했습니다.")

                # ---------- 가입 승인 ----------
                with tab_pending:
                    st.caption("지인이 '회원가입 신청' 탭으로 접수한 요청을 확인하고 승인/거부합니다.")
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
                                st.markdown(f"**{prow.get('이름','')}**  (아이디: {prow.get('아이디','')})")
                                st.caption(f"이메일: {prow.get('이메일','')} · 신청일: {prow.get('등록일','')}")
                                c1, c2 = st.columns(2)
                                with c1:
                                    if st.button("✅ 승인 (템플릿 자동 공유)", key=f"approve_{sheet_row}",
                                                 width="stretch", type="primary"):
                                        with st.spinner("템플릿 시트 복사 및 공유 처리 중..."):
                                            ok, msg = approve_signup(
                                                sheet_row, prow.get("이메일", ""), prow.get("이름", "")
                                            )
                                        (st.success if ok else st.error)(msg)
                                        if ok:
                                            st.rerun()
                                with c2:
                                    if st.button("❌ 거부", key=f"reject_{sheet_row}", width="stretch"):
                                        if reject_signup(sheet_row):
                                            st.warning("거부 처리되었습니다.")
                                            st.rerun()
                                        else:
                                            st.error("거부 처리에 실패했습니다.")

                # ---------- 사용자 현황 ----------
                with tab_b:
                    if not df_acc.empty:
                        display_cols = [c for c in ["아이디", "이름", "이메일", "상태", "등록일"] if c in df_acc.columns]
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
            for k in ("logged_in", "user_name", "spreadsheet_id", "user_id", "is_admin"):
                st.session_state.pop(k, None)
            st.query_params.clear()
            st.rerun()

    # 관리자 메뉴 (본인 계정으로 로그인했을 때만 노출)
    if st.session_state.get("is_admin"):
        render_admin_panel()

    # 데이터 로드
    with st.spinner("데이터 불러오는 중..."):
        trade_df, nonstock_df, cash_df, monthly_df, transfer_df = load_all_data(spreadsheet_id)

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
        render_dashboard(holdings_df, nonstock_df, cash_df, monthly_df, prices, trade_df=trade_df, transfer_df=transfer_df)

    # ══════════════════════════════════════════════
    # 탭2: 보유 종목 상세
    # ══════════════════════════════════════════════
    with tab2:
        render_holdings(holdings_df, prices, nonstock_df)

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


def render_holdings_treemap(holdings_df: pd.DataFrame):
    """보유종목 트리맵 — 사각형 크기=평가금액 비중, 색상=당일(전일 종가 대비) 등락률.
    같은 종목이 여러 계좌에 나뉘어 있으면 종목코드 기준으로 평가금액을 합산해서 하나로 표시."""
    st.markdown('<div class="section-title">보유종목 현황판 (당일 등락률)</div>', unsafe_allow_html=True)

    if holdings_df.empty:
        st.info("보유 중인 종목이 없습니다.")
        return

    grouped = holdings_df.groupby(["종목코드", "종목명"], as_index=False)["평가금액"].sum()
    grouped = grouped[grouped["평가금액"] > 0].reset_index(drop=True)
    if grouped.empty:
        st.info("표시할 보유종목이 없습니다.")
        return

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

    grouped["당일등락률"] = grouped["종목코드"].apply(_change_pct)
    grouped["등락표시"] = grouped["당일등락률"].apply(
        lambda v: f"{v:+.2f}%" if v is not None else "조회 실패"
    )
    color_values = grouped["당일등락률"].fillna(0).tolist()

    fig = go.Figure(go.Treemap(
        labels=grouped["종목명"],
        parents=[""] * len(grouped),
        values=grouped["평가금액"],
        customdata=grouped["등락표시"],
        texttemplate="%{label}<br>%{customdata}",
        textfont=dict(size=14),
        marker=dict(
            colors=color_values,
            colorscale=[[0, "#5b9bd8"], [0.5, "#2b2f3a"], [1, "#e0635e"]],  # 하락=파랑, 상승=빨강 (국내 관례)
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
    st.plotly_chart(fig, use_container_width=True)
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
            f'<div class="asset-breakdown-item" style="--stripe:{stripe};--tint:{stripe}22;--tint-strong:{stripe}33">'
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
            st.plotly_chart(fig_trend, width="stretch")
        except Exception as e:
            st.caption(f"추이 차트 오류: {e}")

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
        width="stretch",
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
                    f'<div class="sell-follow-item">↳ {buy["거래일자_dt"].strftime("%Y-%m-%d")} · '
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
            date = str(row.get("반영일자", "")).strip()
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
                f"<td style='{p_r};color:var(--text-dim);font-size:0.88rem;'>{date}</td>"
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
    if not st.session_state.get("logged_in"):
        token = st.query_params.get("t")
        if token:
            restored_user_id = verify_session_token(token)
            if restored_user_id:
                account = get_active_account(restored_user_id)
                if account:
                    st.session_state["logged_in"] = True
                    st.session_state["user_name"] = account["이름"]
                    st.session_state["spreadsheet_id"] = account["spreadsheet_id"]
                    st.session_state["user_id"] = restored_user_id
                    st.session_state["is_admin"] = (restored_user_id == st.secrets.get("admin", {}).get("user_id"))

    if not st.session_state.get("logged_in"):
        show_login()
        st.stop()
    else:
        main(st.session_state["spreadsheet_id"])

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
import bcrypt
import hmac
import hashlib
import base64
import time

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
    "현금성자산":      "현금성자산",
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
def get_spreadsheet(spreadsheet_id: str):
    """사용자별 자산 데이터 시트를 연다.
    spreadsheet_id를 인자로 받아 캐시 키에 포함시킴으로써,
    사용자마다 다른 시트가 캐시에서 섞이지 않도록 함."""
    client = get_gspread_client()
    if client is None:
        return None
    try:
        return client.open_by_key(spreadsheet_id)
    except Exception as e:
        logging.warning("스프레드시트 열기 실패: %s", e)
        st.error(f"⚠️ 구글시트 열기 실패 (spreadsheet_id: {spreadsheet_id[:8]}...): {type(e).__name__} - {e}")
        return None

@st.cache_data(ttl=30)
def load_sheet(sheet_name: str, spreadsheet_id: str) -> pd.DataFrame:
    try:
        spreadsheet = get_spreadsheet(spreadsheet_id)
        if spreadsheet is None:
            st.error(f"⚠️ '{sheet_name}' 시트 로드 실패: 스프레드시트 자체를 열지 못했습니다 (spreadsheet_id 또는 공유 권한을 확인하세요).")
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
        st.error(f"⚠️ '{sheet_name}' 시트 로드 중 오류: {type(e).__name__} - {e}")
        return pd.DataFrame()

# ============================================================
# 계정 인증 (로그인)
# ============================================================
@st.cache_resource(ttl=60)
def get_accounts_spreadsheet():
    """모든 사용자 계정 정보가 담긴 '관리자용 계정 시트'를 연다.
    이 시트의 ID는 secrets의 [accounts] spreadsheet_id 값으로 고정되어 있으며,
    사용자 개인 자산 시트와는 별개의 시트임."""
    client = get_gspread_client()
    if client is None:
        return None
    try:
        sheet_id = st.secrets["accounts"]["spreadsheet_id"]
        return client.open_by_key(sheet_id)
    except Exception as e:
        logging.warning("계정 시트 열기 실패: %s", e)
        return None

def load_accounts_df() -> pd.DataFrame:
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return pd.DataFrame()
        ws = spreadsheet.worksheet("사용자계정")
        return pd.DataFrame(ws.get_all_records())
    except Exception as e:
        logging.warning("계정 목록 로드 실패: %s", e)
        return pd.DataFrame()

def authenticate(user_id: str, password: str):
    """아이디/비밀번호를 '사용자계정' 시트와 대조.
    성공 시 {'이름':..., 'spreadsheet_id':...} 딕셔너리 반환, 실패 시 None."""
    df = load_accounts_df()
    if df.empty:
        return None
    row = df[(df["아이디"] == user_id) & (df["상태"] == "활성")]
    if row.empty:
        return None
    row = row.iloc[0]
    stored_hash = str(row["비밀번호_해시"]).encode("utf-8")
    try:
        if bcrypt.checkpw(password.encode("utf-8"), stored_hash):
            return {"이름": row["이름"], "spreadsheet_id": row["spreadsheet_id"]}
    except Exception as e:
        logging.warning("비밀번호 검증 실패: %s", e)
    return None

def get_active_account(user_id: str):
    """아이디로 '활성' 상태 계정 정보 조회 (비밀번호 검증 없이). 자동 재로그인 토큰 검증용."""
    df = load_accounts_df()
    if df.empty:
        return None
    row = df[(df["아이디"] == user_id) & (df["상태"] == "활성")]
    if row.empty:
        return None
    row = row.iloc[0]
    return {"이름": row["이름"], "spreadsheet_id": row["spreadsheet_id"]}

# ============================================================
# 세션 유지 토큰 (탭 클릭 등으로 연결이 끊겼다 재연결돼도 로그인 유지)
# ============================================================
def _session_secret() -> bytes:
    """Secrets에 [auth] secret_key가 없으면 세션 유지 기능은 조용히 비활성화됨(로그인 자체는 정상 동작)."""
    key = st.secrets.get("auth", {}).get("secret_key", "")
    return key.encode("utf-8") if key else b""

def make_session_token(user_id: str, ttl_hours: int = 24) -> str | None:
    secret = _session_secret()
    if not secret:
        return None
    expires = int(time.time()) + ttl_hours * 3600
    payload = f"{user_id}:{expires}"
    sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:20]
    raw = f"{payload}:{sig}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")

def verify_session_token(token: str) -> str | None:
    """토큰이 유효하면 user_id를, 아니면 None을 반환."""
    secret = _session_secret()
    if not secret or not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        user_id, expires_str, sig = raw.rsplit(":", 2)
        expected_sig = hmac.new(secret, f"{user_id}:{expires_str}".encode("utf-8"), hashlib.sha256).hexdigest()[:20]
        if not hmac.compare_digest(sig, expected_sig):
            return None
        if int(expires_str) < int(time.time()):
            return None
        return user_id
    except Exception as e:
        logging.warning("세션 토큰 검증 실패: %s", e)
        return None

def add_account(user_id: str, password: str, name: str, spreadsheet_id: str, email: str = "") -> bool:
    """관리자가 신규 계정을 '사용자계정' 시트에 추가. bcrypt로 비밀번호를 해싱해서 저장.
    컬럼 순서: 아이디 / 비밀번호_해시 / 이름 / 이메일 / spreadsheet_id / 등록일 / 상태"""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        ws.append_row([user_id, hashed, name, email, spreadsheet_id, str(date.today()), "활성"])
        return True
    except Exception as e:
        logging.warning("계정 추가 실패: %s", e)
        return False


def add_signup_request(user_id: str, password: str, name: str, email: str) -> tuple[bool, str]:
    """지인이 회원가입을 신청하면 '사용자계정' 시트에 상태='승인대기'로 추가.
    승인 전에는 authenticate()가 '활성' 상태만 통과시키므로 자동으로 로그인이 차단된다."""
    try:
        df = load_accounts_df()
        if not df.empty and user_id in df["아이디"].values:
            return False, "이미 사용 중인 아이디입니다."
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False, "계정 시트 연결에 실패했습니다."
        ws = spreadsheet.worksheet("사용자계정")
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        # spreadsheet_id는 승인 시 템플릿을 복사해 자동으로 채워지므로 신청 단계에서는 빈 값
        ws.append_row([user_id, hashed, name, email, "", str(date.today()), "승인대기"])
        return True, "가입 신청이 접수되었습니다. 관리자 승인이 완료되면 입력하신 이메일로 자산관리 시트 공유 안내가 발송됩니다."
    except Exception as e:
        logging.warning("가입 신청 실패: %s", e)
        return False, f"가입 신청 중 오류가 발생했습니다: {e}"


def approve_signup(sheet_row_number: int, user_email: str, user_name: str) -> tuple[bool, str]:
    """대기 중인 가입 신청을 승인.
    [주의] 서비스 계정으로 새 구글시트를 '복사 생성'하면 서비스 계정 자체의 드라이브 저장공간이 0이라
    'storage quota exceeded' 오류가 남. 그래서 파일을 새로 만들지 않고, Jone이 미리 본인 드라이브에
    만들어 서비스 계정에 편집자로 공유해둔 '템플릿 풀' 중 아직 아무에게도 배정 안 된 것을 하나 골라
    1) 제목만 신청자 이름으로 바꾸고, 2) 신청자 이메일에 편집자로 공유(구글이 초대 메일 자동 발송)한다."""
    try:
        client = get_gspread_client()
        if client is None:
            return False, "구글 인증에 실패했습니다."

        pool = list(st.secrets.get("template", {}).get("pool", []))
        if not pool:
            return False, "secrets에 [template] pool(템플릿 spreadsheet_id 목록)이 설정되어 있지 않습니다."

        df = load_accounts_df()
        assigned_ids = set(df["spreadsheet_id"].astype(str).str.strip()) if not df.empty else set()
        available = [sid for sid in pool if sid not in assigned_ids]
        if not available:
            return False, "배정 가능한 여분 템플릿이 없습니다. 템플릿을 몇 개 더 만들어 secrets에 추가해주세요."

        new_id = available[0]
        sheet = client.open_by_key(new_id)
        sheet.update_title(f"{user_name}_자산관리")
        sheet.share(
            user_email, perm_type="user", role="writer", notify=True,
            email_message="자산관리 앱 계정이 승인되었습니다. 아래 링크의 구글시트에 본인 거래 데이터를 입력해주세요.",
        )

        spreadsheet = get_accounts_spreadsheet()
        ws = spreadsheet.worksheet("사용자계정")
        header = ws.row_values(1)
        sheet_id_col = header.index("spreadsheet_id") + 1
        status_col = header.index("상태") + 1
        ws.update_cell(sheet_row_number, sheet_id_col, new_id)
        ws.update_cell(sheet_row_number, status_col, "활성")
        return True, f"승인 완료: 준비된 템플릿을 배정하고 {user_email}로 편집 권한 공유 메일이 발송되었습니다. (남은 여분 템플릿 {len(available) - 1}개)"
    except Exception as e:
        logging.warning("가입 승인 실패: %s", e)
        return False, f"승인 처리 중 오류가 발생했습니다: {type(e).__name__} - {e}"


def reject_signup(sheet_row_number: int) -> bool:
    """대기 중인 가입 신청을 '거부' 상태로 변경 (바로 삭제하지 않아 이력이 남음)."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        header = ws.row_values(1)
        status_col = header.index("상태") + 1
        ws.update_cell(sheet_row_number, status_col, "거부")
        return True
    except Exception as e:
        logging.warning("가입 거부 처리 실패: %s", e)
        return False

def update_account_status(user_id: str, new_status: str) -> bool:
    """'사용자계정' 시트에서 특정 아이디의 상태(활성/비활성)를 변경."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        df = pd.DataFrame(ws.get_all_records())
        if df.empty or user_id not in df["아이디"].values:
            return False
        row_idx = df.index[df["아이디"] == user_id][0] + 2  # 헤더 행 고려
        status_col = df.columns.get_loc("상태") + 1
        ws.update_cell(row_idx, status_col, new_status)
        return True
    except Exception as e:
        logging.warning("계정 상태 변경 실패: %s", e)
        return False

def reset_account_password(user_id: str, new_password: str) -> bool:
    """'사용자계정' 시트에서 특정 아이디의 비밀번호 해시를 재설정."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        df = pd.DataFrame(ws.get_all_records())
        if df.empty or user_id not in df["아이디"].values:
            return False
        row_idx = df.index[df["아이디"] == user_id][0] + 2
        pw_col = df.columns.get_loc("비밀번호_해시") + 1
        new_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        ws.update_cell(row_idx, pw_col, new_hash)
        return True
    except Exception as e:
        logging.warning("비밀번호 초기화 실패: %s", e)
        return False

def delete_account_by_row(sheet_row_number: int) -> bool:
    """'사용자계정' 시트에서 특정 행(구글시트 실제 행 번호, 헤더=1행)을 통째로 삭제.
    아이디가 아닌 '행 번호'로 지정하는 이유: 등록 실수 등으로 동일 아이디가
    중복 등록된 경우에도 원하는 한 행만 정확히 삭제하기 위함."""
    try:
        spreadsheet = get_accounts_spreadsheet()
        if spreadsheet is None:
            return False
        ws = spreadsheet.worksheet("사용자계정")
        ws.delete_rows(sheet_row_number)
        return True
    except Exception as e:
        logging.warning("계정 삭제 실패: %s", e)
        return False

def update_account_fields(sheet_row_number: int, name: str, spreadsheet_id: str, email: str = None) -> bool:
    """'사용자계정' 시트에서 특정 행의 이름/spreadsheet_id(/이메일)를 그대로 덮어씀.
    spreadsheet_id나 이메일을 잘못 등록한 경우, 계정을 삭제·재생성하지 않고 바로 고칠 수 있도록 함.
    email이 None이면 이메일 컬럼은 건드리지 않는다(하위 호환)."""
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
        if email is not None and "이메일" in header:
            email_col = header.index("이메일") + 1
            ws.update_cell(sheet_row_number, email_col, email.strip())
        return True
    except Exception as e:
        logging.warning("계정 정보 수정 실패: %s", e)
        return False

def show_login():
    """로그인 화면. 성공 시 session_state에 사용자 정보를 저장하고 재실행."""
    st.markdown("## 📊 통합자산관리 시스템")

    tab_login, tab_signup = st.tabs(["로그인", "📝 회원가입 신청"])

    # ---------- 로그인 ----------
    with tab_login:
        with st.form("login_form"):
            user_id = st.text_input("아이디")
            password = st.text_input("비밀번호", type="password")
            submitted = st.form_submit_button("로그인")
        if submitted:
            result = authenticate(user_id, password)
            if result:
                st.session_state["logged_in"] = True
                st.session_state["user_name"] = result["이름"]
                st.session_state["spreadsheet_id"] = result["spreadsheet_id"]
                st.session_state["user_id"] = user_id
                # secrets에 [admin] user_id = "본인 로그인 아이디" 를 등록해두면,
                # 그 아이디로 로그인했을 때만 관리자 메뉴가 보이도록 함
                st.session_state["is_admin"] = (user_id == st.secrets.get("admin", {}).get("user_id"))
                # 세션 연결이 끊겼다 재연결돼도 자동으로 로그인 상태를 복구하기 위한 토큰
                token = make_session_token(user_id)
                if token:
                    st.query_params["t"] = token
                st.rerun()
            else:
                # 아이디는 있지만 아직 '활성'이 아닌 경우, 원인을 구체적으로 안내
                df = load_accounts_df()
                row = df[df["아이디"] == user_id] if (not df.empty and user_id) else pd.DataFrame()
                if not row.empty and row.iloc[0].get("상태") == "승인대기":
                    st.warning("관리자 승인 대기 중인 계정입니다. 승인이 완료되면 이메일로 안내드립니다.")
                elif not row.empty and row.iloc[0].get("상태") == "거부":
                    st.error("가입이 거부된 계정입니다. 관리자에게 문의해주세요.")
                else:
                    st.error("아이디 또는 비밀번호가 올바르지 않습니다.")

        # 관리자 전용 계정 직접 추가 패널 (긴급/소수 계정용. 평소엔 '회원가입 신청' 탭 사용을 권장)
        with st.expander("🔐 관리자 (계정 직접 추가)"):
            admin_pw = st.text_input("관리자 비밀번호", type="password", key="admin_pw")
            if admin_pw and admin_pw == st.secrets.get("admin", {}).get("password"):
                st.success("관리자 인증됨")
                with st.form("add_account_form"):
                    new_id = st.text_input("신규 아이디")
                    new_pw = st.text_input("신규 비밀번호", type="password")
                    new_name = st.text_input("이름")
                    new_email = st.text_input("이메일 (구글 계정)")
                    new_sheet_id = st.text_input("이 사용자의 구글시트 ID")
                    add_submitted = st.form_submit_button("계정 추가")
                if add_submitted:
                    if add_account(new_id, new_pw, new_name, new_sheet_id, new_email):
                        st.success(f"'{new_id}' 계정이 추가되었습니다.")
                    else:
                        st.error("계정 추가에 실패했습니다. 로그를 확인하세요.")

    # ---------- 회원가입 신청 (지인 누구나 접근 가능) ----------
    with tab_signup:
        st.caption(
            "아이디/비밀번호/이름/이메일을 입력해 신청하면, 관리자 승인 후 "
            "자산관리용 구글시트가 자동으로 만들어지고 입력하신 이메일로 편집 권한 공유 메일이 발송됩니다."
        )
        with st.form("signup_form"):
            su_id = st.text_input("사용할 아이디")
            su_pw = st.text_input("사용할 비밀번호", type="password")
            su_pw2 = st.text_input("비밀번호 확인", type="password")
            su_name = st.text_input("이름")
            su_email = st.text_input("이메일 (구글 계정 · 시트 공유용이므로 정확히 입력)")
            su_submitted = st.form_submit_button("가입 신청", type="primary")
        if su_submitted:
            if not (su_id and su_pw and su_name and su_email):
                st.warning("모든 항목을 입력해주세요.")
            elif su_pw != su_pw2:
                st.warning("비밀번호가 서로 일치하지 않습니다.")
            elif "@" not in su_email:
                st.warning("이메일 형식을 확인해주세요.")
            else:
                ok, msg = add_signup_request(su_id, su_pw, su_name, su_email)
                (st.success if ok else st.error)(msg)

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
        qty = int(_safe_num(row.get("거래수량", 0)))
        price = _safe_num(row.get("거래단가", 0))
        account = str(row.get("운용사", "")).strip()
        구분 = str(row.get("거래구분", "")).strip()

        key = (account, code)  # 계좌+종목코드 단위로 집계 (동일 종목이 여러 계좌에 있을 수 있음)
        if key not in holdings:
            holdings[key] = {"종목코드": code, "종목명": name, "계좌": account,
                              "보유수량": 0, "매수금액합계": 0.0}
        if 구분 == "매수":
            holdings[key]["보유수량"] += qty
            holdings[key]["매수금액합계"] += qty * price
        elif 구분 == "매도":
            if holdings[key]["보유수량"] > 0:
                avg = holdings[key]["매수금액합계"] / holdings[key]["보유수량"]
                holdings[key]["보유수량"] = max(0, holdings[key]["보유수량"] - qty)
                holdings[key]["매수금액합계"] = avg * holdings[key]["보유수량"]

    rows = []
    for key, h in holdings.items():
        if h["보유수량"] > 0:
            avg = h["매수금액합계"] / h["보유수량"] if h["보유수량"] else 0
            rows.append({
                "종목코드": h["종목코드"],
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
        qty = int(_safe_num(row["거래수량"]))
        price = _safe_num(row["거래단가"])
        account = row["운용사"]
        date = row["거래일자"]
        key = (str(account).strip(), code)  # 계좌+종목코드 단위로 평균단가 분리 관리

        if row["거래구분"] == "매수":
            prev_qty = qty_held.get(key, 0)
            prev_avg = avg_cost.get(key, 0.0)
            new_qty = prev_qty + qty
            new_avg = (prev_avg * prev_qty + price * qty) / new_qty if new_qty else price
            qty_held[key] = new_qty
            avg_cost[key] = new_avg
        elif row["거래구분"] == "매도":
            prev_avg = avg_cost.get(key, price)
            prev_qty = qty_held.get(key, 0)
            # 보유수량을 초과하는 매도는 실현손익 계산에서 초과분을 제외 (데이터 입력 오류로 인한 손익 부풀림 방지)
            effective_qty = min(qty, prev_qty) if prev_qty > 0 else 0
            매도금액 = effective_qty * price
            매입금액 = effective_qty * prev_avg
            실현손익 = 매도금액 - 매입금액
            realized_rows.append({
                "거래일자": date, "계좌": account, "종목코드": code, "종목명": name,
                "매도수량": qty, "매도단가": price, "평균매입단가": round(prev_avg),
                "매도금액": round(매도금액), "매입금액": round(매입금액),
                "실현손익": round(실현손익),
            })
            qty_held[key] = max(0, prev_qty - qty)

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
    background: linear-gradient(160deg, var(--tint) 0%, var(--overlay-03) 55%);
    border: 1px solid var(--card-border);
    border-radius: 14px;
    padding: 1rem 1.1rem 1rem 1.25rem;
    overflow: hidden;
}
.asset-breakdown-item::before {
    content: "";
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 4px;
    background: var(--stripe);
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
    return trade_df, nonstock_df, cash_df, monthly_df, transfer_df

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
                tab_a, tab_pending, tab_b, tab_c = st.tabs(["계정 관리", "🆕 가입 승인", "사용자 현황", "시스템"])

                # ---------- 계정 관리 ----------
                with tab_a:
                    st.caption("새 계정 추가")
                    with st.form("admin_add_account_form"):
                        new_id = st.text_input("신규 아이디")
                        new_pw = st.text_input("신규 비밀번호", type="password")
                        new_name = st.text_input("이름")
                        new_sheet_id = st.text_input("이 사용자의 구글시트 ID")
                        add_submitted = st.form_submit_button("계정 추가", type="primary", width="stretch")
                    if add_submitted:
                        if new_id and new_pw and new_name and new_sheet_id:
                            if add_account(new_id, new_pw, new_name, new_sheet_id):
                                st.success(f"'{new_id}' 계정이 추가되었습니다.")
                            else:
                                st.error("계정 추가에 실패했습니다. 로그를 확인하세요.")
                        else:
                            st.warning("모든 항목을 입력해주세요.")

                    st.markdown("---")
                    st.caption("기존 계정 상태 변경 / 비밀번호 초기화")
                    df_acc = load_accounts_df()
                    if not df_acc.empty:
                        target_id = st.selectbox("대상 계정", df_acc["아이디"].tolist(), key="admin_target_id")
                        col1, col2 = st.columns(2)
                        with col1:
                            new_status = st.selectbox("상태 변경", ["활성", "비활성"], key="admin_new_status")
                            if st.button("상태 적용", key="admin_status_btn", width="stretch"):
                                if update_account_status(target_id, new_status):
                                    st.success(f"'{target_id}' 계정 상태가 '{new_status}'로 변경되었습니다.")
                                else:
                                    st.error("상태 변경에 실패했습니다.")
                        with col2:
                            reset_pw = st.text_input("새 비밀번호", type="password", key="admin_reset_pw")
                            if st.button("비밀번호 초기화", key="admin_reset_btn", width="stretch"):
                                if reset_pw:
                                    if reset_account_password(target_id, reset_pw):
                                        st.success(f"'{target_id}' 비밀번호가 초기화되었습니다.")
                                    else:
                                        st.error("비밀번호 초기화에 실패했습니다.")
                                else:
                                    st.warning("새 비밀번호를 입력해주세요.")
                    else:
                        st.info("등록된 계정이 없습니다.")

                    st.markdown("---")
                    st.caption("✏️ 계정 정보 확인 / 수정 (이름 · 연결된 구글시트 ID)")
                    if not df_acc.empty:
                        edit_row_options = {}
                        for i, row in df_acc.iterrows():
                            sheet_row = i + 2
                            label = f"행{sheet_row}: {row.get('아이디','')} / {row.get('이름','')}"
                            edit_row_options[label] = (sheet_row, row)
                        edit_label = st.selectbox("확인·수정할 계정", list(edit_row_options.keys()), key="admin_edit_target")
                        edit_row_num, edit_row_data = edit_row_options[edit_label]
                        # key에 행번호를 포함시켜, 계정을 바꿔 선택하면 입력창도 그 계정의 값으로 새로 그려지도록 함
                        # (key가 그대로면 Streamlit이 이전 입력값을 계속 기억해 다른 계정으로 착각하게 됨)
                        edit_name = st.text_input(
                            "이름", value=str(edit_row_data.get("이름", "")),
                            key=f"admin_edit_name_{edit_row_num}",
                        )
                        edit_sheet_id = st.text_input(
                            "연결된 구글시트 spreadsheet_id",
                            value=str(edit_row_data.get("spreadsheet_id", "")),
                            key=f"admin_edit_sheet_id_{edit_row_num}",
                        )
                        edit_email = st.text_input(
                            "이메일 (구글 계정 · 시트 공유용)",
                            value=str(edit_row_data.get("이메일", "")),
                            key=f"admin_edit_email_{edit_row_num}",
                        )
                        if st.button("💾 정보 저장", key="admin_edit_save_btn", width="stretch"):
                            if update_account_fields(edit_row_num, edit_name, edit_sheet_id, edit_email):
                                st.success(f"'{edit_row_data.get('아이디','')}' 계정 정보가 수정되었습니다.")
                                st.rerun()
                            else:
                                st.error("정보 수정에 실패했습니다.")
                    else:
                        st.info("수정할 계정이 없습니다.")
                    st.caption("🗑 계정 삭제 (되돌릴 수 없음)")
                    if not df_acc.empty:
                        display_df = df_acc.copy()
                        display_df.insert(0, "선택", False)
                        display_df.insert(1, "행번호", [i + 2 for i in range(len(df_acc))])  # 헤더가 1행이므로 +2
                        show_cols = [c for c in ["선택", "행번호", "아이디", "이름", "이메일", "상태", "등록일"] if c in display_df.columns]
                        edited_df = st.data_editor(
                            display_df[show_cols],
                            hide_index=True,
                            width="stretch",
                            disabled=[c for c in show_cols if c != "선택"],
                            key="admin_delete_editor",
                        )
                        selected_rows = edited_df.loc[edited_df["선택"] == True, "행번호"].tolist()
                        if selected_rows:
                            st.warning(f"체크된 {len(selected_rows)}개 계정이 삭제 대상입니다.")
                        if st.button("🗑 체크된 계정 삭제", key="admin_delete_btn", width="stretch",
                                     disabled=(len(selected_rows) == 0)):
                            # 행 번호가 큰 것부터 삭제해야 삭제 도중 나머지 행 번호가 밀리지 않음
                            ok_count = sum(delete_account_by_row(r) for r in sorted(selected_rows, reverse=True))
                            if ok_count == len(selected_rows):
                                st.success(f"{ok_count}개 계정이 삭제되었습니다.")
                            else:
                                st.warning(f"{ok_count}/{len(selected_rows)}개만 삭제되었습니다. 목록을 다시 확인해주세요.")
                            st.rerun()
                    else:
                        st.info("삭제할 계정이 없습니다.")

                # ---------- 가입 승인 ----------
                with tab_pending:
                    st.caption("지인이 '회원가입 신청' 탭으로 접수한 요청을 확인하고 승인/거부합니다.")
                    df_acc_pending = load_accounts_df()
                    pending_df = (
                        df_acc_pending[df_acc_pending["상태"] == "승인대기"]
                        if not df_acc_pending.empty else pd.DataFrame()
                    )
                    if pending_df.empty:
                        st.info("승인 대기 중인 신청이 없습니다.")
                    else:
                        st.caption(f"승인 대기 {len(pending_df)}건")
                        for i, prow in pending_df.iterrows():
                            sheet_row = i + 2  # 헤더가 1행이므로 +2
                            with st.container(border=True):
                                st.markdown(f"**{prow.get('이름','')}**  (아이디: {prow.get('아이디','')})")
                                st.caption(f"이메일: {prow.get('이메일','')} · 신청일: {prow.get('등록일','')}")
                                c1, c2 = st.columns(2)
                                with c1:
                                    if st.button("✅ 승인 (템플릿 자동 공유)", key=f"approve_{sheet_row}",
                                                 width="stretch", type="primary"):
                                        with st.spinner("템플릿 시트 복사 및 공유 처리 중..."):
                                            ok, msg = approve_signup(
                                                sheet_row, prow.get("이메일", ""), prow.get("이름", "")
                                            )
                                        (st.success if ok else st.error)(msg)
                                        if ok:
                                            st.rerun()
                                with c2:
                                    if st.button("❌ 거부", key=f"reject_{sheet_row}", width="stretch"):
                                        if reject_signup(sheet_row):
                                            st.warning("거부 처리되었습니다.")
                                            st.rerun()
                                        else:
                                            st.error("거부 처리에 실패했습니다.")

                # ---------- 사용자 현황 ----------
                with tab_b:
                    df_acc = load_accounts_df()
                    if not df_acc.empty:
                        display_cols = [c for c in ["아이디", "이름", "이메일", "상태", "등록일"] if c in df_acc.columns]
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
            for k in ("logged_in", "user_name", "spreadsheet_id", "user_id", "is_admin"):
                st.session_state.pop(k, None)
            st.query_params.clear()
            st.rerun()

    # 관리자 메뉴 (본인 계정으로 로그인했을 때만 노출)
    if st.session_state.get("is_admin"):
        render_admin_panel()

    # 데이터 로드
    with st.spinner("데이터 불러오는 중..."):
        trade_df, nonstock_df, cash_df, monthly_df, transfer_df = load_all_data(spreadsheet_id)

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
        render_dashboard(holdings_df, nonstock_df, cash_df, monthly_df, prices, trade_df=trade_df, transfer_df=transfer_df)

    # ══════════════════════════════════════════════
    # 탭2: 보유 종목 상세
    # ══════════════════════════════════════════════
    with tab2:
        render_holdings(holdings_df, prices, nonstock_df)

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


def render_holdings_treemap(holdings_df: pd.DataFrame):
    """보유종목 트리맵 — 사각형 크기=평가금액 비중, 색상=당일(전일 종가 대비) 등락률.
    같은 종목이 여러 계좌에 나뉘어 있으면 종목코드 기준으로 평가금액을 합산해서 하나로 표시."""
    st.markdown('<div class="section-title">보유종목 현황판 (당일 등락률)</div>', unsafe_allow_html=True)

    if holdings_df.empty:
        st.info("보유 중인 종목이 없습니다.")
        return

    grouped = holdings_df.groupby(["종목코드", "종목명"], as_index=False)["평가금액"].sum()
    grouped = grouped[grouped["평가금액"] > 0].reset_index(drop=True)
    if grouped.empty:
        st.info("표시할 보유종목이 없습니다.")
        return

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

    grouped["당일등락률"] = grouped["종목코드"].apply(_change_pct)
    grouped["등락표시"] = grouped["당일등락률"].apply(
        lambda v: f"{v:+.2f}%" if v is not None else "조회 실패"
    )
    color_values = grouped["당일등락률"].fillna(0).tolist()

    fig = go.Figure(go.Treemap(
        labels=grouped["종목명"],
        parents=[""] * len(grouped),
        values=grouped["평가금액"],
        customdata=grouped["등락표시"],
        texttemplate="%{label}<br>%{customdata}",
        textfont=dict(size=14),
        marker=dict(
            colors=color_values,
            colorscale=[[0, "#5b9bd8"], [0.5, "#2b2f3a"], [1, "#e0635e"]],  # 하락=파랑, 상승=빨강 (국내 관례)
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
    st.plotly_chart(fig, use_container_width=True)
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
            f'<div class="asset-breakdown-item" style="--stripe:{stripe};--tint:{stripe}22;--tint-strong:{stripe}33">'
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
            st.plotly_chart(fig_trend, width="stretch")
        except Exception as e:
            st.caption(f"추이 차트 오류: {e}")

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
        width="stretch",
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
                    f'<div class="sell-follow-item">↳ {buy["거래일자_dt"].strftime("%Y-%m-%d")} · '
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
            date = str(row.get("반영일자", "")).strip()
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
                f"<td style='{p_r};color:var(--text-dim);font-size:0.88rem;'>{date}</td>"
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
    if not st.session_state.get("logged_in"):
        token = st.query_params.get("t")
        if token:
            restored_user_id = verify_session_token(token)
            if restored_user_id:
                account = get_active_account(restored_user_id)
                if account:
                    st.session_state["logged_in"] = True
                    st.session_state["user_name"] = account["이름"]
                    st.session_state["spreadsheet_id"] = account["spreadsheet_id"]
                    st.session_state["user_id"] = restored_user_id
                    st.session_state["is_admin"] = (restored_user_id == st.secrets.get("admin", {}).get("user_id"))

    if not st.session_state.get("logged_in"):
        show_login()
        st.stop()
    else:
        main(st.session_state["spreadsheet_id"])
