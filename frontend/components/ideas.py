import streamlit as st
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database_queries
from components.media_renderer import render_media_item


def render():
    st.header("💡 Idea Store")
    st.caption("All ideas captured from WhatsApp, newest first.")

    search = st.text_input("🔍 Search by subject...", key="idea_search")
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

    total = len(df)
    with_media = int(df['media_type'].notna().sum())
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total Ideas", total)
    with col2:
        st.metric("Ideas with Media", with_media)

    st.divider()

    csv = df.to_csv(index=False)
    st.download_button(
        "⬇️ Export All as CSV",
        data=csv,
        file_name="ideas_export.csv",
        mime="text/csv",
    )
    st.write("")

    for _, row in df.iterrows():
        idea_id      = int(row['id'])
        subject      = row['subject']
        description  = row.get('description') or ""
        media_type   = row.get('media_type')
        media_path   = row.get('media_path')
        original_name = row.get('media_original_name') or ""
        created_at   = row.get('created_at')

        with st.container(border=True):
            header_col, date_col = st.columns([3, 1])
            with header_col:
                st.markdown(f"### 💡 #{idea_id} — {subject}")
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

            st.caption(f"📞 From: {row.get('user_phone', 'Unknown')}")

            # ── Expand to see media ───────────────────────────────────────────
            has_primary    = bool(media_type and media_path)
            extra_attachments = database_queries.get_attachments("idea", idea_id)
            total_media    = (1 if has_primary else 0) + len(extra_attachments)

            if total_media > 0:
                label = f"📎 View {total_media} attachment(s)"
                with st.expander(label, expanded=False):
                    if has_primary:
                        st.caption("**Primary media:**")
                        abs_path = media_path if os.path.isabs(media_path) else os.path.join(
                            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            media_path
                        )
                        render_media_item(
                            media_type, abs_path, original_name,
                            key=f"idea_primary_{idea_id}"
                        )

                    if extra_attachments:
                        st.caption(f"**Extra attachments ({len(extra_attachments)}):**")
                        for i, att in enumerate(extra_attachments):
                            abs_path = att['media_path'] if os.path.isabs(att['media_path']) else os.path.join(
                                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                att['media_path']
                            )
                            render_media_item(
                                att['media_type'], abs_path,
                                att['original_name'] or f"attachment_{i+1}",
                                key=f"idea_att_{idea_id}_{i}"
                            )

            if st.button("🗑️ Delete", key=f"del_idea_{idea_id}"):
                database_queries.delete_idea_by_id(idea_id)
                st.error(f"Idea #{idea_id} deleted.")
                st.rerun()
