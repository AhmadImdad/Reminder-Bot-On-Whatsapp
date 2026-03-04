import streamlit as st
import pandas as pd
import database_queries

def render():
    st.header("📜 Reminder History")
    
    # Filters
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        status_filter = st.selectbox("Status", ["All", "completed", "failed", "pending", "cancelled"])
    with col2:
        days_filter = st.selectbox("Timeframe", ["Last 7 Days", "Last 30 Days", "All Time"])
        days_map = {"Last 7 Days": 7, "Last 30 Days": 30, "All Time": 0}
    with col3:
        search = st.text_input("🔍 Search History...")
        
    df = database_queries.get_reminder_history(status=status_filter, days=days_map[days_filter])
    
    if search and not df.empty:
        df = df[df['task'].str.contains(search, case=False, na=False)]
        
    if df.empty:
        st.info("No history found matching filters.")
        return
        
    # Styling rules
    def styler(val):
        color = 'black'
        if val == 'completed': color = 'green'
        elif val == 'failed': color = 'red'
        elif val == 'cancelled': color = 'gray'
        elif val == 'pending': color = 'blue'
        return f'color: {color}; font-weight: bold'
        
    # Prepare display DF
    display_df = df[['status', 'reminder_datetime', 'task', 'user_phone', 'created_at', 'triggered_at']].copy()
    display_df.rename(columns={
        'status': 'Status',
        'reminder_datetime': 'Scheduled For',
        'task': 'Task',
        'user_phone': 'Recipient',
        'created_at': 'Created',
        'triggered_at': 'Executed At'
    }, inplace=True)
    
    # Render table
    st.dataframe(
        display_df.style.applymap(styler, subset=['Status']),
        use_container_width=True,
        hide_index=True
    )
    
    # Controls
    col_a, col_b = st.columns([1, 4])
    with col_a:
        csv = display_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Export to CSV",
            data=csv,
            file_name='reminder_history.csv',
            mime='text/csv',
        )
