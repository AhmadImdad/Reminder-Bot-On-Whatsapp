import streamlit as st
from datetime import datetime
import sys
import os

# Ensure backend path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
import config_dashboard

def render():
    st.header("➕ Create New Reminder")
    
    with st.form("add_reminder_form", clear_on_submit=False):
        task = st.text_input("📝 Task/Message *", placeholder="e.g. Call the dentist")
        
        col1, col2 = st.columns(2)
        with col1:
            r_date = st.date_input("📅 Date *", min_value=datetime.today())
        with col2:
            r_time = st.time_input("🕐 Time *")
            
        default_phone = config_dashboard.ALLOWED_PHONE_NUMBER if config_dashboard.ALLOWED_PHONE_NUMBER else "+92"
        phone = st.text_input("📞 Recipient WhatsApp Number *", value=default_phone, help="Format: Country code without '+' (e.g. 923066008613)")
        
        st.caption("💡 Leave blank to use configured admin number")
        
        submitted = st.form_submit_button("Create Reminder", type="primary")
        
        if submitted:
            if not task:
                st.error("Task is required.")
                return
            if not phone:
                st.error("Phone number is required.")
                return
                
            try:
                # Combine date and time
                dt = datetime.combine(r_date, r_time)
                
                # We assume the time selected is local time. Need to map carefully depending 
                # on if the server timezone differs from the web client, but keeping it simple for SQLite now.
                
                # Validate future
                if dt < datetime.now():
                    st.error("Reminder time must be in the future!")
                    return
                    
                database.add_reminder(phone, task, dt)
                st.success(f"✅ Reminder created successfully for {dt.strftime('%b %d, %H:%M')}")
                
            except Exception as e:
                st.error(f"Failed to create reminder: {e}")
