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
_CHAT_FOLDER: Final[str] = "zami/chat"


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


def _config_cloudinary() -> None:
    _ensure_configured()
    import cloudinary

    if not os.environ.get("CLOUDINARY_URL"):
        cloudinary.config(
            cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
            api_key=os.environ["CLOUDINARY_API_KEY"],
            api_secret=os.environ["CLOUDINARY_API_SECRET"],
            secure=True,
        )


def upload_chat_image(file_bytes: bytes, *, sender_id: int) -> str:
    """채팅용 이미지 업로드 — zami/chat 폴더에 저장. 발신자별 prefix."""
    _config_cloudinary()
    import cloudinary.uploader

    result = cloudinary.uploader.upload(
        file_bytes,
        folder=f"{_CHAT_FOLDER}/img/{sender_id}",
        resource_type="image",
        transformation=[
            {"width": 1280, "height": 1280, "crop": "limit", "quality": "auto"},
        ],
    )
    secure_url = result.get("secure_url")
    if not isinstance(secure_url, str):
        raise RuntimeError("Cloudinary did not return a secure_url")
    return secure_url


def upload_chat_audio(file_bytes: bytes, *, sender_id: int) -> str:
    """채팅용 음성 메시지 업로드. Cloudinary 는 audio 도 'video' resource 로 처리."""
    _config_cloudinary()
    import cloudinary.uploader

    result = cloudinary.uploader.upload(
        file_bytes,
        folder=f"{_CHAT_FOLDER}/audio/{sender_id}",
        # Cloudinary 의 audio 는 resource_type='video' 로 업로드. 'auto'로 두면
        # webm/mp3/m4a 등 오디오 컨테이너를 알아서 video resource 로 분류.
        resource_type="video",
    )
    secure_url = result.get("secure_url")
    if not isinstance(secure_url, str):
        raise RuntimeError("Cloudinary did not return a secure_url")
    return secure_url