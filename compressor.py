"""
compressor.py — Image auto-compression and file size validation.

Option C policy:
  - Images   > IMAGE_LIMIT (4.5 MB)  → auto-compress silently with Pillow
  - Audio/Video > AV_LIMIT (16 MB)   → reject with user-friendly message
  - Documents   > DOC_LIMIT (100 MB) → reject
  - Everything else accepted as-is
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ── File-size limits ──────────────────────────────────────────────────────────
IMAGE_LIMIT_BYTES  = 4_718_592   # 4.5 MB  (under Meta's 5 MB image cap)
AV_LIMIT_BYTES     = 16_777_216  # 16 MB   (Meta's audio / video cap)
DOC_LIMIT_BYTES    = 104_857_600 # 100 MB  (Meta's document cap)

# ── Compression quality steps ─────────────────────────────────────────────────
_QUALITY_STEPS = [75, 55, 40]
_MAX_DIMENSION  = 2048   # pixels — applied if quality steps aren't enough


def _file_size(path: str) -> int:
    """Return file size in bytes, or 0 if file not found."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def check_file_size(abs_path: str, media_type: str) -> tuple[bool, str]:
    """
    Check whether a downloaded file is within acceptable limits for its type.

    Returns:
        (ok, rejection_message)
        ok=True  → file is within limits (or is an image that will be compressed)
        ok=False → file must be rejected; rejection_message explains why
    """
    size = _file_size(abs_path)

    if media_type == "image":
        # Images may be compressed — accept for now; compress_image() handles the rest
        if size > IMAGE_LIMIT_BYTES:
            return True, ""   # accepted for compression attempt
        return True, ""

    if media_type in ("audio", "video"):
        if size > AV_LIMIT_BYTES:
            size_mb = size / (1024 * 1024)
            return False, (
                f"⚠️ Your {media_type} ({size_mb:.1f} MB) is over the {AV_LIMIT_BYTES // (1024*1024)} MB limit.\n\n"
                f"WhatsApp's API cannot deliver files this large. "
                f"Please compress it and resend."
            )
        return True, ""

    if media_type == "document":
        if size > DOC_LIMIT_BYTES:
            size_mb = size / (1024 * 1024)
            return False, (
                f"⚠️ Your document ({size_mb:.1f} MB) exceeds the 100 MB limit and cannot be saved.\n"
                f"Please split or compress the file and resend."
            )
        return True, ""

    return True, ""


def compress_image(abs_path: str) -> tuple[str, bool, str]:
    """
    Attempt to compress an image file to under IMAGE_LIMIT_BYTES.

    Tries progressively lower quality levels, then a resolution cap.
    Overwrites the file in-place (same path) on success.

    Returns:
        (abs_path, success, message)
        success=True  → compressed file at abs_path, within limits
        success=False → file still too large; caller should reject + delete
    """
    size = _file_size(abs_path)
    if size <= IMAGE_LIMIT_BYTES:
        return abs_path, True, ""   # already within limit — nothing to do

    try:
        from PIL import Image
        import io
    except ImportError:
        logger.warning("Pillow not installed — skipping image compression")
        return abs_path, False, (
            "⚠️ Your image is too large and I couldn't compress it (Pillow not installed). "
            "Please resize it manually and resend."
        )

    try:
        img = Image.open(abs_path)
        original_format = img.format or "JPEG"
        save_format = "JPEG" if original_format.upper() in ("JPEG", "JPG") else original_format

        # Ensure RGB so JPEG save works for RGBA/palette images
        if save_format == "JPEG" and img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # ── Step 1: try quality reduction ─────────────────────────────────────
        for quality in _QUALITY_STEPS:
            buf = io.BytesIO()
            img.save(buf, format=save_format, quality=quality, optimize=True)
            compressed_size = buf.tell()
            logger.info(f"compress_image: quality={quality} → {compressed_size/1024/1024:.2f} MB")
            if compressed_size <= IMAGE_LIMIT_BYTES:
                buf.seek(0)
                with open(abs_path, "wb") as f:
                    f.write(buf.read())
                return abs_path, True, ""

        # ── Step 2: also cap resolution ───────────────────────────────────────
        w, h = img.size
        if max(w, h) > _MAX_DIMENSION:
            scale = _MAX_DIMENSION / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        for quality in _QUALITY_STEPS:
            buf = io.BytesIO()
            img.save(buf, format=save_format, quality=quality, optimize=True)
            compressed_size = buf.tell()
            if compressed_size <= IMAGE_LIMIT_BYTES:
                buf.seek(0)
                with open(abs_path, "wb") as f:
                    f.write(buf.read())
                logger.info(f"compress_image: resized + quality={quality} → success")
                return abs_path, True, ""

        # Could not compress enough
        size_mb = _file_size(abs_path) / (1024 * 1024)
        logger.warning(f"compress_image: could not get under limit — {size_mb:.1f} MB")
        return abs_path, False, (
            f"⚠️ Your image ({size_mb:.1f} MB) is too large even after compression.\n"
            f"Please resize it (under 4 MB) and resend."
        )

    except Exception as e:
        logger.error(f"compress_image error: {e}", exc_info=True)
        return abs_path, False, (
            f"⚠️ I couldn't compress your image due to an error. "
            f"Please resize it manually and resend."
        )
