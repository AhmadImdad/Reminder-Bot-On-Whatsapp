import streamlit as st
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database_queries


def render():
    st.header("🔗 Resources Store")
    st.caption("All resources captured from WhatsApp, newest first.")

    # ── Search bar ─────────────────────────────────────────────────────────────
    search = st.text_input("🔍 Search by subject...", key="resource_search")

    # ── Fetch data ─────────────────────────────────────────────────────────────
    df = database_queries.get_all_resources(search=search)

    if df.empty:
        if search:
            st.warning(f"No resources found matching **'{search}'**.")
        else:
            st.info(
                "🔗 No resources saved yet!\n\n"
                "Send a WhatsApp message to your bot where the last sentence "
                "contains the word **'resource'** to save your first one."
            )
        return

    # ── Stats row ──────────────────────────────────────────────────────────────
    total = len(df)
    with_media = int(df['media_type'].notna().sum())
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total Resources", total)
    with col2:
        st.metric("Resources with Media", with_media)

    st.divider()

    # ── Export ─────────────────────────────────────────────────────────────────
    csv = df.to_csv(index=False)
    st.download_button(
        "⬇️ Export All as CSV",
        data=csv,
        file_name="resources_export.csv",
        mime="text/csv",
    )

    st.write("")  # spacer

    # ── Resource cards ─────────────────────────────────────────────────────────────
    for _, row in df.iterrows():
        resource_id = int(row['id'])
        subject = row['subject']
        description = row.get('description') or ""
        media_type = row.get('media_type')
        media_path = row.get('media_path')
        created_at = row.get('created_at')

        with st.container(border=True):
            header_col, date_col = st.columns([3, 1])
            with header_col:
                st.markdown(f"### 🔗 #{resource_id} — {subject}")
            with date_col:
                if created_at is not None:
                    try:
                        st.caption(f"🗓️ {created_at.strftime('%b %d, %Y  %H:%M')}")
                    except Exception:
                        st.caption(str(created_at))

            if description:
                st.markdown(f"**📝 Description:** {description}")
            else:
                st.caption("_(No description)_")

            # Media preview / download
            if media_type and media_path:
                if os.path.exists(media_path):
                    if media_type == "image":
                        st.image(media_path, caption="Attached Image", use_column_width=True)
                    elif media_type in ("audio", "video"):
                        with open(media_path, "rb") as f:
                            media_bytes = f.read()
                        original_name = row.get('media_original_name') or f"resource_{resource_id}_attachment"
                        mime = "audio/ogg" if media_type == "audio" else "video/mp4"
                        st.download_button(
                            label=f"🎵 Download {media_type.capitalize()} — {original_name}",
                            data=media_bytes,
                            file_name=original_name,
                            mime=mime,
                            key=f"dl_resource_media_{resource_id}"
                        )
                else:
                    st.warning(f"⚠️ Attached {media_type} file not found on server.")

            st.caption(f"📞 From: {row.get('user_phone', 'Unknown')}")

            if st.button("🗑️ Delete", key=f"del_resource_{resource_id}"):
                database_queries.delete_resource_by_id(resource_id)
                st.error(f"Resource #{resource_id} deleted.")
                st.rerun()
