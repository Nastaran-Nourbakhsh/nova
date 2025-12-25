from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional, Literal


# ----------------------------
# Jobs lifecycle
# ----------------------------
class StartJobRequest(BaseModel):
    org_slug: str = Field(..., examples=["first-customer"])
    device_name: str = Field(..., examples=["Scanner-1"])


class StartJobResponse(BaseModel):
    job_id: str


class JobActionResponse(BaseModel):
    job_id: str
    status: str


# ----------------------------
# Storage signed URLs
# ----------------------------
SignedUrlMode = Literal["both", "previews", "originals"]


class SignedUploadRequest(BaseModel):
    org_slug: str = Field(..., examples=["first-customer"])
    job_id: str = Field(..., examples=["<uuid>"])
    ring_label: str = Field(..., examples=["A"])
    slot_index: int = Field(..., ge=0)
    mode: SignedUrlMode = Field(default="both", examples=["both", "previews", "originals"])


class SignedUploadResponse(BaseModel):
    job_id: str

    # Originals (bucket: diamond-images)
    uv_free_path: Optional[str] = None
    aset_path: Optional[str] = None
    uv_free_signed_url: Optional[str] = None
    aset_signed_url: Optional[str] = None

    # Previews (bucket: diamond-previews)
    uv_free_preview_path: Optional[str] = None
    aset_preview_path: Optional[str] = None
    uv_free_preview_signed_url: Optional[str] = None
    aset_preview_signed_url: Optional[str] = None


class SignedDownloadRequest(BaseModel):
    org_slug: str
    bucket: Literal["diamond-images", "diamond-previews"] = "diamond-images"
    storage_path: str
    expires_in: int = 600  # seconds


class SignedDownloadResponse(BaseModel):
    signed_url: str


# ----------------------------
# Ingestion
# ----------------------------
class CreateScanRequest(BaseModel):
    org_slug: str = Field(..., examples=["first-customer"])
    job_id: str = Field(..., examples=["<uuid>"])  # REQUIRED: no auto job creation
    ring_label: str = Field(..., examples=["A"])
    slot_index: int = Field(..., ge=0)

    # Originals (bucket: diamond-images)
    uv_free_path: str = Field(..., examples=["first-customer/<jobId>/A/slot_0_uv_free.jpg"])
    aset_path: str = Field(..., examples=["first-customer/<jobId>/A/slot_0_aset.jpg"])

    # Previews (bucket: diamond-previews)
    uv_free_preview_path: Optional[str] = Field(
        default=None,
        examples=["first-customer/<jobId>/A/slot_0_uv_free_thumb.jpg"],
    )
    aset_preview_path: Optional[str] = Field(
        default=None,
        examples=["first-customer/<jobId>/A/slot_0_aset_thumb.jpg"],
    )

    device_name: Optional[str] = Field(default=None, examples=["Scanner-1"])


class ConfirmOriginalsRequest(BaseModel):
    org_slug: str = Field(..., examples=["first-customer"])
    job_id: str = Field(..., examples=["<uuid>"])
    ring_label: str = Field(..., examples=["A"])
    slot_index: int = Field(..., ge=0)
    device_name: Optional[str] = Field(default=None, examples=["Scanner-1"])


class ConfirmOriginalsResponse(BaseModel):
    ok: bool
    updated_rows: int
    missing_paths: list[str] = []
