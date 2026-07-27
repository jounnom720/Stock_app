"""
월별 스냅샷 자동 저장 스크립트 (GitHub Actions 전용)
====================================================
매일 정해진 시각에 실행되지만, 실제로는 "오늘이 한국시간 기준 1일"일 때만 동작한다
(GitHub Actions의 cron은 '매월 1일'을 직접 지정할 수 없어, 매일 실행 + 내부에서 날짜 체크
방식을 쓴다). '사용자계정' 화이트리스트 시트를 서비스 계정으로 읽어 활성 사용자 목록을
가져오고, 각 사용자에게 저장된 리프레시 토큰으로 그 사람 대신 로그인해 개인 시트에서
거래이력·비주식자산·계좌간이체를 읽어 지난달 통합원금/통합평가금액을 계산한 뒤,
그 사람의 '월별자산스냅샷' 시트에 한 줄로 저장한다.

[중요 - 유지보수 시 주의사항]
아래 계산 로직(_replay_trade_ledger / calc_holdings / calc_realized_pnl /
calc_asset_summary / enrich_with_prices)은 stock_app_main.py에 있는 같은 이름의
함수들을 그대로 복사해 온 것이다. Streamlit이 설치되어 있지 않은 GitHub Actions
환경에서 stock_app_main.py를 직접 import할 수 없어(그 파일은 st.secrets 등 Streamlit
런타임에 의존) 부득이하게 복제했다. 따라서 앞으로 stock_app_main.py에서 이 계산
로직들을 수정하면, 이 파일의 동일한 함수도 반드시 함께 고쳐야 두 곳의 숫자가
어긋나지 않는다. (완전한 중복 제거를 원하면, 이 함수들을 별도의 순수 파이썬 공용
모듈로 뽑아내 두 파일이 그 모듈을 같이 import하도록 리팩토링하는 방법이 있다 —
다음 작업으로 미룸.)

필요한 GitHub Actions 저장소 시크릿(Settings > Secrets and variables > Actions):
  - GOOGLE_SERVICE_ACCOUNT_JSON : 서비스 계정 키 파일(JSON) 전체 내용
  - ACCOUNTS_SPREADSHEET_ID     : '사용자계정' 화이트리스트 시트의 스프레드시트 ID
  - GOOGLE_OAUTH_CLIENT_ID      : 앱의 OAuth 클라이언트 ID
  - GOOGLE_OAUTH_CLIENT_SECRET  : 앱의 OAuth 클라이언트 시크릿
  - AUTH_SECRET_KEY             : 앱의 [auth] secret_key와 반드시 동일한 값
                                  (다르면 저장된 refresh_token을 복호화하지 못한다)
"""

import os
import sys
import json
import base64
import hashlib
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import gspread
import pandas as pd
import yfinance as yf
from cryptography.fernet import Fernet, InvalidToken
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.oauth2.credentials import Credentials as UserOAuthCredentials
from google.auth.transport.requests import Request as GoogleAuthRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("monthly_snapshot_job")

KST = ZoneInfo("Asia/Seoul")

OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

# ============================================================
# 환경변수(GitHub Actions 시크릿) 로드
# ============================================================
def _env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"환경변수 {name} 가 설정되어 있지 않습니다.")
    return val


# ============================================================
# refresh_token 복호화 (stock_app_main.py의 _refresh_token_cipher/_decrypt_refresh_token과 동일 로직)
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
    """stock_app_main.py의 build_credentials_from_refresh_token과 동일한 로직."""
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
    "069500": {"ticker": "069500.KS"},
    "102110": {"ticker": "102110.KS"},
    "471990": {"ticker": "471990.KS"},
    "487240": {"ticker": "487240.KS"},
    "229200": {"ticker": "229200.KS"},
    "0148J0": {"ticker": "148J0.KS"},
    "292150": {"ticker": "292150.KS"},
    "005930": {"ticker": "005930.KS"},
    "000660": {"ticker": "000660.KS"},
    "278470": {"ticker": "278470.KS"},
    "009150": {"ticker": "009150.KS"},
    "005380": {"ticker": "005380.KS"},
    "042660": {"ticker": "042660.KS"},
    "071970": {"ticker": "071970.KS"},
    "034020": {"ticker": "034020.KS"},
}


def get_asset_ticker(code: str) -> str:
    code = str(code).strip()
    if not code:
        return ""
    meta = ASSET_MASTER.get(code)
    if meta:
        return meta["ticker"]
    return f"{code}.KS"


def get_current_price(code: str, prices: dict):
    ticker = get_asset_ticker(code)
    if not ticker:
        return None
    return prices.get(ticker)


def _safe_num(val, default=0.0):
    try:
        s = str(val).strip().replace(",", "")
        if s in ("", "-", "nan", "None"):
            return default
        return float(s)
    except (ValueError, TypeError):
        return default


def get_prices(tickers: list) -> dict:
    """yfinance로 현재가 조회 (stock_app_main.py의 get_prices와 동일 로직, 캐시 데코레이터만 제외)."""
    if not tickers:
        return {}
    prices = {}
    try:
        ticker_str = " ".join(tickers)
        data = yf.download(ticker_str, period="5d", progress=False, auto_adjust=True, threads=False)
        if "Close" in data.columns:
            close = data["Close"].dropna(how="all")
            if not close.empty:
                latest = close.iloc[-1]
                if hasattr(latest, "items"):
                    for t, p in latest.items():
                        if pd.notna(p):
                            prices[t] = float(p)
                elif len(tickers) == 1 and pd.notna(latest):
                    prices[tickers[0]] = float(latest)
    except Exception as e:
        log.warning("일괄 시세 조회 실패: %s", e)

    missing = [t for t in tickers if t not in prices]
    for t in missing:
        try:
            hist = yf.Ticker(t).history(period="5d")
            if not hist.empty:
                prices[t] = float(hist["Close"].dropna().iloc[-1])
        except Exception as e:
            log.warning("개별 시세 조회 실패 [%s]: %s", t, e)
    return prices


# ============================================================
# 보유/손익 계산 (stock_app_main.py와 동일 로직 복제 — 상단 주의사항 참고)
# ============================================================
def _replay_trade_ledger(trade_df: pd.DataFrame):
    if trade_df.empty:
        return [], {}
    df = trade_df.copy()
    df["_거래일자_dt"] = pd.to_datetime(df["거래일자"], errors="coerce")
    df = df.sort_values("_거래일자_dt").reset_index(drop=True)

    qty_held, avg_cost, names, sell_events = {}, {}, {}, []
    for _, row in df.iterrows():
        code = str(row.get("종목코드", "")).strip()
        name = str(row.get("종목명", "")).strip()
        account = str(row.get("운용사", "")).strip()
        qty = int(_safe_num(row.get("거래수량", 0)))
        price = _safe_num(row.get("거래단가", 0))
        구분 = str(row.get("거래구분", "")).strip()
        date_ = row["_거래일자_dt"]
        key = (account, code)
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
            effective_qty = min(qty, prev_qty) if prev_qty > 0 else 0
            sell_events.append({
                "계좌": account, "종목코드": code, "종목명": name,
                "매도수량": qty, "매도단가": price, "평균매입단가": prev_avg,
                "effective_qty": effective_qty,
            })
            qty_held[key] = max(0, prev_qty - qty)

    final_state = {}
    for key, qty in qty_held.items():
        if qty > 0:
            final_state[key] = {
                "종목명": names.get(key, ""), "보유수량": qty, "평균단가": avg_cost.get(key, 0.0),
            }
    return sell_events, final_state


def calc_holdings(trade_df: pd.DataFrame) -> pd.DataFrame:
    _, final_state = _replay_trade_ledger(trade_df)
    rows = []
    for (account, code), h in final_state.items():
        avg, qty = h["평균단가"], h["보유수량"]
        rows.append({
            "종목코드": code, "종목명": h["종목명"], "계좌": account,
            "보유수량": qty, "평균단가": round(avg), "매입금액": round(avg * qty),
        })
    return pd.DataFrame(rows)


def calc_realized_pnl(trade_df: pd.DataFrame) -> pd.DataFrame:
    sell_events, _ = _replay_trade_ledger(trade_df)
    rows = []
    for ev in sell_events:
        매도금액 = ev["effective_qty"] * ev["매도단가"]
        매입금액 = ev["effective_qty"] * ev["평균매입단가"]
        rows.append({"실현손익": round(매도금액 - 매입금액)})
    return pd.DataFrame(rows)


def enrich_with_prices(holdings_df: pd.DataFrame, prices: dict) -> pd.DataFrame:
    if holdings_df.empty:
        return holdings_df
    df = holdings_df.copy()
    df["현재가"] = df["종목코드"].apply(lambda c: get_current_price(c, prices))

    def _is_valid(v):
        if v is None:
            return False
        try:
            return not pd.isna(float(v))
        except Exception:
            return False

    def _calc_eval(r):
        if _is_valid(r["현재가"]):
            return round(float(r["현재가"]) * int(r["보유수량"]))
        return int(r["매입금액"])

    df["평가금액"] = df.apply(_calc_eval, axis=1)
    return df


def calc_asset_summary(holdings_df, nonstock_df, trade_df=None, transfer_df=None):
    """total_cost, total_eval만 필요하므로 stock_app_main.py 버전에서 그 부분만 가져온 축약판."""
    stock_eval = int(holdings_df["평가금액"].sum()) if not holdings_df.empty else 0
    stock_cost = int(holdings_df["매입금액"].sum()) if not holdings_df.empty else 0

    tdf_eval = tdf_cost = 0
    if not nonstock_df.empty:
        for _, row in nonstock_df.iterrows():
            eva = _safe_num(row.get("평가금액", 0))
            pri = _safe_num(row.get("원금", 0))
            if str(row.get("자산군", "")) in ("TDF", "펀드", "채권"):
                tdf_eval += eva
                tdf_cost += pri

    cash_eval = 0
    if not nonstock_df.empty:
        cash_rows = nonstock_df[nonstock_df["자산군"] == "현금성자산"]
        cash_eval = int(cash_rows["평가금액"].apply(_safe_num).sum())

    total_eval = stock_eval + tdf_eval + cash_eval
    total_cost = stock_cost + tdf_cost + cash_eval
    return total_cost, total_eval


# ============================================================
# 월별자산스냅샷 저장 (stock_app_main.py의 save_monthly_snapshot과 동일 로직)
# ============================================================
def save_monthly_snapshot(spreadsheet, yearmonth: str, principal, eval_amount) -> tuple[bool, str]:
    try:
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
        ym_col = header.index("년월")
        cost_col = header.index("통합원금")
        eval_col = header.index("통합평가")
        time_col = header.index("저장시각") if "저장시각" in header else None
        pnl_col = header.index("통합손익") if "통합손익" in header else None
        pct_col = header.index("통합수익률") if "통합수익률" in header else None

        principal_int, eval_int = int(principal), int(eval_amount)
        pnl_val = eval_int - principal_int
        pct_val = round(pnl_val / principal_int * 100, 2) if principal_int else 0
        saved_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

        target_row = None
        for i, row in enumerate(records[1:], start=2):
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
            return True, f"{yearmonth} 스냅샷 값을 갱신했습니다."

        new_row = [""] * len(header)
        new_row[ym_col], new_row[cost_col], new_row[eval_col] = yearmonth, principal_int, eval_int
        if time_col is not None:
            new_row[time_col] = saved_at
        if pnl_col is not None:
            new_row[pnl_col] = pnl_val
        if pct_col is not None:
            new_row[pct_col] = pct_val
        ws.append_row(new_row)
        return True, f"{yearmonth} 스냅샷을 새로 추가했습니다."
    except Exception as e:
        return False, f"저장 실패: {e}"


def load_df(spreadsheet, sheet_name: str) -> pd.DataFrame:
    try:
        ws = spreadsheet.worksheet(sheet_name)
        return pd.DataFrame(ws.get_all_records())
    except gspread.exceptions.WorksheetNotFound:
        return pd.DataFrame()


# ============================================================
# 메인 로직
# ============================================================
def process_one_user(row: dict, client_id: str, client_secret: str, secret_key: str, yearmonth: str) -> str:
    email = row.get("이메일", "")
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
    except Exception as e:
        return f"{email}: 실패 (개인 시트 접근 실패 - {e})"

    trade_df = load_df(spreadsheet, "거래이력")
    nonstock_df = load_df(spreadsheet, "비주식자산")
    transfer_df = load_df(spreadsheet, "계좌간이체")

    holdings_df = calc_holdings(trade_df)
    tickers = sorted({get_asset_ticker(c) for c in holdings_df["종목코드"]}) if not holdings_df.empty else []
    prices = get_prices(tickers)
    holdings_df = enrich_with_prices(holdings_df, prices)

    total_cost, total_eval = calc_asset_summary(holdings_df, nonstock_df, trade_df, transfer_df)
    ok, msg = save_monthly_snapshot(spreadsheet, yearmonth, total_cost, total_eval)
    return f"{email}: {'성공' if ok else '실패'} - {msg}"


def main():
    force = os.environ.get("FORCE_RUN", "").strip() == "1"
    now = datetime.now(KST)
    if now.day != 1 and not force:
        log.info("오늘(%s)은 1일이 아니라 실행하지 않습니다. (FORCE_RUN=1이면 강제 실행 가능)", now.date())
        return

    # 스냅샷을 남길 대상 월 = 지난달 (1일 00시대에 실행되므로, 방금 끝난 달을 마감 기록으로 남김)
    yearmonth = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    log.info("대상 월: %s", yearmonth)

    # base64로 인코딩된 값을 기대한다 (사람이 JSON 파일을 직접 복사/붙여넣기 하면
    # private_key 안의 '\n' 이스케이프가 실제 줄바꿈으로 깨지는 사고가 반복되어,
    # 아예 복사 실수가 불가능한 base64 한 줄 문자열 방식으로 바꿨다).
    raw_b64 = _env("GOOGLE_SERVICE_ACCOUNT_JSON")

    # 붙여넣기 과정에서 섞여 들어올 수 있는 공백/줄바꿈/탭을 전부 제거 (base64 자체에는
    # 원래 이런 문자가 없어야 하므로, 있다면 실수로 섞인 것이니 제거하고 진행한다).
    cleaned_b64 = "".join(raw_b64.split())
    if cleaned_b64 != raw_b64:
        log.warning(
            "[진단] GOOGLE_SERVICE_ACCOUNT_JSON 값에 공백/줄바꿈이 섞여있어 제거했습니다. "
            "(원래 길이=%d → 정리 후 길이=%d)", len(raw_b64), len(cleaned_b64),
        )

    # base64 알파벳(A-Z a-z 0-9 + / =)이 아닌 문자가 섞여 있으면 여기서 바로 명확히 알려준다.
    import re as _re
    invalid_chars = sorted(set(_re.sub(r"[A-Za-z0-9+/=]", "", cleaned_b64)))
    if invalid_chars:
        raise RuntimeError(
            f"GOOGLE_SERVICE_ACCOUNT_JSON에 base64가 아닌 문자가 섞여 있습니다: {invalid_chars!r} "
            f"(시크릿 입력창에 이전 값이 남아있는 상태로 이어 붙여진 것일 수 있습니다 — "
            f"입력창을 전체 삭제한 뒤 다시 붙여넣어보세요)"
        )

    try:
        raw_sa_json = base64.b64decode(cleaned_b64, validate=True).decode("utf-8")
    except Exception as e:
        raise RuntimeError(
            f"GOOGLE_SERVICE_ACCOUNT_JSON base64 디코딩 실패: {e} "
            f"(base64로 인코딩한 값을 넣었는지, 값이 잘리지 않았는지 확인하세요)"
        )
    log.info(
        "[진단] 디코딩된 JSON 길이=%d, 시작=%r, 끝=%r",
        len(raw_sa_json), raw_sa_json[:15], raw_sa_json[-15:],
    )
    service_account_json = json.loads(raw_sa_json)
    accounts_spreadsheet_id = _env("ACCOUNTS_SPREADSHEET_ID")
    client_id = _env("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = _env("GOOGLE_OAUTH_CLIENT_SECRET")
    secret_key = _env("AUTH_SECRET_KEY").strip()
    log.info("[진단] AUTH_SECRET_KEY 길이=%d (Streamlit의 [auth] secret_key 길이와 같아야 함)", len(secret_key))

    sa_creds = ServiceAccountCredentials.from_service_account_info(
        service_account_json,
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"],
    )
    sa_client = gspread.authorize(sa_creds)
    accounts_sheet = sa_client.open_by_key(accounts_spreadsheet_id).worksheet("사용자계정")
    accounts = accounts_sheet.get_all_records()

    active_users = [r for r in accounts if str(r.get("상태", "")).strip() == "활성"]
    log.info("활성 사용자 %d명 중 스냅샷 처리 시작", len(active_users))

    results = []
    for row in active_users:
        try:
            result = process_one_user(row, client_id, client_secret, secret_key, yearmonth)
        except Exception as e:
            result = f"{row.get('이메일','?')}: 예외 발생 - {e}"
        log.info(result)
        results.append(result)

    fail_count = sum(1 for r in results if "실패" in r or "예외" in r)
    log.info("완료: 총 %d명 중 실패/예외 %d건", len(results), fail_count)
    if fail_count:
        sys.exit(1)  # GitHub Actions에서 실패로 표시되어 알림을 받을 수 있도록


if __name__ == "__main__":
    main()
