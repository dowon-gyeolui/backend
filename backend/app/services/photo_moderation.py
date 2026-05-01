"""Profile-photo automated moderation using AWS Rekognition.

Two checks per image:
  1. DetectFaces → ensures the photo has exactly one face that's reasonably
     sized within the frame (rejects landscape shots, group photos, photos
     where the user is barely visible).
  2. DetectModerationLabels → rejects NSFW / violent content.

Both are read-only Rekognition calls billed at ~$0.001 each, so a typical
profile-photo upload triggers about ₩2.6 of cost. Free-tier covers the
first 5,000 calls/month for the first 12 months.

If AWS credentials aren't configured, this module degrades gracefully —
the upload is allowed through (with a logged warning). That keeps local
dev / unconfigured environments working without forcing every contributor
to provision an AWS account.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


# Tunables — exposed as constants so we can A/B them later without
# redeploying. The face-area threshold is roughly "if the face occupies
# less than 8% of the frame, the user is too far / too small in shot."
MIN_FACE_AREA_RATIO = 0.08
MIN_FACE_CONFIDENCE = 90.0  # Rekognition confidence (0..100)
MAX_FACES = 1               # Only single-person photos pass
NSFW_BLOCK_LABELS = {
    # Top-level Rekognition moderation categories we hard-reject.
    "Explicit Nudity",
    "Nudity",
    "Graphic Male Nudity",
    "Graphic Female Nudity",
    "Sexual Activity",
    "Illustrated Explicit Nudity",
    "Adult Toys",
    "Violence",
    "Graphic Violence Or Gore",
    "Visually Disturbing",
    "Self Injury",
    "Hate Symbols",
}
NSFW_MIN_CONFIDENCE = 75.0


@dataclass
class ModerationResult:
    ok: bool
    reason: Optional[str] = None     # 한국어 사용자용 메시지
    detail: Optional[str] = None     # 로그용 상세 (어떤 레이블이 잡혔는지)

    @classmethod
    def passed(cls) -> "ModerationResult":
        return cls(ok=True)


@lru_cache(maxsize=1)
def _client():
    """Lazy boto3 client — only constructed when actually called.

    We import boto3 inside the function so the rest of the app can boot
    even when boto3 isn't installed (legacy deployments).
    """
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is not installed. pip install boto3") from exc

    region = os.environ.get("AWS_REGION", "ap-northeast-2")
    return boto3.client("rekognition", region_name=region)


def _credentials_present() -> bool:
    return bool(
        os.environ.get("AWS_ACCESS_KEY_ID")
        and os.environ.get("AWS_SECRET_ACCESS_KEY")
    )


def verify_profile_photo(image_bytes: bytes) -> ModerationResult:
    """Run face + NSFW checks on the image.

    Returns a ModerationResult — caller decides whether to upload to
    Cloudinary or reject. We run NSFW first because if the image is
    truly explicit we want to fail fast without analysing faces.

    On AWS error or missing credentials, returns `ok=True` (graceful
    degradation) so a misconfigured environment doesn't block real users.
    Production should always have credentials set, and if AWS is down the
    moderator queue catches issues anyway.
    """
    if not _credentials_present():
        logger.warning(
            "AWS credentials not set — skipping photo moderation. "
            "Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY in env."
        )
        return ModerationResult.passed()

    client = _client()

    # 1) NSFW check
    try:
        nsfw = client.detect_moderation_labels(
            Image={"Bytes": image_bytes},
            MinConfidence=NSFW_MIN_CONFIDENCE,
        )
        labels = nsfw.get("ModerationLabels") or []
        for label in labels:
            name = (label.get("Name") or "").strip()
            if name in NSFW_BLOCK_LABELS:
                return ModerationResult(
                    ok=False,
                    reason="부적절한 콘텐츠가 감지되어 등록할 수 없어요.",
                    detail=f"NSFW={name}",
                )
    except Exception as e:
        # Don't fail the whole upload on a transient AWS error — log and
        # let the photo through. The frontend already has a separate
        # report flow for users to flag bad content.
        logger.exception("Rekognition moderation call failed: %s", e)
        return ModerationResult.passed()

    # 2) Face check
    try:
        faces_resp = client.detect_faces(
            Image={"Bytes": image_bytes},
            Attributes=["DEFAULT"],
        )
        face_details = faces_resp.get("FaceDetails") or []
    except Exception as e:
        logger.exception("Rekognition face-detect call failed: %s", e)
        return ModerationResult.passed()

    if len(face_details) == 0:
        return ModerationResult(
            ok=False,
            reason="얼굴이 보이는 사진을 올려주세요.",
            detail="no_face",
        )
    if len(face_details) > MAX_FACES:
        return ModerationResult(
            ok=False,
            reason="본인 사진만 올려주세요. (사진에 사람이 여러 명 있어요)",
            detail=f"faces={len(face_details)}",
        )

    face = face_details[0]
    confidence = float(face.get("Confidence") or 0.0)
    if confidence < MIN_FACE_CONFIDENCE:
        return ModerationResult(
            ok=False,
            reason="더 선명한 사진을 올려주세요.",
            detail=f"low_face_confidence={confidence:.1f}",
        )

    # Face area = bbox width × height (each is a 0..1 ratio of frame).
    bbox = face.get("BoundingBox") or {}
    width = float(bbox.get("Width") or 0.0)
    height = float(bbox.get("Height") or 0.0)
    area_ratio = width * height
    if area_ratio < MIN_FACE_AREA_RATIO:
        return ModerationResult(
            ok=False,
            reason="얼굴이 더 크게 나오는 사진을 올려주세요.",
            detail=f"face_area_ratio={area_ratio:.3f}",
        )

    return ModerationResult.passed()