# ============================================================
# stock_app_main.py 에 추가할 코드
# 아래 두 블록을 각각 표시된 위치에 붙여넣으세요.
# ============================================================

import streamlit as st
import pandas as pd
import bcrypt

# ------------------------------------------------------------
# [블록 A] 관리자 메뉴
# 넣을 위치: 로그인 성공 후, 사이드바를 그리는 코드 부분
#           (기존 "관리자 전용 계정추가 패널"을 아래 함수로 교체/통합하세요)
#
# 전제: st.session_state["is_admin"] 값이 True/False로 이미 설정되어 있다고 가정합니다.
#      (Streamlit Secrets [admin] 아이디와 로그인 아이디를 비교해서 설정한 그 값입니다.
#       변수명이 다르다면 아래 IS_ADMIN 부분만 실제 변수명으로 바꿔주세요.)
# ------------------------------------------------------------

def render_admin_panel(accounts_ws):
    """
    accounts_ws: gspread로 연 '사용자계정' 시트 워크시트 객체
                 (계정관리 구글시트 > 사용자계정 탭)
    """
    st.sidebar.markdown("---")
    with st.sidebar.expander("🔧 관리자 메뉴", expanded=False):

        tab1, tab2, tab3 = st.tabs(["계정 관리", "사용자 현황", "시스템"])

        # ---------- 탭1: 계정 관리 ----------
        with tab1:
            st.caption("새 계정 추가")
            new_id = st.text_input("아이디", key="admin_new_id")
            new_pw = st.text_input("초기 비밀번호", type="password", key="admin_new_pw")
            new_name = st.text_input("이름", key="admin_new_name")
            new_sheet_id = st.text_input("연결할 spreadsheet_id", key="admin_new_sheet")

            if st.button("계정 추가", key="admin_add_btn"):
                if new_id and new_pw and new_name and new_sheet_id:
                    pw_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
                    accounts_ws.append_row([
                        new_id, pw_hash, new_name, new_sheet_id,
                        pd.Timestamp.now().strftime("%Y-%m-%d"), "활성"
                    ])
                    st.success(f"'{new_id}' 계정이 추가되었습니다. Streamlit 캐시를 새로고침하세요.")
                else:
                    st.warning("모든 항목을 입력해주세요.")

            st.markdown("---")
            st.caption("기존 계정 상태 변경")
            records = accounts_ws.get_all_records()
            df_acc = pd.DataFrame(records)

            if not df_acc.empty:
                target_id = st.selectbox("대상 계정", df_acc["아이디"].tolist(), key="admin_target_id")
                col1, col2 = st.columns(2)

                with col1:
                    new_status = st.selectbox("상태 변경", ["활성", "비활성"], key="admin_new_status")
                    if st.button("상태 적용", key="admin_status_btn"):
                        row_idx = df_acc.index[df_acc["아이디"] == target_id][0] + 2  # 헤더 고려 +2
                        # '상태' 컬럼 위치는 실제 시트 컬럼 순서에 맞춰 열 번호를 조정하세요.
                        status_col = df_acc.columns.get_loc("상태") + 1
                        accounts_ws.update_cell(row_idx, status_col, new_status)
                        st.success(f"'{target_id}' 계정 상태가 '{new_status}'로 변경되었습니다.")

                with col2:
                    reset_pw = st.text_input("새 비밀번호", type="password", key="admin_reset_pw")
                    if st.button("비밀번호 초기화", key="admin_reset_btn"):
                        if reset_pw:
                            row_idx = df_acc.index[df_acc["아이디"] == target_id][0] + 2
                            pw_col = df_acc.columns.get_loc("비밀번호_해시") + 1
                            new_hash = bcrypt.hashpw(reset_pw.encode(), bcrypt.gensalt()).decode()
                            accounts_ws.update_cell(row_idx, pw_col, new_hash)
                            st.success(f"'{target_id}' 비밀번호가 초기화되었습니다.")
                        else:
                            st.warning("새 비밀번호를 입력해주세요.")

        # ---------- 탭2: 사용자 현황 ----------
        with tab2:
            records = accounts_ws.get_all_records()
            df_acc = pd.DataFrame(records)
            if not df_acc.empty:
                display_cols = [c for c in ["아이디", "이름", "상태", "등록일"] if c in df_acc.columns]
                st.dataframe(df_acc[display_cols], use_container_width=True, hide_index=True)
                st.caption(f"총 {len(df_acc)}개 계정 · 활성 {sum(df_acc['상태']=='활성')}개")
            else:
                st.info("등록된 계정이 없습니다.")

        # ---------- 탭3: 시스템 ----------
        with tab3:
            st.caption("캐시된 데이터를 지우고 구글시트에서 다시 불러옵니다.")
            if st.button("🔄 전체 캐시 새로고침", key="admin_cache_clear"):
                st.cache_data.clear()
                st.success("캐시가 초기화되었습니다. 페이지를 새로고침 해주세요.")


# 호출 예시 (로그인 성공 + 관리자인 경우에만):
#
# if st.session_state.get("is_admin"):
#     render_admin_panel(accounts_ws)


# ------------------------------------------------------------
# [블록 B] 개발자 정보 (사이드바 캡션 + 모달 팝업)
# 넣을 위치: 사이드바를 그리는 코드 맨 아래
#           (탭 5개와는 별개로, 로그인 성공 후 항상 보이는 부분에 추가)
#
# 주의: st.dialog 는 Streamlit 1.31 이상에서 지원됩니다.
#      Streamlit Cloud는 자동으로 최신 버전을 쓰는 경우가 많지만,
#      만약 오류가 나면 requirements.txt에 streamlit>=1.31 을 추가하세요.
# ------------------------------------------------------------

@st.dialog("앱 정보")
def show_developer_info():
    st.markdown("**개발: 조현웅**")
    st.markdown("**버전: v1.0**")
    st.markdown("**문의: hwcho@me.com**")
    st.caption("버그 제보나 기능 제안은 위 이메일로 보내주세요.")

# 사이드바 맨 아래에 배치할 코드:
#
# st.sidebar.markdown("---")
# col_a, col_b = st.sidebar.columns([3, 1])
# with col_a:
#     st.caption("제작: 조현웅 · v1.0")
# with col_b:
#     if st.button("ℹ️", key="dev_info_btn", help="앱 정보"):
#         show_developer_info()
