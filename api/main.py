import os
import uuid
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Depends
from supabase import create_client, Client

import requests

from schemas import (
    JobStartRequest,
    JobStartResponse,
    JobControlResponse,
    SignedUploadRequest,
    SignedUploadResponse,
    CreateScanRequest,
    ConfirmOriginalsRequest,
    ConfirmOriginalsResponse,
    SignedDownloadRequest,
)

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

# ----------------------------
# Simple device auth (v0)
# ----------------------------
DEVICE_API_KEY = os.environ.get("DEVICE_API_KEY")
if not DEVICE_API_KEY:
    raise RuntimeError("DEVICE_API_KEY is not set")


def require_device_key(x_device_key: str = Header(default="")):
    if x_device_key != DEVICE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid device key")


# ----------------------------
# Supabase client (service role)
# ----------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Buckets
ORIGINALS_BUCKET = "diamond-images"
PREVIEWS_BUCKET = "diamond-previews"

app = FastAPI(title="Nova API")


@app.get("/health")
def health():
    return {"status": "ok"}


# ----------------------------
# Helpers
# ----------------------------
def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def storage_object_exists(bucket: str, path: str) -> bool:
    """
    Check if an object exists in Supabase Storage using HTTP HEAD.
    Works with service role key (bypasses RLS/policies).
    """
    base = SUPABASE_URL.rstrip("/")  # remove trailing slash if any
    url = f"{base}/storage/v1/object/{bucket}/{path}"

    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
    }

    r = requests.head(url, headers=headers, timeout=10)
    if r.status_code == 200:
        return True
    if r.status_code == 404:
        return False

    # other errors are real problems (permissions, bad url, etc.)
    raise HTTPException(
        status_code=502,
        detail=f"Storage HEAD unexpected status={r.status_code} bucket={bucket} path={path} body={r.text}",
    )


def object_exists_in_storage(bucket: str, object_name: str) -> bool:
    """
    Verifies existence by querying Postgres: storage.objects
    This is reliable and does NOT require downloading the file.
    """
    res = (
        supabase.schema("storage")
        .table("objects")
        .select("id")
        .eq("bucket_id", bucket)
        .eq("name", object_name)
        .limit(1)
        .execute()
    )
    return bool(res.data)

def get_org_id_by_slug(org_slug: str) -> str:
    org_res = supabase.table("orgs").select("id").eq("slug", org_slug).execute()
    if not org_res.data:
        raise HTTPException(status_code=404, detail=f"org_slug '{org_slug}' not found")
    return org_res.data[0]["id"]


def get_or_create_device(org_id: str, device_name: str) -> str:
    dev_res = (
        supabase.table("devices")
        .select("id")
        .eq("org_id", org_id)
        .eq("name", device_name)
        .execute()
    )
    if dev_res.data:
        return dev_res.data[0]["id"]

    ins_dev = supabase.table("devices").insert({"org_id": org_id, "name": device_name}).execute()
    return ins_dev.data[0]["id"]


def ensure_job_exists(job_id: str, org_id: str) -> dict:
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


# ----------------------------
# Signed download (debug / UI)
# ----------------------------
@app.post("/storage/signed-download")
def signed_download(payload: SignedDownloadRequest):
    bucket = payload.bucket
    path = payload.storage_path

    # Security check: path must belong to org
    if not path.startswith(payload.org_slug + "/"):
        raise HTTPException(status_code=403, detail="storage_path does not belong to org")

    res = supabase.storage.from_(bucket).create_signed_url(path, payload.expires_in)
    url = res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
    if not url:
        raise HTTPException(status_code=500, detail=f"Unexpected signed url response: {res}")
    return {"signed_url": url}


# ----------------------------
# Job lifecycle
# ----------------------------
@app.post("/jobs/start", response_model=JobStartResponse, dependencies=[Depends(require_device_key)])
def jobs_start(payload: JobStartRequest):
    org_id = get_org_id_by_slug(payload.org_slug)

    device_id = None
    if payload.device_name:
        device_id = get_or_create_device(org_id, payload.device_name)

    job_id = str(uuid.uuid4())

    ins = (
        supabase.table("jobs")
        .insert(
            {
                "id": job_id,
                "org_id": org_id,
                "device_id": device_id,
                "external_ref": payload.external_ref,
                "status": "SCANNING",
                "started_at": now_utc_iso(),
            }
        )
        .execute()
    )
    _ = ins.data[0]
    return JobStartResponse(job_id=job_id, status="SCANNING")


@app.post("/jobs/{job_id}/pause", response_model=JobControlResponse, dependencies=[Depends(require_device_key)])
def jobs_pause(job_id: str, org_slug: str):
    org_id = get_org_id_by_slug(org_slug)
    _ = ensure_job_exists(job_id, org_id)

    upd = (
        supabase.table("jobs")
        .update({"status": "PAUSED", "paused_at": now_utc_iso()})
        .eq("id", job_id)
        .execute()
    )
    return JobControlResponse(job_id=job_id, status=upd.data[0]["status"])


@app.post("/jobs/{job_id}/resume", response_model=JobControlResponse, dependencies=[Depends(require_device_key)])
def jobs_resume(job_id: str, org_slug: str):
    org_id = get_org_id_by_slug(org_slug)
    _ = ensure_job_exists(job_id, org_id)

    upd = (
        supabase.table("jobs")
        .update({"status": "SCANNING", "paused_at": None})
        .eq("id", job_id)
        .execute()
    )
    return JobControlResponse(job_id=job_id, status=upd.data[0]["status"])


@app.post("/jobs/{job_id}/stop", response_model=JobControlResponse, dependencies=[Depends(require_device_key)])
def jobs_stop(job_id: str, org_slug: str):
    org_id = get_org_id_by_slug(org_slug)
    _ = ensure_job_exists(job_id, org_id)

    upd = (
        supabase.table("jobs")
        .update({"status": "PROCESSING", "ended_at": now_utc_iso()})
        .eq("id", job_id)
        .execute()
    )
    return JobControlResponse(job_id=job_id, status=upd.data[0]["status"])


# ----------------------------
# Signed upload URLs (requires existing job_id)
# ----------------------------
@app.post("/storage/signed-urls", response_model=SignedUploadResponse, dependencies=[Depends(require_device_key)])
def create_signed_urls(payload: SignedUploadRequest):
    org_id = get_org_id_by_slug(payload.org_slug)
    _job = ensure_job_exists(payload.job_id, org_id)

    job_id = payload.job_id

    # Originals
    uv_free_path = f"{payload.org_slug}/{job_id}/{payload.ring_label}/slot_{payload.slot_index}_uv_free.jpg"
    aset_path = f"{payload.org_slug}/{job_id}/{payload.ring_label}/slot_{payload.slot_index}_aset.jpg"

    # Previews
    uv_free_preview_path = f"{payload.org_slug}/{job_id}/{payload.ring_label}/slot_{payload.slot_index}_uv_free_thumb.jpg"
    aset_preview_path = f"{payload.org_slug}/{job_id}/{payload.ring_label}/slot_{payload.slot_index}_aset_thumb.jpg"

    uv = supabase.storage.from_(ORIGINALS_BUCKET).create_signed_upload_url(uv_free_path)
    aset = supabase.storage.from_(ORIGINALS_BUCKET).create_signed_upload_url(aset_path)
    uv_p = supabase.storage.from_(PREVIEWS_BUCKET).create_signed_upload_url(uv_free_preview_path)
    aset_p = supabase.storage.from_(PREVIEWS_BUCKET).create_signed_upload_url(aset_preview_path)

    uv_url = uv.get("signedUrl") or uv.get("signed_url")
    aset_url = aset.get("signedUrl") or aset.get("signed_url")
    uv_p_url = uv_p.get("signedUrl") or uv_p.get("signed_url")
    aset_p_url = aset_p.get("signedUrl") or aset_p.get("signed_url")

    if not uv_url or not aset_url or not uv_p_url or not aset_p_url:
        raise HTTPException(status_code=500, detail=f"Unexpected signed upload response")

    return SignedUploadResponse(
        job_id=job_id,
        uv_free_path=uv_free_path,
        aset_path=aset_path,
        uv_free_signed_url=uv_url,
        aset_signed_url=aset_url,
        uv_free_preview_path=uv_free_preview_path,
        aset_preview_path=aset_preview_path,
        uv_free_preview_signed_url=uv_p_url,
        aset_preview_signed_url=aset_p_url,
    )


# ----------------------------
# Ingest scan (requires existing job_id; stores previews now)
# ----------------------------
@app.post("/ingest/scan", dependencies=[Depends(require_device_key)])
def ingest_scan(payload: CreateScanRequest):
    org_id = get_org_id_by_slug(payload.org_slug)
    job = ensure_job_exists(payload.job_id, org_id)

    if job["status"] not in ("SCANNING", "PAUSED"):
        raise HTTPException(status_code=409, detail=f"job status is {job['status']}, cannot ingest")

    # ring
    ring_id = get_or_create_ring(payload.job_id, payload.ring_label)

    base = f"{payload.org_slug}/{payload.job_id}/{payload.ring_label}/slot_{payload.slot_index}"
    uv_free_path = base + "_uv_free.jpg"
    aset_path = base + "_aset.jpg"
    uv_free_preview_path = base + "_uv_free_thumb.jpg"
    aset_preview_path = base + "_aset_thumb.jpg"

    # diamond idempotency
    existing = (
        supabase.table("diamonds")
        .select("id")
        .eq("job_id", payload.job_id)
        .eq("ring_id", ring_id)
        .eq("slot_index", payload.slot_index)
        .execute()
    )
    if existing.data:
        raise HTTPException(status_code=409, detail="Diamond already exists for this job / ring / slot_index")

    dia_ins = (
        supabase.table("diamonds")
        .insert({"job_id": payload.job_id, "ring_id": ring_id, "slot_index": payload.slot_index})
        .execute()
    )
    diamond_id = dia_ins.data[0]["id"]

    # image rows (previews are ready immediately)
    rows = [
        {
            "diamond_id": diamond_id,
            "image_type": "UV_FREE",
            "storage_path": uv_free_path,
            "preview_storage_path": uv_free_preview_path,
            "preview_ready": True,
            "original_ready": False,
        },
        {
            "diamond_id": diamond_id,
            "image_type": "ASET",
            "storage_path": aset_path,
            "preview_storage_path": aset_preview_path,
            "preview_ready": True,
            "original_ready": False,
        },
    ]
    supabase.table("diamond_images").insert(rows).execute()

    return {"job_id": payload.job_id, "ring_id": ring_id, "diamond_id": diamond_id, "message": "scan ingested"}


# ----------------------------
# Confirm originals uploaded later
# ----------------------------
@app.post(
    "/ingest/confirm-originals",
    response_model=ConfirmOriginalsResponse,
    dependencies=[Depends(require_device_key)],
)
def confirm_originals(payload: ConfirmOriginalsRequest):
    # 1) Find org
    org_res = supabase.table("orgs").select("id").eq("slug", payload.org_slug).execute()
    if not org_res.data:
        raise HTTPException(status_code=404, detail=f"org_slug '{payload.org_slug}' not found")
    org_id = org_res.data[0]["id"]

    # 2) Verify job exists + belongs to org
    job_res = (
        supabase.table("jobs")
        .select("id, org_id")
        .eq("id", payload.job_id)
        .maybe_single()
        .execute()
    )
    if not job_res.data:
        raise HTTPException(status_code=404, detail="job_id not found")
    if job_res.data["org_id"] != org_id:
        raise HTTPException(status_code=403, detail="job_id does not belong to this org")

    # 3) Find ring
    ring_res = (
        supabase.table("rings")
        .select("id")
        .eq("job_id", payload.job_id)
        .eq("ring_label", payload.ring_label)
        .maybe_single()
        .execute()
    )
    if not ring_res.data:
        raise HTTPException(status_code=404, detail="ring not found for job_id + ring_label")
    ring_id = ring_res.data["id"]

    # 4) Find diamond
    dia_res = (
        supabase.table("diamonds")
        .select("id")
        .eq("job_id", payload.job_id)
        .eq("ring_id", ring_id)
        .eq("slot_index", payload.slot_index)
        .maybe_single()
        .execute()
    )
    if not dia_res.data:
        raise HTTPException(status_code=404, detail="diamond not found for job_id + ring_label + slot_index")
    diamond_id = dia_res.data["id"]

    # 5) Derive expected originals paths (canonical)
    base = f"{payload.org_slug}/{payload.job_id}/{payload.ring_label}/slot_{payload.slot_index}"
    expected = {
        "UV_FREE": f"{base}_uv_free.jpg",
        "ASET": f"{base}_aset.jpg",
    }

    # 6) Verify the objects exist in the ORIGINALS bucket
    missing: list[str] = []
    confirmed: list[str] = []

    for t in payload.image_types:
        path = expected[t]
        # must belong to org
        if not path.startswith(payload.org_slug + "/"):
            raise HTTPException(status_code=403, detail=f"path not in org: {path}")
        # must belong to job
        job_prefix = f"{payload.org_slug}/{payload.job_id}/"
        if not path.startswith(job_prefix):
            raise HTTPException(status_code=403, detail=f"path not in job: {path}")
        
        if storage_object_exists(ORIGINALS_BUCKET, path):
            confirmed.append(t)
        else:
            missing.append(path)

    # If anything missing, do NOT mark DB as ready
    if missing:
        # 409 makes sense: “not yet uploaded”
        raise HTTPException(
            status_code=409,
            detail={"message": "originals not found in storage yet", "missing": missing},
        )

    # 7) Mark DB rows as originals-ready
    ts = now_utc_iso()
    for t in confirmed:
        upd = (
            supabase.table("diamond_images")
            .update({"original_ready": True, "original_uploaded_at": ts})
            .eq("diamond_id", diamond_id)
            .eq("image_type", t)
            .execute()
        )

    return ConfirmOriginalsResponse(
        job_id=payload.job_id,
        diamond_id=diamond_id,
        confirmed=confirmed,
        missing=[],
    )

