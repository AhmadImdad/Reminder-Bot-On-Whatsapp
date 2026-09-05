import streamlit as st
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database_queries
from components.media_renderer import render_media_item

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _abs(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(_BASE, path)


def render():
    st.header("🔗 Resources Store")
    st.caption("All resources captured from WhatsApp, newest first.")

    search = st.text_input("🔍 Search by subject...", key="resource_search")
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

    total = len(df)
    with_media = int(df['media_type'].notna().sum())
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total Resources", total)
    with col2:
        st.metric("Resources with Media", with_media)

    st.divider()

    csv = df.to_csv(index=False)
    st.download_button(
        "⬇️ Export All as CSV",
        data=csv,
        file_name="resources_export.csv",
        mime="text/csv",
    )
    st.write("")

    for _, row in df.iterrows():
        resource_id  = int(row['id'])
        subject      = row['subject']
        description  = row.get('description') or ""
        media_type   = row.get('media_type')
        media_path   = row.get('media_path')
        original_name = row.get('media_original_name') or ""
        created_at   = row.get('created_at')

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

            st.caption(f"📞 From: {row.get('user_phone', 'Unknown')}")

            has_primary       = bool(media_type and media_path)
            extra_attachments = database_queries.get_attachments("resource", resource_id)
            total_media       = (1 if has_primary else 0) + len(extra_attachments)

            if total_media > 0:
                with st.expander(f"📎 View {total_media} attachment(s)", expanded=False):
                    if has_primary:
                        st.caption("**Primary media:**")
                        render_media_item(media_type, _abs(media_path), original_name, key=f"res_primary_{resource_id}")
                    if extra_attachments:
                        st.caption(f"**Extra attachments ({len(extra_attachments)}):**")
                        for i, att in enumerate(extra_attachments):
                            render_media_item(
                                att['media_type'], _abs(att['media_path']),
                                att['original_name'] or f"attachment_{i+1}",
                                key=f"res_att_{resource_id}_{i}"
                            )

            if st.button("🗑️ Delete", key=f"del_resource_{resource_id}"):
                database_queries.delete_resource_by_id(resource_id)
                st.error(f"Resource #{resource_id} deleted.")
                st.rerun()
