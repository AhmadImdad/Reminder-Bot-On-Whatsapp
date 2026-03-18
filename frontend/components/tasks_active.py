import streamlit as st
from datetime import datetime
import pandas as pd
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database_queries
import config_dashboard

def render():
    st.header("📝 Active Tasks")
    
    # Filters
    col1, col2 = st.columns([2, 1])
    with col1:
        search = st.text_input("🔍 Search active tasks...")
    with col2:
        filter_date = st.selectbox("📅 Due Filter", ["All", "Today", "Overdue", "No Due Date"])
        
    # Fetch data
    df = database_queries.get_pending_tasks()
    
    if df.empty:
        st.info("No active tasks found! You can add one from the 'Add Task' tab.")
        return
        
    # Apply search filter
    if search:
        df = df[df['task_name'].str.contains(search, case=False, na=False)]
        
    # Apply date filter
    now = datetime.utcnow()
    import pytz
    tz = pytz.timezone(config_dashboard.backend_config.TIMEZONE if hasattr(config_dashboard, 'backend_config') else "UTC")
    now_local = pytz.utc.localize(now).astimezone(tz).replace(tzinfo=None)
    
    if filter_date == "Today":
        df = df[(df['end_datetime'].notna()) & (df['end_datetime'].dt.date == now_local.date())]
    elif filter_date == "Overdue":
        df = df[(df['end_datetime'].notna()) & (df['end_datetime'] < now_local)]
    elif filter_date == "No Due Date":
        df = df[df['end_datetime'].isna()]

    if df.empty:
        st.warning("No active tasks match your filters.")
        return
        
    st.write(f"Showing {len(df)} active task(s):")
    
    # Display cards
    for idx, row in df.iterrows():
        dt = row['end_datetime']
        has_due = pd.notna(dt)
        
        if has_due:
            time_until = dt - now_local
            if time_until.total_seconds() < 0:
                color = config_dashboard.COLOR_DANGER if hasattr(config_dashboard, 'COLOR_DANGER') else "#dc3545"
                badge = "🔴 Overdue"
            elif time_until < pd.Timedelta(hours=24):
                color = config_dashboard.COLOR_WARNING if hasattr(config_dashboard, 'COLOR_WARNING') else "#ffc107"
                badge = "🟡 Due Soon"
            else:
                color = config_dashboard.COLOR_SUCCESS if hasattr(config_dashboard, 'COLOR_SUCCESS') else "#28a745"
                badge = "🟢 Upcoming"
            due_str = dt.strftime('%b %d, %Y at %H:%M')
        else:
            badge = "⚪ Open-ended"
            due_str = "No specific due date"
            
        with st.container(border=True):
            st.markdown(f"### 📅 {due_str} {badge}")
            st.markdown(f"**📝 {row['task_name']}**")
            st.caption(f"📞 Owner: {row['user_phone']}  |  🕐 Created: {row['created_at'].strftime('%b %d, %H:%M')}")
            
            if has_due:
                if time_until.total_seconds() > 0:
                    days = time_until.days
                    hours, remainder = divmod(time_until.seconds, 3600)
                    if days > 0:
                        st.write(f"⏱️ **Time remaining:** {days} days, {hours} hours")
                    else:
                        st.write(f"⏱️ **Time remaining:** {hours} hours, {remainder//60} mins")
                else:
                    st.write(f"⏱️ **Time remaining:** Overdue by {abs(time_until.days)} days!")
                
            col_a, col_b = st.columns([1, 4])
            with col_a:
                if st.button("✅ Complete", key=f"comp_{row['id']}"):
                    database_queries.mark_task_status(row['id'], 'completed')
                    st.success("Task Complete!")
                    st.rerun()
            with col_b:
                if st.button("🗑️ Delete", key=f"deltask_{row['id']}"):
                    database_queries.delete_task(row['id'])
                    st.error("Task Deleted!")
                    st.rerun()
