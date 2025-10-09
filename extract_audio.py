#!/usr/bin/env python3
"""Extract an audio clip from an MP4 and save it as MP3."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Input video file (e.g., MP4)")
    parser.add_argument(
        "duration",
        nargs="?",
        type=float,
        help="Length to extract in seconds (optional; default is to extract entire track)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Destination MP3 file (default: input name with .mp3)",
    )
    parser.add_argument(
        "--ffmpeg",
        type=str,
        default="ffmpeg",
        help="Path to the ffmpeg executable (default: ffmpeg in PATH)",
    )
    parser.add_argument(
        "--start",
        type=float,
        default=0.0,
        help="Start time in seconds (default: 0)",
    )
    parser.add_argument(
        "--trim-start",
        type=float,
        default=0.0,
        help="Amount of audio (seconds) to trim from the beginning before exporting.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the output file if it already exists",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.duration is not None and args.duration <= 0:
        raise SystemExit("Duration must be positive")

    if args.trim_start < 0:
        raise SystemExit("--trim-start must be zero or positive")

    if not args.source.exists():
        raise SystemExit(f"Source file {args.source} does not exist")

    ffmpeg_path = shutil.which(args.ffmpeg)
    if not ffmpeg_path:
        raise SystemExit("ffmpeg executable not found; install ffmpeg or provide --ffmpeg path")

    if args.output is None:
        args.output = args.source.with_suffix(".mp3")

    if args.output.exists() and not args.overwrite:
        raise SystemExit(f"Output file {args.output} already exists. Use --overwrite to replace it.")

    trim_start = args.trim_start
    effective_start = (args.start or 0.0) + trim_start

    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y" if args.overwrite else "-n",
        "-ss",
        str(effective_start),
        "-i",
        str(args.source),
    ]

    if args.duration is not None:
        cmd.extend(["-t", str(args.duration)])

    cmd.extend(
        [
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-b:a",
            "192k",
            str(args.output),
        ]
    )

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"ffmpeg failed with exit code {exc.returncode}")

    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
