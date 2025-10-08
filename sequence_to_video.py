#!/usr/bin/env python3
"""Create an MP4 slideshow from the media files in the sequence folder."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".hevc", ".mpg", ".mpeg", ".wmv"}
TEXT_EXTENSIONS = {".txt", ".pug"}

VERBOSE = False
LABEL_YEAR = False
FONT_PATH: Optional[Path] = None


@dataclass(frozen=True)
class Segment:
    source: Path
    output: Path
    kind: str  # "image", "video", or "text"
    label: Optional[str] = None
    text: Optional[str] = None


class FFMpegError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("sequence"),
        help="Folder containing the ordered media files (default: sequence).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("slideshow.mp4"),
        help="Output MP4 filename (default: slideshow.mp4).",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=2.0,
        help="Duration in seconds for each still image (default: 2.0).",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="Frame rate for the generated video (default: 30).",
    )
    parser.add_argument(
        "--resolution",
        type=str,
        default="1920x1080",
        help="Target resolution WIDTHxHEIGHT (default: 1920x1080).",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep intermediate segment files for debugging.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Only process the first N media files (useful for quick tests).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print ffmpeg commands before executing them.",
    )
    parser.add_argument(
        "--label-year",
        action="store_true",
        help="Overlay the detected year in the bottom-right corner of each segment.",
    )
    parser.add_argument(
        "--label-font",
        type=Path,
        help="Path to a .ttf/.otf font file to use with --label-year.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    global VERBOSE
    VERBOSE = args.verbose
    global LABEL_YEAR, FONT_PATH
    LABEL_YEAR = args.label_year
    if args.label_font and args.label_font.exists():
        FONT_PATH = args.label_font
    else:
        default_font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if default_font.exists():
            FONT_PATH = default_font
    if LABEL_YEAR and FONT_PATH is None:
        print(
            "Warning: --label-year requested but no usable font found. "
            "Provide --label-font to enable labels."
        )
        LABEL_YEAR = False

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise SystemExit("ffmpeg is required but not found in PATH.")

    width, height = parse_resolution(args.resolution)

    source_dir = args.source_dir.resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise SystemExit(f"Source directory {source_dir} does not exist or is not a directory")

    media_files = sorted(p for p in source_dir.iterdir() if p.is_file())
    if args.limit:
        media_files = media_files[: args.limit]
    if not media_files:
        print("No media files found in source directory.")
        return

    segments: List[Segment] = []
    tmp_dir_ctx = tempfile.TemporaryDirectory() if not args.keep_temp else None
    if tmp_dir_ctx is not None:
        temp_dir_path = Path(tmp_dir_ctx.name)
    else:
        temp_dir_path = args.output.parent / ".segments"
        temp_dir_path.mkdir(parents=True, exist_ok=True)

    for idx, media in enumerate(media_files, start=1):
        suffix = media.suffix.lower()
        segment_path = temp_dir_path / f"segment_{idx:04d}.mp4"
        if suffix in IMAGE_EXTENSIONS:
            segments.append(
                Segment(
                    source=media,
                    output=segment_path,
                    kind="image",
                    label=infer_year_text(media),
                )
            )
        elif suffix in VIDEO_EXTENSIONS:
            segments.append(
                Segment(
                    source=media,
                    output=segment_path,
                    kind="video",
                    label=infer_year_text(media),
                )
            )
        elif suffix in TEXT_EXTENSIONS:
            text = parse_text_slide(media)
            segments.append(
                Segment(
                    source=media,
                    output=segment_path,
                    kind="text",
                    text=text,
                )
            )
        else:
            print(f"Skipping unsupported file {media.name}")

    if not segments:
        print("No convertible media files found.")
        return

    concat_entries: List[str] = []
    for segment in segments:
        concat_entries.append(f"file '{segment.output.as_posix()}'")

    concat_path = temp_dir_path / "concat.txt"
    concat_path.write_text("\n".join(concat_entries), encoding="utf-8")

    try:
        total = len(segments)
        for idx, segment in enumerate(segments, start=1):
            label = segment.label if LABEL_YEAR else None
            extra: List[str] = []
            if label:
                extra.append(f"label {label}")
            if segment.kind == "text" and segment.text:
                non_empty = next((ln for ln in segment.text.splitlines() if ln.strip()), "")
                if non_empty:
                    suffix = "…" if len(non_empty) > 40 else ""
                    extra.append(f"text '{non_empty[:40]}{suffix}'")
                else:
                    extra.append("text slide")
            print(
                f"[{idx}/{total}] {segment.kind} {segment.source.name}"
                f" -> {segment.output.name}" + (f" ({', '.join(extra)})" if extra else "")
            )
            if segment.kind == "image":
                run_ffmpeg(
                    [
                        ffmpeg_path,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-loop",
                        "1",
                        "-framerate",
                        str(args.fps),
                        "-t",
                        f"{args.duration}",
                        "-i",
                        str(segment.source),
                        "-f",
                        "lavfi",
                        "-t",
                        f"{args.duration}",
                        "-i",
                        "anullsrc=channel_layout=stereo:sample_rate=48000",
                        "-shortest",
                        "-vf",
                        build_filter_chain(width, height, label),
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        "-r",
                        str(args.fps),
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-movflags",
                        "+faststart",
                        str(segment.output),
                    ]
                )
            elif segment.kind == "video":
                run_ffmpeg(
                    [
                        ffmpeg_path,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(segment.source),
                        "-vf",
                        build_filter_chain(width, height, label),
                        "-r",
                        str(args.fps),
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a?",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-movflags",
                        "+faststart",
                        str(segment.output),
                    ]
                )
            else:  # text
                run_ffmpeg(
                    build_text_segment_cmd(
                        ffmpeg_path,
                        segment.text or "",
                        segment.output,
                        args.duration,
                        width,
                        height,
                        args.fps,
                    )
                )

        run_ffmpeg(
            [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-c",
                "copy",
                str(args.output),
            ]
        )
        print(f"Created {args.output}")
    finally:
        if tmp_dir_ctx:
            tmp_dir_ctx.cleanup()


def parse_resolution(value: str) -> Tuple[int, int]:
    parts = value.lower().split("x")
    if len(parts) != 2:
        raise SystemExit(f"Invalid resolution '{value}'. Use WIDTHxHEIGHT.")
    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError as exc:
        raise SystemExit(f"Invalid resolution '{value}'.") from exc
    return width, height


def build_filter_chain(width: int, height: int, label: Optional[str]) -> str:
    base = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
    )
    if label:
        text = escape_drawtext(label)
        font_clause = ""
        if FONT_PATH:
            font_clause = f"fontfile='{escape_drawtext(FONT_PATH.as_posix())}':"
        base += (
            f",drawtext={font_clause}text='{text}':fontsize=48:fontcolor=white:"
            f"box=1:boxcolor=0x00000088:x=w-tw-40:y=h-th-40"
        )
    return base


def run_ffmpeg(cmd: Iterable[str]) -> None:
    args_list = list(cmd)
    if VERBOSE:
        print("Running:", " ".join(args_list))
    result = subprocess.run(args_list, capture_output=not VERBOSE, text=True)
    if result.returncode != 0:
        output = result.stderr.strip() or result.stdout.strip() or "ffmpeg command failed"
        raise FFMpegError(output)
    if VERBOSE and result.stdout:
        print(result.stdout.strip())


def infer_year_text(source: Path) -> Optional[str]:
    match = re.search(r"(19|20)\d{2}", source.stem)
    if match:
        return match.group(0)
    return None


def escape_drawtext(value: str) -> str:
    return value.replace("\\", r"\\").replace("'", r"\'")


def parse_text_slide(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = path.read_text(encoding="utf-8", errors="replace")

    lines = []
    for raw in content.splitlines():
        if not raw.strip():
            lines.append("")
            continue
        stripped = raw.strip()
        if stripped.startswith("#"):
            lines.append(stripped.lstrip("# "))
            continue
        level = (len(raw) - len(raw.lstrip(" \t"))) // 2
        bullet = "• " if stripped.startswith("-") else ""
        text = stripped.lstrip("- ")
        prefix = "  " * level + ("• " if level and not bullet else bullet)
        lines.append(prefix + text)

    return "\n".join(lines).strip() or path.stem


def build_text_segment_cmd(
    ffmpeg_path: str,
    text: str,
    output: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
) -> List[str]:
    base_color = f"color=color=0x101010:size={width}x{height}"
    font_clause = (
        f"fontfile='{escape_drawtext(FONT_PATH.as_posix())}':" if FONT_PATH else ""
    )
    escaped_text = escape_drawtext(text)
    vf = (
        "format=yuv420p," +
        f"drawtext={font_clause}text='{escaped_text}':fontsize=64:line_spacing=18:"
        "fontcolor=white:box=1:boxcolor=0x00000088:x=(w-text_w)/2:y=(h-text_h)/2"
    )

    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-t",
        f"{duration}",
        "-i",
        base_color,
        "-f",
        "lavfi",
        "-t",
        f"{duration}",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-shortest",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output),
    ]


if __name__ == "__main__":
    main()
