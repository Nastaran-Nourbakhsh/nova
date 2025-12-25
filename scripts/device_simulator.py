#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

import requests
from dotenv import load_dotenv


DEFAULT_API_BASE = "http://127.0.0.1:8000"
DEFAULT_DB_PATH = "scripts/device_simulator_queue.sqlite3"


# ----------------------------
# Queue DB
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


# ----------------------------
# HTTP helpers
# ----------------------------
def _headers(device_key: str) -> Dict[str, str]:
    return {"x-device-key": device_key, "Content-Type": "application/json"}


def api_post_json(api_base: str, path: str, device_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = api_base.rstrip("/") + path
    r = requests.post(url, headers=_headers(device_key), json=payload, timeout=60)
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
        timeout=120,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"PUT upload failed: {r.status_code} {r.text}")


# ----------------------------
# API calls
# ----------------------------
def jobs_start(api_base: str, device_key: str, org_slug: str, device_name: str) -> str:
    res = api_post_json(api_base, "/jobs/start", device_key, {"org_slug": org_slug, "device_name": device_name})
    job_id = res.get("job_id") or res.get("id")
    if not job_id:
        raise RuntimeError(f"/jobs/start response missing job_id: {res}")
    return job_id


@dataclass
class SignedUrls:
    job_id: str
    uv_free_path: Optional[str] = None
    aset_path: Optional[str] = None
    uv_free_signed_url: Optional[str] = None
    aset_signed_url: Optional[str] = None

    uv_free_preview_path: Optional[str] = None
    aset_preview_path: Optional[str] = None
    uv_free_preview_signed_url: Optional[str] = None
    aset_preview_signed_url: Optional[str] = None


def get_signed_urls(api_base: str, device_key: str, org_slug: str, job_id: str, ring_label: str, slot_index: int, mode: str) -> SignedUrls:
    res = api_post_json(
        api_base,
        "/storage/signed-urls",
        device_key,
        {"org_slug": org_slug, "job_id": job_id, "ring_label": ring_label, "slot_index": slot_index, "mode": mode},
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
    uv_free_path: str,
    aset_path: str,
    uv_preview_path: str,
    aset_preview_path: str,
) -> Dict[str, Any]:
    payload = {
        "org_slug": org_slug,
        "job_id": job_id,
        "ring_label": ring_label,
        "slot_index": slot_index,
        "uv_free_path": uv_free_path,
        "aset_path": aset_path,
        "uv_free_preview_path": uv_preview_path,
        "aset_preview_path": aset_preview_path,
        "device_name": device_name,
    }
    return api_post_json(api_base, "/ingest/scan", device_key, payload)


def confirm_originals(api_base: str, device_key: str, org_slug: str, job_id: str, ring_label: str, slot_index: int, device_name: str) -> Dict[str, Any]:
    return api_post_json(
        api_base,
        "/ingest/confirm-originals",
        device_key,
        {"org_slug": org_slug, "job_id": job_id, "ring_label": ring_label, "slot_index": slot_index, "device_name": device_name},
    )


# ----------------------------
# Folder discovery (UV+ASET pairs per slot)
# ----------------------------
def discover_slots(folder: Path) -> List[Tuple[int, Path, Path]]:
    """
    Expect either:
      slot_<n>_uv_free.jpg + slot_<n>_aset.jpg
    OR any files that contain:
      ...slot_<n>...uv... and ...slot_<n>...aset...
    Your dataset seems normalized; this covers both.
    """
    files = list(folder.glob("*.jpg")) + list(folder.glob("*.jpeg")) + list(folder.glob("*.png"))
    by_slot: Dict[int, Dict[str, Path]] = {}

    def parse_slot(p: Path) -> Optional[int]:
        name = p.name.lower()
        # common: slot_12_uv_free.jpg
        if "slot_" in name:
            try:
                after = name.split("slot_", 1)[1]
                num_s = ""
                for ch in after:
                    if ch.isdigit():
                        num_s += ch
                    else:
                        break
                if num_s:
                    return int(num_s)
            except Exception:
                return None
        return None

    for p in files:
        slot = parse_slot(p)
        if slot is None:
            continue
        d = by_slot.setdefault(slot, {})
        n = p.name.lower()
        if "uv" in n:
            d["uv"] = p
        elif "aset" in n:
            d["aset"] = p

    out: List[Tuple[int, Path, Path]] = []
    for slot in sorted(by_slot.keys()):
        d = by_slot[slot]
        if "uv" in d and "aset" in d:
            out.append((slot, d["uv"], d["aset"]))
    return out


# ----------------------------
# Online operations
# ----------------------------
def do_slot_previews_and_ingest(
    api_base: str,
    device_key: str,
    org_slug: str,
    job_id: str,
    ring_label: str,
    device_name: str,
    slot_index: int,
    uv_preview_file: Path,
    aset_preview_file: Path,
) -> SignedUrls:
    # mode=both here so we get both preview + originals paths in DB
    su = get_signed_urls(api_base, device_key, org_slug, job_id, ring_label, slot_index, mode="both")

    # upload previews
    assert su.uv_free_preview_signed_url and su.aset_preview_signed_url
    put_file_to_signed_url(su.uv_free_preview_signed_url, uv_preview_file)
    put_file_to_signed_url(su.aset_preview_signed_url, aset_preview_file)

    # ingest scan (paths must exist in response)
    assert su.uv_free_path and su.aset_path and su.uv_free_preview_path and su.aset_preview_path
    ingest_scan(
        api_base,
        device_key,
        org_slug,
        job_id,
        ring_label,
        slot_index,
        device_name,
        su.uv_free_path,
        su.aset_path,
        su.uv_free_preview_path,
        su.aset_preview_path,
    )
    return su


def do_slot_upload_originals_and_confirm(
    api_base: str,
    device_key: str,
    org_slug: str,
    job_id: str,
    ring_label: str,
    device_name: str,
    slot_index: int,
    uv_original_file: Path,
    aset_original_file: Path,
) -> None:
    # IMPORTANT: originals only → avoids 409 Duplicate previews
    su = get_signed_urls(api_base, device_key, org_slug, job_id, ring_label, slot_index, mode="originals")
    assert su.uv_free_signed_url and su.aset_signed_url and su.uv_free_path and su.aset_path

    put_file_to_signed_url(su.uv_free_signed_url, uv_original_file)
    put_file_to_signed_url(su.aset_signed_url, aset_original_file)

    confirm_originals(api_base, device_key, org_slug, job_id, ring_label, slot_index, device_name)


# ----------------------------
# Sync queue
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

            if kind == "upload_originals":
                do_slot_upload_originals_and_confirm(
                    api_base=api_base,
                    device_key=device_key,
                    org_slug=payload["org_slug"],
                    job_id=payload["job_id"],
                    ring_label=payload["ring_label"],
                    device_name=payload["device_name"],
                    slot_index=int(payload["slot_index"]),
                    uv_original_file=Path(payload["uv_original_file"]),
                    aset_original_file=Path(payload["aset_original_file"]),
                )
            else:
                raise RuntimeError(f"Unknown kind: {kind}")

            q_pop(db_path, row_id)
        except Exception as e:
            print(f"⚠️ sync failed for #{row_id}: {e}")
            q_bump_try(db_path, row_id)
            time.sleep(2.0)


# ----------------------------
# Main
# ----------------------------
def main():
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")
    load_dotenv(repo_root / "api" / ".env")

    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=os.getenv("API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--device-key", default=os.getenv("DEVICE_API_KEY", ""))
    parser.add_argument("--org", default=os.getenv("ORG_SLUG", "first-customer"))
    parser.add_argument("--device", default=os.getenv("DEVICE_NAME", "Scanner-1"))
    parser.add_argument("--ring", default=os.getenv("RING_LABEL", "A"))

    parser.add_argument("--folder", type=str, default="")
    parser.add_argument("--job-id", default="")

    parser.add_argument("--use-originals-as-previews", action="store_true")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--db", default=os.getenv("SIM_DB", DEFAULT_DB_PATH))
    args = parser.parse_args()

    if not args.device_key:
        raise SystemExit("DEVICE_API_KEY missing. Put it in nova/api/.env or pass --device-key.")
    q_init(args.db)

    if args.sync:
        run_sync_queue(args.api, args.device_key, args.db)
        return

    if not args.folder:
        raise SystemExit("Pass --folder to ingest a dataset folder, or use --sync to sync queued originals.")

    folder = Path(args.folder).resolve()
    slots = discover_slots(folder)
    print(f"📁 discovered {len(slots)} slots from: {folder}")
    if not slots:
        raise SystemExit("No slots discovered. Expected files like slot_<n>_uv_free.jpg and slot_<n>_aset.jpg")

    # create job unless provided
    job_id = args.job_id or jobs_start(args.api, args.device_key, args.org, args.device)
    print("✅ job_id:", job_id)

    for slot_index, uv_file, aset_file in slots:
        # choose preview inputs
        if args.use_originals_as_previews:
            uv_preview = uv_file
            aset_preview = aset_file
        else:
            # if you later add real thumbnails on disk, change this.
            uv_preview = uv_file
            aset_preview = aset_file

        # upload previews + ingest
        do_slot_previews_and_ingest(
            api_base=args.api,
            device_key=args.device_key,
            org_slug=args.org,
            job_id=job_id,
            ring_label=args.ring,
            device_name=args.device,
            slot_index=slot_index,
            uv_preview_file=uv_preview,
            aset_preview_file=aset_preview,
        )

        # queue originals for later sync
        q_push(
            args.db,
            "upload_originals",
            {
                "org_slug": args.org,
                "job_id": job_id,
                "ring_label": args.ring,
                "slot_index": slot_index,
                "device_name": args.device,
                "uv_original_file": str(uv_file),
                "aset_original_file": str(aset_file),
            },
        )

        print(f"✅ done slot {slot_index}")

    print("\nDONE ✅")
    print("Dashboard: http://localhost:3000/dashboard")
    print(f"Job: http://localhost:3000/jobs/{job_id}")
    print("Originals were queued for later. Run: python scripts/device_simulator.py --sync")


if __name__ == "__main__":
    main()
