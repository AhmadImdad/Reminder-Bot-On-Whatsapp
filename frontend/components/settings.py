import streamlit as st
import os
import sqlite3
import config_dashboard

def get_db_size():
    if os.path.exists(config_dashboard.DB_PATH):
        size_bytes = os.path.getsize(config_dashboard.DB_PATH)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
    return "0 B"

def render():
    st.header("⚙️ Settings")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Account Settings")
        try:
            if st.session_state.get('authenticator'):
                try:
                    if st.session_state['authenticator'].reset_password(st.session_state["username"], 'Change Password'):
                        st.success('Password changed successfully')
                        st.session_state['authenticator'].logout('Logout', 'main')
                except Exception as e:
                    st.error(e)
        except AttributeError:
            st.warning("Password reset not available in this authentication mode.")
            
        st.divider()
        st.subheader("Bot Configuration")
        phone = st.text_input("Default Recipient", value=config_dashboard.ALLOWED_PHONE_NUMBER, disabled=True)
        st.caption("Change this in your backend `.env` file and restart.")

    with col2:
        st.subheader("Database Management")
        st.write(f"**Current Size:** {get_db_size()}")
        
        if os.path.exists(config_dashboard.DB_PATH):
            with open(config_dashboard.DB_PATH, "rb") as file:
                btn = st.download_button(
                    label="📥 Download Database Backup",
                    data=file,
                    file_name="reminder_bot_backup.db",
                    mime="application/x-sqlite3",
                    type="primary"
                )
                
        st.divider()
        st.subheader("Danger Zone")
        
        if st.button("🗑️ Reset Database", type="primary"):
            st.session_state.confirm_reset = True
            
        if st.session_state.get('confirm_reset', False):
            st.error("⚠️ Are you absolutely sure? This will delete all reminders and messages permanently!")
            c1, c2 = st.columns(2)
            if c1.button("Yes, erase everything"):
                try:
                    conn = sqlite3.connect(config_dashboard.DB_PATH)
                    c = conn.cursor()
                    c.execute("DELETE FROM reminders")
                    c.execute("DELETE FROM messages")
                    c.execute("DELETE FROM conversation_state")
                    conn.commit()
                    conn.close()
                    st.success("Database erased.")
                    st.session_state.confirm_reset = False
                except Exception as e:
                    st.error(f"Failed: {e}")
                    
            if c2.button("No, cancel"):
                st.session_state.confirm_reset = False
                st.rerun()
