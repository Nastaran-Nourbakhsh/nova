from pydantic import BaseModel, Field
from typing import Optional, Literal, List


JobStatus = Literal["CREATED", "SCANNING", "PAUSED", "PROCESSING", "DONE", "FAILED"]


class JobStartRequest(BaseModel):
    org_slug: str = Field(..., examples=["first-customer"])
    device_name: Optional[str] = Field(default=None, examples=["Scanner-1"])
    external_ref: Optional[str] = Field(default=None, examples=["operator-session-xyz"])


class JobStartResponse(BaseModel):
    job_id: str
    status: JobStatus


class JobControlResponse(BaseModel):
    job_id: str
    status: JobStatus


class SignedUploadRequest(BaseModel):
    org_slug: str
    job_id: str
    ring_label: str
    slot_index: int


class SignedUploadResponse(BaseModel):
    job_id: str

    # Originals (bucket: diamond-images)
    uv_free_path: str
    aset_path: str
    uv_free_signed_url: str
    aset_signed_url: str

    # Previews (bucket: diamond-previews)
    uv_free_preview_path: str
    aset_preview_path: str
    uv_free_preview_signed_url: str
    aset_preview_signed_url: str


class CreateScanRequest(BaseModel):
    org_slug: str = Field(..., examples=["first-customer"])
    job_id: str = Field(..., examples=["<uuid>"])
    ring_label: str = Field(..., examples=["A"])
    slot_index: int = Field(..., ge=0)

    # # Originals paths (diamond-images)
    # uv_free_path: str = Field(..., examples=["first-customer/<jobId>/A/slot_0_uv_free.jpg"])
    # aset_path: str = Field(..., examples=["first-customer/<jobId>/A/slot_0_aset.jpg"])
    #
    # # Preview paths (diamond-previews) - REQUIRED for v1 flow
    # uv_free_preview_path: str = Field(..., examples=["first-customer/<jobId>/A/slot_0_uv_free_thumb.jpg"])
    # aset_preview_path: str = Field(..., examples=["first-customer/<jobId>/A/slot_0_aset_thumb.jpg"])

    device_name: Optional[str] = Field(default=None, examples=["Scanner-1"])

class ConfirmOriginalsRequest(BaseModel):
    org_slug: str
    job_id: str
    ring_label: str
    slot_index: int
    # Which originals are now uploaded
    uv_free_uploaded: bool = True
    aset_uploaded: bool = True
    # optional: confirm one or both
    image_types: List[Literal["UV_FREE", "ASET"]] = Field(default_factory=lambda: ["UV_FREE", "ASET"])

class ConfirmOriginalsResponse(BaseModel):
    job_id: str
    diamond_id: str
    confirmed: List[str]
    missing: List[str]

class SignedDownloadRequest(BaseModel):
    org_slug: str
    bucket: str = Field(..., examples=["diamond-previews"])
    storage_path: str
    expires_in: int = 600
