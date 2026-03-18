import streamlit as st
from datetime import datetime
import sys
import os

# Ensure backend path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
import config_dashboard

def render():
    st.header("➕ Create New Task")
    
    with st.form("add_task_form", clear_on_submit=True):
        task_name = st.text_input("📝 Task Name *", placeholder="e.g. Read Chapter 5")
        
        st.write("⏱️ Uncheck 'Has Due Date' to create a task with no strict end time.")
        has_due_date = st.checkbox("Has Due Date?", value=True)
        
        col1, col2 = st.columns(2)
        with col1:
            t_date = st.date_input("📅 End Date", min_value=datetime.today(), disabled=not has_due_date)
        with col2:
            t_time = st.time_input("🕐 End Time", disabled=not has_due_date)
            
        default_phone = config_dashboard.ALLOWED_PHONE_NUMBER if hasattr(config_dashboard, 'ALLOWED_PHONE_NUMBER') and config_dashboard.ALLOWED_PHONE_NUMBER else "+92"
        phone = st.text_input("📞 Owner WhatsApp Number *", value=default_phone, help="Format: Country code without '+' (e.g. 923066008613)")
        
        submitted = st.form_submit_button("Create Task", type="primary")
        
        if submitted:
            if not task_name:
                st.error("Task name is required.")
                return
            if not phone:
                st.error("Phone number is required.")
                return
                
            try:
                dt = None
                if has_due_date:
                    dt = datetime.combine(t_date, t_time)
                    if dt < datetime.now():
                        st.error("Task end time must be in the future!")
                        return
                    
                database.add_task(phone, task_name, dt)
                
                if dt:
                    st.success(f"✅ Task created successfully! Due: {dt.strftime('%b %d, %H:%M')}")
                else:
                    st.success(f"✅ Open-ended task created successfully!")
                    
            except Exception as e:
                st.error(f"Failed to create task: {e}")
