#!/usr/bin/env python3
from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path
from typing import Dict, Tuple, List, Optional

# ----------------------------
# Config
# ----------------------------
BASE = Path("~/repo/nasa/resource/dataset").expanduser()
ASET_DIR = BASE / "aset"
TABLE_DIR = BASE / "table"

OUT_DIR = BASE / "normalized"
NAME_MAP_CSV = OUT_DIR / "name_map.csv"
SLOT_MATCHES_CSV = OUT_DIR / "slot_matches.csv"

# Behavior: "copy" | "symlink" | "move"
MODE = "copy"

TABLE_TEMPLATE = "{n}-{v}-Table Color UV Free-01.jpg"
ASET_TEMPLATE = "{n}-{v}-ASET Black-01.jpg"

KEY_RE = re.compile(r"^\s*(\d+)-(\d+)-")  # N-V-...


def extract_key(filename: str) -> Tuple[int, int]:
    m = KEY_RE.match(filename)
    if not m:
        raise ValueError(f"Cannot parse key from filename: {filename!r}")
    return int(m.group(1)), int(m.group(2))


def collect_files(folder: Path) -> Dict[Tuple[int, int], Path]:
    mapping: Dict[Tuple[int, int], Path] = {}
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() == ".jpg":
            key = extract_key(p.name)
            mapping.setdefault(key, p)  # keep first if duplicates exist
    return mapping


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def place(src: Path, dst: Path, mode: str) -> None:
    if mode == "copy":
        shutil.copy2(src, dst)
    elif mode == "move":
        shutil.move(str(src), str(dst))
    elif mode == "symlink":
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        dst.symlink_to(src.resolve())
    else:
        raise ValueError(f"Unknown MODE={mode!r}. Use 'copy', 'move', or 'symlink'.")


def maybe_write(
    src: Optional[Path],
    supposed_old_name: str,
    dst: Path,
    mode: str,
    rows: List[Tuple[str, str]],
) -> None:
    """
    If src exists: create dst and write (src, dst).
    If missing: do not create dst, write (supposed_old_name, 'null').
    """
    if src is not None and src.exists():
        place(src, dst, mode)
        rows.append((str(src), str(dst)))
    else:
        rows.append((supposed_old_name, "null"))


def main() -> None:
    aset = collect_files(ASET_DIR)
    table = collect_files(TABLE_DIR)

    # slots are assigned over the union of keys
    keys = sorted(set(aset.keys()) | set(table.keys()))
    ensure_dir(OUT_DIR)

    # Build key <-> slot index maps
    key_to_slot: Dict[Tuple[int, int], int] = {key: i for i, key in enumerate(keys)}
    slot_rows: List[Tuple[str, int, int, str]] = []  # slot, N, V, match_slot

    # 1) Build slot match CSV data
    for (n, v), slot_idx in key_to_slot.items():
        other_v = 2 if v == 1 else 1
        other_key = (n, other_v)
        match_slot = key_to_slot.get(other_key)
        slot_rows.append(
            (f"slot_{slot_idx}", n, v, (f"slot_{match_slot}" if match_slot is not None else "null"))
        )

    # 2) Create renamed files + name map CSV
    name_map_rows: List[Tuple[str, str]] = []

    for slot_idx, (n, v) in enumerate(keys):
        uv_dst = OUT_DIR / f"slot_{slot_idx}_uv_free.jpg"
        aset_dst = OUT_DIR / f"slot_{slot_idx}_aset.jpg"

        supposed_uv = str(TABLE_DIR / TABLE_TEMPLATE.format(n=n, v=v))
        supposed_aset = str(ASET_DIR / ASET_TEMPLATE.format(n=n, v=v))

        uv_src = table.get((n, v))
        aset_src = aset.get((n, v))

        maybe_write(uv_src, supposed_uv, uv_dst, MODE, name_map_rows)
        maybe_write(aset_src, supposed_aset, aset_dst, MODE, name_map_rows)

    # Write name map CSV
    with NAME_MAP_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["old_name", "new_name"])
        w.writerows(name_map_rows)

    # Write slot matches CSV
    # (sorted by slot number for readability)
    slot_rows.sort(key=lambda r: int(r[0].split("_")[1]))
    with SLOT_MATCHES_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["slot", "pair_n", "diamond_v", "match_slot"])
        w.writerows(slot_rows)

    print(
        "Done.\n"
        f"Output folder: {OUT_DIR}\n"
        f"CSV (name map): {NAME_MAP_CSV}\n"
        f"CSV (slot matches): {SLOT_MATCHES_CSV}\n"
        f"Rows in name map: {len(name_map_rows)} (2 per slot)\n"
        f"Slots: {len(keys)}"
    )


if __name__ == "__main__":
    main()

