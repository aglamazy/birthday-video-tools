#!/usr/bin/env python3
"""Print audio stream details (codec, channels, layout, sample rate)."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Input media file")
    parser.add_argument("--ffprobe", help="Path to ffprobe (optional)")
    return parser.parse_args()


def run_ffprobe(ffprobe_path: str, media_path: Path) -> dict:
    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        str(media_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def format_stream_info(streams: list[dict]) -> str:
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not audio_streams:
        return "No audio streams found."

    lines = []
    for idx, stream in enumerate(audio_streams, start=1):
        codec = stream.get("codec_name", "unknown")
        profile = stream.get("profile")
        channels = stream.get("channels")
        layout = stream.get("channel_layout") or "unknown"
        sample_rate = stream.get("sample_rate")
        bits_per_sample = stream.get("bits_per_sample")
        tag_duration = stream.get("tags", {}).get("DURATION")
        duration = stream.get("duration") or tag_duration

        parts = [f"audio#{idx}: codec={codec}"]
        if profile:
            parts.append(f"profile={profile}")
        if channels is not None:
            parts.append(f"channels={channels}")
        if layout:
            parts.append(f"layout={layout}")
        if sample_rate:
            parts.append(f"sample_rate={sample_rate}")
        if bits_per_sample:
            parts.append(f"bits_per_sample={bits_per_sample}")
        if duration:
            parts.append(f"duration={duration}")
        lines.append(", ".join(parts))

    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    media_path = args.source.resolve()
    if not media_path.exists():
        raise SystemExit(f"File {media_path} does not exist.")

    ffprobe_path = args.ffprobe or shutil.which("ffprobe")
    if not ffprobe_path:
        raise SystemExit("ffprobe not found; specify via --ffprobe")

    try:
        data = run_ffprobe(ffprobe_path, media_path)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr or exc.stdout or str(exc)
        raise SystemExit(message) from exc

    streams = data.get("streams", [])
    print(format_stream_info(streams))


if __name__ == "__main__":
    main()
