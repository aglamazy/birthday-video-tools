#!/usr/bin/env python3
"""Flatten the data folder into a chronological sequence based on simple year detection."""

from __future__ import annotations

import argparse
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

YEAR_PATTERN = re.compile(r"(19|20)\d{2}")


@dataclass(frozen=True)
class CopyPlan:
    source: Path
    destination: Path
    year: str
    index: int
    reason: str


def iter_all_files(base_dir: Path, exclude: Optional[set[Path]] = None) -> Iterable[Path]:
    base_dir = base_dir.resolve()
    exclude = {p.resolve() for p in exclude} if exclude else set()

    def _is_excluded(path: Path) -> bool:
        for root in exclude:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _iter(current: Path) -> Iterable[Path]:
        entries = sorted(current.iterdir(), key=_entry_sort_key)
        for entry in entries:
            entry_resolved = entry.resolve()
            if _is_excluded(entry_resolved):
                continue
            if entry.is_file():
                yield entry
            elif entry.is_dir():
                yield from _iter(entry)

    yield from _iter(base_dir)


def _entry_sort_key(path: Path) -> tuple[int, int, str]:
    name = path.name
    numeric_value: Optional[int] = None
    if re.fullmatch(r"\d+", name):
        try:
            numeric_value = int(name)
        except ValueError:
            numeric_value = None
    is_dir = path.is_dir()
    if is_dir and numeric_value is not None:
        return (0, numeric_value, "")
    if is_dir:
        return (1, 0, name.lower())
    if numeric_value is not None:
        return (2, numeric_value, "")
    return (3, 0, name.lower())


def build_sequence_plan(base_dir: Path, dest_dir: Path) -> list[CopyPlan]:
    plan: list[CopyPlan] = []
    counters: Dict[str, int] = {}

    exclude: set[Path] = set()
    dest_resolved = dest_dir.resolve()
    try:
        dest_resolved.relative_to(base_dir.resolve())
    except ValueError:
        pass
    else:
        exclude.add(dest_resolved)

    for file_path in iter_all_files(base_dir, exclude=exclude):
        year, reason = determine_year(base_dir, file_path)
        if not year:
            continue
        destination, index = resolve_destination(dest_dir, file_path, year, counters)
        plan.append(
            CopyPlan(
                source=file_path,
                destination=destination,
                year=year,
                index=index,
                reason=reason,
            )
        )
    return plan


def determine_year(base_dir: Path, path: Path) -> tuple[Optional[str], Optional[str]]:
    year = find_year_in_string(path.stem)
    if year:
        return year, "year from filename"

    year = find_year_in_parents(base_dir, path)
    if year:
        return year, "year from folder"

    return None, None


def find_year_in_string(text: str) -> Optional[str]:
    for match in YEAR_PATTERN.finditer(text):
        value = int(match.group())
        if 1900 <= value <= 2100:
            return f"{value:04d}"
    return None


def find_year_in_parents(base_dir: Path, path: Path) -> Optional[str]:
    base_resolved = base_dir.resolve()
    current = path.resolve()
    for ancestor in current.parents:
        if ancestor == base_resolved:
            break
        year = find_year_in_string(ancestor.name)
        if year:
            return year
    return None


def resolve_destination(
    dest_dir: Path,
    source: Path,
    year: str,
    counters: Dict[str, int],
) -> tuple[Path, int]:
    counters.setdefault(year, highest_existing_index(dest_dir, year))
    index = counters[year]
    suffix = source.suffix.lower()
    while True:
        index += 1
        candidate = dest_dir / f"{year}-{index:04d}{suffix}"
        if not candidate.exists():
            counters[year] = index
            return candidate, index


def highest_existing_index(dest_dir: Path, year: str) -> int:
    if not dest_dir.exists():
        return 0
    pattern = re.compile(rf"^{re.escape(year)}-(?P<idx>\d+)\.", re.IGNORECASE)
    max_index = 0
    for entry in dest_dir.iterdir():
        if not entry.is_file():
            continue
        match = pattern.match(entry.name)
        if not match:
            continue
        idx = int(match.group("idx"))
        if idx > max_index:
            max_index = idx
    return max_index


def apply_plan(
    base_dir: Path,
    dest_dir: Path,
    plans: Iterable[CopyPlan],
    dry_run: bool,
) -> None:
    actions = list(plans)
    if not actions:
        print("No files matched the heuristics.")
        return

    for item in actions:
        try:
            relative_src = item.source.relative_to(base_dir)
        except ValueError:
            relative_src = item.source
        try:
            relative_dest = item.destination.relative_to(dest_dir)
            dest_display = Path(dest_dir.name) / relative_dest
        except ValueError:
            dest_display = item.destination
        print(
            f"{relative_src} -> {dest_display} "
            f"({item.reason} -> {item.year}-{item.index:04d})"
        )
        if dry_run:
            continue
        item.destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.source, item.destination)

    if not dry_run:
        print(f"Copied {len(actions)} file(s).")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("data"),
        help="Root folder to scan (default: data).",
    )
    parser.add_argument(
        "--dest-dir",
        type=Path,
        default=Path("sequence"),
        help="Destination directory for the flattened sequence (default: sequence).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List planned copies without touching the filesystem.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    base_dir = args.source_dir.resolve()
    dest_dir = args.dest_dir.resolve()

    if not base_dir.exists():
        raise SystemExit(f"Source directory {base_dir} does not exist")
    if not base_dir.is_dir():
        raise SystemExit(f"Source path {base_dir} is not a directory")

    plan = build_sequence_plan(base_dir, dest_dir)
    apply_plan(base_dir, dest_dir, plan, args.dry_run)


if __name__ == "__main__":
    main()
