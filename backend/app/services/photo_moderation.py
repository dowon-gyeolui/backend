"""프로필 사진 얼굴·NSFW 자동 모더레이션 — AWS Rekognition 기반."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


MIN_FACE_AREA_RATIO = 0.25
MIN_FACE_CONFIDENCE = 90.0
MAX_FACES = 1
NSFW_BLOCK_LABELS = {
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
    reason: Optional[str] = None
    detail: Optional[str] = None

    @classmethod
    def passed(cls) -> "ModerationResult":
        return cls(ok=True)


@lru_cache(maxsize=1)
def _client():
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
    if not _credentials_present():
        logger.warning(
            "AWS credentials not set — skipping photo moderation. "
            "Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY in env."
        )
        return ModerationResult.passed()

    client = _client()

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
        logger.exception("Rekognition moderation call failed: %s", e)
        return ModerationResult.passed()

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

    bbox = face.get("BoundingBox") or {}
    width = float(bbox.get("Width") or 0.0)
    height = float(bbox.get("Height") or 0.0)
    area_ratio = width * height
    if area_ratio < MIN_FACE_AREA_RATIO:
        return ModerationResult(
            ok=False,
            reason=(
                "얼굴이 사진의 25% 이상 보여야 등록할 수 있어요. "
                "얼굴 위주의 셀카 형태로 다시 올려주세요."
            ),
            detail=f"face_area_ratio={area_ratio:.3f}",
        )

    return ModerationResult.passed()