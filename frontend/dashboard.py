import streamlit as st
import time

# --- Page Config
st.set_page_config(
    page_title="WhatsApp Reminder Bot",
    page_icon="🤖", layout="wide",
    initial_sidebar_state="expanded"
)

# --- Imports
import auth
from components import upcoming, history, add_reminder, statistics, settings
# Import new tasks components
from components import tasks_active, tasks_history, add_task
# Import idea store component
from components import ideas
# Import notes store component
from components import notes
# Import resource store component
from components import resources
# Import dump store component
from components import dumps

# --- Authentication hook
is_authenticated, authenticator = auth.authenticate()

if is_authenticated:
    # --- Sidebar
    with st.sidebar:
        st.title("🤖 Reminder Bot")
        st.write(f"Welcome, *{st.session_state['name']}*!")
        
        # --- Navigation ---
        st.divider()
        st.subheader("Navigation")
        page = st.radio("Go to", [
            "⏰ Reminders", "📝 Tasks", "💡 Ideas", "📓 Notes", 
            "🔗 Resources", "🗑️ Dumps", "📊 Analytics", "⚙️ Settings"
        ])
        st.divider()
        
        authenticator.logout('Logout', 'sidebar')
        
        if st.button("🔄 Manual Refresh Data"):
            st.rerun()
        
    # --- Quick Stats Header
    import database_queries
    
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
    
    if page == "⏰ Reminders":
        stats = database_queries.get_reminder_stats()
        col1, col2, col3, col4 = st.columns(4)
        with col1: st.markdown(f"<div class='metric-card'><div class='metric-label'>Total Reminders</div><div class='metric-value'>{stats.get('total', 0)}</div></div>", unsafe_allow_html=True)
        with col2: st.markdown(f"<div class='metric-card'><div class='metric-label'>Pending</div><div class='metric-value'>{stats.get('pending', 0)}</div></div>", unsafe_allow_html=True)
        with col3: st.markdown(f"<div class='metric-card'><div class='metric-label'>Success</div><div class='metric-value' style='color: #28a745;'>{stats.get('completed', 0)}</div></div>", unsafe_allow_html=True)
        with col4: st.markdown(f"<div class='metric-card'><div class='metric-label'>Failed</div><div class='metric-value' style='color: #dc3545;'>{stats.get('failed', 0)}</div></div>", unsafe_allow_html=True)
    
    elif page == "📝 Tasks":
        stats = database_queries.get_task_stats()
        col1, col2, col3 = st.columns(3)
        with col1: st.markdown(f"<div class='metric-card'><div class='metric-label'>Total Tasks</div><div class='metric-value'>{stats.get('total', 0)}</div></div>", unsafe_allow_html=True)
        with col2: st.markdown(f"<div class='metric-card'><div class='metric-label'>Active (Pending)</div><div class='metric-value'>{stats.get('pending', 0)}</div></div>", unsafe_allow_html=True)
        with col3: st.markdown(f"<div class='metric-card'><div class='metric-label'>Completed</div><div class='metric-value' style='color: #28a745;'>{stats.get('completed', 0)}</div></div>", unsafe_allow_html=True)

    elif page == "💡 Ideas":
        idea_stats = database_queries.get_idea_stats()
        col1, col2 = st.columns(2)
        with col1: st.markdown(f"<div class='metric-card'><div class='metric-label'>Total Ideas</div><div class='metric-value' style='color: #f7a800;'>{idea_stats.get('total', 0)}</div></div>", unsafe_allow_html=True)
        with col2: st.markdown(f"<div class='metric-card'><div class='metric-label'>With Media</div><div class='metric-value' style='color: #6f42c1;'>{idea_stats.get('with_media', 0)}</div></div>", unsafe_allow_html=True)

    elif page == "📓 Notes":
        note_stats = database_queries.get_note_stats()
        col1, col2 = st.columns(2)
        with col1: st.markdown(f"<div class='metric-card'><div class='metric-label'>Total Notes</div><div class='metric-value' style='color: #007bff;'>{note_stats.get('total', 0)}</div></div>", unsafe_allow_html=True)
        with col2: st.markdown(f"<div class='metric-card'><div class='metric-label'>With Media</div><div class='metric-value' style='color: #e83e8c;'>{note_stats.get('with_media', 0)}</div></div>", unsafe_allow_html=True)

    elif page == "🔗 Resources":
        res_stats = database_queries.get_resource_stats()
        col1, col2 = st.columns(2)
        with col1: st.markdown(f"<div class='metric-card'><div class='metric-label'>Total Resources</div><div class='metric-value' style='color: #17a2b8;'>{res_stats.get('total', 0)}</div></div>", unsafe_allow_html=True)
        with col2: st.markdown(f"<div class='metric-card'><div class='metric-label'>With Media</div><div class='metric-value' style='color: #20c997;'>{res_stats.get('with_media', 0)}</div></div>", unsafe_allow_html=True)

    elif page == "🗑️ Dumps":
        dump_stats = database_queries.get_dump_stats()
        col1, col2 = st.columns(2)
        with col1: st.markdown(f"<div class='metric-card'><div class='metric-label'>Total Dumps</div><div class='metric-value' style='color: #6c757d;'>{dump_stats.get('total', 0)}</div></div>", unsafe_allow_html=True)
        with col2: st.markdown(f"<div class='metric-card'><div class='metric-label'>With Media</div><div class='metric-value' style='color: #343a40;'>{dump_stats.get('with_media', 0)}</div></div>", unsafe_allow_html=True)
        
    st.write("") # Spacer
    
    # --- Page Routing
    if page == "⏰ Reminders":
        tab1, tab2, tab3 = st.tabs(["📋 Upcoming", "📜 History", "➕ Add Reminder"])
        with tab1: upcoming.render()
        with tab2: history.render()
        with tab3: add_reminder.render()
        
    elif page == "📝 Tasks":
        tab1, tab2, tab3 = st.tabs(["📝 Active Tasks", "📜 History", "➕ Add Task"])
        with tab1: tasks_active.render()
        with tab2: tasks_history.render()
        with tab3: add_task.render()
        
    elif page == "💡 Ideas":
        ideas.render()

    elif page == "📓 Notes":
        notes.render()

    elif page == "🔗 Resources":
        resources.render()

    elif page == "🗑️ Dumps":
        dumps.render()

    elif page == "📊 Analytics":
        statistics.render()
        
    elif page == "⚙️ Settings":
        settings.render()

elif st.session_state.get("authentication_status") is False:
    st.error('Username/password is incorrect')
elif st.session_state.get("authentication_status") is None:
    st.warning('Please enter your username and password')
