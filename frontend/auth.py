import streamlit as st
import streamlit_authenticator as stauth
import yaml
from yaml.loader import SafeLoader
import os
import sys
import bcrypt

import config_dashboard

# ── Add backend to path so we can use database.py directly ───────────────────
sys.path.insert(0, config_dashboard.BASE_DIR)
import database as backend_db
import meta_api_client as whatsapp

# ── File helpers ──────────────────────────────────────────────────────────────

def load_config():
    if not os.path.exists(config_dashboard.AUTH_DB_PATH):
        return None
    with open(config_dashboard.AUTH_DB_PATH) as file:
        return yaml.load(file, Loader=SafeLoader)

def save_config(config_dict):
    with open(config_dashboard.AUTH_DB_PATH, 'w') as file:
        yaml.dump(config_dict, file, default_flow_style=False)

def hash_password(password):
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


# ── First-time setup ──────────────────────────────────────────────────────────

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
                    'expiry_days': 1,
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


# ── Change Password (no old password required) ────────────────────────────────

def render_change_password():
    """Renders a Change Password form. Call from Settings page."""
    st.subheader("🔑 Change Password")
    with st.form("change_password_form"):
        new_pass = st.text_input("New Password", type="password")
        confirm_pass = st.text_input("Confirm New Password", type="password")
        submitted = st.form_submit_button("Update Password")

    if submitted:
        if not new_pass:
            st.error("Password cannot be empty.")
            return
        if new_pass != confirm_pass:
            st.error("Passwords do not match.")
            return
        ok, msg = is_strong_password(new_pass)
        if not ok:
            st.error(msg)
            return

        cfg = load_config()
        if cfg is None:
            st.error("Auth config not found.")
            return

        username = st.session_state.get("username")
        if not username or username not in cfg["credentials"]["usernames"]:
            st.error("Could not identify current user.")
            return

        cfg["credentials"]["usernames"][username]["password"] = hash_password(new_pass)
        save_config(cfg)
        st.success("✅ Password updated successfully!")


# ── Forgot Password flow ──────────────────────────────────────────────────────

def _get_admin_phone() -> str:
    """Returns the admin phone number from allowed_users DB."""
    try:
        users = backend_db.get_all_allowed_users()
        for u in users:
            if u["is_admin"] == 1:
                return u["phone"]
    except Exception:
        pass
    import config as backend_config
    return backend_config.ALLOWED_PHONE_NUMBER


def render_forgot_password():
    """
    Renders the Forgot Password flow below the login form.
    States managed via st.session_state:
        fp_challenge_sent  — WhatsApp challenge has been fired
        fp_otp_verified    — OTP was accepted, show new password form
    """
    st.divider()

    # ── Step 0: Button to initiate ───────────────────────────────────────────
    if not st.session_state.get("fp_challenge_sent") and not st.session_state.get("fp_otp_verified"):
        if st.button("🔒 Forgot Password?", key="fp_btn"):
            admin_phone = _get_admin_phone()
            if not admin_phone:
                st.error("No admin phone configured. Cannot send challenge.")
                return
            try:
                backend_db.update_conversation_state(
                    admin_phone, "awaiting_titan_response", {}
                )
                whatsapp.send_message(
                    admin_phone,
                    "🔐 *Password Reset Requested*\n\n"
                    "Someone is trying to reset your dashboard password.\n"
                    "To proceed, reply with the answer to this question:\n\n"
                    "*What are you?*"
                )
                st.session_state["fp_challenge_sent"] = True
                st.rerun()
            except Exception as e:
                st.error(f"Failed to send WhatsApp challenge: {e}")
        return

    # ── Step 1: OTP entry ────────────────────────────────────────────────────
    if st.session_state.get("fp_challenge_sent") and not st.session_state.get("fp_otp_verified"):
        st.info("📱 A security challenge has been sent to the admin WhatsApp. "
                "Reply **Titan** to receive your OTP, then enter it below.")

        otp_input = st.text_input("Enter OTP (6 digits)", max_chars=6, key="fp_otp_input")
        col1, col2 = st.columns(2)

        with col1:
            if st.button("✅ Verify OTP", key="fp_verify_btn"):
                if not otp_input:
                    st.error("Please enter the OTP.")
                elif backend_db.verify_otp(otp_input.strip()):
                    st.session_state["fp_otp_verified"] = True
                    st.session_state["fp_challenge_sent"] = False
                    st.rerun()
                else:
                    st.error("❌ Invalid or expired OTP. Please try again.")

        with col2:
            if st.button("🔁 Resend Challenge", key="fp_resend_btn"):
                admin_phone = _get_admin_phone()
                if admin_phone:
                    backend_db.update_conversation_state(
                        admin_phone, "awaiting_titan_response", {}
                    )
                    whatsapp.send_message(
                        admin_phone,
                        "🔐 *Password Reset Requested*\n\n"
                        "To proceed, reply with the answer to:\n\n"
                        "*What are you?*"
                    )
                    st.success("Challenge resent!")
        return

    # ── Step 2: New password form ─────────────────────────────────────────────
    if st.session_state.get("fp_otp_verified"):
        st.success("✅ OTP verified! Set your new password below.")

        with st.form("fp_new_pass_form"):
            new_pass = st.text_input("New Password", type="password")
            confirm_pass = st.text_input("Confirm New Password", type="password")
            submitted = st.form_submit_button("Set New Password")

        if submitted:
            if not new_pass:
                st.error("Password cannot be empty.")
                return
            if new_pass != confirm_pass:
                st.error("Passwords do not match.")
                return
            ok, msg = is_strong_password(new_pass)
            if not ok:
                st.error(msg)
                return

            cfg = load_config()
            if cfg is None:
                st.error("Auth config not found.")
                return

            for uname in cfg["credentials"]["usernames"]:
                cfg["credentials"]["usernames"][uname]["password"] = hash_password(new_pass)

            save_config(cfg)

            st.session_state.pop("fp_otp_verified", None)
            st.session_state.pop("fp_challenge_sent", None)

            st.success("✅ Password reset successfully! Please log in with your new password.")
            st.rerun()


# ── Authenticator factory ─────────────────────────────────────────────────────

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


# ── Main authenticate() called by dashboard.py ────────────────────────────────

def authenticate():
    authenticator = get_authenticator()
    
    if authenticator is None:
        first_time_setup()
        return False, None
        
    try:
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
        render_forgot_password()
        return False, authenticator
    elif authentication_status is None:
        st.warning('Please enter your username and password')
        render_forgot_password()
        return False, authenticator

    return False, authenticator
