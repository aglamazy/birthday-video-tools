#!/usr/bin/env python3
"""Create an MP4 slideshow from the media files in the sequence folder."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from lib.subtitle_renderer import create_ass_subtitle
from lib.text_renderer import INDENT_WIDTH, LEFT_MARGIN, render_text_panel
from lib.text_utils import TextLayout, combine_overlay_texts, load_text_layout

DEFAULT_CONFIG = {
    "source_dir": "sequence",
    "output": "slideshow.mp4",
    "duration_image": 2.0,
    "duration_overlay": 6.0,
    "duration_text": 6.0,
    "fps": 30,
    "resolution": "1920x1080",
    "chunk_size": None,
    "chunk_index": 1,
    "debug_filename": False,
    "audio_files": [],
}

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".hevc", ".mpg", ".mpeg", ".wmv"}
TEXT_EXTENSIONS = {".txt", ".pug"}

VERBOSE = False
LABEL_YEAR = False
FONT_PATH: Optional[Path] = None
SHOW_FILENAME = False
FFMPEG_DEBUG = False
CROSSFADE_SECONDS = 1.0
def load_config(path: Path) -> dict[str, object]:
    config = DEFAULT_CONFIG.copy()
    if not path.exists():
        return config
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Warning: failed to read config {path}: {exc}")
        return config
    if not isinstance(data, dict):
        print(f"Warning: config {path} is not a JSON object; using defaults")
        return config
    for key in config:
        if key in data:
            config[key] = data[key]
    return config


def _config_float(config: dict[str, object], key: str, fallback: float) -> float:
    try:
        value = config.get(key, fallback)
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _config_str(config: dict[str, object], key: str, fallback: str) -> str:
    value = config.get(key, fallback)
    if isinstance(value, str) and value:
        return value
    return fallback


def _config_int(config: dict[str, object], key: str, fallback: int) -> int:
    try:
        value = config.get(key, fallback)
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _config_optional_positive_int(
    config: dict[str, object], key: str, fallback: Optional[int]
) -> Optional[int]:
    value = config.get(key, fallback)
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    if number <= 0:
        return fallback
    return number


def _config_bool(config: dict[str, object], key: str, fallback: bool) -> bool:
    value = config.get(key, fallback)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return fallback


def _config_list(config: dict[str, object], key: str) -> list[str]:
    value = config.get(key, [])
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


def resolve_media_path(path: Path) -> Optional[Path]:
    candidates: list[Path] = []
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.append((CONFIG_PATH.parent / path).resolve())
        candidates.append((Path.cwd() / path).resolve())
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def resolve_audio_files(paths: Iterable[Path]) -> tuple[list[Path], list[Path]]:
    resolved: list[Path] = []
    missing: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        candidate = resolve_media_path(raw)
        if candidate is None:
            missing.append(raw)
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        resolved.append(candidate)
    return resolved, missing


def build_batch_output_path(base: Path, chunk_index: int) -> Path:
    suffix = base.suffix if base.suffix else ".mp4"
    stem = base.stem if base.suffix else base.name
    return base.with_name(f"{stem}-{chunk_index}{suffix}")


@dataclass(frozen=True)
class Segment:
    source: Path
    output: Path
    kind: str  # "image", "video", or "text"
    label: Optional[str] = None
    text_layout: Optional[TextLayout] = None
    overlay_text: Optional[str] = None
    overlay_sources: tuple[Path, ...] = ()
    duration: Optional[float] = None
    overlay_layout: Optional[TextLayout] = None
    panel_image: Optional[Path] = None
    overlay_subtitle: Optional[Path] = None
    expected_duration: Optional[float] = None


@dataclass(frozen=True)
class AudioMarker:
    path: Path
    segment_index: int


@dataclass(frozen=True)
class AudioTimelineEntry:
    path: Path
    start: float
    end: float
    fade_in: float = 0.0
    fade_out: float = 0.0


class FFMpegError(RuntimeError):
    pass


def parse_args(config: dict[str, object]) -> argparse.Namespace:
    default_source = _config_str(config, "source_dir", DEFAULT_CONFIG["source_dir"])
    default_output = _config_str(config, "output", DEFAULT_CONFIG["output"])
    default_duration_image = _config_float(
        config, "duration_image", DEFAULT_CONFIG["duration_image"]
    )
    default_duration_overlay = _config_float(
        config, "duration_overlay", DEFAULT_CONFIG["duration_overlay"]
    )
    default_duration_text = _config_float(
        config, "duration_text", DEFAULT_CONFIG["duration_text"]
    )
    default_fps = _config_int(config, "fps", DEFAULT_CONFIG["fps"])
    default_resolution = _config_str(
        config, "resolution", DEFAULT_CONFIG["resolution"]
    )
    default_chunk_size = _config_optional_positive_int(
        config, "chunk_size", DEFAULT_CONFIG["chunk_size"]
    )
    default_chunk_index = _config_int(
        config, "chunk_index", DEFAULT_CONFIG["chunk_index"]
    )
    default_debug_filename = _config_bool(
        config, "debug_filename", DEFAULT_CONFIG["debug_filename"]
    )
    config_audio_list = _config_list(config, "audio_files")
    audio_default_desc = (
        ", ".join(config_audio_list) if config_audio_list else "none"
    )

    chunk_size_default_desc = (
        str(default_chunk_size) if default_chunk_size is not None else "disabled"
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path(default_source),
        help=f"Folder containing the ordered media files (default: {default_source}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(default_output),
        help=f"Output MP4 filename (default: {default_output}).",
    )
    parser.add_argument(
        "--duration",
        "--duration-image",
        dest="duration_image",
        type=float,
        default=default_duration_image,
        help=(
            "Duration in seconds for each still image without overlays "
            f"(default: {default_duration_image})."
        ),
    )
    parser.add_argument(
        "--duration-overlay",
        dest="duration_overlay",
        type=float,
        default=default_duration_overlay,
        help=(
            "Duration in seconds for images that include overlay text "
            f"(default: {default_duration_overlay})."
        ),
    )
    parser.add_argument(
        "--duration-text",
        dest="duration_text",
        type=float,
        default=default_duration_text,
        help=(
            "Duration in seconds for full-screen text slides "
            f"(default: {default_duration_text})."
        ),
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=default_fps,
        help=f"Frame rate for the generated video (default: {default_fps}).",
    )
    parser.add_argument(
        "--resolution",
        type=str,
        default=default_resolution,
        help=f"Target resolution WIDTHxHEIGHT (default: {default_resolution}).",
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
        "--start-at",
        type=str,
        help="Begin processing at the first file whose name (case-sensitive) matches this value.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=default_chunk_size,
        help=(
            "Number of files per chunk for paginated preview runs "
            f"(default: {chunk_size_default_desc})."
        ),
    )
    parser.add_argument(
        "--chunk-index",
        type=int,
        default=default_chunk_index,
        help=(
            "Chunk number to process when --chunk-size is set "
            f"(1-based, default: {default_chunk_index})."
        ),
    )
    parser.add_argument(
        "--audio-file",
        dest="audio_files",
        action="append",
        type=Path,
        help=(
            "Audio file to append to the soundtrack (repeatable). "
            f"Default sources: {audio_default_desc}."
        ),
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help=(
            "Render every chunk sequentially using --chunk-size and write numbered "
            "output files (e.g., slideshow-1.mp4)."
        ),
    )
    parser.add_argument(
        "--debug-filename",
        dest="debug_filename",
        action="store_true",
        default=default_debug_filename,
        help=(
            "Overlay each segment's source filename for debugging "
            f"(default: {'on' if default_debug_filename else 'off'})."
        ),
    )
    parser.add_argument(
        "--no-debug-filename",
        dest="debug_filename",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show additional progress while keeping ffmpeg logs visible.",
    )
    parser.add_argument(
        "--debug-ffmpeg",
        action="store_true",
        help="Print each ffmpeg command before execution (implies --verbose).",
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
    config = load_config(CONFIG_PATH)
    args = parse_args(config)
    if args.debug_ffmpeg:
        args.verbose = True
    global VERBOSE, FFMPEG_DEBUG
    VERBOSE = args.verbose
    FFMPEG_DEBUG = args.debug_ffmpeg
    global LABEL_YEAR, FONT_PATH, SHOW_FILENAME
    LABEL_YEAR = args.label_year
    SHOW_FILENAME = args.debug_filename
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

    config_audio_entries = _config_list(config, "audio_files")
    config_audio_paths = [Path(entry) for entry in config_audio_entries]
    cli_audio_paths = args.audio_files or []
    audio_candidates: list[Path] = []
    if cli_audio_paths:
        audio_candidates.extend(cli_audio_paths)
    else:
        audio_candidates.extend(config_audio_paths)

    resolved_audio_paths, missing_audio_paths = resolve_audio_files(audio_candidates)
    for missing_audio in missing_audio_paths:
        print(f"Warning: audio file {missing_audio} not found; skipping.")
    if resolved_audio_paths:
        audio_list = ", ".join(path.as_posix() for path in resolved_audio_paths)
        print(f"Using audio tracks: {audio_list}")

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        raise SystemExit("ffmpeg is required but not found in PATH.")

    ffprobe_path = shutil.which("ffprobe")
    if not ffprobe_path:
        print("Warning: ffprobe not found; precise video durations may be unavailable.")

    width, height = parse_resolution(args.resolution)

    source_dir = args.source_dir.resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        raise SystemExit(f"Source directory {source_dir} does not exist or is not a directory")

    media_files = sorted(p for p in source_dir.iterdir() if p.is_file())
    total_media = len(media_files)

    if total_media == 0:
        print("No media files found in source directory.")
        return

    if args.batch and args.limit:
        print("Warning: --limit is ignored when --batch is used.")

    if args.batch:
        if args.chunk_size is None:
            raise SystemExit("--batch requires --chunk-size (set via config or CLI).")
        if args.chunk_size <= 0:
            raise SystemExit("--chunk-size must be a positive integer")
        chunk_size = args.chunk_size
        chunk_count = (total_media + chunk_size - 1) // chunk_size
        if chunk_count == 0:
            print("No media files found in source directory.")
            return
        for chunk_idx in range(1, chunk_count + 1):
            start = (chunk_idx - 1) * chunk_size
            end = start + chunk_size
            chunk_media = media_files[start:end]
            if not chunk_media:
                continue
            start_pos = start + 1
            end_pos = start + len(chunk_media)
            output_path = build_batch_output_path(args.output, chunk_idx)
            print(
                f"Chunk {chunk_idx}/{chunk_count} (items {start_pos}-{end_pos} of {total_media})"
                f" -> {output_path.name}"
            )
            render_slideshow(
                chunk_media,
                args,
                ffmpeg_path,
                ffprobe_path,
                width,
                height,
                output_path,
                audio_paths=resolved_audio_paths,
            )
        return

    selected_media = media_files
    if args.chunk_size is not None:
        if args.chunk_size <= 0:
            raise SystemExit("--chunk-size must be a positive integer")
        if args.chunk_index <= 0:
            raise SystemExit("--chunk-index must be a positive integer")
        start = (args.chunk_index - 1) * args.chunk_size
        end = start + args.chunk_size
        selected_media = selected_media[start:end]
        if selected_media:
            start_pos = start + 1
            end_pos = start + len(selected_media)
            print(
                f"Chunk {args.chunk_index} (items {start_pos}-{end_pos} of {total_media})"
            )
        else:
            print(
                f"No media files matched chunk {args.chunk_index} "
                f"(chunk size {args.chunk_size}, total files {total_media})."
            )
            return

    if args.start_at:
        try:
            start_index = next(
                idx for idx, path in enumerate(selected_media) if path.name == args.start_at
            )
            selected_media = selected_media[start_index:]
        except StopIteration:
            raise SystemExit(f"--start-at {args.start_at} not found in source directory")

    if args.limit:
        selected_media = selected_media[: args.limit]

    if not selected_media:
        print("No media files found in source directory.")
        return

    render_slideshow(
        selected_media,
        args,
        ffmpeg_path,
        ffprobe_path,
        width,
        height,
        args.output,
        audio_paths=resolved_audio_paths,
    )


def render_slideshow(
    media_files: List[Path],
    args: argparse.Namespace,
    ffmpeg_path: str,
    ffprobe_path: Optional[str],
    width: int,
    height: int,
    output_path: Path,
    audio_paths: Optional[Sequence[Path]] = None,
) -> None:
    media_files = list(media_files)
    if args.limit and not args.batch:
        media_files = media_files[: args.limit]

    resolved_audio_paths = list(audio_paths or [])

    media_stems_with_visual = {
        path.stem
        for path in media_files
        if path.suffix.lower() in IMAGE_EXTENSIONS or path.suffix.lower() in VIDEO_EXTENSIONS
    }

    # Pair auxiliary text files with matching media for overlays.
    overlay_text_map: dict[str, list[Path]] = {}
    # Track texts already consumed as overlays so they are not treated as slides.
    overlay_text_paths: set[Path] = set()
    for path in media_files:
        suffix = path.suffix.lower()
        if suffix in TEXT_EXTENSIONS and path.stem in media_stems_with_visual:
            overlay_text_map.setdefault(path.stem, []).append(path)
            overlay_text_paths.add(path)

    segments: List[Segment] = []
    audio_markers: list[AudioMarker] = []
    tmp_dir_ctx = tempfile.TemporaryDirectory() if not args.keep_temp else None
    if tmp_dir_ctx is not None:
        temp_dir_path = Path(tmp_dir_ctx.name)
    else:
        temp_dir_path = output_path.parent / ".segments"
        temp_dir_path.mkdir(parents=True, exist_ok=True)

    text_panel_temp_dir = temp_dir_path / "text_panels"
    subtitle_temp_dir = temp_dir_path / "subtitles"

    segment_index = 0
    segment_index_by_stem: dict[str, int] = {}
    for media in media_files:
        suffix = media.suffix.lower()
        if suffix in IMAGE_EXTENSIONS or suffix in VIDEO_EXTENSIONS:
            segment_index += 1
            segment_path = temp_dir_path / f"segment_{segment_index:04d}.mp4"
            overlay_sources = overlay_text_map.get(media.stem, [])
            overlay_text_value: Optional[str] = None
            overlay_layout: Optional[TextLayout] = None
            overlay_subtitle_path: Optional[Path] = None
            if overlay_sources:
                combined_layout = combine_overlay_texts(overlay_sources)
                if combined_layout.lines or combined_layout.title:
                    overlay_layout = combined_layout
                    overlay_text_value = combined_layout.overlay_text()
            duration_value: Optional[float] = None
            if suffix in IMAGE_EXTENSIONS:
                duration_value = (
                    args.duration_overlay if overlay_text_value else args.duration_image
                )
            if overlay_layout and overlay_text_value:
                overlay_subtitle_path = create_ass_subtitle(
                    overlay_layout,
                    width,
                    height,
                    FONT_PATH,
                    subtitle_temp_dir,
                    duration_value,
                )
            if suffix in IMAGE_EXTENSIONS:
                expected_duration = duration_value or args.duration_image
            else:
                expected_duration = probe_media_duration(ffprobe_path, media)
            segments.append(
                Segment(
                    source=media,
                    output=segment_path,
                    kind="image" if suffix in IMAGE_EXTENSIONS else "video",
                    label=infer_year_text(media),
                    overlay_text=overlay_text_value,
                    overlay_sources=tuple(overlay_sources),
                    duration=duration_value,
                    overlay_layout=overlay_layout,
                    overlay_subtitle=overlay_subtitle_path,
                    expected_duration=expected_duration,
                )
            )
            segment_index_by_stem[media.stem] = len(segments) - 1
        elif suffix in TEXT_EXTENSIONS:
            if media in overlay_text_paths:
                continue
            segment_index += 1
            segment_path = temp_dir_path / f"segment_{segment_index:04d}.mp4"
            text_layout = load_text_layout(media)
            panel_image = render_text_panel(
                text_layout,
                width,
                height,
                FONT_PATH,
                background=True,
                output_dir=text_panel_temp_dir,
            )
            segments.append(
                Segment(
                    source=media,
                    output=segment_path,
                    kind="text",
                    text_layout=text_layout,
                    duration=args.duration_text,
                    panel_image=panel_image,
                    expected_duration=args.duration_text,
                )
            )
            segment_index_by_stem[media.stem] = len(segments) - 1
        elif suffix in AUDIO_EXTENSIONS:
            target_index = segment_index_by_stem.get(media.stem, len(segments))
            audio_markers.append(AudioMarker(path=media, segment_index=target_index))
            continue
        else:
            print(f"Skipping unsupported file {media.name}")

    if not segments:
        print("No convertible media files found.")
        if tmp_dir_ctx:
            tmp_dir_ctx.cleanup()
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
            if segment.overlay_text:
                overlay_names = (
                    ", ".join(src.name for src in segment.overlay_sources)
                    if segment.overlay_sources
                    else "overlay text"
                )
                extra.append(f"overlay {overlay_names}")
            if SHOW_FILENAME:
                extra.append(f"filename {segment.source.name}")
            if segment.kind == "text" and segment.text_layout:
                preview = segment.text_layout.preview_text()
                if preview:
                    ellipsis = "…" if len(preview) > 40 else ""
                    extra.append(f"text '{preview[:40]}{ellipsis}'")
                else:
                    extra.append("text slide")
            print(
                f"[{idx}/{total}] {segment.kind} {segment.source.name}"
                f" -> {segment.output.name}" + (f" ({', '.join(extra)})" if extra else "")
            )
            debug_text = segment.source.name if SHOW_FILENAME else None
            if segment.kind == "image":
                still_duration = segment.duration or args.duration_image
                filter_graph, filter_output = build_media_filter_graph(
                    width,
                    height,
                    segment.overlay_text,
                    label,
                    debug_text,
                    segment.overlay_subtitle,
                )
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
                        f"{still_duration}",
                        "-i",
                        str(segment.source),
                        "-f",
                        "lavfi",
                        "-t",
                        f"{still_duration}",
                        "-i",
                        "anullsrc=channel_layout=stereo:sample_rate=48000",
                        "-shortest",
                        "-filter_complex",
                        filter_graph,
                        "-map",
                        f"[{filter_output}]",
                        "-map",
                        "1:a:0",
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
                filter_graph, filter_output = build_media_filter_graph(
                    width,
                    height,
                    segment.overlay_text,
                    label,
                    debug_text,
                    segment.overlay_subtitle,
                )
                run_ffmpeg(
                    [
                        ffmpeg_path,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(segment.source),
                        "-filter_complex",
                        filter_graph,
                        "-map",
                        f"[{filter_output}]",
                        "-map",
                        "0:a?",
                        "-r",
                        str(args.fps),
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
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
                slide_duration = segment.duration or args.duration_text
                if segment.text_layout is None:
                    raise RuntimeError(f"Missing text layout for segment {segment.source}")
                run_ffmpeg(
                    build_text_segment_cmd(
                        ffmpeg_path,
                        segment.text_layout,
                        segment.output,
                        slide_duration,
                        width,
                        height,
                        args.fps,
                        debug_text,
                    )
                )

        if audio_markers:
            if ffprobe_path is None:
                print(
                    "Warning: audio cues detected but ffprobe is unavailable; skipping timeline audio."
                )
                audio_entries = []
            else:
                segment_durations = ensure_segment_durations(ffprobe_path, segments)
                audio_entries = build_audio_timeline(audio_markers, segment_durations)
        else:
            audio_entries = []

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
                str(output_path),
            ]
        )
        print(f"Created {output_path}")
        if audio_entries:
            audio_output_path = output_path.with_name(f"{output_path.stem}_audio.mp3")
            create_timeline_audio(ffmpeg_path, audio_entries, audio_output_path)
            final_output_path = output_path.with_name(
                f"{output_path.stem}_with_audio{output_path.suffix}"
            )
            mux_video_with_audio(ffmpeg_path, output_path, audio_output_path, final_output_path)
            print(f"Created {final_output_path}")
        elif resolved_audio_paths:
            apply_audio_track(ffmpeg_path, output_path, resolved_audio_paths)
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


def build_media_filter_graph(
    width: int,
    height: int,
    overlay_text: Optional[str],
    label_text: Optional[str],
    debug_text: Optional[str],
    overlay_subtitle: Optional[Path] = None,
) -> tuple[str, str]:
    label_counter = 0

    def next_label() -> str:
        nonlocal label_counter
        label_name = f"v{label_counter}"
        label_counter += 1
        return label_name

    steps: list[str] = []
    current = next_label()
    base_filters = [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "format=yuv420p",
    ]
    steps.append(f"[0:v]{','.join(base_filters)}[{current}]")

    def append_filter(filter_expr: str) -> None:
        nonlocal current
        next_out = next_label()
        steps.append(f"[{current}]{filter_expr}[{next_out}]")
        current = next_out

    if overlay_subtitle:
        subtitle_path = escape_subtitle_path(overlay_subtitle)
        subtitle_filter = f"subtitles='{subtitle_path}'"
        if FONT_PATH:
            fonts_dir = escape_subtitle_path(FONT_PATH.parent)
            subtitle_filter += f":fontsdir='{fonts_dir}'"
        append_filter(subtitle_filter)
    elif overlay_text:
        overlay_font_clause = (
            f"fontfile='{escape_drawtext(FONT_PATH.as_posix())}':" if FONT_PATH else ""
        )
        overlay_value = escape_drawtext(overlay_text)
        append_filter(
            f"drawtext={overlay_font_clause}text='{overlay_value}':fontsize=52:"
            "line_spacing=16:fontcolor=white:borderw=3:bordercolor=black:text_shaping=1:"
            "x=(w-text_w)/2:y=(h-text_h)/2"
        )

    if label_text:
        font_clause = (
            f"fontfile='{escape_drawtext(FONT_PATH.as_posix())}':" if FONT_PATH else ""
        )
        label_value = escape_drawtext(label_text)
        append_filter(
            f"drawtext={font_clause}text='{label_value}':fontsize=48:fontcolor=white:"
            "box=1:boxcolor=0x00000088:text_shaping=1:x=w-tw-40:y=h-th-40"
        )

    if debug_text:
        debug_font_clause = (
            f"fontfile='{escape_drawtext(FONT_PATH.as_posix())}':" if FONT_PATH else ""
        )
        debug_value = escape_drawtext(debug_text)
        append_filter(
            f"drawtext={debug_font_clause}text='{debug_value}':fontsize=32:"
            "fontcolor=white:borderw=2:bordercolor=black:text_shaping=1:"
            "x=40:y=40"
        )

    filter_graph = ";".join(steps)
    return filter_graph, current


def build_text_filter_graph(
    width: int,
    height: int,
    layout: TextLayout,
    debug_text: Optional[str],
) -> tuple[str, str]:
    label_counter = 0

    def next_label() -> str:
        nonlocal label_counter
        label_name = f"t{label_counter}"
        label_counter += 1
        return label_name

    steps: list[str] = []
    current = next_label()
    steps.append(f"[0:v]format=yuv420p[{current}]")

    font_clause = (
        f"fontfile='{escape_drawtext(FONT_PATH.as_posix())}':" if FONT_PATH else ""
    )

    title_font_size = 72
    body_font_size = 56
    body_spacing = 22
    indent_px = INDENT_WIDTH
    margin_right = 120
    margin_left = LEFT_MARGIN

    if layout.title:
        title_text = layout.title.strip()
        title_value = escape_drawtext(title_text)
        title_label = next_label()
        steps.append(
            f"[{current}]drawtext={font_clause}text='{title_value}':fontsize={title_font_size}:"
            "fontcolor=white:borderw=3:bordercolor=black:text_shaping=1:"
            "x=(w-text_w)/2:y=80"
            f"[{title_label}]"
        )
        current = title_label

    body_lines = [line for line in layout.lines if line.kind not in {"blank", "top"} and line.display.strip()]
    line_height = body_font_size + body_spacing
    top_lines = [line for line in layout.lines if line.kind == "top" and line.display.strip()]
    top_base_y = 140
    top_index = 0

    if layout.title:
        base_body_y = top_base_y + len(top_lines) * line_height + 40
    else:
        base_body_y = top_base_y + len(top_lines) * line_height

    if body_lines:
        current_y = base_body_y
    else:
        current_y = base_body_y

    if not body_lines and not top_lines and not layout.title:
        current_y = max((height - line_height) // 2, 120)

    for line in layout.lines:
        if line.kind == "blank":
            current_y += line_height
            continue
        display_text = line.display.strip()
        if not display_text:
            current_y += line_height
            continue
        escaped_display = escape_drawtext(display_text)

        if line.align == "center":
            x_expr = "(w-text_w)/2"
            y_expr = str(current_y)
            current_y += line_height
        elif line.align == "top":
            x_expr = "w-text_w-120"
            y_expr = str(top_base_y + top_index * line_height)
            top_index += 1
        elif line.align == "left":
            x_expr = f"{margin_left + line.level * indent_px}"
            y_expr = str(current_y)
            current_y += line_height
        else:
            x_expr = f"w-text_w-{margin_right + line.level * indent_px}"
            y_expr = str(current_y)
            current_y += line_height

        body_label = next_label()
        steps.append(
            f"[{current}]drawtext={font_clause}text='{escaped_display}':fontsize={body_font_size}:"
            "fontcolor=white:box=1:boxcolor=0x00000088:text_shaping=1:"
            f"x={x_expr}:y={y_expr}"
            f"[{body_label}]"
        )
        current = body_label

    if debug_text:
        debug_font_clause = (
            f"fontfile='{escape_drawtext(FONT_PATH.as_posix())}':" if FONT_PATH else ""
        )
        debug_value = escape_drawtext(debug_text)
        debug_label = next_label()
        steps.append(
            f"[{current}]drawtext={debug_font_clause}text='{debug_value}':fontsize=32:"
            "fontcolor=white:borderw=2:bordercolor=black:text_shaping=1:x=40:y=40"
            f"[{debug_label}]"
        )
        current = debug_label

    filter_graph = ";".join(steps)
    return filter_graph, current


def run_ffmpeg(cmd: Iterable[str]) -> None:
    args_list = list(cmd)
    if FFMPEG_DEBUG:
        print("Running:", " ".join(args_list))
    result = subprocess.run(args_list, capture_output=not VERBOSE, text=True)
    if result.returncode != 0:
        output = result.stderr.strip() or result.stdout.strip() or "ffmpeg command failed"
        raise FFMpegError(output)
    if VERBOSE and result.stdout:
        print(result.stdout.strip())


def apply_audio_track(
    ffmpeg_path: str,
    video_path: Path,
    audio_paths: Sequence[Path],
) -> None:
    if not audio_paths:
        return

    existing_sources = [path for path in audio_paths if path.exists()]
    if not existing_sources:
        return

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        combined_audio = tmp_dir_path / "combined_audio.m4a"

        if len(existing_sources) == 1:
            run_ffmpeg(
                [
                    ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(existing_sources[0]),
                    "-c:a",
                    "aac",
                    "-ac",
                    "2",
                    "-ar",
                    "48000",
                    str(combined_audio),
                ]
            )
        else:
            concat_cmd: List[str] = [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
            ]
            for source in existing_sources:
                concat_cmd.extend(["-i", str(source)])
            filter_inputs = "".join(f"[{idx}:a]" for idx in range(len(existing_sources)))
            filter_complex = (
                f"{filter_inputs}concat=n={len(existing_sources)}:v=0:a=1[aout]"
            )
            concat_cmd.extend(
                [
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[aout]",
                    "-c:a",
                    "aac",
                    "-ac",
                    "2",
                    "-ar",
                    "48000",
                    str(combined_audio),
                ]
            )
            run_ffmpeg(concat_cmd)

        tmp_video = tmp_dir_path / "with_audio.mp4"
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
                str(combined_audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-shortest",
                str(tmp_video),
            ]
        )
        shutil.move(str(tmp_video), str(video_path))
    print(f"Attached audio from {len(existing_sources)} file(s).")


def infer_year_text(source: Path) -> Optional[str]:
    match = re.search(r"(19|20)\d{2}", source.stem)
    if match:
        return match.group(0)
    return None


def escape_drawtext(value: str) -> str:
    return value.replace("\\", r"\\").replace("'", r"\'")


def escape_subtitle_path(path: Path) -> str:
    value = path.as_posix()
    value = value.replace("\\", r"\\")
    value = value.replace(":", r"\:")
    value = value.replace("'", r"\'")
    return value


def probe_media_duration(ffprobe_path: Optional[str], media_path: Path) -> Optional[float]:
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


def ensure_segment_durations(
    ffprobe_path: Optional[str], segments: Sequence[Segment]
) -> list[float]:
    durations: list[float] = []
    for segment in segments:
        duration = segment.expected_duration
        if duration is None and ffprobe_path:
            duration = probe_media_duration(ffprobe_path, segment.output)
        if duration is None:
            raise RuntimeError(f"Unable to determine duration for segment {segment.output}")
        durations.append(duration)
    return durations


def build_audio_timeline(
    audio_markers: Sequence[AudioMarker],
    segment_durations: Sequence[float],
) -> list[AudioTimelineEntry]:
    if not audio_markers:
        return []
    cumulative: list[float] = [0.0]
    for duration in segment_durations:
        cumulative.append(cumulative[-1] + duration)

    entries: list[AudioTimelineEntry] = []
    total_segments = len(segment_durations)
    for idx, marker in enumerate(audio_markers):
        if marker.segment_index > total_segments:
            continue
        start = cumulative[marker.segment_index]
        if idx + 1 < len(audio_markers):
            next_index = audio_markers[idx + 1].segment_index
            next_index = min(next_index, total_segments)
            end = cumulative[next_index]
        else:
            end = cumulative[-1]
        if end <= start:
            continue
        entries.append(AudioTimelineEntry(path=marker.path, start=start, end=end))

    if not entries:
        return []

    adjusted = entries[:]
    for idx in range(len(adjusted) - 1):
        current_entry = adjusted[idx]
        next_entry = adjusted[idx + 1]
        current_duration = current_entry.end - current_entry.start
        next_duration = next_entry.end - next_entry.start
        if current_duration <= 0 or next_duration <= 0:
            continue
        fade_candidate = min(CROSSFADE_SECONDS, current_duration, next_duration)
        if fade_candidate <= 0:
            continue
        fade = min(fade_candidate, next_entry.start)
        if fade <= 0:
            continue
        current_entry = replace(current_entry, fade_out=fade)
        new_start = max(0.0, next_entry.start - fade)
        actual_fade_in = next_entry.start - new_start
        next_entry = replace(next_entry, start=new_start, fade_in=actual_fade_in)
        adjusted[idx] = current_entry
        adjusted[idx + 1] = next_entry

    return adjusted


def create_timeline_audio(
    ffmpeg_path: str,
    entries: Sequence[AudioTimelineEntry],
    output_path: Path,
) -> None:
    if not entries:
        return

    filter_parts: list[str] = []
    mix_inputs: list[str] = []
    input_args: list[str] = []

    for idx, entry in enumerate(entries):
        duration = entry.end - entry.start
        if duration <= 0:
            continue
        input_args.extend(["-i", str(entry.path)])
        stream_label = f"aud{idx}"
        start_ms = max(0, int(round(entry.start * 1000)))
        trim_clause = f"[{idx}:a]atrim=0:{duration:.6f},asetpts=PTS-STARTPTS"
        fade_clauses: list[str] = []
        if entry.fade_in > 0:
            fade_clauses.append(f"afade=t=in:st=0:d={entry.fade_in:.6f}")
        if entry.fade_out > 0 and duration > entry.fade_out:
            fade_start = duration - entry.fade_out
            fade_clauses.append(
                f"afade=t=out:st={fade_start:.6f}:d={entry.fade_out:.6f}"
            )
        filter_expr = trim_clause
        for clause in fade_clauses:
            filter_expr += f",{clause}"
        filter_expr += f",adelay={start_ms}|{start_ms}[{stream_label}]"
        filter_parts.append(filter_expr)
        mix_inputs.append(f"[{stream_label}]")

    if not filter_parts:
        raise RuntimeError("No usable audio timeline entries were produced.")

    mix_count = len(mix_inputs)
    mix_clause = "".join(mix_inputs)
    filter_parts.append(
        f"{mix_clause}amix=inputs={mix_count}:dropout_transition=0,"
        "aformat=sample_fmts=s16:sample_rates=44100:channel_layouts=stereo[mix]"
    )
    filter_graph = ";".join(filter_parts)

    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        *input_args,
        "-filter_complex",
        filter_graph,
        "-map",
        "[mix]",
        "-c:a",
        "libmp3lame",
        "-ar",
        "44100",
        "-b:a",
        "192k",
        "-y",
        str(output_path),
    ]
    run_ffmpeg(cmd)


def mux_video_with_audio(
    ffmpeg_path: str,
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> None:
    cmd = [
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
        "-b:a",
        "192k",
        "-shortest",
        str(output_path),
    ]
    run_ffmpeg(cmd)


def build_text_segment_cmd(
    ffmpeg_path: str,
    layout: TextLayout,
    output: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
    debug_text: Optional[str] = None,
) -> List[str]:
    base_color = f"color=color=0x101010:size={width}x{height}"
    filter_graph, filter_output = build_text_filter_graph(
        width,
        height,
        layout,
        debug_text,
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
        "-filter_complex",
        filter_graph,
        "-map",
        f"[{filter_output}]",
        "-map",
        "1:a:0",
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
