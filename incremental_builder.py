#!/usr/bin/env python3
"""Incremental slideshow builder.

Renders per-slide segments into a cache directory and reuses them across runs.
Only segments whose source media or overlays have changed are rebuilt. Each
final render is written with an incrementing suffix (e.g. `slideshow-001.mp4`)
so previous exports remain untouched, and configured audio tracks are muxed into
that versioned output.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

try:
    from watchfiles import watch as watchfiles_watch
except ImportError:  # pragma: no cover - optional dependency
    watchfiles_watch = None

import sequence_to_video as stv
from lib import text_renderer
from lib.subtitle_renderer import create_ass_subtitle
from lib.text_utils import TextLayout, combine_overlay_texts, load_text_layout

CONFIG_PATH = Path("config.json")


@dataclass(frozen=True)
class SegmentInfo:
    index: int
    source: Path
    kind: str  # "image", "video", or "text"
    overlay_sources: tuple[Path, ...] = ()
    overlay_layout: Optional[TextLayout] = None
    overlay_text: Optional[str] = None
    duration: Optional[float] = None


def load_config(config_path: Path) -> dict[str, object]:
    return stv.load_config(config_path)


def list_media(source_dir: Path) -> List[Path]:
    return sorted(p for p in source_dir.iterdir() if p.is_file())


def build_segment_plan(
    media_files: Sequence[Path],
    limit: Optional[int],
    duration_image: float,
    duration_overlay: float,
    duration_text: float,
) -> List[SegmentInfo]:
    if limit:
        selected = list(media_files[:limit])
    else:
        selected = list(media_files)

    media_stems_with_visual = {
        path.stem
        for path in selected
        if path.suffix.lower() in stv.IMAGE_EXTENSIONS
        or path.suffix.lower() in stv.VIDEO_EXTENSIONS
    }

    overlay_text_map: dict[str, list[Path]] = {}
    overlay_text_paths: set[Path] = set()
    for path in selected:
        suffix = path.suffix.lower()
        if suffix in stv.TEXT_EXTENSIONS and path.stem in media_stems_with_visual:
            overlay_text_map.setdefault(path.stem, []).append(path)
            overlay_text_paths.add(path)

    plan: List[SegmentInfo] = []
    index = 0
    for media in selected:
        suffix = media.suffix.lower()
        if suffix in stv.IMAGE_EXTENSIONS or suffix in stv.VIDEO_EXTENSIONS:
            overlay_sources = tuple(overlay_text_map.get(media.stem, []))
            overlay_layout: Optional[TextLayout] = None
            overlay_text: Optional[str] = None
            if overlay_sources:
                combined = combine_overlay_texts(overlay_sources)
                if combined.lines or combined.title:
                    overlay_layout = combined
                    overlay_text = combined.overlay_text()
            duration_value: Optional[float] = None
            if suffix in stv.IMAGE_EXTENSIONS:
                duration_value = duration_overlay if overlay_text else duration_image
            plan.append(
                SegmentInfo(
                    index=index + 1,
                    source=media,
                    kind="image" if suffix in stv.IMAGE_EXTENSIONS else "video",
                    overlay_sources=overlay_sources,
                    overlay_layout=overlay_layout,
                    overlay_text=overlay_text,
                    duration=duration_value,
                )
            )
            index += 1
        elif suffix in stv.TEXT_EXTENSIONS:
            if media in overlay_text_paths:
                continue
            layout = load_text_layout(media)
            plan.append(
                SegmentInfo(
                    index=index + 1,
                    source=media,
                    kind="text",
                    overlay_sources=(),
                    overlay_layout=layout,
                    overlay_text=None,
                    duration=duration_text,
                )
            )
            index += 1
        else:
            print(f"Skipping unsupported file {media.name}")

    return plan


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def list_dependencies(segment: SegmentInfo) -> List[Path]:
    deps = [segment.source]
    deps.extend(segment.overlay_sources)
    return deps


def needs_render(
    output: Path,
    dependencies: Sequence[Path],
    additional_mtime: float,
) -> bool:
    if not output.exists():
        return True
    output_mtime = output.stat().st_mtime
    if additional_mtime > output_mtime:
        return True
    for dep in dependencies:
        try:
            if dep.stat().st_mtime > output_mtime:
                return True
        except OSError:
            return True
    return False


def render_image_segment(
    segment: SegmentInfo,
    output_path: Path,
    subtitle_root: Path,
    width: int,
    height: int,
    fps: int,
    ffmpeg_path: str,
    default_duration: float,
) -> None:
    overlay_subtitle_path: Optional[Path] = None
    if segment.overlay_layout and segment.overlay_text:
        subtitle_dir = subtitle_root / f"{segment.index:04d}"
        if subtitle_dir.exists():
            shutil.rmtree(subtitle_dir)
        subtitle_dir.mkdir(parents=True, exist_ok=True)
        overlay_subtitle_path = create_ass_subtitle(
            segment.overlay_layout,
            width,
            height,
            stv.FONT_PATH,
            subtitle_dir,
            duration=segment.duration,
        )

    still_duration = segment.duration or default_duration
    label = stv.infer_year_text(segment.source)
    debug_text = segment.source.name if stv.SHOW_FILENAME else None
    filter_graph, filter_output = stv.build_media_filter_graph(
        width,
        height,
        segment.overlay_text,
        label,
        debug_text,
        overlay_subtitle_path,
    )
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(fps),
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
        str(fps),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    stv.run_ffmpeg(cmd)


def render_video_segment(
    segment: SegmentInfo,
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    ffmpeg_path: str,
) -> None:
    label = stv.infer_year_text(segment.source)
    debug_text = segment.source.name if stv.SHOW_FILENAME else None
    filter_graph, filter_output = stv.build_media_filter_graph(
        width,
        height,
        segment.overlay_text,
        label,
        debug_text,
        None,
    )
    cmd = [
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
        str(fps),
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
        str(output_path),
    ]
    stv.run_ffmpeg(cmd)


def render_text_segment(
    segment: SegmentInfo,
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    ffmpeg_path: str,
) -> None:
    if segment.overlay_layout is None:
        raise RuntimeError(f"Missing text layout for {segment.source}")
    slide_duration = segment.duration or 1.0
    cmd = stv.build_text_segment_cmd(
        ffmpeg_path,
        segment.overlay_layout,
        output_path,
        slide_duration,
        width,
        height,
        fps,
        segment.source.name if stv.SHOW_FILENAME else None,
    )
    stv.run_ffmpeg(cmd)


def render_segment(
    segment: SegmentInfo,
    output_path: Path,
    subtitles_dir: Path,
    width: int,
    height: int,
    fps: int,
    ffmpeg_path: str,
    default_duration: float,
) -> None:
    if segment.kind == "image":
        render_image_segment(
            segment,
            output_path,
            subtitles_dir,
            width,
            height,
            fps,
            ffmpeg_path,
            default_duration,
        )
    elif segment.kind == "video":
        render_video_segment(segment, output_path, width, height, fps, ffmpeg_path)
    else:
        render_text_segment(segment, output_path, width, height, fps, ffmpeg_path)


def next_versioned_path(base: Path) -> Path:
    ensure_dir(base.parent)
    stem = base.stem
    suffix = base.suffix or ""
    pattern = re.compile(rf"^{re.escape(stem)}-(\d+){re.escape(suffix)}$")
    max_index = 0
    for candidate in base.parent.glob(f"{stem}-*{suffix}"):
        match = pattern.match(candidate.name)
        if match:
            try:
                value = int(match.group(1))
            except ValueError:
                continue
            max_index = max(max_index, value)
    return base.parent / f"{stem}-{max_index + 1:03d}{suffix}"


def concat_segments(
    segments: Sequence[SegmentInfo],
    output_path: Path,
    segments_dir: Path,
    ffmpeg_path: str,
) -> None:
    concat_path = segments_dir / "concat.txt"
    with concat_path.open("w", encoding="utf-8") as handle:
        for segment in segments:
            segment_file = segments_dir / f"segment_{segment.index:04d}.mp4"
            handle.write(f"file '{segment_file.as_posix()}'\n")

    cmd = [
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
    stv.run_ffmpeg(cmd)


def attach_audio_tracks(
    ffmpeg_path: str,
    video_path: Path,
    audio_paths: Sequence[Path],
) -> None:
    existing_sources = [path for path in audio_paths if path.exists()]
    if not existing_sources:
        return

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        combined_audio = tmp_dir / "combined_audio.m4a"

        if len(existing_sources) == 1:
            stv.run_ffmpeg(
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
            stv.run_ffmpeg(concat_cmd)

        tmp_video = tmp_dir / "with_audio.mp4"
        stv.run_ffmpeg(
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
        shutil.move(tmp_video, video_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="Path to config.json (default: config.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Base output filename (default: value from config, e.g., slideshow.mp4)",
    )
    parser.add_argument(
        "--segments-dir",
        type=Path,
        default=Path("segments"),
        help="Directory to store per-slide segments (default: segments/)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Only process the first N media files (for testing).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print ffmpeg commands during execution.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-render of all segments.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch for changes and rebuild automatically.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds when --watch is used without watchfiles (default: 1.0).",
    )
    parser.add_argument(
        "--debug-filename",
        action="store_true",
        help="Overlay each segment's source filename during the build (temporary override).",
    )
    return parser.parse_args()


def collect_snapshot(paths: Iterable[Path]) -> Dict[Path, float]:
    snapshot: Dict[Path, float] = {}
    for path in paths:
        try:
            snapshot[path] = path.stat().st_mtime
        except OSError:
            snapshot[path] = -1.0
    return snapshot


def diff_snapshot(old: Dict[Path, float], new: Dict[Path, float]) -> List[Path]:
    changed: set[Path] = set()
    for path, mtime in new.items():
        if old.get(path) != mtime:
            changed.add(path)
    for path in old:
        if path not in new:
            changed.add(path)
    return sorted(changed)


def run_build(args: argparse.Namespace, announce_audio: bool = True) -> Set[Path]:
    config = load_config(args.config)

    stv.VERBOSE = False
    stv.FFMPEG_DEBUG = False
    stv.SHOW_FILENAME = args.debug_filename or bool(config.get("debug_filename", False))
    stv.LABEL_YEAR = bool(config.get("label_year", False))

    label_font = config.get("label_font")
    if label_font:
        font_path = Path(label_font)
        if font_path.exists():
            stv.FONT_PATH = font_path
    if stv.FONT_PATH is None:
        default_font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if default_font.exists():
            stv.FONT_PATH = default_font

    title_font_size = int(config.get("title_font_size", stv.DEFAULT_CONFIG["title_font_size"]))
    body_font_size = int(config.get("body_font_size", stv.DEFAULT_CONFIG["body_font_size"]))
    text_renderer.set_font_sizes(title_font_size, body_font_size)

    source_dir = Path(config.get("source_dir", stv.DEFAULT_CONFIG["source_dir"])).resolve()
    if not source_dir.exists():
        raise SystemExit(f"Source directory {source_dir} does not exist")

    base_output = (
        args.output if args.output else Path(config.get("output", "slideshow.mp4"))
    ).resolve()

    width, height = stv.parse_resolution(str(config.get("resolution", "1920x1080")))
    fps = int(config.get("fps", stv.DEFAULT_CONFIG["fps"]))
    duration_image = float(config.get("duration_image", stv.DEFAULT_CONFIG["duration_image"]))
    duration_overlay = float(
        config.get("duration_overlay", stv.DEFAULT_CONFIG["duration_overlay"])
    )
    duration_text = float(config.get("duration_text", stv.DEFAULT_CONFIG["duration_text"]))

    watch_paths: Set[Path] = {args.config.resolve(), source_dir}

    media_files = list_media(source_dir)
    if not media_files:
        print("No media files found; nothing to do.")
        return watch_paths

    plan = build_segment_plan(
        media_files,
        args.limit,
        duration_image,
        duration_overlay,
        duration_text,
    )
    if not plan:
        print("No convertible media files found.")
        return watch_paths

    segments_dir = args.segments_dir.resolve()
    ensure_dir(segments_dir)
    subtitles_root = segments_dir / "subtitles"
    ensure_dir(subtitles_root)

    config_mtime = args.config.stat().st_mtime if args.config.exists() else time.time()
    script_mtime = Path(stv.__file__).stat().st_mtime
    additional_mtime = max(config_mtime, script_mtime)

    ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"

    for segment in plan:
        watch_paths.add(segment.source.resolve())
        for dep in segment.overlay_sources:
            watch_paths.add(dep.resolve())
        output_segment = segments_dir / f"segment_{segment.index:04d}.mp4"
        deps = list_dependencies(segment)
        if args.force or needs_render(output_segment, deps, additional_mtime):
            if args.verbose:
                extras = (
                    " + " + ", ".join(src.name for src in segment.overlay_sources)
                    if segment.overlay_sources
                    else ""
                )
                print(f"processing {segment.source.name}{extras}")
            render_segment(
                segment,
                output_segment,
                subtitles_root,
                width,
                height,
                fps,
                ffmpeg_path,
                duration_image,
            )
        elif args.verbose:
            pass

    final_output = next_versioned_path(base_output)
    if args.verbose:
        print(f"concatenating into {final_output.name}")
    concat_segments(plan, final_output, segments_dir, ffmpeg_path)
    print(f"generated {final_output.name}")

    audio_paths = [Path(entry) for entry in config.get("audio_files", []) if entry]
    for audio in audio_paths:
        resolved_audio = audio.resolve()
        watch_paths.add(resolved_audio)
        watch_paths.add(resolved_audio.parent)
    if audio_paths:
        resolved, missing = stv.resolve_audio_files(audio_paths)
        for missing_path in missing:
            print(f"Warning: audio file {missing_path} not found; skipping.")
        if resolved:
            if announce_audio and args.verbose:
                resolved_list = ", ".join(path.as_posix() for path in resolved)
                print(f"using audio tracks: {resolved_list}")
            attach_audio_tracks(ffmpeg_path, final_output, resolved)
            watch_paths.update(path.resolve() for path in resolved)
            watch_paths.update(path.resolve().parent for path in resolved)

    return watch_paths


def watch_loop(args: argparse.Namespace) -> None:
    initial_config = load_config(args.config)
    audio_entries = [Path(entry) for entry in initial_config.get("audio_files", []) if entry]
    if audio_entries:
        resolved, _ = stv.resolve_audio_files(audio_entries)
        if resolved:
            resolved_list = ", ".join(path.as_posix() for path in resolved)
            print(f"using audio tracks: {resolved_list}")

    print("Watching for changes... Press Ctrl+C to stop.")
    snapshot: Dict[Path, float] = {}
    watch_paths: Set[Path] = set()
    try:
        while True:
            baseline = collect_snapshot(watch_paths) if watch_paths else snapshot
            watch_paths = run_build(args, announce_audio=False)
            current_snapshot = collect_snapshot(watch_paths)
            if baseline and current_snapshot != baseline:
                changed_paths = diff_snapshot(baseline, current_snapshot)
                if changed_paths:
                    names = ", ".join(sorted(path.name for path in changed_paths))
                    print(f"detected change in {names}")
                snapshot = current_snapshot
                continue
            snapshot = current_snapshot
            watch_dirs = {path if path.is_dir() else path.parent for path in watch_paths}
            if watchfiles_watch is not None:
                for changes in watchfiles_watch(*{str(path) for path in watch_dirs}):
                    changed_paths = sorted({Path(p) for _change, p in changes})
                    names = ", ".join(path.name for path in changed_paths) if changed_paths else "files"
                    print(f"detected change in {names}")
                    break
            else:
                while True:
                    time.sleep(max(args.interval, 0.1))
                    current = collect_snapshot(watch_paths)
                    if current != snapshot:
                        changed_paths = diff_snapshot(snapshot, current)
                        names = ", ".join(path.name for path in changed_paths) if changed_paths else "files"
                        print(f"detected change in {names}")
                        snapshot = current
                        break
    except KeyboardInterrupt:
        print("\nStopped watching.")


def main() -> None:
    args = parse_args()
    if args.watch:
        watch_loop(args)
    else:
        run_build(args)


if __name__ == "__main__":
    main()
