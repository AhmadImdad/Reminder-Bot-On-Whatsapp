import streamlit as st
import time

# --- Page Config
st.set_page_config(
    page_title="WhatsApp Reminder Bot",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Imports
import auth
from components import upcoming, history, add_reminder, statistics, settings

# --- Authentication hook
is_authenticated, authenticator = auth.authenticate()

if is_authenticated:
    # --- Sidebar
    with st.sidebar:
        st.title("🤖 Reminder Bot")
        st.write(f"Welcome, *{st.session_state['name']}*!")
        authenticator.logout('Logout', 'sidebar')
        
    # --- Quick Stats Header
    import database_queries
    stats = database_queries.get_reminder_stats()
    
    st.markdown("""
        <style>
            .metric-card {
                background-color: #f8f9fa;
                border: 1px solid #e9ecef;
                padding: 15px;
                border-radius: 8px;
                text-align: center;
                box-shadow: 0 4px 6px rgba(0,0,0,0.05);
            }
            .metric-value { font-size: 24px; font-weight: bold; color: #1f77b4; }
            .metric-label { font-size: 14px; color: #6c757d; }
        </style>
    """, unsafe_allow_html=True)
    
    col1, col2, col3, col4 = st.columns(4)
    with col1: st.markdown(f"<div class='metric-card'><div class='metric-label'>Total</div><div class='metric-value'>{stats.get('total', 0)}</div></div>", unsafe_allow_html=True)
    with col2: st.markdown(f"<div class='metric-card'><div class='metric-label'>Pending</div><div class='metric-value'>{stats.get('pending', 0)}</div></div>", unsafe_allow_html=True)
    with col3: st.markdown(f"<div class='metric-card'><div class='metric-label'>Success</div><div class='metric-value' style='color: #28a745;'>{stats.get('completed', 0)}</div></div>", unsafe_allow_html=True)
    with col4: st.markdown(f"<div class='metric-card'><div class='metric-label'>Failed</div><div class='metric-value' style='color: #dc3545;'>{stats.get('failed', 0)}</div></div>", unsafe_allow_html=True)
    
    st.write("") # Spacer
    
    # --- Main Tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📋 Upcoming Reminders", 
        "📜 Reminder History", 
        "➕ Add New Reminder", 
        "📊 Statistics & Analytics", 
        "⚙️ Settings"
    ])
    
    with tab1: upcoming.render()
    with tab2: history.render()
    with tab3: add_reminder.render()
    with tab4: statistics.render()
    with tab5: settings.render()
    
    # Auto-refresh logic (Soft timer using st.rerun)
    # Using a 60s sleep loop in a hidden placeholder would block execution.
    # To do real auto-refresh in streamlit, Streamlit Native st_autorefresh is best, 
    # but since it's an external library not in standard pip, we'll provide a manual refresh button instead 
    # and rely on user interactions.
    st.sidebar.divider()
    if st.sidebar.button("🔄 Manual Refresh Data"):
        st.rerun()

elif st.session_state.get("authentication_status") is False:
    st.error('Username/password is incorrect')
elif st.session_state.get("authentication_status") is None:
    st.warning('Please enter your username and password')
