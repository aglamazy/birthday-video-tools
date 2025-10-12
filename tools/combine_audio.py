#!/usr/bin/env python3
"""Combine multiple audio files into a single MP3 track."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Destination MP3 file.")
    parser.add_argument(
        "--target-duration",
        type=float,
        help="Trim the combined audio to this many seconds (optional).",
    )
    parser.add_argument(
        "--ffprobe",
        type=str,
        help="Path to ffprobe (optional; defaults to autodetect).",
    )
    parser.add_argument("inputs", nargs="*", help="Audio files to concatenate in order.")
    return parser.parse_args()


def run_ffmpeg(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        output = result.stderr.strip() or result.stdout.strip() or "ffmpeg command failed"
        raise SystemExit(output)


def probe_duration(ffprobe_path: Optional[str], media_path: Path) -> Optional[float]:
    if not ffprobe_path:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    text = result.stdout.strip()
    if not text:
        return None
    try:
        value = float(text)
        if value <= 0:
            return None
        return value
    except ValueError:
        return None


def _trim_audio_tail(
    ffmpeg_path: str,
    source_path: Path,
    target_duration: float,
    tmp_dir: Path,
) -> Path:
    output_path = tmp_dir / f"trimmed_{source_path.stem}.m4a"
    fade_duration = min(1.0, max(0.0, target_duration / 10.0))
    filters = [f"atrim=0:{target_duration:.6f}", "asetpts=PTS-STARTPTS"]
    if fade_duration > 0:
        fade_start = max(0.0, target_duration - fade_duration)
        filters.append(f"afade=t=out:st={fade_start:.6f}:d={fade_duration:.6f}")
    filter_complex = ",".join(filters)
    run_ffmpeg(
        [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_path),
            "-filter_complex",
            filter_complex,
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "48000",
            str(output_path),
        ]
    )
    return output_path


def _adjust_tracks(
    ffmpeg_path: str,
    sources: List[Path],
    target_duration: Optional[float],
    ffprobe_path: Optional[str],
    tmp_dir: Path,
) -> List[Path]:
    if target_duration is None or target_duration <= 0 or len(sources) == 0:
        return sources

    durations = [probe_duration(ffprobe_path, path) for path in sources]
    if any(duration is None for duration in durations):
        return sources

    total = sum(durations)  # type: ignore[arg-type]
    excess = total - target_duration
    if excess <= 0:
        return sources

    adjusted = list(sources)
    min_length = 0.5

    for idx in range(len(sources) - 2, -1, -1):
        if excess <= 0:
            break
        current_duration = durations[idx]  # type: ignore[index]
        if current_duration is None or current_duration <= min_length:
            continue
        max_trim = current_duration - min_length
        trim_amount = min(excess, max_trim)
        target = current_duration - trim_amount
        trimmed = _trim_audio_tail(ffmpeg_path, adjusted[idx], target, tmp_dir)
        adjusted[idx] = trimmed
        durations[idx] = target
        excess -= trim_amount

    if excess > 0:
        last_idx = len(sources) - 1
        last_duration = durations[last_idx]  # type: ignore[index]
        if last_duration and last_duration > min_length:
            max_trim = max(0.0, last_duration - min_length)
            trim_amount = min(excess, max_trim)
            target = last_duration - trim_amount
            trimmed = _trim_audio_tail(ffmpeg_path, adjusted[last_idx], target, tmp_dir)
            adjusted[last_idx] = trimmed

    return adjusted


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
    ffprobe_path = args.ffprobe or shutil.which("ffprobe")

    target_duration = args.target_duration
    if target_duration is None:
        candidate = output_path.with_name(f"{output_path.stem}.video.mp4")
        if candidate.exists():
            probed = probe_duration(ffprobe_path, candidate)
            if probed:
                target_duration = probed

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        adjusted_inputs = _adjust_tracks(
            ffmpeg_path,
            list(inputs),
            target_duration,
            ffprobe_path,
            tmp_dir,
        )

        if len(adjusted_inputs) == 1:
            run_ffmpeg(
                [
                    ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(adjusted_inputs[0]),
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
        for source in adjusted_inputs:
            cmd.extend(["-i", str(source)])
        filter_inputs = "".join(f"[{idx}:a]" for idx in range(len(adjusted_inputs)))
        cmd.extend(
            [
                "-filter_complex",
                f"{filter_inputs}concat=n={len(adjusted_inputs)}:v=0:a=1[aout]",
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
