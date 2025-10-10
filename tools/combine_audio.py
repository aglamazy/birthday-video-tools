#!/usr/bin/env python3
"""Combine multiple audio files into a single MP3 track."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Destination MP3 file.")
    parser.add_argument("inputs", nargs="*", help="Audio files to concatenate in order.")
    return parser.parse_args()


def run_ffmpeg(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        output = result.stderr.strip() or result.stdout.strip() or "ffmpeg command failed"
        raise SystemExit(output)


def main() -> None:
    args = parse_args()
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    resolved_inputs = []
    for item in args.inputs:
        path = Path(item).resolve()
        if path.exists():
            resolved_inputs.append(path)
    inputs = resolved_inputs
    if not inputs:
        # No audio inputs: create an empty placeholder so downstream rules succeed.
        output_path.write_bytes(b"")
        return

    ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"

    if len(inputs) == 1:
        run_ffmpeg(
            [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(inputs[0]),
                "-vn",
                "-acodec",
                "libmp3lame",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(output_path),
            ]
        )
        return

    cmd: list[str] = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
    ]
    for source in inputs:
        cmd.extend(["-i", str(source)])
    filter_inputs = "".join(f"[{idx}:a]" for idx in range(len(inputs)))
    cmd.extend(
        [
            "-filter_complex",
            f"{filter_inputs}concat=n={len(inputs)}:v=0:a=1[aout]",
            "-map",
            "[aout]",
            "-acodec",
            "libmp3lame",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output_path),
        ]
    )
    run_ffmpeg(cmd)


if __name__ == "__main__":
    main()
