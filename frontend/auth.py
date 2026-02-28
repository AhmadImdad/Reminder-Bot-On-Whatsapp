import streamlit as st
import streamlit_authenticator as stauth
import yaml
from yaml.loader import SafeLoader
import os
import bcrypt

import config_dashboard

def load_config():
    if not os.path.exists(config_dashboard.AUTH_DB_PATH):
        return None
    with open(config_dashboard.AUTH_DB_PATH) as file:
        return yaml.load(file, Loader=SafeLoader)

def save_config(config_dict):
    with open(config_dashboard.AUTH_DB_PATH, 'w') as file:
        yaml.dump(config_dict, file, default_flow_style=False)

def hash_password(password):
    # bcrypt with cost factor 12
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')

def is_strong_password(password):
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter."
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number."
    return True, "Strong ✅"

def first_time_setup():
    st.title("🔐 First-Time Setup")
    st.markdown("Welcome! Let's set up your admin account.")
    
    with st.form("setup_form"):
        username = st.text_input("👤 Username *", value="admin")
        password = st.text_input("🔒 Password *", type="password")
        confirm_password = st.text_input("🔒 Confirm Password *", type="password")
        terms = st.checkbox("✅ I agree to terms and conditions")
        
        submitted = st.form_submit_button("Create Admin Account")
        
        if submitted:
            if not terms:
                st.error("You must agree to the terms.")
                return False
                
            if password != confirm_password:
                st.error("Passwords do not match!")
                return False
                
            is_strong, msg = is_strong_password(password)
            if not is_strong:
                st.error(msg)
                return False
                
            # Create config
            config_dict = {
                'credentials': {
                    'usernames': {
                        username: {
                            'email': f'{username}@localhost',
                            'name': 'Administrator',
                            'password': hash_password(password)
                        }
                    }
                },
                'cookie': {
                    'expiry_days': 1, # handled custom by session timeout but required for yaml
                    'key': 'reminder_bot_signature_key',
                    'name': 'reminder_bot_session'
                },
                'preauthorized': {
                    'emails': []
                }
            }
            save_config(config_dict)
            st.success("Account created successfully! Reloading...")
            st.rerun()

def get_authenticator():
    config = load_config()
    if config is None:
        return None
        
    return stauth.Authenticate(
        config['credentials'],
        config['cookie']['name'],
        config['cookie']['key'],
        config['cookie']['expiry_days']
    )
    
def authenticate():
    authenticator = get_authenticator()
    
    if authenticator is None:
        first_time_setup()
        return False, None
        
    # Standard Login Logic
    try:
        # In streamlit-authenticator >= 0.4.0, login() does not return a tuple anymore.
        # It sets session states and we just call it.
        authenticator.login()
        authentication_status = st.session_state.get("authentication_status")
    except Exception as e:
        st.error(f"Login error: {e}")
        return False, authenticator
        
    if authentication_status:
        st.session_state['authenticator'] = authenticator
        return True, authenticator
    elif authentication_status is False:
        st.error('Username/password is incorrect')
        return False, authenticator
    elif authentication_status is None:
        st.warning('Please enter your username and password')
        return False, authenticator
        
    return False, authenticator
