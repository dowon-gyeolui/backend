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
        # iPhone Safari uploads HEIC by default — force-convert at storage
        # time so secure_url ends in .jpg, which every browser can render.
        "format": "jpg",
        # Android phones embed EXIF orientation tags ("rotate 90° CW for
        # display") instead of rotating the pixel data. Without
        # angle: "exif" Cloudinary strips the metadata and ships the
        # un-rotated pixels — photo shows up sideways. Apply the
        # rotation to actual pixels first, then it's safe to drop EXIF.
        "transformation": [
            {"angle": "exif"},
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


def upload_image_full(
    file_bytes: bytes,
    *,
    public_id: str | None = None,
) -> dict[str, str]:
    """Like upload_image but also returns Cloudinary's public_id.

    Multi-photo galleries need public_id so we can delete the asset from
    Cloudinary when the user removes a photo. The single-photo upload_image
    above can stay simple since profile photos overwrite the same id.
    """
    _ensure_configured()

    import cloudinary
    import cloudinary.uploader

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
        "format": "jpg",
        # See upload_image — Android EXIF orientation gets stripped
        # without losing the rotation info if we apply it first.
        "transformation": [
            {"angle": "exif"},
            {"width": 800, "height": 800, "crop": "limit", "quality": "auto"},
        ],
    }
    if public_id is not None:
        upload_kwargs["public_id"] = public_id
        upload_kwargs["overwrite"] = True

    result = cloudinary.uploader.upload(file_bytes, **upload_kwargs)
    secure_url = result.get("secure_url")
    cloud_public_id = result.get("public_id")
    if not isinstance(secure_url, str):
        raise RuntimeError("Cloudinary did not return a secure_url")
    return {
        "url": secure_url,
        "public_id": str(cloud_public_id) if cloud_public_id else "",
    }


def delete_image(public_id: str) -> None:
    """Best-effort delete of a Cloudinary image by public_id.

    Called when a user removes a photo from their gallery. We swallow
    any error so the DB row is still removed even if the Cloudinary
    side already evicted the asset.
    """
    if not public_id:
        return
    try:
        _ensure_configured()
        import cloudinary
        import cloudinary.uploader

        if not os.environ.get("CLOUDINARY_URL"):
            cloudinary.config(
                cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
                api_key=os.environ["CLOUDINARY_API_KEY"],
                api_secret=os.environ["CLOUDINARY_API_SECRET"],
                secure=True,
            )
        cloudinary.uploader.destroy(public_id, resource_type="image")
    except Exception:
        # Orphan asset is fine — DB is the source of truth for what's shown.
        pass


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
        # Same HEIC + EXIF concerns as profile photos — apply EXIF
        # rotation first, then normalize to .jpg so chat images render
        # right-side-up across iOS/Android.
        format="jpg",
        transformation=[
            {"angle": "exif"},
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