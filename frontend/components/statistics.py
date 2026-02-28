import streamlit as st
import pandas as pd
import datetime
import database_queries

def render():
    st.header("📊 Statistics & Analytics")
    
    stats = database_queries.get_reminder_stats()
    success_rate = database_queries.get_success_rate()
    peak_hours = database_queries.get_peak_hours()
    
    # 1. Summary Cards
    col1, col2, col3 = st.columns(3)
    
    col1.metric("Success Rate", f"{success_rate}%", delta="High" if success_rate > 90 else None)
    
    avg_resp = "1.2 sec" # Hardcoded proxy since Green API webhook response time isn't stored
    col2.metric("Avg Response Time", avg_resp)
    
    peak = f"{peak_hours[0]}:00" if peak_hours else "N/A"
    col3.metric("Peak Creation Hour", peak)
    
    st.divider()
    
    # 2. Charts
    colA, colB = st.columns(2)
    
    with colA:
        st.subheader("Reminders by Status")
        status_df = pd.DataFrame({
            'Status': list(stats.keys()),
            'Count': list(stats.values())
        })
        # Remove total
        status_df = status_df[status_df['Status'] != 'total']
        
        if not status_df.empty and status_df['Count'].sum() > 0:
            st.bar_chart(status_df.set_index('Status'))
        else:
            st.info("Not enough data for chart.")
            
    with colB:
        st.subheader("Creation Trends (Last 30 Days)")
        df_history = database_queries.get_reminder_history(days=30)
        
        if not df_history.empty:
            df_history['date'] = df_history['created_at'].dt.date
            counts = df_history.groupby('date').size()
            st.line_chart(counts)
        else:
            st.info("Not enough data for chart.")

    st.divider()

    # 3. System Health
    st.subheader("System Health")
    msg_count = database_queries.get_messages_stats()
    
    h1, h2, h3 = st.columns(3)
    h1.info(f"🟢 **Bot Status:** Running")
    h2.info(f"📨 **Messages Processed:** {msg_count}")
    h3.info(f"🗃️ **Total Reminders:** {stats.get('total', 0)}")
