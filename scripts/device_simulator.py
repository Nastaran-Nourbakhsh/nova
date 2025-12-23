#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv


# -----------------------------
# Config
# -----------------------------
@dataclass
class Config:
    api_base: str
    device_key: str
    org_slug: str
    device_name: str
    ring_label: str

    # files (local disk) used as the "captured" images
    preview_uv_file: Path
    preview_aset_file: Path
    original_uv_file: Path
    original_aset_file: Path

    # behavior
    slots: int
    delay_between_slots_s: float
    upload_originals_mode: str  # "immediate" | "delayed" | "never"
    delayed_originals_after_s: float

    # offline / retry
    queue_path: Path
    request_timeout_s: float
    retry_sleep_s: float


def _must_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


def _read_bytes(p: Path) -> bytes:
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    return p.read_bytes()


def _now_iso() -> str:
    # simple readable timestamp
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# -----------------------------
# Offline queue
# -----------------------------
def load_queue(queue_path: Path) -> List[Dict[str, Any]]:
    if not queue_path.exists():
        return []
    try:
        return json.loads(queue_path.read_text())
    except Exception:
        # if corrupted, keep a backup and start fresh
        backup = queue_path.with_suffix(".corrupt.json")
        queue_path.rename(backup)
        print(f"⚠️ Queue file was corrupted. Moved to {backup}")
        return []


def save_queue(queue_path: Path, items: List[Dict[str, Any]]) -> None:
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(json.dumps(items, indent=2))


def enqueue(queue_path: Path, item: Dict[str, Any]) -> None:
    q = load_queue(queue_path)
    q.append(item)
    save_queue(queue_path, q)


# -----------------------------
# API helpers
# -----------------------------
def api_headers(cfg: Config) -> Dict[str, str]:
    return {"x-device-key": cfg.device_key}


def safe_request(
    cfg: Config,
    client: httpx.Client,
    method: str,
    path: str,
    json_body: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Returns: (ok, json, error_message)
    ok=False means "treat as offline / retry later".
    """
    url = cfg.api_base.rstrip("/") + path
    try:
        resp = client.request(
            method,
            url,
            headers=api_headers(cfg),
            json=json_body,
            timeout=cfg.request_timeout_s,
        )
    except Exception as e:
        return False, None, f"network error: {e}"

    # For device simulator: 5xx or 429 or network = retry later
    if resp.status_code >= 500 or resp.status_code == 429:
        return False, None, f"server busy: {resp.status_code} {resp.text}"

    # 4xx means "logic error" usually (don’t retry forever)
    if resp.status_code >= 400:
        return False, None, f"client error: {resp.status_code} {resp.text}"

    if not resp.text.strip():
        return True, {}, None

    try:
        return True, resp.json(), None
    except Exception:
        return True, {"raw": resp.text}, None


def storage_upload_signed_put(signed_url: str, file_path: Path) -> None:
    """
    Upload binary file to signed URL (Supabase signed upload URL endpoint).
    This uses PUT with image/jpeg.
    """
    data = _read_bytes(file_path)
    # NOTE: signed upload URLs are on port 54321 and do NOT need x-device-key
    r = httpx.put(
        signed_url,
        headers={"Content-Type": "image/jpeg"},
        content=data,
        timeout=60.0,
    )
    r.raise_for_status()


# -----------------------------
# Queue item schema
# -----------------------------
def qitem_upload_and_ingest(
    *,
    org_slug: str,
    device_name: str,
    ring_label: str,
    slot_index: int,
    job_id: str,
    # paths in storage (relative)
    uv_preview_path: str,
    aset_preview_path: str,
    uv_original_path: str,
    aset_original_path: str,
    # signed URLs to upload
    uv_preview_signed_url: str,
    aset_preview_signed_url: str,
    uv_original_signed_url: str,
    aset_original_signed_url: str,
    # local files
    local_preview_uv: str,
    local_preview_aset: str,
    local_original_uv: str,
    local_original_aset: str,
) -> Dict[str, Any]:
    return {
        "type": "UPLOAD_AND_INGEST",
        "created_at": _now_iso(),
        "org_slug": org_slug,
        "device_name": device_name,
        "ring_label": ring_label,
        "slot_index": slot_index,
        "job_id": job_id,
        "paths": {
            "uv_preview_path": uv_preview_path,
            "aset_preview_path": aset_preview_path,
            "uv_original_path": uv_original_path,
            "aset_original_path": aset_original_path,
        },
        "signed": {
            "uv_preview_signed_url": uv_preview_signed_url,
            "aset_preview_signed_url": aset_preview_signed_url,
            "uv_original_signed_url": uv_original_signed_url,
            "aset_original_signed_url": aset_original_signed_url,
        },
        "local": {
            "preview_uv": local_preview_uv,
            "preview_aset": local_preview_aset,
            "original_uv": local_original_uv,
            "original_aset": local_original_aset,
        },
        "state": {
            "preview_uploaded": False,
            "ingested": False,
            "original_uploaded": False,
            "originals_confirmed": False,
        },
    }


# -----------------------------
# Core flow
# -----------------------------
def drain_queue(cfg: Config, client: httpx.Client) -> None:
    q = load_queue(cfg.queue_path)
    if not q:
        return

    print(f"🔁 Draining queue: {len(q)} pending item(s)")
    kept: List[Dict[str, Any]] = []

    for item in q:
        ok = process_queue_item(cfg, client, item)
        if not ok:
            kept.append(item)
            # stop early if offline to avoid spamming
            print("⏸️ Still offline or blocked; keeping remaining items for later.")
            break

    save_queue(cfg.queue_path, kept)
    if kept:
        print(f"📦 Queue remaining: {len(kept)}")
    else:
        print("✅ Queue drained.")


def process_queue_item(cfg: Config, client: httpx.Client, item: Dict[str, Any]) -> bool:
    if item.get("type") != "UPLOAD_AND_INGEST":
        print("⚠️ Unknown queue item type, skipping:", item.get("type"))
        return True

    st = item["state"]
    signed = item["signed"]
    paths = item["paths"]
    local = item["local"]

    # 1) upload previews (always first)
    if not st["preview_uploaded"]:
        try:
            storage_upload_signed_put(signed["uv_preview_signed_url"], Path(local["preview_uv"]))
            storage_upload_signed_put(signed["aset_preview_signed_url"], Path(local["preview_aset"]))
            st["preview_uploaded"] = True
            print(f"✅ Preview uploaded for slot {item['slot_index']}")
        except Exception as e:
            print(f"🌐 Preview upload failed (offline?) slot {item['slot_index']}: {e}")
            return False

    # 2) ingest scan (creates diamond + diamond_images rows)
    if not st["ingested"]:
        payload = {
            "org_slug": item["org_slug"],
            "job_id": item["job_id"],
            "ring_label": item["ring_label"],
            "slot_index": item["slot_index"],
            # originals paths (relative)
            "uv_free_path": paths["uv_original_path"],
            "aset_path": paths["aset_original_path"],
            # previews paths (relative)
            "uv_free_preview_path": paths["uv_preview_path"],
            "aset_preview_path": paths["aset_preview_path"],
            "device_name": item["device_name"],
        }
        ok, data, err = safe_request(cfg, client, "POST", "/ingest/scan", payload)
        if not ok:
            print(f"🌐 ingest failed slot {item['slot_index']}: {err}")
            return False
        st["ingested"] = True
        print(f"✅ Ingested slot {item['slot_index']} diamond_id={data.get('diamond_id')}")

    # 3) upload originals (maybe delayed / never)
    if cfg.upload_originals_mode == "never":
        # do not try
        return True

    if not st["original_uploaded"]:
        try:
            storage_upload_signed_put(signed["uv_original_signed_url"], Path(local["original_uv"]))
            storage_upload_signed_put(signed["aset_original_signed_url"], Path(local["original_aset"]))
            st["original_uploaded"] = True
            print(f"✅ Originals uploaded for slot {item['slot_index']}")
        except Exception as e:
            print(f"🌐 Originals upload failed (offline?) slot {item['slot_index']}: {e}")
            return False

    # 4) confirm originals (API marks original_ready=true and timestamps)
    if not st["originals_confirmed"]:
        payload = {
            "org_slug": item["org_slug"],
            "job_id": item["job_id"],
            "ring_label": item["ring_label"],
            "slot_index": item["slot_index"],
            "uv_free_path": paths["uv_original_path"],
            "aset_path": paths["aset_original_path"],
        }
        ok, _data, err = safe_request(cfg, client, "POST", "/ingest/confirm-originals", payload)
        if not ok:
            print(f"🌐 confirm-originals failed slot {item['slot_index']}: {err}")
            return False
        st["originals_confirmed"] = True
        print(f"✅ Confirmed originals for slot {item['slot_index']}")

    return True


def main():
    # Load env from nova/.env and nova/api/.env if present
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")
    load_dotenv(repo_root / "api" / ".env")

    # Default files (you can override via env)
    default_preview_uv = repo_root / "test_images" / "uv_preview_signed_url.jpg"
    default_preview_aset = repo_root / "test_images" / "aset_preview_signed_url.jpg"
    default_original_uv = repo_root / "test_images" / "uv_free_signed_url.jpg"
    default_original_aset = repo_root / "test_images" / "aset_signed_url.jpg"

    cfg = Config(
        api_base=os.getenv("API_BASE", "http://127.0.0.1:8000"),
        device_key=_must_env("DEVICE_API_KEY"),
        org_slug=os.getenv("ORG_SLUG", "first-customer"),
        device_name=os.getenv("DEVICE_NAME", "Scanner-1"),
        ring_label=os.getenv("RING_LABEL", "A"),
        preview_uv_file=Path(os.getenv("PREVIEW_UV_FILE", str(default_preview_uv))).resolve(),
        preview_aset_file=Path(os.getenv("PREVIEW_ASET_FILE", str(default_preview_aset))).resolve(),
        original_uv_file=Path(os.getenv("ORIGINAL_UV_FILE", str(default_original_uv))).resolve(),
        original_aset_file=Path(os.getenv("ORIGINAL_ASET_FILE", str(default_original_aset))).resolve(),
        slots=int(os.getenv("SLOTS", "5")),
        delay_between_slots_s=float(os.getenv("DELAY_BETWEEN_SLOTS_S", "0.5")),
        upload_originals_mode=os.getenv("UPLOAD_ORIGINALS_MODE", "delayed"),  # immediate|delayed|never
        delayed_originals_after_s=float(os.getenv("DELAYED_ORIGINALS_AFTER_S", "5.0")),
        queue_path=Path(os.getenv("QUEUE_PATH", str(repo_root / "tmp" / "device_queue.json"))).resolve(),
        request_timeout_s=float(os.getenv("REQUEST_TIMEOUT_S", "10.0")),
        retry_sleep_s=float(os.getenv("RETRY_SLEEP_S", "2.0")),
    )

    print("\n=== DEVICE SIMULATOR ===")
    print("API_BASE:", cfg.api_base)
    print("ORG:", cfg.org_slug)
    print("DEVICE:", cfg.device_name)
    print("RING:", cfg.ring_label)
    print("SLOTS:", cfg.slots)
    print("UPLOAD_ORIGINALS_MODE:", cfg.upload_originals_mode)
    print("QUEUE:", cfg.queue_path)
    print("FILES:")
    print("  preview_uv:", cfg.preview_uv_file)
    print("  preview_aset:", cfg.preview_aset_file)
    print("  original_uv:", cfg.original_uv_file)
    print("  original_aset:", cfg.original_aset_file)
    print()

    with httpx.Client() as client:
        # First: try to drain queue from previous offline runs
        drain_queue(cfg, client)

        # 1) Create job
        ok, data, err = safe_request(
            cfg,
            client,
            "POST",
            "/jobs/start",
            {"org_slug": cfg.org_slug, "device_name": cfg.device_name},
        )
        if not ok:
            print("❌ Cannot start job (offline?). Queueing a START_JOB is possible,")
            print("but easiest is: rerun when online.")
            print("Error:", err)
            return

        job_id = data.get("job_id")
        if not job_id:
            raise RuntimeError(f"/jobs/start did not return job_id: {data}")

        print("✅ Job started:", job_id)
        print("Dashboard should show it now.")
        print()

        # 2) Loop slots: ask signed URLs, upload previews, ingest, queue originals upload/confirm
        delayed_original_items: List[Dict[str, Any]] = []

        for slot in range(cfg.slots):
            # Request signed URLs for BOTH buckets (your API should return preview + original signed URLs)
            ok, su, err = safe_request(
                cfg,
                client,
                "POST",
                "/storage/signed-urls",
                {
                    "org_slug": cfg.org_slug,
                    "job_id": job_id,
                    "ring_label": cfg.ring_label,
                    "slot_index": slot,
                },
            )
            if not ok or not su:
                print(f"🌐 signed-urls failed slot {slot}: {err}")
                print("➡️ Going offline: nothing to upload/ingest yet; retry later.")
                break

            # Expect keys from your API response:
            # uv_free_signed_url, aset_signed_url, uv_free_preview_signed_url, aset_preview_signed_url
            # and paths: uv_free_path, aset_path, uv_free_preview_path, aset_preview_path
            item = qitem_upload_and_ingest(
                org_slug=cfg.org_slug,
                device_name=cfg.device_name,
                ring_label=cfg.ring_label,
                slot_index=slot,
                job_id=job_id,
                uv_preview_path=su["uv_free_preview_path"],
                aset_preview_path=su["aset_preview_path"],
                uv_original_path=su["uv_free_path"],
                aset_original_path=su["aset_path"],
                uv_preview_signed_url=su["uv_free_preview_signed_url"],
                aset_preview_signed_url=su["aset_preview_signed_url"],
                uv_original_signed_url=su["uv_free_signed_url"],
                aset_original_signed_url=su["aset_signed_url"],
                local_preview_uv=str(cfg.preview_uv_file),
                local_preview_aset=str(cfg.preview_aset_file),
                local_original_uv=str(cfg.original_uv_file),
                local_original_aset=str(cfg.original_aset_file),
            )

            # For immediate mode: process fully now (preview+ingest+original+confirm)
            if cfg.upload_originals_mode == "immediate":
                ok_item = process_queue_item(cfg, client, item)
                if not ok_item:
                    print("🌐 Failed mid-slot; queueing item for later.")
                    enqueue(cfg.queue_path, item)
                    break

            # For delayed mode: process preview+ingest now, postpone originals+confirm
            elif cfg.upload_originals_mode == "delayed":
                # do preview+ingest now by temporarily setting mode to never for this pass
                prev_mode = cfg.upload_originals_mode
                cfg.upload_originals_mode = "never"
                ok_item = process_queue_item(cfg, client, item)
                cfg.upload_originals_mode = prev_mode

                if not ok_item:
                    print("🌐 Failed mid-slot; queueing item for later.")
                    enqueue(cfg.queue_path, item)
                    break

                delayed_original_items.append(item)

            # Never mode: preview+ingest only
            else:
                prev_mode = cfg.upload_originals_mode
                cfg.upload_originals_mode = "never"
                ok_item = process_queue_item(cfg, client, item)
                cfg.upload_originals_mode = prev_mode

                if not ok_item:
                    print("🌐 Failed mid-slot; queueing item for later.")
                    enqueue(cfg.queue_path, item)
                    break

            time.sleep(cfg.delay_between_slots_s)

        # 3) In delayed mode, upload originals later + confirm
        if cfg.upload_originals_mode == "delayed" and delayed_original_items:
            print()
            print(f"⏳ Waiting {cfg.delayed_originals_after_s}s before uploading originals…")
            time.sleep(cfg.delayed_originals_after_s)

            # Now process each delayed item fully (original+confirm). If fails: queue it.
            for item in delayed_original_items:
                ok_item = process_queue_item(cfg, client, item)
                if not ok_item:
                    print("🌐 Failed uploading originals; queueing for later.")
                    enqueue(cfg.queue_path, item)
                    break

        # 4) Stop job (optional)
        ok, _data, err = safe_request(cfg, client, "POST", f"/jobs/{job_id}/stop", {"org_slug": cfg.org_slug})
        if not ok:
            print("⚠️ Could not stop job (not fatal):", err)
        else:
            print()
            print("✅ Job stopped:", job_id)

        print()
        print("DONE ✅")
        print("Dashboard: http://localhost:3000/dashboard")
        print(f"Job page: http://localhost:3000/jobs/{job_id}")
        print(f"Queue file: {cfg.queue_path}  (should be empty if everything online)")


if __name__ == "__main__":
    main()
