#!/usr/bin/env python3
"""Mux a video-only mp4 with an audio track and stamp a sequential output file."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True, help="Input video-only mp4.")
    parser.add_argument("--audio", type=Path, required=True, help="Input mp3 track.")
    parser.add_argument(
        "--output-base",
        type=Path,
        default=Path("output"),
        help="Base name for the generated outputs (default: output).",
    )
    return parser.parse_args()


def run_ffmpeg(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        output = result.stderr.strip() or result.stdout.strip() or "ffmpeg command failed"
        raise SystemExit(output)


def next_versioned_path(base: Path) -> Path:
    parent = base.parent
    stem = base.stem
    pattern = re.compile(rf"^{re.escape(stem)}_(\d+)\.mp4$")
    max_index = 0
    for candidate in parent.glob(f"{stem}_*.mp4"):
        match = pattern.match(candidate.name)
        if match:
            try:
                value = int(match.group(1))
            except ValueError:
                continue
            max_index = max(max_index, value)
    return parent / f"{stem}_{max_index + 1:03d}.mp4"


def main() -> None:
    args = parse_args()
    video_path = args.video.resolve()
    audio_path = args.audio.resolve()

    if not video_path.exists():
        raise SystemExit(f"Video file {video_path} not found.")

    base_path = args.output_base.resolve()
    final_output = base_path.with_suffix(".mp4").resolve()
    final_output.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"

    audio_available = audio_path.exists() and audio_path.stat().st_size > 0
    tmp_output = final_output.with_suffix(".tmp.mp4")
    try:
        if audio_available:
            run_ffmpeg(
                [
                    ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(video_path),
                    "-i",
                    str(audio_path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    "-shortest",
                    str(tmp_output),
                ]
            )
        else:
            shutil.copyfile(video_path, tmp_output)

        shutil.move(tmp_output, final_output)
    finally:
        if tmp_output.exists():
            tmp_output.unlink(missing_ok=True)

    versioned_path = next_versioned_path(final_output)
    shutil.copyfile(final_output, versioned_path)
    print(f"generated {final_output.name} ({versioned_path.name})")


if __name__ == "__main__":
    main()
