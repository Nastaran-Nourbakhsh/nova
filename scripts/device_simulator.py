#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
from dotenv import load_dotenv


# ----------------------------
# Config
# ----------------------------
DEFAULT_API_BASE = "http://127.0.0.1:8000"
DEFAULT_DB_PATH = "scripts/device_simulator_queue.sqlite3"


@dataclass
class SignedUrls:
    job_id: str
    # originals
    uv_free_path: str
    aset_path: str
    uv_free_signed_url: str
    aset_signed_url: str
    # previews
    uv_free_preview_path: str
    aset_preview_path: str
    uv_free_preview_signed_url: str
    aset_preview_signed_url: str


# ----------------------------
# SQLite queue (offline support)
# ----------------------------
def q_init(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """
            create table if not exists queue (
              id integer primary key autoincrement,
              created_at integer not null,
              kind text not null,
              payload text not null,
              tries integer not null default 0
            );
            """
        )
        con.commit()
    finally:
        con.close()


def q_push(db_path: str, kind: str, payload: Dict[str, Any]) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "insert into queue(created_at, kind, payload, tries) values (?, ?, ?, 0)",
            (int(time.time()), kind, json.dumps(payload)),
        )
        con.commit()
    finally:
        con.close()


def q_peek(db_path: str) -> Optional[Tuple[int, str, Dict[str, Any], int]]:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "select id, kind, payload, tries from queue order by id asc limit 1"
        ).fetchone()
        if not row:
            return None
        _id, kind, payload_s, tries = row
        return _id, kind, json.loads(payload_s), tries
    finally:
        con.close()


def q_pop(db_path: str, row_id: int) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute("delete from queue where id = ?", (row_id,))
        con.commit()
    finally:
        con.close()


def q_bump_try(db_path: str, row_id: int) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute("update queue set tries = tries + 1 where id = ?", (row_id,))
        con.commit()
    finally:
        con.close()


def q_replace_job_id(db_path: str, old_job_id: str, new_job_id: str) -> int:
    """
    When syncing offline queue, we often create a temp local job_id.
    After we call /jobs/start online, we need to rewrite queued payloads.
    """
    con = sqlite3.connect(db_path)
    updated = 0
    try:
        rows = con.execute("select id, payload from queue").fetchall()
        for row_id, payload_s in rows:
            payload = json.loads(payload_s)
            if payload.get("job_id") == old_job_id:
                payload["job_id"] = new_job_id
                con.execute(
                    "update queue set payload = ? where id = ?",
                    (json.dumps(payload), row_id),
                )
                updated += 1
        con.commit()
    finally:
        con.close()
    return updated


# ----------------------------
# HTTP helpers
# ----------------------------
def _headers(device_key: str) -> Dict[str, str]:
    return {"x-device-key": device_key, "Content-Type": "application/json"}


def api_post_json(api_base: str, path: str, device_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = api_base.rstrip("/") + path
    r = requests.post(url, headers=_headers(device_key), json=payload, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"POST {path} failed: {r.status_code} {r.text}")
    return r.json()


def put_file_to_signed_url(signed_url: str, file_path: Path) -> None:
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    data = file_path.read_bytes()
    r = requests.put(
        signed_url,
        data=data,
        headers={"Content-Type": "image/jpeg"},
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"PUT upload failed: {r.status_code} {r.text}")


# ----------------------------
# API calls
# ----------------------------
def jobs_start(api_base: str, device_key: str, org_slug: str, device_name: str) -> str:
    # POST /jobs/start  {org_slug, device_name}
    res = api_post_json(api_base, "/jobs/start", device_key, {"org_slug": org_slug, "device_name": device_name})
    job_id = res.get("job_id") or res.get("id")
    if not job_id:
        raise RuntimeError(f"/jobs/start response missing job_id: {res}")
    return job_id


def get_signed_urls(api_base: str, device_key: str, org_slug: str, job_id: str, ring_label: str, slot_index: int) -> SignedUrls:
    # POST /storage/signed-urls  {org_slug, job_id, ring_label, slot_index}
    res = api_post_json(
        api_base,
        "/storage/signed-urls",
        device_key,
        {"org_slug": org_slug, "job_id": job_id, "ring_label": ring_label, "slot_index": slot_index},
    )
    return SignedUrls(**res)


def ingest_scan(
    api_base: str,
    device_key: str,
    org_slug: str,
    job_id: str,
    ring_label: str,
    slot_index: int,
    device_name: str,
) -> Dict[str, Any]:
    # Your newer ingest/scan builds paths server-side from org_slug + job_id + ring_label + slot_index.
    payload = {
        "org_slug": org_slug,
        "job_id": job_id,
        "ring_label": ring_label,
        "slot_index": slot_index,
        "device_name": device_name,
    }
    return api_post_json(api_base, "/ingest/scan", device_key, payload)


def confirm_originals(
    api_base: str,
    device_key: str,
    org_slug: str,
    job_id: str,
    ring_label: str,
    slot_index: int,
) -> Dict[str, Any]:
    # POST /ingest/confirm-originals  {org_slug, job_id, ring_label, slot_index}
    return api_post_json(
        api_base,
        "/ingest/confirm-originals",
        device_key,
        {"org_slug": org_slug, "job_id": job_id, "ring_label": ring_label, "slot_index": slot_index},
    )


# ----------------------------
# Main slot flow (online)
# ----------------------------
def do_one_slot_online(
    api_base: str,
    device_key: str,
    org_slug: str,
    job_id: str,
    ring_label: str,
    slot_index: int,
    device_name: str,
    uv_preview_file: Path,
    aset_preview_file: Path,
    uv_original_file: Optional[Path],
    aset_original_file: Optional[Path],
    upload_originals_now: bool,
    queue_db: str,
) -> None:
    su = get_signed_urls(api_base, device_key, org_slug, job_id, ring_label, slot_index)

    # 1) Upload previews first
    put_file_to_signed_url(su.uv_free_preview_signed_url, uv_preview_file)
    put_file_to_signed_url(su.aset_preview_signed_url, aset_preview_file)

    # 2) Ingest scan (creates DB rows and sets preview_* columns)
    ingest_scan(api_base, device_key, org_slug, job_id, ring_label, slot_index, device_name)

    # 3) Originals now OR queue originals for later
    if upload_originals_now:
        if not uv_original_file or not aset_original_file:
            raise RuntimeError("upload_originals_now=true but original files not provided.")
        put_file_to_signed_url(su.uv_free_signed_url, uv_original_file)
        put_file_to_signed_url(su.aset_signed_url, aset_original_file)
        confirm_originals(api_base, device_key, org_slug, job_id, ring_label, slot_index)
    else:
        # Signed URLs expire. So we queue a task that will re-request signed urls later,
        # then upload originals + confirm.
        q_push(
            queue_db,
            "upload_originals",
            {
                "org_slug": org_slug,
                "job_id": job_id,
                "ring_label": ring_label,
                "slot_index": slot_index,
                "device_name": device_name,
                "uv_original_file": str(uv_original_file) if uv_original_file else "",
                "aset_original_file": str(aset_original_file) if aset_original_file else "",
            },
        )


# ----------------------------
# Sync queue (replay offline)
# ----------------------------
def run_sync_queue(api_base: str, device_key: str, db_path: str) -> None:
    print("=== SYNC QUEUE ===")
    while True:
        item = q_peek(db_path)
        if not item:
            print("Queue empty ✅")
            return

        row_id, kind, payload, tries = item
        try:
            print(f"-> syncing #{row_id} kind={kind} tries={tries}")

            if kind == "job_start":
                # Start a real job online, then rewrite queued items that refer to the temp job_id
                temp_job_id = payload["job_id"]
                real_job_id = jobs_start(api_base, device_key, payload["org_slug"], payload["device_name"])
                updated = q_replace_job_id(db_path, temp_job_id, real_job_id)
                print(f"✅ job started online: {real_job_id}  (rewrote {updated} queued items from {temp_job_id})")
                q_pop(db_path, row_id)

            elif kind == "slot":
                # Full slot: previews -> ingest -> maybe originals now
                do_one_slot_online(
                    api_base=api_base,
                    device_key=device_key,
                    org_slug=payload["org_slug"],
                    job_id=payload["job_id"],
                    ring_label=payload["ring_label"],
                    slot_index=int(payload["slot_index"]),
                    device_name=payload["device_name"],
                    uv_preview_file=Path(payload["uv_preview_file"]),
                    aset_preview_file=Path(payload["aset_preview_file"]),
                    uv_original_file=Path(payload["uv_original_file"]) if payload.get("uv_original_file") else None,
                    aset_original_file=Path(payload["aset_original_file"]) if payload.get("aset_original_file") else None,
                    upload_originals_now=bool(payload.get("upload_originals_now", False)),
                    queue_db=db_path,
                )
                q_pop(db_path, row_id)

            elif kind == "upload_originals":
                # Originals later: re-request signed urls, upload originals, confirm
                org_slug = payload["org_slug"]
                job_id = payload["job_id"]
                ring_label = payload["ring_label"]
                slot_index = int(payload["slot_index"])

                uv_file = Path(payload.get("uv_original_file", "")).resolve()
                aset_file = Path(payload.get("aset_original_file", "")).resolve()
                if not uv_file.exists() or not aset_file.exists():
                    raise RuntimeError(f"Original files missing: uv={uv_file} aset={aset_file}")

                su = get_signed_urls(api_base, device_key, org_slug, job_id, ring_label, slot_index)
                put_file_to_signed_url(su.uv_free_signed_url, uv_file)
                put_file_to_signed_url(su.aset_signed_url, aset_file)
                confirm_originals(api_base, device_key, org_slug, job_id, ring_label, slot_index)

                q_pop(db_path, row_id)

            else:
                raise RuntimeError(f"Unknown queue kind: {kind}")

        except Exception as e:
            print(f"⚠️ sync failed for #{row_id}: {e}")
            q_bump_try(db_path, row_id)
            time.sleep(2.0)  # simple backoff


# ----------------------------
# CLI
# ----------------------------
def main():
    repo_root = Path(__file__).resolve().parents[1]  # nova/
    load_dotenv(repo_root / ".env")
    load_dotenv(repo_root / "api" / ".env")

    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=os.getenv("API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--device-key", default=os.getenv("DEVICE_API_KEY", ""))
    parser.add_argument("--org", default=os.getenv("ORG_SLUG", "first-customer"))
    parser.add_argument("--device", default=os.getenv("DEVICE_NAME", "Scanner-1"))
    parser.add_argument("--ring", default=os.getenv("RING_LABEL", "A"))
    parser.add_argument("--slots", type=int, default=int(os.getenv("SLOTS", "3")))

    parser.add_argument("--job-id", default=os.getenv("JOB_ID", ""))  # optional: reuse job
    parser.add_argument("--upload-originals-now", action="store_true")

    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--db", default=os.getenv("SIM_DB", DEFAULT_DB_PATH))

    parser.add_argument("--uv-preview", default=str(repo_root / "test_images" / "uv_free_preview_signed_url.jpg"))
    parser.add_argument("--aset-preview", default=str(repo_root / "test_images" / "aset_preview_signed_url.jpg"))
    parser.add_argument("--uv-original", default=str(repo_root / "test_images" / "uv_free_signed_url.jpg"))
    parser.add_argument("--aset-original", default=str(repo_root / "test_images" / "aset_signed_url.jpg"))

    args = parser.parse_args()

    if not args.device_key:
        raise SystemExit("DEVICE_API_KEY is missing. Put it in nova/api/.env or pass --device-key.")

    q_init(args.db)

    # Sync mode: replay queued tasks
    if args.sync:
        run_sync_queue(args.api, args.device_key, args.db)
        return

    uv_prev = Path(args.uv_preview).resolve()
    aset_prev = Path(args.aset_preview).resolve()
    uv_org = Path(args.uv_original).resolve()
    aset_org = Path(args.aset_original).resolve()

    # Decide job id
    job_id = args.job_id.strip()

    if args.offline:
        # Offline: create a TEMP local job id and queue a job_start that will remap later.
        if not job_id:
            job_id = str(uuid.uuid4())
            q_push(args.db, "job_start", {"org_slug": args.org, "device_name": args.device, "job_id": job_id})
            print(f"🧾 queued job_start (offline) temp_job_id={job_id}")
        else:
            # If user provided a job_id in offline mode, we still need to start it online later.
            q_push(args.db, "job_start", {"org_slug": args.org, "device_name": args.device, "job_id": job_id})
            print(f"🧾 queued job_start (offline) temp_job_id={job_id}")

        # Queue slot actions
        for slot in range(args.slots):
            q_push(
                args.db,
                "slot",
                {
                    "org_slug": args.org,
                    "job_id": job_id,
                    "ring_label": args.ring,
                    "slot_index": slot,
                    "device_name": args.device,
                    "uv_preview_file": str(uv_prev),
                    "aset_preview_file": str(aset_prev),
                    "uv_original_file": str(uv_org),
                    "aset_original_file": str(aset_org),
                    "upload_originals_now": bool(args.upload_originals_now),
                },
            )
            print(f"🧾 queued slot {slot} (offline)")

        print("\nOFFLINE DONE ✅")
        print(f"Queue DB: {args.db}")
        print("When back online, run:  python scripts/device_simulator.py --sync")
        return

    # Online mode: start job if not provided
    if not job_id:
        job_id = jobs_start(args.api, args.device_key, args.org, args.device)
        print("✅ job_id:", job_id)

    # Execute slots online
    for slot in range(args.slots):
        do_one_slot_online(
            api_base=args.api,
            device_key=args.device_key,
            org_slug=args.org,
            job_id=job_id,
            ring_label=args.ring,
            slot_index=slot,
            device_name=args.device,
            uv_preview_file=uv_prev,
            aset_preview_file=aset_prev,
            uv_original_file=uv_org,
            aset_original_file=aset_org,
            upload_originals_now=bool(args.upload_originals_now),
            queue_db=args.db,
        )
        print(f"✅ done slot {slot}")
        time.sleep(0.8)

    print("\nDONE ✅")
    print("Dashboard: http://localhost:3000/dashboard")
    print(f"Job: http://localhost:3000/jobs/{job_id}")
    if not args.upload_originals_now:
        print(f"Originals were queued for later. Run: python scripts/device_simulator.py --sync")


if __name__ == "__main__":
    main()
