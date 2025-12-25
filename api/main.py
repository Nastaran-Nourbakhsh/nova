from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Depends
from supabase import create_client, Client

from schemas import (
    StartJobRequest,
    StartJobResponse,
    JobActionResponse,
    SignedUploadRequest,
    SignedUploadResponse,
    SignedDownloadRequest,
    SignedDownloadResponse,
    CreateScanRequest,
    ConfirmOriginalsRequest,
    ConfirmOriginalsResponse,
)

# ----------------------------
# Env
# ----------------------------
load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

DEVICE_API_KEY = os.environ.get("DEVICE_API_KEY")
if not DEVICE_API_KEY:
    raise RuntimeError("DEVICE_API_KEY is not set")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

# Fix: storage3 warns if URL lacks trailing slash
if not SUPABASE_URL.endswith("/"):
    SUPABASE_URL = SUPABASE_URL + "/"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

ORIGINALS_BUCKET = "diamond-images"
PREVIEWS_BUCKET = "diamond-previews"

app = FastAPI(title="Nova API")


def require_device_key(x_device_key: str = Header(default="")):
    if x_device_key != DEVICE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid device key")


@app.get("/health")
def health():
    return {"status": "ok"}


# ----------------------------
# Helpers
# ----------------------------
def get_org_id(org_slug: str) -> str:
    org_res = supabase.table("orgs").select("id").eq("slug", org_slug).execute()
    if not org_res.data:
        raise HTTPException(status_code=404, detail=f"org_slug '{org_slug}' not found")
    return org_res.data[0]["id"]


def get_or_create_device(org_id: str, device_name: Optional[str]) -> Optional[str]:
    if not device_name:
        return None
    dev_res = (
        supabase.table("devices")
        .select("id")
        .eq("org_id", org_id)
        .eq("name", device_name)
        .execute()
    )
    if dev_res.data:
        return dev_res.data[0]["id"]
    ins = supabase.table("devices").insert({"org_id": org_id, "name": device_name}).execute()
    return ins.data[0]["id"]


def ensure_job_exists(job_id: str, org_id: str) -> Dict[str, Any]:
    # maybe_single() can be flaky across versions; use select + check list
    res = supabase.table("jobs").select("id, org_id, status").eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="job_id not found")
    job = res.data[0]
    if job["org_id"] != org_id:
        raise HTTPException(status_code=403, detail="job_id does not belong to this org")
    return job

def get_or_create_ring(job_id: str, ring_label: str) -> str:
    ring_res = (
        supabase.table("rings")
        .select("id")
        .eq("job_id", job_id)
        .eq("ring_label", ring_label)
        .execute()
    )
    if ring_res.data:
        return ring_res.data[0]["id"]

    ring_ins = supabase.table("rings").insert({"job_id": job_id, "ring_label": ring_label}).execute()
    return ring_ins.data[0]["id"]


def object_exists_in_storage(bucket: str, storage_path: str) -> bool:
    """
    storage_path is bucket-relative, e.g.:
      first-customer/<jobId>/A/slot_0_uv_free.jpg

    We check existence via Storage list() in the parent folder,
    using server-side search to avoid list() pagination limits.
    """
    storage_path = storage_path.lstrip("/")
    if "/" in storage_path:
        parent, name = storage_path.rsplit("/", 1)
    else:
        parent, name = "", storage_path

    # IMPORTANT: storage3 python expects options dict, not a 'search=' kwarg.
    res = supabase.storage.from_(bucket).list(
        parent,
        options={"limit": 100, "offset": 0, "search": name},
    )

    # res is list[dict] like {"name": "..."}
    return any(obj.get("name") == name for obj in (res or []))


    # # list() returns objects in a folder; use search if supported by your storage3 version
    # try:
    #     res = supabase.storage.from_(bucket).list(path=parent)
    # except Exception:
    #     # Some versions require path="" instead of None
    #     res = supabase.storage.from_(bucket).list(path=parent or "")
    #
    # if not isinstance(res, list):
    #     return False
    #
    # for obj in res:
    #     if obj.get("name") == name:
    #         return True
    # return False


def canonical_paths(org_slug: str, job_id: str, ring_label: str, slot_index: int) -> Dict[str, str]:
    base = f"{org_slug}/{job_id}/{ring_label}/slot_{slot_index}"
    return {
        "uv_free_path": f"{base}_uv_free.jpg",
        "aset_path": f"{base}_aset.jpg",
        "uv_free_preview_path": f"{base}_uv_free_thumb.jpg",
        "aset_preview_path": f"{base}_aset_thumb.jpg",
    }


# ----------------------------
# Jobs lifecycle
# ----------------------------
@app.post("/jobs/start", response_model=StartJobResponse, dependencies=[Depends(require_device_key)])
def jobs_start(payload: StartJobRequest):
    org_id = get_org_id(payload.org_slug)
    device_id = get_or_create_device(org_id, payload.device_name)

    ins = supabase.table("jobs").insert(
        {"org_id": org_id, "device_id": device_id, "status": "SCANNING"}
    ).execute()
    job_id = ins.data[0]["id"]
    return StartJobResponse(job_id=job_id)


@app.post("/jobs/{job_id}/pause", response_model=JobActionResponse, dependencies=[Depends(require_device_key)])
def jobs_pause(job_id: str):
    upd = supabase.table("jobs").update({"status": "PAUSED"}).eq("id", job_id).execute()
    if not upd.data:
        raise HTTPException(status_code=404, detail="job not found")
    return JobActionResponse(job_id=job_id, status=upd.data[0]["status"])


@app.post("/jobs/{job_id}/resume", response_model=JobActionResponse, dependencies=[Depends(require_device_key)])
def jobs_resume(job_id: str):
    upd = supabase.table("jobs").update({"status": "SCANNING"}).eq("id", job_id).execute()
    if not upd.data:
        raise HTTPException(status_code=404, detail="job not found")
    return JobActionResponse(job_id=job_id, status=upd.data[0]["status"])


@app.post("/jobs/{job_id}/stop", response_model=JobActionResponse, dependencies=[Depends(require_device_key)])
def jobs_stop(job_id: str):
    upd = supabase.table("jobs").update({"status": "STOPPED"}).eq("id", job_id).execute()
    if not upd.data:
        raise HTTPException(status_code=404, detail="job not found")
    return JobActionResponse(job_id=job_id, status=upd.data[0]["status"])


# ----------------------------
# Signed upload/download URLs
# ----------------------------
@app.post("/storage/signed-urls", response_model=SignedUploadResponse, dependencies=[Depends(require_device_key)])
def create_signed_urls(payload: SignedUploadRequest):
    org_id = get_org_id(payload.org_slug)
    ensure_job_exists(payload.job_id, org_id)

    p = canonical_paths(payload.org_slug, payload.job_id, payload.ring_label, payload.slot_index)

    mode = payload.mode
    out = SignedUploadResponse(job_id=payload.job_id)

    # originals
    if mode in ("both", "originals"):
        out.uv_free_path = p["uv_free_path"]
        out.aset_path = p["aset_path"]

        uv = supabase.storage.from_(ORIGINALS_BUCKET).create_signed_upload_url(out.uv_free_path)
        aset = supabase.storage.from_(ORIGINALS_BUCKET).create_signed_upload_url(out.aset_path)

        out.uv_free_signed_url = uv.get("signedUrl") or uv.get("signed_url")
        out.aset_signed_url = aset.get("signedUrl") or aset.get("signed_url")
        if not out.uv_free_signed_url or not out.aset_signed_url:
            raise HTTPException(status_code=500, detail=f"Unexpected originals signed upload response: uv={uv} aset={aset}")

    # previews
    if mode in ("both", "previews"):
        out.uv_free_preview_path = p["uv_free_preview_path"]
        out.aset_preview_path = p["aset_preview_path"]

        # NOTE: this is what used to break your --sync when previews already existed
        uvp = supabase.storage.from_(PREVIEWS_BUCKET).create_signed_upload_url(out.uv_free_preview_path)
        asetp = supabase.storage.from_(PREVIEWS_BUCKET).create_signed_upload_url(out.aset_preview_path)

        out.uv_free_preview_signed_url = uvp.get("signedUrl") or uvp.get("signed_url")
        out.aset_preview_signed_url = asetp.get("signedUrl") or asetp.get("signed_url")
        if not out.uv_free_preview_signed_url or not out.aset_preview_signed_url:
            raise HTTPException(status_code=500, detail=f"Unexpected previews signed upload response: uvp={uvp} asetp={asetp}")

    return out


@app.post("/storage/signed-download", response_model=SignedDownloadResponse)
def signed_download(payload: SignedDownloadRequest):
    bucket = payload.bucket
    path = payload.storage_path

    # normalize: allow "bucket/path"
    prefix = f"{bucket}/"
    if path.startswith(prefix):
        path = path[len(prefix):]

    if not path.startswith(payload.org_slug + "/"):
        raise HTTPException(status_code=403, detail="storage_path does not belong to org")

    res = supabase.storage.from_(bucket).create_signed_url(path, payload.expires_in)
    url = res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
    if not url:
        raise HTTPException(status_code=500, detail=f"Unexpected signed url response: {res}")
    return SignedDownloadResponse(signed_url=url)


# ----------------------------
# Ingest scan
# ----------------------------
@app.post("/ingest/scan", dependencies=[Depends(require_device_key)])
def ingest_scan(payload: CreateScanRequest):
    org_id = get_org_id(payload.org_slug)
    device_id = get_or_create_device(org_id, payload.device_name)

    # job must already exist (created by /jobs/start)
    job = ensure_job_exists(payload.job_id, org_id)
    job_id = job["id"]

    ring_id = get_or_create_ring(job_id, payload.ring_label)

    # idempotency: one diamond per (job, ring, slot)
    existing = (
        supabase.table("diamonds")
        .select("id")
        .eq("job_id", job_id)
        .eq("ring_id", ring_id)
        .eq("slot_index", payload.slot_index)
        .execute()
    )
    if existing.data:
        raise HTTPException(status_code=409, detail="Diamond already exists for this job / ring / slot_index")

    dia_ins = (
        supabase.table("diamonds")
        .insert({"job_id": job_id, "ring_id": ring_id, "slot_index": payload.slot_index})
        .execute()
    )
    diamond_id = dia_ins.data[0]["id"]

    rows = [
        {
            "diamond_id": diamond_id,
            "image_type": "UV_FREE",
            "storage_path": payload.uv_free_path,
            "preview_storage_path": payload.uv_free_preview_path,
            "preview_ready": bool(payload.uv_free_preview_path),
            "original_ready": False,
        },
        {
            "diamond_id": diamond_id,
            "image_type": "ASET",
            "storage_path": payload.aset_path,
            "preview_storage_path": payload.aset_preview_path,
            "preview_ready": bool(payload.aset_preview_path),
            "original_ready": False,
        },
    ]

    supabase.table("diamond_images").insert(rows).execute()

    return {
        "job_id": job_id,
        "ring_id": ring_id,
        "diamond_id": diamond_id,
        "message": "scan ingested",
    }


# ----------------------------
# Confirm originals (server verifies storage)
# ----------------------------
@app.post("/ingest/confirm-originals", response_model=ConfirmOriginalsResponse, dependencies=[Depends(require_device_key)])
def confirm_originals(payload: ConfirmOriginalsRequest):
    org_id = get_org_id(payload.org_slug)
    ensure_job_exists(payload.job_id, org_id)

    ring = (
        supabase.table("rings")
        .select("id")
        .eq("job_id", payload.job_id)
        .eq("ring_label", payload.ring_label)
        .maybe_single()
        .execute()
    )
    if not ring.data:
        raise HTTPException(status_code=404, detail="ring not found for job")
    ring_id = ring.data["id"]

    diamond = (
        supabase.table("diamonds")
        .select("id")
        .eq("job_id", payload.job_id)
        .eq("ring_id", ring_id)
        .eq("slot_index", payload.slot_index)
        .maybe_single()
        .execute()
    )
    if not diamond.data:
        raise HTTPException(status_code=404, detail="diamond not found for job/ring/slot")
    diamond_id = diamond.data["id"]

    imgs = (
        supabase.table("diamond_images")
        .select("id, image_type, storage_path, original_ready")
        .eq("diamond_id", diamond_id)
        .execute()
    )
    if not imgs.data:
        raise HTTPException(status_code=404, detail="diamond_images rows not found")

    updated = 0
    missing: List[str] = []

    for img in imgs.data:
        if img.get("original_ready"):
            continue

        path = img["storage_path"]
        if object_exists_in_storage(ORIGINALS_BUCKET, path):
            supabase.table("diamond_images").update(
                {"original_ready": True, "original_uploaded_at": "now()"}
            ).eq("id", img["id"]).execute()
            updated += 1
        else:
            missing.append(path)

    # NOTE: "now()" is not always treated as SQL; if you want strict timestamps,
    # add a DB trigger to set original_uploaded_at when original_ready flips true.

    return ConfirmOriginalsResponse(ok=True, updated_rows=updated, missing_paths=missing)
