"""
DreamBaseball - 초등학교 6학년 야구선수 꿈나무를 위한 성장 기록 앱
=================================================================
버전: v0.1 (기본 뼈대 + Google Sheets 6개 탭 초기화)

[구조 설명 - 비개발자를 위한 안내]
Streamlit 앱은 위에서 아래로 코드를 실행하는 구조입니다.
이 파일(main.py)이 "진입점"이고, 사이드바에서 메뉴를 선택하면
각 화면에 해당하는 함수를 호출하는 방식(if/elif)으로 페이지를 전환합니다.
(추후 화면이 많아지면 st.Page / st.navigation 방식으로 리팩터링 가능)
"""

import streamlit as st
from utils.sheets import initialize_sheets

# ------------------------------------------------------------
# 기본 설정
# ------------------------------------------------------------
st.set_page_config(
    page_title="DreamBaseball ⚾",
    page_icon="⚾",
    layout="centered",
)

# secrets.toml에 저장해둔 이 프로젝트 전용 구글시트 ID
# (.streamlit/secrets.toml 예시는 README.md 참고)
SPREADSHEET_ID = st.secrets.get("dreambaseball_spreadsheet_id", "")


# ------------------------------------------------------------
# 화면(페이지)별 함수 - v0.1에서는 자리만 잡아두는 뼈대 상태
# 다음 버전(v0.2~)에서 각 함수 내부를 실제 기능으로 채워나갑니다.
# ------------------------------------------------------------

def page_home():
    st.title("⚾ DreamBaseball")
    st.caption("노력은 재능을 이긴다")
    st.markdown("### 오늘도 한 걸음!")

    col1, col2, col3 = st.columns(3)
    col1.metric("오늘 달성률", "- %")
    col2.metric("레벨", "LV -")
    col3.metric("연속 성공", "- 일")

    st.info("v0.2에서 실제 데이터가 연결될 예정입니다.")
    st.button("오늘 시작하기 ▶", use_container_width=True, type="primary")


def page_today_mission():
    st.title("📋 오늘의 미션")
    st.write("(v0.3에서 만다라트 목표를 불러와 체크리스트로 표시합니다)")


def page_mandalart():
    st.title("🗂️ 만다라트 등록/수정")
    st.write("(v0.2에서 81칸 목표 등록 화면이 만들어집니다)")


def page_growth_chart():
    st.title("📈 성장 그래프")
    st.write("(v0.6에서 Plotly 그래프가 연결됩니다)")


def page_level_badge():
    st.title("🏅 레벨 & 배지")
    st.write("(v0.4~v0.5에서 경험치·배지 시스템이 추가됩니다)")


def page_parent_mode():
    st.title("👨‍👩‍👦 부모 모드")
    pw = st.text_input("비밀번호", type="password")
    if pw:
        if pw == st.secrets.get("parent_password", ""):
            st.success("인증되었습니다. (v0.7에서 메모/통계 기능이 추가됩니다)")
        else:
            st.error("비밀번호가 올바르지 않습니다.")


def page_admin_setup():
    """
    관리자 전용: Google Sheets 6개 탭을 처음 한 번 생성하는 화면.
    실제 서비스 오픈 후에는 사이드바 메뉴에서 숨겨도 됩니다.
    """
    st.title("🔧 초기 설정 (관리자)")

    if not SPREADSHEET_ID:
        st.error(
            "secrets.toml에 dreambaseball_spreadsheet_id가 설정되어 있지 않습니다."
        )
        return

    st.write(f"연결된 스프레드시트 ID: `{SPREADSHEET_ID}`")

    if st.button("Google Sheets 6개 탭 생성/확인하기"):
        with st.spinner("시트를 확인하고 있습니다..."):
            try:
                result = initialize_sheets(SPREADSHEET_ID)
                for name, status in result.items():
                    if status == "생성됨":
                        st.success(f"'{name}' 탭 생성 완료")
                    else:
                        st.info(f"'{name}' 탭은 이미 존재합니다")
            except Exception as e:
                st.error(f"오류가 발생했습니다: {e}")


# ------------------------------------------------------------
# 사이드바 메뉴 - 화면 전환
# ------------------------------------------------------------
PAGES = {
    "🏠 메인 화면": page_home,
    "📋 오늘의 미션": page_today_mission,
    "🗂️ 만다라트 등록": page_mandalart,
    "📈 성장 그래프": page_growth_chart,
    "🏅 레벨/배지": page_level_badge,
    "👨‍👩‍👦 부모 모드": page_parent_mode,
    "🔧 초기 설정(관리자)": page_admin_setup,
}

st.sidebar.title("⚾ DreamBaseball")
choice = st.sidebar.radio("메뉴", list(PAGES.keys()))
st.sidebar.caption("v0.1 · 기본 뼈대 단계")

PAGES[choice]()
