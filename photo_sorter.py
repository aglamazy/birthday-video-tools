#!/usr/bin/env python3
"""Utility to group media files into year folders based on filename heuristics."""

from __future__ import annotations

import argparse
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import exifread
from PIL import Image, ImageChops, ImageStat, UnidentifiedImageError

# Heuristics for extracting an 8 digit date token from the filename.
DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?P<date>\d{8})(?:[_-].*)?"), "leading yyyymmdd token"),
    (re.compile(r"^IMG[-_](?P<date>\d{8}).*", re.IGNORECASE), "IMG prefix"),
)

# Common media extensions we care about. Extend as needed.
MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".tif",
    ".tiff",
    ".bmp",
    ".gif",
    ".mp4",
    ".mov",
    ".avi",
}

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".tif",
    ".tiff",
    ".bmp",
    ".gif",
}

EXIF_KEYS = (
    "EXIF DateTimeOriginal",
    "EXIF DateTimeDigitized",
    "Image DateTime",
)

PIL_EXIF_TAGS = (36867, 36868, 306)  # DateTimeOriginal, DateTimeDigitized, DateTime


@dataclass(frozen=True)
class MovePlan:
    source: Path
    destination: Path
    year: str
    date_token: str
    index: int
    reason: str


def infer_date_token_from_name(path: Path) -> tuple[Optional[str], Optional[str]]:
    """Return (yyyymmdd token, reason) if filename contains a date hint."""
    stem = path.name
    for pattern, reason in DATE_PATTERNS:
        match = pattern.match(stem)
        if not match:
            continue
        token = match.group("date")
        year = token[:4]
        if 1900 <= int(year) <= 2100:
            return token, reason
    return None, None


def iter_media_files(base_dir: Path) -> Iterable[Path]:
    for entry in sorted(base_dir.iterdir()):
        if entry.is_dir():
            continue
        if entry.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        yield entry


def determine_date_token(path: Path) -> tuple[Optional[str], Optional[str]]:
    suffix = path.suffix.lower()
    is_image = suffix in IMAGE_EXTENSIONS

    if is_image:
        token, reason = extract_metadata_date(path)
        if token:
            return token, reason

    token, reason = infer_date_token_from_name(path)
    if token:
        return token, reason

    if is_image and is_black_and_white(path):
        return "19600101", "black-and-white heuristic"

    return None, None


def build_move_plan(base_dir: Path) -> list[MovePlan]:
    plan: list[MovePlan] = []
    counters: Dict[Tuple[Path, str], int] = {}
    for media in iter_media_files(base_dir):
        token, reason = determine_date_token(media)
        if not token:
            continue
        year = token[:4]
        destination_dir = base_dir / year
        destination_path, index = resolve_destination_path(
            destination_dir, media, token, counters
        )
        if destination_path == media:
            continue
        plan.append(
            MovePlan(
                source=media,
                destination=destination_path,
                year=year,
                date_token=token,
                index=index,
                reason=reason,
            )
        )
    return plan


def resolve_destination_path(
    target_dir: Path,
    source: Path,
    date_token: str,
    counters: Dict[Tuple[Path, str], int],
) -> tuple[Path, int]:
    key = (target_dir, date_token)
    if key not in counters:
        counters[key] = highest_existing_index(target_dir, date_token)

    counters[key] += 1
    index = counters[key]
    suffix = source.suffix.lower()
    destination = target_dir / f"{date_token}-{index:02d}{suffix}"
    return destination, index


def highest_existing_index(target_dir: Path, date_token: str) -> int:
    if not target_dir.exists():
        return 0
    pattern = re.compile(rf"^{re.escape(date_token)}-(?P<idx>\d+)\.", re.IGNORECASE)
    max_index = 0
    for entry in target_dir.iterdir():
        if not entry.is_file():
            continue
        match = pattern.match(entry.name)
        if not match:
            continue
        idx = int(match.group("idx"))
        if idx > max_index:
            max_index = idx
    return max_index


def extract_metadata_date(path: Path) -> tuple[Optional[str], Optional[str]]:
    token, reason = _extract_metadata_with_exifread(path)
    if token:
        return token, reason

    token, reason = _extract_metadata_with_pillow(path)
    if token:
        return token, reason

    return None, None


def _extract_metadata_with_exifread(path: Path) -> tuple[Optional[str], Optional[str]]:
    try:
        with path.open("rb") as fh:
            tags = exifread.process_file(fh, stop_tag="Image DateTime", details=False)
    except Exception:
        return None, None

    for key in EXIF_KEYS:
        value = tags.get(key)
        if not value:
            continue
        token = parse_exif_datetime(str(value))
        if token:
            return token, f"metadata {key}"
    return None, None


def _extract_metadata_with_pillow(path: Path) -> tuple[Optional[str], Optional[str]]:
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None, None
            for tag in PIL_EXIF_TAGS:
                raw_value = exif.get(tag)
                if not raw_value:
                    continue
                if isinstance(raw_value, bytes):
                    try:
                        raw_value = raw_value.decode("utf-8", errors="ignore")
                    except Exception:
                        continue
                token = parse_exif_datetime(str(raw_value))
                if token:
                    return token, f"metadata EXIF tag {tag}"
    except (UnidentifiedImageError, OSError):
        return None, None
    except Exception:
        return None, None
    return None, None


def parse_exif_datetime(value: str) -> Optional[str]:
    value = value.strip()
    if len(value) < 10:
        return None
    date_part = value.split()[0]
    if date_part.count(":") >= 2:
        parts = date_part.split(":")
    elif date_part.count("-") >= 2:
        parts = date_part.split("-")
    else:
        return None
    try:
        year, month, day = parts[0], parts[1], parts[2]
    except IndexError:
        return None
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return None
    try:
        datetime(int(year), int(month), int(day))
    except ValueError:
        return None
    return f"{int(year):04d}{int(month):02d}{int(day):02d}"


def is_black_and_white(path: Path) -> bool:
    try:
        with Image.open(path) as img:
            if img.mode in {"1", "L", "LA"}:
                return True
            rgb = img.convert("RGB")
            sample = rgb.resize((64, 64)) if max(rgb.size) > 64 else rgb
            gray = sample.convert("L").convert("RGB")
            diff = ImageChops.difference(sample, gray)
            stat = ImageStat.Stat(diff)
            mean_diff = sum(stat.mean) / (len(stat.mean) * 255)
            return mean_diff < 0.02
    except (UnidentifiedImageError, OSError):
        return False
    except Exception:
        return False


def apply_move_plan(base_dir: Path, plans: Iterable[MovePlan], dry_run: bool) -> None:
    moves = list(plans)
    if not moves:
        print("No files matched the heuristics.")
        return

    for item in moves:
        relative_src = item.source.relative_to(base_dir)
        relative_dest = item.destination.relative_to(base_dir)
        print(f"{relative_src} -> {relative_dest} ({item.reason})")
        if dry_run:
            continue
        item.destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(item.source), str(item.destination))

    if not dry_run:
        print(f"Moved {len(moves)} file(s).")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("data"),
        help="Directory containing the media files (default: data).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List planned moves without touching the filesystem.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_dir = args.source_dir
    if not base_dir.exists():
        raise SystemExit(f"Source directory {base_dir} does not exist")
    if not base_dir.is_dir():
        raise SystemExit(f"Source path {base_dir} is not a directory")

    plan = build_move_plan(base_dir)
    apply_move_plan(base_dir, plan, args.dry_run)


if __name__ == "__main__":
    main()
