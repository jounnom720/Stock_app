# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path

"""
원본 투자 분석 시스템 파일에 '서울시간(KST) 고정'을 안전하게 적용하는 패처

사용 목적
- 기존 전체 코드를 통째로 새로 쓰지 않고,
  실제 사용 중인 원본 파일 구조를 최대한 유지한 채 시간 표시만 정확히 수정
- Streamlit Cloud / 웹 서버의 UTC 기본 시간 때문에 조회 시간이 어긋나는 문제 해결

기본 대상 파일명
- stock_app_260410_technical_analysis_practical_v8_3_9g_refresh_text_final.py

실행 예시
1) 같은 폴더에 이 파일과 원본 파일을 둡니다.
2) 터미널에서 실행:
   python apply_kst_patch_to_original_stock_app.py

결과 파일
- stock_app_260410_technical_analysis_practical_v8_3_9g_refresh_text_final_kst_fixed.py
"""

TARGET_NAME = "stock_app_260410_technical_analysis_practical_v8_3_9g_refresh_text_final.py"
OUTPUT_NAME = "stock_app_260410_technical_analysis_practical_v8_3_9g_refresh_text_final_kst_fixed.py"

KST_HELPER_BLOCK = '''
# -----------------------------------
# 시간대 고정: 한국 서울 시간
# -----------------------------------
try:
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
except Exception:
    KST = None

def 서울현재시각():
    if KST is not None:
        return datetime.now(KST)
    return datetime.now()

def 서울현재시각ISO():
    return 서울현재시각().isoformat()

def 서울조회문자열(값=None, 포맷="조회 %Y-%m-%d %H:%M"):
    대상 = 값
    if 대상 is None:
        대상 = 서울현재시각()

    try:
        ts = pd.to_datetime(대상)
        if getattr(ts, "tzinfo", None) is None:
            if KST is not None:
                try:
                    ts = ts.tz_localize("Asia/Seoul")
                except Exception:
                    ts = ts
        else:
            if KST is not None:
                try:
                    ts = ts.tz_convert("Asia/Seoul")
                except Exception:
                    ts = ts
        return ts.strftime(포맷)
    except Exception:
        try:
            return pd.to_datetime(대상).strftime(포맷)
        except Exception:
            return str(대상)
'''.strip("\n")

OLD_HELPER_FUNC = r'''def 기준일시표시문자열\(기준일=None, 조회시각=None\):.*?return f"기준 \{기준문자\}"'''
NEW_HELPER_FUNC = '''def 기준일시표시문자열(기준일=None, 조회시각=None):
    기준문자 = "-"
    if 기준일 is not None and not pd.isna(기준일):
        try:
            기준문자 = pd.to_datetime(기준일).strftime("%Y-%m-%d")
        except Exception:
            기준문자 = str(기준일)
    조회문자 = ""
    if 조회시각 is not None and not pd.isna(조회시각):
        조회문자 = 서울조회문자열(조회시각, "%Y-%m-%d %H:%M")
    return f"기준 {기준문자} · 조회 {조회문자}" if 조회문자 else f"기준 {기준문자}"'''

def ensure_zoneinfo_import(text: str) -> str:
    if "from zoneinfo import ZoneInfo" in text:
        return text

    patterns = [
        r"from datetime import datetime, timedelta",
        r"from datetime import datetime,timedelta",
        r"from datetime import datetime",
    ]

    for pat in patterns:
        if re.search(pat, text):
            return re.sub(
                pat,
                lambda m: m.group(0) + "\nfrom zoneinfo import ZoneInfo",
                text,
                count=1,
            )

    return "from zoneinfo import ZoneInfo\n" + text

def inject_kst_helper_block(text: str) -> str:
    if "def 서울현재시각():" in text or "def 서울조회문자열(" in text:
        return text

    anchor = 'st.set_page_config(page_title="투자 분석 시스템", layout="wide")'
    if anchor in text:
        return text.replace(anchor, anchor + "\n\n" + KST_HELPER_BLOCK, 1)

    anchor2 = "st.set_page_config("
    if anchor2 in text:
        idx = text.find(anchor2)
        end_idx = text.find(")", idx)
        if end_idx != -1:
            end_idx += 1
            return text[:end_idx] + "\n\n" + KST_HELPER_BLOCK + text[end_idx:]

    return KST_HELPER_BLOCK + "\n\n" + text

def replace_manual_refresh_time_logic(text: str) -> str:
    replacements = [
        (
            'st.session_state["manual_price_refresh_ts_v1"] = datetime.now().isoformat()',
            'st.session_state["manual_price_refresh_ts_v1"] = 서울현재시각ISO()',
        ),
        (
            "st.session_state['manual_price_refresh_ts_v1'] = datetime.now().isoformat()",
            "st.session_state['manual_price_refresh_ts_v1'] = 서울현재시각ISO()",
        ),
        (
            'st.session_state["manual_price_refresh_ts_v1"] = pd.Timestamp.now().isoformat()',
            'st.session_state["manual_price_refresh_ts_v1"] = 서울현재시각ISO()',
        ),
        (
            "st.session_state['manual_price_refresh_ts_v1'] = pd.Timestamp.now().isoformat()",
            "st.session_state['manual_price_refresh_ts_v1'] = 서울현재시각ISO()",
        ),
        (
            '조회일시문자 = pd.to_datetime(st.session_state["manual_price_refresh_ts_v1"]).strftime("조회 %Y-%m-%d %H:%M")',
            '조회일시문자 = 서울조회문자열(st.session_state["manual_price_refresh_ts_v1"])',
        ),
        (
            "조회일시문자 = pd.to_datetime(st.session_state['manual_price_refresh_ts_v1']).strftime(\"조회 %Y-%m-%d %H:%M\")",
            "조회일시문자 = 서울조회문자열(st.session_state['manual_price_refresh_ts_v1'])",
        ),
    ]

    for old, new in replacements:
        text = text.replace(old, new)

    text = re.sub(r"datetime\.now\(\)\.isoformat\(\)", "서울현재시각ISO()", text)
    text = re.sub(r"pd\.Timestamp\.now\(\)\.isoformat\(\)", "서울현재시각ISO()", text)

    return text

def replace_display_helper(text: str) -> str:
    if "def 기준일시표시문자열(기준일=None, 조회시각=None):" in text:
        text = re.sub(OLD_HELPER_FUNC, NEW_HELPER_FUNC, text, flags=re.DOTALL, count=1)
    return text

def main() -> None:
    source = Path(TARGET_NAME)
    if not source.exists():
        raise FileNotFoundError(
            f"원본 파일을 찾지 못했습니다: {TARGET_NAME}\n"
            "이 패처 파일과 원본 전체 코드를 같은 폴더에 두고 다시 실행해 주세요."
        )

    original_text = source.read_text(encoding="utf-8")

    patched = original_text
    patched = ensure_zoneinfo_import(patched)
    patched = inject_kst_helper_block(patched)
    patched = replace_manual_refresh_time_logic(patched)
    patched = replace_display_helper(patched)

    output_path = Path(OUTPUT_NAME)
    output_path.write_text(patched, encoding="utf-8")

    print("패치 완료")
    print(f"원본 파일 : {source.resolve()}")
    print(f"결과 파일 : {output_path.resolve()}")
    print("")
    print("주요 반영 내용")
    print("1. datetime.now().isoformat() → 서울현재시각ISO()")
    print("2. 조회 문자열 생성 → 서울조회문자열()")
    print("3. 기준/조회 시간 표시 보조 함수도 KST 기준으로 통일")

if __name__ == "__main__":
    main()
