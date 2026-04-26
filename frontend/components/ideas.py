import streamlit as st
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database_queries


def render():
    st.header("💡 Idea Store")
    st.caption("All ideas captured from WhatsApp, newest first.")

    # ── Search bar ─────────────────────────────────────────────────────────────
    search = st.text_input("🔍 Search by subject...", key="idea_search")

    # ── Fetch data ─────────────────────────────────────────────────────────────
    df = database_queries.get_all_ideas(search=search)

    if df.empty:
        if search:
            st.warning(f"No ideas found matching **'{search}'**.")
        else:
            st.info(
                "💡 No ideas saved yet!\n\n"
                "Send a WhatsApp message to your bot where the last sentence "
                "contains the word **'idea'** to save your first one."
            )
        return

    # ── Stats row ──────────────────────────────────────────────────────────────
    total = len(df)
    with_media = int(df['media_type'].notna().sum())
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total Ideas", total)
    with col2:
        st.metric("Ideas with Media", with_media)

    st.divider()

    # ── Export ─────────────────────────────────────────────────────────────────
    csv = df.to_csv(index=False)
    st.download_button(
        "⬇️ Export All as CSV",
        data=csv,
        file_name="ideas_export.csv",
        mime="text/csv",
    )

    st.write("")  # spacer

    # ── Idea cards ─────────────────────────────────────────────────────────────
    for _, row in df.iterrows():
        idea_id = int(row['id'])
        subject = row['subject']
        description = row.get('description') or ""
        media_type = row.get('media_type')
        media_path = row.get('media_path')
        created_at = row.get('created_at')

        with st.container(border=True):
            # Header row: ID + subject + date
            header_col, date_col = st.columns([3, 1])
            with header_col:
                st.markdown(f"### 💡 #{idea_id} — {subject}")
            with date_col:
                if created_at is not None:
                    try:
                        st.caption(f"🗓️ {created_at.strftime('%b %d, %Y  %H:%M')}")
                    except Exception:
                        st.caption(str(created_at))

            # Description
            if description:
                st.markdown(f"**📝 Description:** {description}")
            else:
                st.caption("_(No description)_")

            # Media preview
            if media_type and media_path:
                if os.path.exists(media_path):
                    if media_type == "image":
                        st.image(media_path, caption="Attached Image", use_column_width=True)
                    elif media_type in ("audio", "video"):
                        with open(media_path, "rb") as f:
                            media_bytes = f.read()
                        original_name = row.get('media_original_name') or f"idea_{idea_id}_attachment"
                        mime = "audio/ogg" if media_type == "audio" else "video/mp4"
                        st.download_button(
                            label=f"🎵 Download {media_type.capitalize()} — {original_name}",
                            data=media_bytes,
                            file_name=original_name,
                            mime=mime,
                            key=f"dl_media_{idea_id}"
                        )
                else:
                    st.warning(f"⚠️ Attached {media_type} file not found on server.")
            
            # Phone / owner tag
            st.caption(f"📞 From: {row.get('user_phone', 'Unknown')}")

            # Delete button
            if st.button("🗑️ Delete", key=f"del_idea_{idea_id}"):
                database_queries.delete_idea_by_id(idea_id)
                st.error(f"Idea #{idea_id} deleted.")
                st.rerun()
