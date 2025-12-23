#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional, Any

from dotenv import load_dotenv
from supabase import create_client, Client
from postgrest.exceptions import APIError


def must_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        print(f"❌ Missing env var: {name}", file=sys.stderr)
        sys.exit(1)
    return v


def _bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def read_bytes(p: Path) -> bytes:
    if not p.exists():
        print(f"❌ File not found: {p}", file=sys.stderr)
        sys.exit(1)
    return p.read_bytes()


def normalize_url(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def retry(fn, tries: int = 12, sleep_s: float = 1.0, label: str = "operation"):
    last: Optional[Exception] = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            print(f"⏳ Retry {i+1}/{tries} for {label}: {e}")
            time.sleep(sleep_s)
    print(f"❌ Failed {label} after {tries} tries.")
    raise last  # type: ignore


def storage_upload_jpeg(sb: Client, bucket: str, key: str, file_path: Path, upsert: bool = True) -> None:
    data = read_bytes(file_path)
    sb.storage.from_(bucket).upload(
        path=key,
        file=data,
        file_options={
            "content-type": "image/jpeg",
            # supabase-py versions differ; this string form works broadly
            "upsert": "true" if upsert else "false",
        },
    )
    print(f"✅ Uploaded: {bucket}/{key}  ({file_path.name})")


def get_or_create_org(sb: Client, slug: str, name: str) -> str:
    sel = sb.table("orgs").select("id").eq("slug", slug).execute()
    if sel.data and len(sel.data) > 0:
        return sel.data[0]["id"]

    ins = sb.table("orgs").insert({"slug": slug, "name": name}).execute()
    return ins.data[0]["id"]


def get_or_create_device(sb: Client, org_id: str, device_name: str) -> str:
    sel = sb.table("devices").select("id").eq("org_id", org_id).eq("name", device_name).execute()
    if sel.data and len(sel.data) > 0:
        return sel.data[0]["id"]

    ins = sb.table("devices").insert({"org_id": org_id, "name": device_name}).execute()
    return ins.data[0]["id"]


def get_or_create_ring(sb: Client, job_id: str, ring_label: str) -> str:
    sel = sb.table("rings").select("id").eq("job_id", job_id).eq("ring_label", ring_label).execute()
    if sel.data and len(sel.data) > 0:
        return sel.data[0]["id"]

    ins = sb.table("rings").insert({"job_id": job_id, "ring_label": ring_label}).execute()
    return ins.data[0]["id"]


def diamond_exists(sb: Client, job_id: str, ring_id: str, slot_index: int) -> Optional[str]:
    sel = (
        sb.table("diamonds")
        .select("id")
        .eq("job_id", job_id)
        .eq("ring_id", ring_id)
        .eq("slot_index", slot_index)
        .execute()
    )
    if sel.data and len(sel.data) > 0:
        return sel.data[0]["id"]
    return None


def upsert_diamond_image(
    sb: Client,
    diamond_id: str,
    image_type: str,
    storage_path: str,
    preview_storage_path: Optional[str] = None,
) -> None:
    payload: dict[str, Any] = {"storage_path": storage_path}

    if preview_storage_path:
        payload["preview_storage_path"] = preview_storage_path
        payload["preview_ready"] = True

    ex = (
        sb.table("diamond_images")
        .select("id")
        .eq("diamond_id", diamond_id)
        .eq("image_type", image_type)
        .execute()
    )

    if ex.data and len(ex.data) > 0:
        sb.table("diamond_images").update(payload).eq("id", ex.data[0]["id"]).execute()
        print(f"✅ Updated diamond_images for {image_type}")
    else:
        sb.table("diamond_images").insert(
            {"diamond_id": diamond_id, "image_type": image_type, **payload}
        ).execute()
        print(f"✅ Inserted diamond_images for {image_type}")


def main():
    # Load env from common locations
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")          # nova/.env
    load_dotenv(Path(__file__).resolve().parents[1] / "api" / ".env")  # nova/api/.env

    supabase_url = normalize_url(must_env("SUPABASE_URL"))
    service_key = must_env("SUPABASE_SERVICE_ROLE_KEY")

    org_slug = os.getenv("ORG_SLUG", "first-customer")
    org_name = os.getenv("ORG_NAME", "First Customer")
    device_name = os.getenv("DEVICE_NAME", "Scanner-1")
    ring_label = os.getenv("RING_LABEL", "A")
    slot_index = int(os.getenv("SLOT_INDEX", "0"))

    originals_bucket = os.getenv("ORIGINALS_BUCKET", "diamond-images")
    previews_bucket = os.getenv("PREVIEWS_BUCKET", "diamond-previews")
    upsert_storage = _bool(os.getenv("UPSERT_STORAGE", "true"))

    # Default images (adjust if your repo paths differ)
    repo_root = Path(__file__).resolve().parents[1]  # nova/
    default_uv = repo_root / "test_images" / "uv_free_signed_url.jpg"
    default_aset = repo_root / "test_images" / "aset_signed_url.jpg"
    default_uv_preview = repo_root / "test_images" / "uv_free_preview_signed_url.jpg"
    default_aset_preview = repo_root / "test_images" / "aset_preview_signed_url.jpg"

    original_uv = Path(os.getenv("ORIGINAL_UV", str(default_uv))).resolve()
    original_aset = Path(os.getenv("ORIGINAL_ASET", str(default_aset))).resolve()
    thumb_uv = Path(os.getenv("THUMB_UV", str(default_uv_preview))).resolve()
    thumb_aset = Path(os.getenv("THUMB_ASET", str(default_aset_preview))).resolve()

    print("\n=== DEV SEED STORAGE (LOCAL) ===")
    print("SUPABASE_URL:", supabase_url)
    print("ORG:", org_slug, "/", org_name)
    print("Device:", device_name)
    print("Ring:", ring_label, "Slot:", slot_index)
    print("Buckets:", originals_bucket, previews_bucket)
    print("UPSERT_STORAGE:", upsert_storage)

    sb = create_client(supabase_url, service_key)

    # IMPORTANT: after db reset, services can take a moment.
    # Retry the first DB call until PostgREST is ready.
    org_id = retry(lambda: get_or_create_org(sb, org_slug, org_name), label="get_or_create_org")

    device_id = retry(lambda: get_or_create_device(sb, org_id, device_name), label="get_or_create_device")

    job_ins = retry(
        lambda: sb.table("jobs").insert({"org_id": org_id, "device_id": device_id, "status": "SCANNING"}).execute(),
        label="create_job",
    )
    job_id = job_ins.data[0]["id"]
    print("✅ Job created:", job_id)

    ring_id = retry(lambda: get_or_create_ring(sb, job_id, ring_label), label="get_or_create_ring")
    print("✅ Ring:", ring_id)

    existing_diamond_id = retry(lambda: diamond_exists(sb, job_id, ring_id, slot_index), label="diamond_exists")
    if existing_diamond_id:
        diamond_id = existing_diamond_id
        print("ℹ️ Diamond already exists:", diamond_id)
    else:
        dia_ins = retry(
            lambda: sb.table("diamonds").insert({"job_id": job_id, "ring_id": ring_id, "slot_index": slot_index}).execute(),
            label="create_diamond",
        )
        diamond_id = dia_ins.data[0]["id"]
        print("✅ Diamond created:", diamond_id)

    # Keys
    uv_key = f"{org_slug}/{job_id}/{ring_label}/slot_{slot_index}_uv_free.jpg"
    aset_key = f"{org_slug}/{job_id}/{ring_label}/slot_{slot_index}_aset.jpg"
    uv_thumb_key = f"{org_slug}/{job_id}/{ring_label}/slot_{slot_index}_uv_free_thumb.jpg"
    aset_thumb_key = f"{org_slug}/{job_id}/{ring_label}/slot_{slot_index}_aset_thumb.jpg"

    # Upload originals + thumbnails
    retry(lambda: storage_upload_jpeg(sb, originals_bucket, uv_key, original_uv, upsert=upsert_storage), label="upload_uv_original")
    retry(lambda: storage_upload_jpeg(sb, originals_bucket, aset_key, original_aset, upsert=upsert_storage), label="upload_aset_original")
    retry(lambda: storage_upload_jpeg(sb, previews_bucket, uv_thumb_key, thumb_uv, upsert=upsert_storage), label="upload_uv_thumb")
    retry(lambda: storage_upload_jpeg(sb, previews_bucket, aset_thumb_key, thumb_aset, upsert=upsert_storage), label="upload_aset_thumb")

    # DB rows point to originals (UI derives thumb path)
    retry(lambda: upsert_diamond_image(sb, diamond_id, "UV_FREE", uv_key, uv_thumb_key),
          label="upsert_diamond_images_uv")
    retry(lambda: upsert_diamond_image(sb, diamond_id, "ASET", aset_key, aset_thumb_key),
          label="upsert_diamond_images_aset")

    print("\nDONE ✅")
    print("Dashboard:", "http://localhost:3000/dashboard")
    print("Job:", f"http://localhost:3000/jobs/{job_id}")
    print("Original keys:", uv_key, aset_key)
    print("Thumb keys:", uv_thumb_key, aset_thumb_key)


if __name__ == "__main__":
    main()
