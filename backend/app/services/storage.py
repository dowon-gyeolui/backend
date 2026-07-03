"""이미지/미디어 저장소 헬퍼 — Cloudinary 백엔드."""

from __future__ import annotations

import os
from typing import Final

_FOLDER: Final[str] = "zami/profile"
_CHAT_FOLDER: Final[str] = "zami/chat"


class StorageNotConfiguredError(RuntimeError):
    pass


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
        "overwrite": True,
        "format": "jpg",
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
    _config_cloudinary()
    import cloudinary.uploader

    result = cloudinary.uploader.upload(
        file_bytes,
        folder=f"{_CHAT_FOLDER}/img/{sender_id}",
        resource_type="image",
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


def _public_id_from_cloudinary_url(url: str) -> str | None:
    try:
        after = url.split("/upload/", 1)[1]
    except (IndexError, AttributeError):
        return None
    parts = after.split("/")
    if parts and parts[0].startswith("v") and parts[0][1:].isdigit():
        parts = parts[1:]
    path = "/".join(parts)
    if "." in path.rsplit("/", 1)[-1]:
        path = path.rsplit(".", 1)[0]
    return path or None


def delete_chat_audio_by_url(url: str) -> None:
    public_id = _public_id_from_cloudinary_url(url)
    if not public_id:
        return
    try:
        _config_cloudinary()
        import cloudinary.uploader

        cloudinary.uploader.destroy(public_id, resource_type="video")
    except Exception:
        pass


def upload_chat_audio(file_bytes: bytes, *, sender_id: int) -> str:
    _config_cloudinary()
    import cloudinary.uploader

    result = cloudinary.uploader.upload(
        file_bytes,
        folder=f"{_CHAT_FOLDER}/audio/{sender_id}",
        resource_type="video",
    )
    secure_url = result.get("secure_url")
    if not isinstance(secure_url, str):
        raise RuntimeError("Cloudinary did not return a secure_url")
    return secure_url