#!/usr/bin/env python3
"""Find duplicate files under a directory.

Default mode reports exact byte-for-byte matches. With --deep enabled the
script also searches for visually similar images using a simple perceptual
hash and Hamming-distance threshold.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from PIL import Image

BUFFER_SIZE = 1024 * 1024  # 1 MiB
IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
}


def hash_file(path: Path) -> str:
    hasher = hashlib.md5()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(BUFFER_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def iter_files(root: Path, follow_links: bool = False) -> Iterable[Path]:
    for dirpath, _, filenames in os.walk(root, followlinks=follow_links):
        directory = Path(dirpath)
        for name in filenames:
            yield directory / name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        type=Path,
        nargs="?",
        default=Path("sequence"),
        help="Directory to scan (default: sequence)",
    )
    parser.add_argument(
        "--follow-links",
        action="store_true",
        help="Follow symbolic links while walking the tree",
    )
    parser.add_argument(
        "--min-size",
        type=int,
        default=1,
        help="Only consider files at least this many bytes in size (default: 1)",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Also look for visually similar images using perceptual hashing",
    )
    parser.add_argument(
        "--hamming",
        type=int,
        default=5,
        help="Hamming-distance threshold when comparing perceptual hashes (default: 5)",
    )
    return parser.parse_args()


def average_hash(path: Path, hash_size: int = 8) -> int:
    with Image.open(path) as img:
        img = img.convert("L").resize((hash_size, hash_size), Image.LANCZOS)
        pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for idx, value in enumerate(pixels):
        if value >= avg:
            bits |= 1 << idx
    return bits


def hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def build_similar_groups(
    images: Sequence[Tuple[Path, int]],
    threshold: int,
) -> List[List[Path]]:
    if len(images) < 2:
        return []

    parent = list(range(len(images)))

    def find(idx: int) -> int:
        if parent[idx] != idx:
            parent[idx] = find(parent[idx])
        return parent[idx]

    def union(a: int, b: int) -> None:
        root_a = find(a)
        root_b = find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    for idx in range(len(images)):
        _, hash_a = images[idx]
        for jdx in range(idx + 1, len(images)):
            _, hash_b = images[jdx]
            if hamming_distance(hash_a, hash_b) <= threshold:
                union(idx, jdx)

    clusters: Dict[int, List[Path]] = defaultdict(list)
    for idx, (path, _) in enumerate(images):
        clusters[find(idx)].append(path)
    return [group for group in clusters.values() if len(group) > 1]


def main() -> None:
    args = parse_args()
    if args.hamming < 0:
        raise SystemExit("--hamming must be non-negative")

    if not args.root.exists() or not args.root.is_dir():
        raise SystemExit(f"Root directory {args.root} does not exist or is not a directory")

    candidates: Dict[str, List[Path]] = defaultdict(list)
    processed: List[Path] = []
    total_files = 0

    for path in iter_files(args.root, follow_links=args.follow_links):
        try:
            if path.stat().st_size < args.min_size:
                continue
        except OSError:
            continue
        try:
            digest = hash_file(path)
        except OSError as exc:
            print(f"Warning: failed to read {path}: {exc}")
            continue
        candidates[digest].append(path)
        processed.append(path)
        total_files += 1

    duplicate_groups = [paths for paths in candidates.values() if len(paths) > 1]
    if duplicate_groups:
        print(
            f"Scanned {total_files} file(s); found {len(duplicate_groups)} exact duplicate group(s):"
        )
        for idx, group in enumerate(duplicate_groups, start=1):
            print(f"\nExact Group {idx} ({len(group)} files):")
            for path in group:
                print(f"  {path}")
    else:
        print(f"Scanned {total_files} file(s); no exact duplicates found.")

    if not args.deep:
        return

    image_entries: List[Tuple[Path, int]] = []
    for path in processed:
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        try:
            phash = average_hash(path)
        except OSError as exc:
            print(f"Warning: failed to hash {path}: {exc}")
            continue
        image_entries.append((path, phash))

    similar_groups = build_similar_groups(image_entries, args.hamming)
    if similar_groups:
        print(
            f"\nFound {len(similar_groups)} visually similar group(s) (Hamming ≤ {args.hamming}):"
        )
        for idx, group in enumerate(similar_groups, start=1):
            print(f"\nSimilar Group {idx} ({len(group)} files):")
            for path in group:
                print(f"  {path}")
    else:
        print("\nNo visually similar images found under the current threshold.")


if __name__ == "__main__":
    main()
