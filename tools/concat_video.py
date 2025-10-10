#!/usr/bin/env python3
"""Concatenate rendered segments into a single mp4."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import incremental_builder as ib


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segments", nargs="+", required=True, help="Ordered list of segment mp4 files.")
    parser.add_argument("--segments-dir", type=Path, help="Directory to place concat.txt (defaults to first segment's parent).")
    parser.add_argument("--output", type=Path, required=True, help="Output mp4 file.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    segment_paths = [Path(item).resolve() for item in args.segments]
    if not segment_paths:
        raise SystemExit("No segments provided.")

    missing = [path for path in segment_paths if not path.exists()]
    if missing:
        missing_list = ", ".join(str(path) for path in missing)
        raise SystemExit(f"Missing segment files: {missing_list}")

    output_path = Path(args.output).resolve()
    ib.ensure_dir(output_path.parent)

    segments_dir = args.segments_dir.resolve() if args.segments_dir else segment_paths[0].parent
    ib.ensure_dir(segments_dir)

    ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"
    ib.concat_segments(segment_paths, output_path, segments_dir, ffmpeg_path)


if __name__ == "__main__":
    main()
