import streamlit as st
import os
import io
import zipfile
import sys
import config_dashboard

# ── Add backend to sys.path so we can call send_message ──────────────────────
sys.path.insert(0, config_dashboard.BASE_DIR)
import meta_api_client as whatsapp


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_db_size():
    if os.path.exists(config_dashboard.DB_PATH):
        size_bytes = os.path.getsize(config_dashboard.DB_PATH)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
    return "0 B"


def build_media_zip() -> tuple[bytes, int, float]:
    """
    Zip all media files from ideas_media/, notes_media/, resources_media/,
    dumps_media/ into an in-memory ZIP archive.
    Returns (zip_bytes, file_count, total_size_mb)
    """
    MEDIA_DIRS = ["ideas_media", "notes_media", "resources_media", "dumps_media"]
    project_root = config_dashboard.BASE_DIR
    buf = io.BytesIO()
    file_count = 0
    total_bytes = 0

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dir_name in MEDIA_DIRS:
            dir_path = os.path.join(project_root, dir_name)
            if not os.path.isdir(dir_path):
                continue
            for fname in os.listdir(dir_path):
                fpath = os.path.join(dir_path, fname)
                if os.path.isfile(fpath):
                    zf.write(fpath, arcname=os.path.join(dir_name, fname))
                    file_count += 1
                    total_bytes += os.path.getsize(fpath)

    buf.seek(0)
    return buf.getvalue(), file_count, total_bytes / (1024 * 1024)


def get_media_stats() -> dict:
    MEDIA_DIRS = ["ideas_media", "notes_media", "resources_media", "dumps_media"]
    project_root = config_dashboard.BASE_DIR
    stats = {}
    for d in MEDIA_DIRS:
        path = os.path.join(project_root, d)
        if os.path.isdir(path):
            stats[d] = len([f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))])
        else:
            stats[d] = 0
    return stats


# ── Page render ───────────────────────────────────────────────────────────────

def render():
    st.header("⚙️ Settings")

    col1, col2 = st.columns(2)

    # ── Left column: Account + User Management ────────────────────────────────
    with col1:
        # ── Change Password (no old password required) ────────────────────
        st.subheader("🔑 Change Password")
        with st.form("change_password_form"):
            new_pass = st.text_input("New Password", type="password")
            confirm_pass = st.text_input("Confirm New Password", type="password")
            cp_submitted = st.form_submit_button("Update Password")

        if cp_submitted:
            import auth
            if not new_pass:
                st.error("Password cannot be empty.")
            elif new_pass != confirm_pass:
                st.error("Passwords do not match.")
            else:
                ok, msg = auth.is_strong_password(new_pass)
                if not ok:
                    st.error(msg)
                else:
                    cfg = auth.load_config()
                    username = st.session_state.get("username")
                    if cfg and username and username in cfg["credentials"]["usernames"]:
                        cfg["credentials"]["usernames"][username]["password"] = auth.hash_password(new_pass)
                        auth.save_config(cfg)
                        st.success("✅ Password updated successfully!")
                    else:
                        st.error("Could not identify current user.")

        st.divider()

        # ── User Management ───────────────────────────────────────────────
        st.subheader("👥 User Management")
        st.caption("Manage who can use the WhatsApp bot. The admin number cannot be removed.")

        import database_queries as dq

        users_df = dq.get_all_allowed_users()

        if users_df.empty:
            st.info("No users found. The admin will appear here after the bot restarts.")
        else:
            for _, row in users_df.iterrows():
                phone    = str(row["phone"])
                label    = str(row["label"]) if row["label"] and str(row["label"]) != "nan" else "—"
                is_admin = int(row["is_admin"]) == 1
                added_at = str(row["added_at"])[:10]

                uc1, uc3 = st.columns([5, 1])
                with uc1:
                    badge = " 👑 Admin" if is_admin else ""
                    st.markdown(f"**{phone}**{badge}  \n`{label}` · Added {added_at}")
                with uc3:
                    if not is_admin:
                        confirm_key = f"confirm_remove_{phone}"
                        if not st.session_state.get(confirm_key):
                            if st.button("🗑️", key=f"remove_{phone}", help=f"Remove {phone}"):
                                st.session_state[confirm_key] = True
                                st.rerun()
                        else:
                            st.warning(f"Remove **{phone}**? This deletes ALL their data.")
                            y_col, n_col = st.columns(2)
                            if y_col.button("✅ Yes", key=f"yes_{phone}"):
                                ok, msg = dq.remove_allowed_user(phone)
                                if ok:
                                    st.success(msg)
                                else:
                                    st.error(msg)
                                st.session_state.pop(confirm_key, None)
                                st.rerun()
                            if n_col.button("❌ No", key=f"no_{phone}"):
                                st.session_state.pop(confirm_key, None)
                                st.rerun()

        st.divider()

        # ── Add New User ──────────────────────────────────────────────────
        st.subheader("➕ Add New User")
        with st.form("add_user_form"):
            new_phone = st.text_input(
                "Phone Number *",
                placeholder="923001234567  (digits only, no + or spaces)"
            )
            new_label = st.text_input("Label / Name", placeholder="e.g. Brother, Alice")
            au_submitted = st.form_submit_button("Add User")

        if au_submitted:
            if not new_phone:
                st.error("Phone number is required.")
            else:
                ok, msg = dq.add_allowed_user(new_phone, new_label)
                if ok:
                    st.success(msg)
                    try:
                        whatsapp.send_message(
                            new_phone,
                            "👋 *Welcome!* You've been added as an authorized user of the "
                            "WhatsApp Reminder Bot.\n\n"
                            "Type *help* to see what I can do for you!"
                        )
                        st.info(f"📱 Welcome message sent to {new_phone}.")
                    except Exception as e:
                        st.warning(f"User added but welcome message failed: {e}")
                    st.rerun()
                else:
                    st.error(msg)

    # ── Right column: Backups + Danger Zone ──────────────────────────────────
    with col2:
        st.subheader("Database Management")
        st.write(f"**Current Size:** {get_db_size()}")

        if os.path.exists(config_dashboard.DB_PATH):
            with open(config_dashboard.DB_PATH, "rb") as f:
                st.download_button(
                    label="📥 Download Database Backup",
                    data=f,
                    file_name="reminder_bot_backup.db",
                    mime="application/x-sqlite3",
                    type="primary",
                )

        st.divider()

        # ── Media Backup ──────────────────────────────────────────────────
        st.subheader("📦 Media Backup")
        st.caption(
            "Downloads all your saved media (images, videos, documents, audio) "
            "as a single ZIP file. Unzip into your new server's project folder "
            "to restore — paths will resolve automatically."
        )

        media_stats = get_media_stats()
        total_files = sum(media_stats.values())

        if total_files == 0:
            st.info("No media files saved yet.")
        else:
            cols = st.columns(4)
            labels = {
                "ideas_media":     ("💡", "Ideas"),
                "notes_media":     ("📓", "Notes"),
                "resources_media": ("🔗", "Resources"),
                "dumps_media":     ("🗑️", "Dumps"),
            }
            for i, (key, (icon, name)) in enumerate(labels.items()):
                cols[i].metric(f"{icon} {name}", media_stats.get(key, 0))

            st.write(f"**{total_files} file(s)** across all media stores")

            if st.button("⬇️ Generate & Download Media ZIP", type="primary"):
                with st.spinner("Zipping media files..."):
                    zip_bytes, count, size_mb = build_media_zip()

                st.download_button(
                    label=f"📦 Download media_backup.zip  ({count} files · {size_mb:.1f} MB)",
                    data=zip_bytes,
                    file_name="media_backup.zip",
                    mime="application/zip",
                    type="primary",
                )
                st.success(
                    f"✅ ZIP ready! {count} file(s), {size_mb:.1f} MB. "
                    "Click the button above to download."
                )

        st.divider()

        # ── Danger Zone ───────────────────────────────────────────────────
        st.subheader("🚨 Danger Zone")
        st.caption(
            "Permanently deletes **all content** for a selected user "
            "(ideas, notes, resources, dumps, reminders, tasks, all media files). "
            "Their account stays active — they can still use the bot."
        )

        users_df_dz = dq.get_all_allowed_users()
        non_admin_df = users_df_dz[users_df_dz["is_admin"] != 1] if not users_df_dz.empty else users_df_dz

        if non_admin_df.empty:
            st.info("No non-admin users to wipe.")
        else:
            phone_options = [
                f"{row['phone']}  ({row['label'] or 'no label'})"
                for _, row in non_admin_df.iterrows()
            ]
            selected_label = st.selectbox(
                "Select user to wipe content for:",
                options=phone_options,
                key="wipe_user_select"
            )
            selected_phone = selected_label.split()[0]  # extract phone number

            if st.button("🗑️ Wipe All Content", type="primary", key="wipe_content_btn"):
                st.session_state.confirm_wipe = True
                st.session_state.wipe_phone   = selected_phone
                st.rerun()

            if st.session_state.get("confirm_wipe") and st.session_state.get("wipe_phone") == selected_phone:
                st.error(
                    f"⚠️ Are you absolutely sure? This will permanently delete **all content** "
                    f"for **{selected_phone}** — including all media files on disk. "
                    f"The account will remain active."
                )
                w1, w2 = st.columns(2)
                if w1.button("✅ Yes, wipe everything", key="confirm_wipe_yes"):
                    with st.spinner("Wiping content..."):
                        ok, msg, count = dq.wipe_user_content(selected_phone)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)
                    st.session_state.confirm_wipe = False
                    st.session_state.wipe_phone   = None
                    st.rerun()
                if w2.button("❌ No, cancel", key="confirm_wipe_no"):
                    st.session_state.confirm_wipe = False
                    st.session_state.wipe_phone   = None
                    st.rerun()
