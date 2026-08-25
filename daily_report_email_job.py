"""
일일 리포트 이메일 자동 발송 스크립트 (GitHub Actions 전용)
====================================================
매일 정해진 시각(기본: 한국시간 07:30)에 실행되어, '사용자계정' 화이트리스트 시트의
활성(상태='활성') 사용자 전원에게 "오늘의 시황 + 보유종목 리포트" 이메일을 발송한다.

동작 순서:
  1. 서비스 계정으로 화이트리스트 시트를 읽어 활성 사용자 목록(이메일, 개인 시트 ID,
     암호화된 refresh_token)을 가져온다.
  2. 시황(코스피/코스닥/해외지수/환율 등)은 사용자와 무관한 공통 데이터이므로 딱 1번만 조회한다.
  3. 사용자별로 저장된 refresh_token으로 그 사람 대신 로그인해 '거래이력' 시트를 읽고
     보유 종목(종목코드·종목명)을 뽑아낸다.
  4. 종목별 공시(DART)·뉴스·애널리스트 리포트·컨센서스는 같은 종목을 여러 사용자가
     동시에 보유한 경우 API를 반복 호출하지 않도록 이번 실행(run) 안에서만 유지되는
     캐시(functools.lru_cache)를 쓴다 — 지인 규모(수십 명, 종목 수는 ASSET_MASTER
     기준 15종 내외)에서는 이 캐시만으로 외부 API 호출량이 사용자 수가 아니라
     "그날 실제로 보유된 종목 가짓수"에 비례하게 된다.
  5. HTML 이메일을 만들어 Gmail SMTP(앱 비밀번호)로 발송한다.

[중요 - 유지보수 시 주의사항]
아래 시황·공시·뉴스·리포트·컨센서스 수집 함수들은 stock_app_main.py의 동일한 이름의
함수를 그대로 옮겨온 것이다(로직 동일, @st.cache_data 데코레이터만 @lru_cache로 교체).
GitHub Actions 환경에는 Streamlit이 없어 stock_app_main.py를 직접 import할 수 없기
때문에 부득이하게 복제했다. monthly_snapshot_job.py 상단 주석과 동일한 이유이며,
앞으로 stock_app_main.py에서 이 함수들을 고치면 이 파일의 동일 함수도 반드시 함께
고쳐야 한다.

[설계상 의도적으로 뺀 것]
stock_app_main.py의 render_daily_report()에는 Claude(Haiku) API로 만드는 "AI 종합
브리핑" 문단이 있는데, 이 자동 발송 스크립트에는 넣지 않았다. 종목 수 × 문장 생성 API
호출이 반복되면 비용·응답시간이 늘어나고, 일일 자동 발송은 사람이 매번 확인하는 화면이
아니라서 API 실패 시 재시도할 사람도 없다. 우선은 원본 데이터(시황·뉴스·공시·컨센서스)만
정리해서 보내고, 필요하면 나중에 generate_stock_daily_summary()를 그대로 가져와
추가하면 된다(같은 캐시 전략 적용 가능).

필요한 GitHub Actions 저장소 시크릿(Settings > Secrets and variables > Actions):
  - GOOGLE_SERVICE_ACCOUNT_JSON : 서비스 계정 키 파일(JSON) 전체 내용 (기존과 동일)
  - ACCOUNTS_SPREADSHEET_ID     : '사용자계정' 화이트리스트 시트의 스프레드시트 ID (기존과 동일)
  - GOOGLE_OAUTH_CLIENT_ID      : 앱의 OAuth 클라이언트 ID (기존과 동일)
  - GOOGLE_OAUTH_CLIENT_SECRET  : 앱의 OAuth 클라이언트 시크릿 (기존과 동일)
  - AUTH_SECRET_KEY             : 앱의 [auth] secret_key와 반드시 동일한 값 (기존과 동일)
  - DART_API_KEY                : DART Open API 인증키 (앱 secrets의 [dart] api_key와 동일 값)
  - GMAIL_SENDER_EMAIL          : 발송에 쓸 Gmail 주소 (신규)
  - GMAIL_APP_PASSWORD          : 그 Gmail 계정의 앱 비밀번호(16자리, 일반 로그인 비번 아님) (신규)
"""

import os
import sys
import json
import base64
import hashlib
import logging
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo
from io import BytesIO
import zipfile
import xml.etree.ElementTree as ET

import gspread
import pandas as pd
import requests
import yfinance as yf
from pykrx import stock as krx_stock
from cryptography.fernet import Fernet, InvalidToken
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.oauth2.credentials import Credentials as UserOAuthCredentials
from google.auth.transport.requests import Request as GoogleAuthRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("daily_report_email_job")

KST = ZoneInfo("Asia/Seoul")

OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]


# ============================================================
# 환경변수(GitHub Actions 시크릿) 로드
# ============================================================
def _env(name: str, required: bool = True) -> str:
    val = os.environ.get(name, "").strip()
    if required and not val:
        raise RuntimeError(f"환경변수 {name} 가 설정되어 있지 않습니다.")
    return val


# ============================================================
# refresh_token 복호화 (stock_app_main.py / monthly_snapshot_job.py와 동일 로직)
# ============================================================
def _refresh_token_cipher(secret_key: str):
    fernet_key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode("utf-8")).digest())
    return Fernet(fernet_key)


def decrypt_refresh_token(token_enc: str, secret_key: str):
    if not token_enc:
        return None
    try:
        cipher = _refresh_token_cipher(secret_key)
        return cipher.decrypt(token_enc.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception) as e:
        log.warning("refresh_token 복호화 실패: %s", e)
        return None


def build_credentials_from_refresh_token(refresh_token: str, client_id: str, client_secret: str):
    credentials = UserOAuthCredentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=OAUTH_SCOPES,
    )
    credentials.refresh(GoogleAuthRequest())
    return credentials


# ============================================================
# 자산 마스터 (stock_app_main.py의 ASSET_MASTER와 동일 — 신규 종목 추가 시 그쪽도 함께 갱신)
# ============================================================
ASSET_MASTER = {
    "069500": {"ticker": "069500.KS", "market": "KR", "type": "ETF"},
    "102110": {"ticker": "102110.KS", "market": "KR", "type": "ETF"},
    "471990": {"ticker": "471990.KS", "market": "KR", "type": "ETF"},
    "487240": {"ticker": "487240.KS", "market": "KR", "type": "ETF"},
    "229200": {"ticker": "229200.KS", "market": "KR", "type": "ETF"},
    "0148J0": {"ticker": "148J0.KS", "market": "KR", "type": "ETF"},
    "292150": {"ticker": "292150.KS", "market": "KR", "type": "ETF"},
    "005930": {"ticker": "005930.KS", "market": "KR", "type": "주식"},
    "000660": {"ticker": "000660.KS", "market": "KR", "type": "주식"},
    "278470": {"ticker": "278470.KS", "market": "KR", "type": "주식"},
    "009150": {"ticker": "009150.KS", "market": "KR", "type": "주식"},
    "005380": {"ticker": "005380.KS", "market": "KR", "type": "주식"},
    "042660": {"ticker": "042660.KS", "market": "KR", "type": "주식"},
    "071970": {"ticker": "071970.KS", "market": "KR", "type": "주식"},
    "034020": {"ticker": "034020.KS", "market": "KR", "type": "주식"},
}

# 국내 상장 ETF는 대부분 이 브랜드명으로 시작 — ASSET_MASTER에 등록되지 않은 새 종목이 들어와도
# 종목명만으로 ETF/주식을 자동 구분하기 위한 보조 목록 (stock_app_main.py와 동일)
ETF_BRAND_PREFIXES = (
    "KODEX", "TIGER", "KBSTAR", "ARIRANG", "SOL", "ACE", "HANARO",
    "KOSEF", "PLUS", "RISE", "WOORI", "마이다스", "히어로즈", "TIMEFOLIO",
)


def get_asset_type(code: str, name: str = "") -> str:
    """stock_app_main.py의 get_asset_type과 동일 로직. ETF/주식 구분."""
    code = str(code).strip()
    meta = ASSET_MASTER.get(code)
    if meta:
        return meta["type"]
    name_str = str(name).strip().upper()
    if name_str.startswith(ETF_BRAND_PREFIXES):
        return "ETF"
    return "주식"


def get_asset_market(code: str) -> str:
    """stock_app_main.py의 get_asset_market과 동일 로직(간이판)."""
    code = str(code).strip().upper()
    meta = ASSET_MASTER.get(code)
    if meta:
        return meta.get("market", "KR")
    import re
    if re.fullmatch(r"[A-Z]{1,5}", code):
        return "US"
    return "KR"


def _safe_num(val, default=0.0):
    try:
        s = str(val).strip().replace(",", "")
        if s in ("", "-", "nan", "None"):
            return default
        return float(s)
    except (ValueError, TypeError):
        return default


def _safe_float_or_none(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ============================================================
# 거래이력 → 보유 종목(중복 없이 종목코드·종목명만) — 이메일 대상 종목 뽑기용
# [2026-08-25] 표시 순서를 stock_app_main.py의 보유종목 표(4190번 줄 부근)와 동일하게
# 맞춤: "ETF 먼저, 그 다음 주식 — 각 그룹 내에서는 투자원금(매입금액) 큰 순서".
# 이를 위해 계좌별 평균단가를 replay해서 종목코드 단위로 투자원금(매입금액)을 합산한다
# (여러 계좌에 나눠 보유해도 종목당 하나의 투자원금 합계로 정렬).
# ============================================================
def get_held_stocks(trade_df: pd.DataFrame) -> list[tuple[str, str]]:
    if trade_df.empty:
        return []
    df = trade_df.copy()
    df["_dt"] = pd.to_datetime(df["거래일자"], errors="coerce")
    df = df.sort_values("_dt")

    qty_held: dict[tuple[str, str], float] = {}
    avg_cost: dict[tuple[str, str], float] = {}
    names: dict[str, str] = {}
    for _, row in df.iterrows():
        code = str(row.get("종목코드", "")).strip()
        name = str(row.get("종목명", "")).strip()
        account = str(row.get("운용사", "")).strip()
        qty = _safe_num(row.get("거래수량", 0))
        price = _safe_num(row.get("거래단가", 0))
        구분 = str(row.get("거래구분", "")).strip()
        if not code:
            continue
        names[code] = name
        key = (account, code)
        if 구분 == "매수":
            prev_qty = qty_held.get(key, 0)
            prev_avg = avg_cost.get(key, 0.0)
            new_qty = prev_qty + qty
            avg_cost[key] = (prev_avg * prev_qty + price * qty) / new_qty if new_qty else price
            qty_held[key] = new_qty
        elif 구분 == "매도":
            qty_held[key] = qty_held.get(key, 0) - qty

    # 종목코드 단위로 투자원금(매입금액 = 평균단가 × 보유수량)을 계좌 합산
    invested_by_code: dict[str, float] = {}
    for (account, code), qty in qty_held.items():
        if qty > 0.0001:
            invested_by_code[code] = invested_by_code.get(code, 0.0) + avg_cost.get((account, code), 0.0) * qty

    held_codes = list(invested_by_code.keys())
    held_codes.sort(
        key=lambda code: (
            0 if get_asset_type(code, names.get(code, "")) == "ETF" else 1,
            -invested_by_code[code],
        )
    )
    return [(code, names.get(code, "")) for code in held_codes]


# ============================================================
# 시황 브리핑 (stock_app_main.py의 get_market_overview 계열과 동일 로직)
# ============================================================
def _yf_last_two_closes(ticker: str, period_days: int = 12):
    try:
        hist = yf.Ticker(ticker).history(period=f"{period_days}d")
        closes = hist["Close"].dropna()
        if len(closes) >= 2:
            return float(closes.iloc[-1]), float(closes.iloc[-2])
        if len(closes) == 1:
            return float(closes.iloc[-1]), None
    except Exception as e:
        log.warning("시황 지표 조회 실패 [%s]: %s", ticker, e)
    return None, None


def _kr_index_last_two_closes(pykrx_code: str, yf_fallback_ticker: str, from_date: str, today: str):
    try:
        df = krx_stock.get_index_ohlcv_by_date(from_date, today, pykrx_code)
        if len(df) >= 2:
            return float(df["종가"].iloc[-1]), float(df["종가"].iloc[-2]), None
        pykrx_error = f"pykrx 조회 결과 {len(df)}행 (2행 미만)"
    except Exception as e:
        pykrx_error = str(e)
        log.warning("국내 지수 pykrx 조회 실패 [%s]: %s — 야후로 대체 시도", pykrx_code, e)

    cur, prev = _yf_last_two_closes(yf_fallback_ticker)
    if cur is not None:
        return cur, prev, f"pykrx 실패해서 야후 대체값 사용 중"
    return None, None, f"pykrx·야후 둘 다 실패 (pykrx: {pykrx_error})"


_DAUM_INVESTOR_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.daum.net/domestic/investors/KOSPI",
}


@lru_cache(maxsize=8)
def get_investor_trend(market: str) -> dict:
    try:
        url = f"https://finance.daum.net/api/charts/investors/{market}/days"
        resp = requests.get(
            url, params={"limit": 2, "adjusted": "true"},
            headers=_DAUM_INVESTOR_HEADERS, timeout=10,
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
        log.warning("다음 금융 투자자별 매매동향 조회 실패 [%s]: %s", market, e)
        return {}


def get_market_overview() -> dict:
    m: dict[str, dict] = {}

    def _add(key: str, ticker: str):
        cur, prev = _yf_last_two_closes(ticker)
        m[key] = {
            "값": cur,
            "등락률": ((cur - prev) / prev * 100) if (cur is not None and prev) else None,
        }

    today = datetime.now(KST).strftime("%Y%m%d")
    from_date = (datetime.now(KST) - timedelta(days=10)).strftime("%Y%m%d")

    cur_k, prev_k, err_k = _kr_index_last_two_closes("1001", "^KS11", from_date, today)
    m["코스피"] = {
        "값": cur_k,
        "등락률": ((cur_k - prev_k) / prev_k * 100) if (cur_k is not None and prev_k) else None,
    }
    cur_q, prev_q, err_q = _kr_index_last_two_closes("2001", "^KQ11", from_date, today)
    m["코스닥"] = {
        "값": cur_q,
        "등락률": ((cur_q - prev_q) / prev_q * 100) if (cur_q is not None and prev_q) else None,
    }

    kospi_flow = get_investor_trend("KOSPI")
    if kospi_flow:
        m["코스피_수급"] = kospi_flow
    kosdaq_flow = get_investor_trend("KOSDAQ")
    if kosdaq_flow:
        m["코스닥_수급"] = kosdaq_flow

    _add("다우존스", "^DJI")
    _add("S&P500", "^GSPC")
    _add("나스닥", "^IXIC")
    _add("필라델피아반도체", "^SOX")
    _add("니케이225", "^N225")
    _add("상하이종합", "000001.SS")
    _add("항셍지수", "^HSI")
    _add("VIX", "^VIX")
    _add("원달러환율", "KRW=X")
    _add("달러인덱스", "DX-Y.NYB")
    _add("WTI", "CL=F")
    _add("브렌트유", "BZ=F")
    _add("국제금", "GC=F")
    _add("미국채10년", "^TNX")

    m["기준시각"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    return m


# ============================================================
# DART 공시 (stock_app_main.py의 동일 함수와 로직 동일, @lru_cache로 실행-내 캐시)
# ============================================================
@lru_cache(maxsize=1)
def _get_dart_corp_code_map(dart_api_key: str) -> dict:
    try:
        resp = requests.get(
            "https://opendart.fss.or.kr/api/corpCode.xml",
            params={"crtfc_key": dart_api_key}, timeout=15,
        )
        resp.raise_for_status()
        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            xml_bytes = zf.read("CORPCODE.xml")
        root = ET.fromstring(xml_bytes)
        mapping = {}
        for item in root.iter("list"):
            stock_code = (item.findtext("stock_code") or "").strip()
            corp_code = (item.findtext("corp_code") or "").strip()
            if stock_code and corp_code:
                mapping[stock_code] = corp_code
        return mapping
    except Exception as e:
        log.warning("DART 고유번호 목록 다운로드 실패: %s", e)
        return {}


def get_dart_corp_code(stock_code: str, dart_api_key: str):
    return _get_dart_corp_code_map(dart_api_key).get(str(stock_code).strip())


@lru_cache(maxsize=64)
def get_dart_disclosures(corp_code: str, dart_api_key: str, days: int = 14, max_count: int = 5) -> tuple:
    try:
        today = datetime.now(KST)
        resp = requests.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={
                "crtfc_key": dart_api_key, "corp_code": corp_code,
                "bgn_de": (today - timedelta(days=days)).strftime("%Y%m%d"),
                "end_de": today.strftime("%Y%m%d"),
                "page_no": 1, "page_count": max_count,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") not in ("000",):
            return tuple()
        results = []
        for item in data.get("list", []):
            rcept_no = item.get("rcept_no", "")
            results.append((
                item.get("report_nm", ""), item.get("rcept_dt", ""),
                f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else "",
            ))
        return tuple(results)
    except Exception as e:
        log.warning("DART 공시 조회 실패 [%s]: %s", corp_code, e)
        return tuple()


# ============================================================
# 네이버 증권 비공식 API (stock_app_main.py와 동일 로직, @lru_cache로 실행-내 캐시)
# ============================================================
_NAVER_STOCK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://stock.naver.com/",
}


@lru_cache(maxsize=64)
def get_naver_news(item_code: str, max_count: int = 5) -> tuple:
    try:
        resp = requests.get(
            "https://stock.naver.com/api/domestic/detail/news",
            params={"itemCode": item_code, "page": 1, "pageSize": max_count},
            headers=_NAVER_STOCK_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for cluster in data.get("clusters", []):
            for it in cluster.get("items", []):
                dt_raw = it.get("datetime", "")
                날짜 = f"{dt_raw[:4]}-{dt_raw[4:6]}-{dt_raw[6:8]}" if len(dt_raw) == 12 else dt_raw
                office_id, article_id = it.get("officeId", ""), it.get("articleId", "")
                link = f"https://n.news.naver.com/mnews/article/{office_id}/{article_id}" if office_id and article_id else ""
                results.append((it.get("title", ""), it.get("officeName", ""), 날짜, link))
                if len(results) >= max_count:
                    return tuple(results)
        return tuple(results)
    except Exception as e:
        log.warning("네이버 증권 뉴스 조회 실패 [%s]: %s", item_code, e)
        return tuple()


@lru_cache(maxsize=64)
def get_naver_consensus(item_code: str) -> tuple:
    """(투자의견_참고라벨, 목표주가, 기준일) 튜플로 반환 (lru_cache는 dict를 캐시하기 까다로워 튜플 사용)."""
    try:
        resp = requests.get(
            f"https://stock.naver.com/api/domestic/detail/{item_code}/consensus",
            headers=_NAVER_STOCK_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        score = _safe_float_or_none(data.get("opinion"))
        label = ""
        if score is not None:
            if score >= 4.5:
                label = "적극매수"
            elif score >= 3.5:
                label = "매수"
            elif score >= 2.5:
                label = "중립"
            elif score >= 1.5:
                label = "비중축소"
            else:
                label = "매도"
        target = _safe_float_or_none(data.get("targetPrice"))
        return (label, target, data.get("date", ""))
    except Exception as e:
        log.warning("네이버 증권 컨센서스 조회 실패 [%s]: %s", item_code, e)
        return ("", None, "")


def get_daily_stock_report(code: str, name: str, dart_api_key: str) -> dict:
    """종목 하나의 공시+뉴스+컨센서스를 한 번에 모아 반환. lru_cache가 걸린 하위 함수들만
    실제 API를 부르므로, 여러 사용자가 같은 종목을 보유해도 이 실행 안에서는 한 번만 호출된다."""
    corp_code = get_dart_corp_code(code, dart_api_key)
    disclosures = get_dart_disclosures(corp_code, dart_api_key) if corp_code else tuple()
    news = get_naver_news(code)
    label, target, base_date = get_naver_consensus(code)
    return {
        "종목코드": code, "종목명": name,
        "공시": disclosures, "뉴스": news,
        "투자의견": label, "목표주가": target, "컨센서스_기준일": base_date,
    }


# ============================================================
# 이메일 HTML 조립
# ============================================================
_UP_COLOR, _DOWN_COLOR = "#e0635e", "#5b9bd8"


def _pct_html(pct) -> str:
    if pct is None:
        return ""
    color = _UP_COLOR if pct > 0 else _DOWN_COLOR if pct < 0 else "#8a8d96"
    arrow = "▲" if pct > 0 else "▼" if pct < 0 else "-"
    return f'<span style="color:{color};font-weight:600;">{arrow} {abs(pct):.2f}%</span>'


def build_email_html(display_name: str, market: dict, stock_reports: list[dict], report_date: str) -> str:
    def _market_row(key):
        v = market.get(key)
        if not v or v.get("값") is None:
            return ""
        return (
            f'<tr><td style="padding:4px 10px;color:#555;">{key}</td>'
            f'<td style="padding:4px 10px;text-align:right;font-weight:600;">{v["값"]:,.2f}</td>'
            f'<td style="padding:4px 10px;text-align:right;">{_pct_html(v.get("등락률"))}</td></tr>'
        )

    market_rows = "".join(
        _market_row(k) for k in
        ["코스피", "코스닥", "다우존스", "S&P500", "나스닥", "니케이225", "원달러환율", "WTI", "국제금"]
    )

    stock_blocks = []
    for r in stock_reports:
        news_html = "".join(
            f'<div style="padding:4px 0;font-size:13px;">'
            f'<a href="{link}" style="color:#333;text-decoration:none;">{title}</a>'
            f'<span style="color:#999;"> · {press}</span></div>'
            for title, press, date_, link in r["뉴스"][:3]
        ) or '<div style="font-size:13px;color:#999;">최근 뉴스 없음</div>'

        disc_html = "".join(
            f'<div style="padding:4px 0;font-size:13px;">'
            f'<a href="{link}" style="color:#333;text-decoration:none;">{title}</a>'
            f'<span style="color:#999;"> ({date_})</span></div>'
            for title, date_, link in r["공시"][:2]
        ) or '<div style="font-size:13px;color:#999;">최근 공시 없음</div>'

        consensus_html = (
            f'투자의견 <b>{r["투자의견"]}</b> · 목표주가 {r["목표주가"]:,.0f}원 ({r["컨센서스_기준일"]})'
            if r["투자의견"] and r["목표주가"] else '컨센서스 정보 없음'
        )

        stock_blocks.append(f"""
        <div style="border:1px solid #e5e5e5;border-radius:10px;padding:14px 16px;margin-bottom:10px;">
            <div style="font-weight:700;font-size:15px;margin-bottom:8px;">{r['종목명']} ({r['종목코드']})</div>
            <div style="font-size:13px;color:#555;margin-bottom:8px;">{consensus_html}</div>
            <div style="font-size:12px;color:#777;margin-bottom:2px;">📰 최근 뉴스</div>
            {news_html}
            <div style="font-size:12px;color:#777;margin:8px 0 2px;">📢 최근 공시</div>
            {disc_html}
        </div>""")

    stocks_html = "".join(stock_blocks) if stock_blocks else '<p style="color:#999;">보유 중인 종목이 없습니다.</p>'

    return f"""
    <div style="font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;max-width:600px;margin:0 auto;color:#222;">
        <h2 style="margin-bottom:4px;">📊 {report_date} 오늘의 시황 · 종목 리포트</h2>
        <p style="color:#888;font-size:13px;margin-top:0;">{display_name}님, 안녕하세요. 보유 종목 기준 자동 발송된 리포트입니다.</p>
        <table style="width:100%;border-collapse:collapse;background:#fafafa;border-radius:10px;margin-bottom:16px;">
            {market_rows}
        </table>
        <h3 style="font-size:16px;">보유 종목 리포트</h3>
        {stocks_html}
        <p style="color:#aaa;font-size:11px;margin-top:20px;">
            공시(DART)·뉴스·컨센서스는 참고용 정보이며, 투자 판단의 근거로 쓰기엔 부족할 수 있습니다.<br>
            문의: hwcho@me.com
        </p>
    </div>
    """


def send_email(smtp_conn, sender: str, to_addr: str, subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    smtp_conn.sendmail(sender, [to_addr], msg.as_string())


# ============================================================
# 사용자별 처리
# ============================================================
def process_one_user(
    row: dict, client_id: str, client_secret: str, secret_key: str,
    dart_api_key: str, market: dict, report_date: str,
    smtp_conn, sender_email: str,
) -> str:
    email = row.get("이메일", "")
    name = str(row.get("이름", "")).strip() or email.split("@")[0]
    spreadsheet_id = str(row.get("spreadsheet_id", "")).strip()
    token_enc = str(row.get("refresh_token_enc", "")).strip()

    if not spreadsheet_id:
        return f"{email}: 건너뜀 (개인 시트 없음)"
    if not token_enc:
        return f"{email}: 건너뜀 (저장된 refresh_token 없음 — 한 번 더 로그인 필요)"

    refresh_token = decrypt_refresh_token(token_enc, secret_key)
    if not refresh_token:
        return f"{email}: 실패 (refresh_token 복호화 실패)"

    try:
        credentials = build_credentials_from_refresh_token(refresh_token, client_id, client_secret)
        gc = gspread.authorize(credentials)
        spreadsheet = gc.open_by_key(spreadsheet_id)
        ws = spreadsheet.worksheet("거래이력")
        trade_df = pd.DataFrame(ws.get_all_records())
    except Exception as e:
        return f"{email}: 실패 (개인 시트 접근 실패 - {e})"

    held = get_held_stocks(trade_df)
    stock_reports = []
    for code, name_ in held:
        try:
            stock_reports.append(get_daily_stock_report(code, name_, dart_api_key))
        except Exception as e:
            log.warning("종목 리포트 생성 실패 [%s]: %s", code, e)

    html = build_email_html(name, market, stock_reports, report_date)
    subject = f"[통합자산관리] {report_date} 오늘의 시황·종목 리포트"

    try:
        send_email(smtp_conn, sender_email, email, subject, html)
    except Exception as e:
        return f"{email}: 실패 (메일 발송 실패 - {e})"

    return f"{email}: 성공 (보유 {len(held)}종목)"


def main():
    force = os.environ.get("FORCE_RUN", "").strip() == "1"
    now = datetime.now(KST)

    # 주말(토=5, 일=6)에는 국내·해외 시장 모두 휴장이라 전일과 내용이 사실상 같으므로
    # 기본적으로는 건너뛴다. 테스트나 특별한 이유로 주말에도 돌려보고 싶으면 FORCE_RUN=1.
    if now.weekday() >= 5 and not force:
        log.info("오늘(%s)은 주말이라 발송하지 않습니다. (FORCE_RUN=1이면 강제 실행 가능)", now.date())
        return

    report_date = now.strftime("%Y-%m-%d (%a)")

    service_account_json = json.loads(_env("GOOGLE_SERVICE_ACCOUNT_JSON"))
    accounts_spreadsheet_id = _env("ACCOUNTS_SPREADSHEET_ID")
    client_id = _env("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = _env("GOOGLE_OAUTH_CLIENT_SECRET")
    secret_key = _env("AUTH_SECRET_KEY")
    dart_api_key = _env("DART_API_KEY")
    sender_email = _env("GMAIL_SENDER_EMAIL")
    app_password = _env("GMAIL_APP_PASSWORD")

    sa_creds = ServiceAccountCredentials.from_service_account_info(
        service_account_json,
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"],
    )
    sa_client = gspread.authorize(sa_creds)
    accounts_sheet = sa_client.open_by_key(accounts_spreadsheet_id).worksheet("사용자계정")
    accounts = accounts_sheet.get_all_records()

    active_users = [r for r in accounts if str(r.get("상태", "")).strip() == "활성"]
    log.info("활성 사용자 %d명 대상 일일 리포트 발송 시작", len(active_users))

    log.info("시황 데이터 조회 중 (전체 사용자 공통, 1회만 조회)...")
    market = get_market_overview()

    results = []
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp_conn:
        smtp_conn.starttls()
        smtp_conn.login(sender_email, app_password)

        for row in active_users:
            try:
                result = process_one_user(
                    row, client_id, client_secret, secret_key, dart_api_key,
                    market, report_date, smtp_conn, sender_email,
                )
            except Exception as e:
                result = f"{row.get('이메일','?')}: 예외 발생 - {e}"
            log.info(result)
            results.append(result)
            time.sleep(1)  # Gmail 연속 발송 사이 최소 간격 (스팸 필터·연결 안정성 고려)

    fail_count = sum(1 for r in results if "실패" in r or "예외" in r)
    log.info("완료: 총 %d명 중 실패/예외 %d건", len(results), fail_count)
    if fail_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
