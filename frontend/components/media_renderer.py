import streamlit as st
import os


def render_media_item(media_type: str, media_path: str,
                      original_name: str = "", key: str = "") -> None:
    """
    Shared utility to render any media item in a Streamlit card.
    Handles images, audio, video, and other files (download button).
    Uses the current Streamlit API (no deprecated kwargs).
    """
    if not media_path:
        return

    if not os.path.exists(media_path):
        st.warning(f"⚠️ Attached file not found on server: {original_name or media_path}")
        return

    label = original_name or os.path.basename(media_path)

    if media_type == "image":
        st.image(media_path, caption=label, use_container_width=True)

    elif media_type == "audio":
        with open(media_path, "rb") as f:
            st.audio(f.read(), format="audio/ogg")
        st.caption(f"🎤 {label}")

    elif media_type == "video":
        with open(media_path, "rb") as f:
            st.video(f.read())
        st.caption(f"🎥 {label}")

    else:
        # Document or unknown — offer download
        with open(media_path, "rb") as f:
            data = f.read()
        st.download_button(
            label=f"📥 Download: {label}",
            data=data,
            file_name=label,
            key=key or f"dl_{hash(media_path)}",
        )
