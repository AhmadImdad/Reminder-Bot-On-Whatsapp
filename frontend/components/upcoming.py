import streamlit as st
from datetime import datetime
import pandas as pd
import sys
import os

# Add parent to path to import green_api_client safely
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database_queries
import config_dashboard

try:
    import green_api_client
except ImportError:
    green_api_client = None

def render():
    st.header("📋 Upcoming Reminders")
    
    # Filters
    col1, col2 = st.columns([2, 1])
    with col1:
        search = st.text_input("🔍 Search tasks...")
    with col2:
        filter_date = st.selectbox("📅 Date Filter", ["All", "Today", "Tomorrow", "This Week"])
        
    # Fetch data
    df = database_queries.get_pending_reminders()
    
    if df.empty:
        st.info("No upcoming reminders found! You can add one from the 'Add New' tab.")
        return
        
    # Apply search filter
    if search:
        df = df[df['task'].str.contains(search, case=False, na=False)]
        
    # Apply date filter
    now = datetime.utcnow()
    # Apply the same timezone offset to 'now' so relative math works on the frontend
    import pytz
    tz = pytz.timezone(config_dashboard.backend_config.TIMEZONE if hasattr(config_dashboard, 'backend_config') else "UTC")
    now_local = pytz.utc.localize(now).astimezone(tz).replace(tzinfo=None)
    
    if filter_date == "Today":
        df = df[df['reminder_datetime'].dt.date == now_local.date()]
    elif filter_date == "Tomorrow":
        df = df[df['reminder_datetime'].dt.date == (now + pd.Timedelta(days=1)).date()]
    elif filter_date == "This Week":
        end_of_week = now + pd.Timedelta(days=7)
        df = df[df['reminder_datetime'] <= end_of_week]

    if df.empty:
        st.warning("No reminders match your filters.")
        return
        
    st.write(f"Showing {len(df)} upcoming reminder(s):")
    
    # Display cards
    for idx, row in df.iterrows():
        dt = row['reminder_datetime']
        time_until = dt - now_local
        
        # Color coding
        if time_until < pd.Timedelta(hours=1):
            color = config_dashboard.COLOR_DANGER
            badge = "🔴 Soon"
        elif time_until < pd.Timedelta(hours=24):
            color = config_dashboard.COLOR_WARNING
            badge = "🟡 $< 24h$"
        else:
            color = config_dashboard.COLOR_SUCCESS
            badge = "🟢 Scheduled"
            
        with st.container(border=True):
            st.markdown(f"### ⏰ {dt.strftime('%b %d, %Y at %H:%M')} {badge}")
            st.markdown(f"**📝 {row['task']}**")
            st.caption(f"📞 Recipient: {row['user_phone']}  |  🕐 Created: {row['created_at'].strftime('%b %d, %H:%M')}")
            
            hours, remainder = divmod(time_until.total_seconds(), 3600)
            minutes, _ = divmod(remainder, 60)
            if hours > 0:
                st.write(f"⏱️ **Time until reminder:** {int(hours)} hours {int(minutes)} minutes")
            elif time_until.total_seconds() > 0:
                st.write(f"⏱️ **Time until reminder:** {int(minutes)} minutes")
            else:
                st.write(f"⏱️ **Time until reminder:** Overdue!")
                
            col_a, col_b, col_c = st.columns([1, 1, 4])
            with col_a:
                if st.button("🗑️ Delete", key=f"del_{row['id']}"):
                    database_queries.delete_reminder(row['id'])
                    st.success("Deleted!")
                    st.rerun()
            with col_b:
                if st.button("📤 Test Send", key=f"test_{row['id']}"):
                    if green_api_client:
                        green_api_client.send_message(row['user_phone'], f"[TEST] ⏰ Reminder: {row['task']}")
                        st.toast("Test message sent!")
                    else:
                        st.error("Green API Client not loaded.")
