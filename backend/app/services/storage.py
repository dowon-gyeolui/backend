"""Image storage helpers — Cloudinary backend.

Profile photos go through this so we keep DB rows light (just URLs)
and the heavy bytes live on Cloudinary's CDN. Other storage backends
(S3, Render disk) can plug in here later behind the same `upload_image`
contract.
"""

from __future__ import annotations

import os
from typing import Final

# Cloudinary's SDK reads CLOUDINARY_URL or the three split env vars
# (CLOUDINARY_CLOUD_NAME / API_KEY / API_SECRET) at module import time,
# so configuration is implicit once the env is set on Render / .env.

_FOLDER: Final[str] = "zami/profile"


class StorageNotConfiguredError(RuntimeError):
    """Raised when Cloudinary credentials are missing."""


def _ensure_configured() -> None:
    has_url = bool(os.environ.get("CLOUDINARY_URL"))
    has_split = all(
        os.environ.get(k)
        for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET")
    )
    if not (has_url or has_split):
        raise StorageNotConfiguredError(
            "Cloudinary credentials are not set. Define CLOUDINARY_URL or "
            "the CLOUDINARY_CLOUD_NAME / API_KEY / API_SECRET trio in .env "
            "(local) or the Render Environment tab (production)."
        )


def upload_image(file_bytes: bytes, *, public_id: str | None = None) -> str:
    """Upload raw image bytes to Cloudinary and return the secure URL.

    `public_id` is optional — when provided we use it (e.g. "user_42") so
    re-uploading replaces the previous photo instead of accumulating.
    Cloudinary auto-detects the format from the bytes.
    """
    _ensure_configured()

    # Lazy import — avoids requiring cloudinary at module load when the
    # endpoint isn't being hit (e.g. in unit tests with no creds).
    import cloudinary
    import cloudinary.uploader

    # If only the URL form is set, the SDK auto-configures. Otherwise we
    # explicitly bind the split env vars for safety across deploys.
    if not os.environ.get("CLOUDINARY_URL"):
        cloudinary.config(
            cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
            api_key=os.environ["CLOUDINARY_API_KEY"],
            api_secret=os.environ["CLOUDINARY_API_SECRET"],
            secure=True,
        )

    upload_kwargs: dict[str, object] = {
        "folder": _FOLDER,
        "resource_type": "image",
        "overwrite": True,
        # Square thumbnails for match cards. Cloudinary returns extra URLs
        # we don't need; we just keep the secure_url.
        "transformation": [
            {"width": 800, "height": 800, "crop": "limit", "quality": "auto"},
        ],
    }
    if public_id is not None:
        upload_kwargs["public_id"] = public_id

    result = cloudinary.uploader.upload(file_bytes, **upload_kwargs)
    secure_url = result.get("secure_url")
    if not isinstance(secure_url, str):
        raise RuntimeError("Cloudinary did not return a secure_url")
    return secure_url