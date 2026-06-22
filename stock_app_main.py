# -*- coding: utf-8 -*-
# v5.26.5 최근자산변화 엔진 단일화 패치 생성기
#
# 사용법:
# 1) 이 파일을 stock_app_v5_26_4_recent_dedup_ledger_align.py 와 같은 폴더에 저장
# 2) 실행: python make_v5_26_5_recent_engine_unified.py
# 3) 생성: stock_app_v5_26_5_recent_engine_unified.py

from pathlib import Path
import re

SRC = Path("stock_app_v5_26_4_recent_dedup_ledger_align.py")
DST = Path("stock_app_v5_26_5_recent_engine_unified.py")

if not SRC.exists():
    raise FileNotFoundError(f"원본 파일을 찾을 수 없습니다: {SRC}")

text = SRC.read_text(encoding="utf-8")

text = re.sub(
    r'APP_VERSION\s*=\s*"[^"]*"',
    'APP_VERSION = "v5.26.5-recent-engine-unified"',
    text,
    count=1,
)

pattern_52221_dedup = re.compile(
    r"\n\s*df\s*=\s*df\.sort_values\(\s*\['_date_sort_v52221','_src_rank_v52221','금액'\]\s*,\s*ascending=\[False,True,False\]\s*\)\.drop_duplicates\('_key_v52221',\s*keep='first'\)\s*",
    re.DOTALL,
)

text, n1 = pattern_52221_dedup.subn(
    "\n        # v5.26.5: 동일일자·동일종목 복수거래 보존을 위해 drop_duplicates('_key_v52221') 제거\n",
    text,
)

text = text.replace(
    "df = df.drop(columns=['_date_sort_v52221','_src_rank_v52221','_key_v52221'], errors='ignore')",
    "df = df.drop(columns=['_date_sort_v52221','_src_rank_v52221','_key_v52221'], errors='ignore')  # v5.26.5: 정렬 임시열만 제거"
)

text = text.replace("원금부분", "원금")
text = text.replace("수익손실부분", "실현손익")
text = text.replace("수익/손실부분", "실현손익")
text = text.replace("수익·손실부분", "실현손익")

final_patch = r"""

# ============================================================
# v5.26.5 최근자산변화 엔진 단일화 최종 패치
# ------------------------------------------------------------
# - _key_v52221 + drop_duplicates() 후처리 레이어 제거
# - 동일 날짜·동일 종목·복수 매도 거래를 각각 독립 거래로 보존
# - 최근자산변화는 v5.23.5 계열 전체 원장 병합/정렬 엔진만 사용
# ============================================================

APP_VERSION = "v5.26.5-recent-engine-unified"

try:
    _최근자산변화표시_v5265_base = (
        _최근자산변화표시_v5235_base
        if "_최근자산변화표시_v5235_base" in globals()
        else 최근자산변화표시_v5224
    )
except Exception:
    _최근자산변화표시_v5265_base = 최근자산변화표시_v5224


def _v5265_recent_sort_only(df):
    try:
        out = pd.DataFrame(df).copy()
        if out.empty:
            return out

        if "날짜" in out.columns:
            out["_sort_date_v5265"] = pd.to_datetime(out["날짜"], errors="coerce")
        elif "거래일자" in out.columns:
            out["_sort_date_v5265"] = pd.to_datetime(out["거래일자"], errors="coerce")
        else:
            out["_sort_date_v5265"] = pd.NaT

        if "_ledger_order_v5239" not in out.columns:
            out["_ledger_order_v5239"] = range(len(out))

        out = out.sort_values(
            ["_sort_date_v5265", "_ledger_order_v5239"],
            ascending=[False, True],
            kind="mergesort",
        )

        return out.drop(columns=["_sort_date_v5265"], errors="ignore").reset_index(drop=True)
    except Exception:
        return df


def 최근자산변화표시_v5224(이동df, 최대표시=80):
    try:
        df = _v5235_merge_and_sort_ledger(이동df)
    except Exception:
        df = pd.DataFrame(이동df).copy() if 이동df is not None else pd.DataFrame()

    df = _v5265_recent_sort_only(df)
    return _최근자산변화표시_v5265_base(df, 최대표시=max(최대표시, 80))


최근자산변화표시_v5223 = 최근자산변화표시_v5224
최근자산변화표시_v5226 = 최근자산변화표시_v5224


def 최근자산변화카드표시(거래df, 비주식자산df=None, 최대표시=80):
    try:
        이동df = _v5235_build_full_movement_base(거래df, 비주식자산df, 최근일수=3650)
    except Exception:
        try:
            이동df = 자산이동목록통합_v5225(거래df, 비주식자산df, 최근일수=3650)
        except Exception:
            이동df = pd.DataFrame()

    try:
        이동df = _v5235_merge_and_sort_ledger(이동df)
    except Exception:
        pass

    이동df = _v5265_recent_sort_only(이동df)

    try:
        확인문자열 = 이동df.astype(str).agg(" ".join, axis=1)
        후보 = 확인문자열[
            확인문자열.str.contains("2026-05-15", na=False)
            & 확인문자열.str.contains("KODEX AI반도체핵심장비", na=False)
            & 확인문자열.str.replace(",", "", regex=False).str.contains("18453", na=False)
        ]
        st.session_state["v5265_kodex_ai_3share_sell_found"] = int(len(후보))
        st.session_state["v5265_recent_rows"] = int(len(이동df))

        if "실현손익" in 이동df.columns:
            st.session_state["v5265_recent_realized_sum"] = int(
                pd.to_numeric(이동df["실현손익"], errors="coerce").fillna(0).sum()
            )
        elif "수익손실부분" in 이동df.columns:
            st.session_state["v5265_recent_realized_sum"] = int(
                pd.to_numeric(이동df["수익손실부분"], errors="coerce").fillna(0).sum()
            )
    except Exception:
        pass

    return 최근자산변화표시_v5224(이동df, 최대표시=max(최대표시, 80))


# ============================================================
# end v5.26.5 recent engine unified patch
# ============================================================
"""

if "v5.26.5 최근자산변화 엔진 단일화 최종 패치" not in text:
    text = text.rstrip() + "\n\n" + final_patch

DST.write_text(text, encoding="utf-8")

print("생성 완료:", DST)
print("제거된 drop_duplicates('_key_v52221') 라인 수:", n1)
print("다음 확인값:")
print("- 최근자산변화 거래건수: 54건")
print("- 최근자산변화 실현손익: 8,726,021원")
print("- 2026-05-15 KODEX AI반도체핵심장비 3주 매도 +18,453원 표시")
