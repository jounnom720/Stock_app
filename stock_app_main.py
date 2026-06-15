#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# v5.22.0-stable 패치 스크립트
# 사용법:
# 1) 이 파일을 stock_app_v5_21_5_ui_completion_fix.py 와 같은 폴더에 저장
# 2) python apply_v5_22_0_stable_patch.py 실행
# 3) stock_app_v5_22_0_stable.py 생성 확인

from __future__ import annotations

import py_compile
import re
import shutil
from pathlib import Path


INPUT_FILE = Path("stock_app_v5_21_5_ui_completion_fix.py")
OUTPUT_FILE = Path("stock_app_v5_22_0_stable.py")
BACKUP_FILE = Path("stock_app_v5_21_5_ui_completion_fix_backup_before_v5220.py")


def replace_version(text: str) -> str:
    text = re.sub(
        r'APP_VERSION\s*=\s*["\'][^"\']+["\']',
        'APP_VERSION = "v5.22.0-stable"',
        text,
        count=1,
    )
    return text.replace("v5.21.5-ui-completion-fix", "v5.22.0-stable")


def replace_undefined_trade_loader(text: str) -> str:
    return text.replace("거래이력불러오기()", "현재거래이력가져오기()")


def fix_title_and_footer_text(text: str) -> str:
    text = text.replace("📈 나의 포트폴리오 관리 시스템", "📈 자산관리 시스템")
    text = text.replace("나의 포트폴리오 관리 시스템", "자산관리 시스템")
    text = text.replace("hwvcho@me.com", "hwcho@me.com")
    return text


def ensure_asset_reason_candidate_init(text: str) -> str:
    old = '추천사유 = str(추천정보.get("추천사유", "")).strip()\n    이동후보 = 이동후보 or {}'
    new = (
        '추천사유 = str(추천정보.get("추천사유", "")).strip()\n'
        '    # v5.22.0: 이동후보가 None/빈값/비정상 타입이어도 안전하게 처리합니다.\n'
        '    이동후보 = 이동후보 if isinstance(이동후보, dict) else {}\n'
    )
    if old in text:
        text = text.replace(old, new, 1)

    call = "원금변화사유, 원금변화확인금액, 원금변화설명 = 자산변화사유입력UI(미리보기행, 이동후보=이동후보)"
    guarded_call = (
        "# v5.22.0: 이동후보 미정의 방지\n"
        "    이동후보 = 이동후보 if isinstance(이동후보, dict) else {}\n"
        f"    {call}"
    )
    if call in text and "v5.22.0: 이동후보 미정의 방지" not in text:
        text = text.replace(call, guarded_call, 1)
    return text


def add_compact_asset_move_helpers(text: str) -> str:
    if "def 자산이동짧은문구_v5220" in text:
        return text

    helper = '''
# ============================================================
# v5.22.0 자산변화추이 문장 축약 보조 함수
# ============================================================
def 자산이동짧은문구_v5220(거래구분="", 종목명="", 금액=0, 출처="예수금", 도착=""):
    try:
        구분 = str(거래구분 or "").strip()
        이름 = str(종목명 or "").strip() or "종목"
        금액값 = float(금액 or 0)
        금액문구 = f"{금액값 / 10000:,.0f}만원" if abs(금액값) >= 10000 else 원화정수포맷(금액값)

        if "매수" in 구분:
            제목 = f"{이름} 매수"
            경로 = f"{출처 or '예수금'} → 주식/ETF"
        elif "매도" in 구분:
            제목 = f"{이름} 매도"
            경로 = f"주식/ETF → {도착 or '예수금'}"
        else:
            제목 = f"{이름} 자산이동"
            경로 = f"{출처 or '이전 자산'} → {도착 or '변경 자산'}"

        return {"제목": 제목, "요약": 경로, "금액": 금액문구, "설명": f"{제목} · {금액문구}"}
    except Exception:
        return {"제목": "자산이동", "요약": "현금성자산 ↔ 주식/ETF", "금액": 원화정수포맷(금액 or 0), "설명": "자산이동"}


def 자산이동카드표시_v5220(거래구분="", 종목명="", 금액=0, 출처="예수금", 도착=""):
    try:
        정보 = 자산이동짧은문구_v5220(거래구분, 종목명, 금액, 출처, 도착)
        카드HTML = (
            '<div style="border:1px solid rgba(148,163,184,0.28);border-radius:14px;'
            'padding:0.9rem 1rem;margin:0.45rem 0;background:rgba(248,250,252,0.55);">'
            '<div style="font-size:0.86rem;color:#64748b;font-weight:600;">자산이동</div>'
            f'<div style="font-size:1.05rem;font-weight:700;margin-top:0.15rem;">{정보["제목"]}</div>'
            f'<div style="font-size:0.92rem;color:#475569;margin-top:0.1rem;">{정보["요약"]} · {정보["금액"]}</div>'
            '</div>'
        )
        st.markdown(카드HTML, unsafe_allow_html=True)
    except Exception as e:
        st.caption(f"자산이동 카드 표시 오류: {type(e).__name__}: {e}")
# ============================================================
# /v5.22.0 자산변화추이 문장 축약 보조 함수
# ============================================================
'''

    marker = "def 자산변화최근거래기반이동후보"
    if marker in text:
        return text.replace(marker, helper + "\n" + marker, 1)
    return text.rstrip() + "\n\n" + helper


def compact_existing_long_sentences(text: str) -> str:
    replacements = {
        "외부 입금·생활비 인출이 아니라 현금성자산과 주식/ETF 사이의 내부 이동입니다.": "현금성자산과 주식/ETF 사이의 내부 이동입니다.",
        "직전대비 원금 변화는 평가손익이 아니라 입금·인출·현금 사용·자산 재분류·입력 정정 때문에 생깁니다. 저장 전에 사유를 남기면 나중에 자산변화로그에서 원인을 확인할 수 있습니다.": "입금·인출·자산이동·입력정정을 구분해 저장하면 이후 원금 변화 원인을 확인하기 쉽습니다.",
        "거래이력·비주식자산이 바뀐 뒤 저장하면 직전 저장값과 자동 비교됩니다. 이번 버전은 계좌별·자산군별 현재 구성까지 함께 보여줍니다.": "거래이력·비주식자산 변경 후 저장하면 직전 값과 자동 비교됩니다.",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def patch_main_section_buttons(text: str) -> str:
    if "_버튼왼쪽여백, _버튼칸1, _버튼칸2, _버튼오른쪽여백" in text:
        return text

    old = '''버튼칸 = st.columns(len(섹터목록), gap="small")
for idx, 섹터명 in enumerate(섹터목록):
    with 버튼칸[idx]:
        if st.button(
            섹터명,
            key=f"main_section_btn_v5106d_{idx}",
            width="stretch",
            type="primary" if 선택섹터 == 섹터명 else "secondary",
        ):
            st.session_state[섹터선택키] = 섹터명
            st.rerun()
'''
    new = '''# v5.22.0: 상단 2개 버튼은 화면 전체를 채우지 않고 중앙에 보기 좋게 배치합니다.
_버튼왼쪽여백, _버튼칸1, _버튼칸2, _버튼오른쪽여백 = st.columns([1.7, 1.0, 1.0, 1.7], gap="medium")
버튼칸 = [_버튼칸1, _버튼칸2]
for idx, 섹터명 in enumerate(섹터목록):
    with 버튼칸[idx]:
        if st.button(
            섹터명,
            key=f"main_section_btn_v5106d_{idx}",
            width="stretch",
            type="primary" if 선택섹터 == 섹터명 else "secondary",
        ):
            st.session_state[섹터선택키] = 섹터명
            st.rerun()
'''
    return text.replace(old, new, 1)


def patch_footer(text: str) -> str:
    footer = (
        "\n# ============================================================\n"
        "# v5.22.0 footer\n"
        "# ============================================================\n"
        "st.markdown(\n"
        "    '''\n"
        "    <div style=\"margin-top:3rem;padding:1.15rem 0;border-top:1px solid rgba(148,163,184,0.25);text-align:center;color:#9ca3af;font-size:0.92rem;\">\n"
        "        © 자산관리 시스템<br>\n"
        "        개발자 조현웅&nbsp;&nbsp;|&nbsp;&nbsp;hwcho@me.com\n"
        "    </div>\n"
        "    ''',\n"
        "    unsafe_allow_html=True,\n"
        ")\n"
    )

    text = re.sub(
        r"# ============================================================\n# v5\.21\.5 footer\n# ============================================================\nst\.markdown\(\s*\"\"\".*?unsafe_allow_html=True,\s*\)\s*",
        footer,
        text,
        flags=re.DOTALL,
    )
    if "v5.22.0 footer" not in text:
        text = text.rstrip() + "\n\n" + footer
    return text


def main() -> None:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"기준 파일을 찾지 못했습니다: {INPUT_FILE}")

    source = INPUT_FILE.read_text(encoding="utf-8-sig")
    if not BACKUP_FILE.exists():
        shutil.copy2(INPUT_FILE, BACKUP_FILE)

    patched = source
    patched = replace_version(patched)
    patched = replace_undefined_trade_loader(patched)
    patched = fix_title_and_footer_text(patched)
    patched = ensure_asset_reason_candidate_init(patched)
    patched = add_compact_asset_move_helpers(patched)
    patched = compact_existing_long_sentences(patched)
    patched = patch_main_section_buttons(patched)
    patched = patch_footer(patched)

    OUTPUT_FILE.write_text(patched, encoding="utf-8")
    py_compile.compile(str(OUTPUT_FILE), doraise=True)

    print("v5.22.0-stable 패치 완료")
    print(f"기준 파일: {INPUT_FILE}")
    print(f"백업 파일: {BACKUP_FILE}")
    print(f"생성 파일: {OUTPUT_FILE}")
    print("문법 검사: Python compile OK")


if __name__ == "__main__":
    main()
