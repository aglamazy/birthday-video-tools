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
from lib import collage, text_renderer
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
    visual_sources: tuple[Path, ...] = ()


@dataclass(frozen=True)
class BuildContext:
    watch_paths: Set[Path]
    input_paths: Set[Path]


def _slugify(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_") or "segment"


def segment_output_path(segments_dir: Path, segment: SegmentInfo) -> Path:
    names = [_slugify(segment.source.name)]
    if segment.overlay_sources:
        names.extend(_slugify(dep.name) for dep in segment.overlay_sources)
    slug = "_".join(names)
    # keep deterministic prefix for ordering and avoid overly long filenames
    prefix = f"{segment.index:04d}"
    combined = f"{prefix}_{slug}" if slug else prefix
    if len(combined) > 120:
        combined = combined[:120]
    return segments_dir / f"{combined}.mp4"


def has_existing_output(base: Path) -> bool:
    if base.exists():
        return True
    stem = base.stem
    suffix = base.suffix or ""
    pattern = re.compile(rf"^{re.escape(stem)}-(\d+){re.escape(suffix)}$")
    for candidate in base.parent.glob(f"{stem}-*{suffix}"):
        if pattern.match(candidate.name):
            return True
    return False


def is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def filter_relevant_changes(changed_paths: Iterable[Path], input_paths: Set[Path]) -> List[Path]:
    relevant: Set[Path] = set()
    directories = {p for p in input_paths if p.is_dir()}
    resolved_inputs = {p.resolve() for p in input_paths}
    for raw_path in changed_paths:
        candidate = raw_path.resolve()
        if candidate in resolved_inputs:
            relevant.add(candidate)
            continue
        if any(is_within(candidate, directory) for directory in directories):
            relevant.add(candidate)
    return sorted(relevant)


def load_config(config_path: Path) -> dict[str, object]:
    return stv.load_config(config_path)


def iter_media_prefixes(source_dir: Path) -> Iterable[str]:
    seen: set[str] = set()
    for path in sorted(source_dir.iterdir(), key=lambda p: p.name):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in stv.IMAGE_EXTENSIONS and suffix not in stv.VIDEO_EXTENSIONS:
            continue
        prefix = path.stem.split("_", 1)[0]
        if prefix in seen:
            continue
        seen.add(prefix)
        yield prefix


def list_media(source_dir: Path) -> List[Path]:
    return [source_dir / prefix for prefix in iter_media_prefixes(source_dir)]


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

    plan: List[SegmentInfo] = []
    index = 0
    for entry in selected:
        prefix = entry.name
        visuals, overlays = collage.collect_assets(entry.parent, prefix)
        if not visuals:
            continue

        image_files = [path for path in visuals if path.suffix.lower() in stv.IMAGE_EXTENSIONS]
        video_files = [path for path in visuals if path.suffix.lower() in stv.VIDEO_EXTENSIONS]

        overlay_layout: Optional[TextLayout] = None
        overlay_text: Optional[str] = None
        if overlays:
            combined = combine_overlay_texts(overlays)
            if combined.lines or combined.title or combined.metadata:
                overlay_layout = combined
                overlay_text = combined.overlay_text()
                if not overlay_text:
                    overlay_text = None

        duration_override: Optional[float] = None
        if overlay_layout:
            duration_str = overlay_layout.metadata.get("duration")
            if duration_str:
                try:
                    duration_override = float(duration_str)
                except ValueError:
                    duration_override = None

        if video_files and not image_files:
            plan.append(
                SegmentInfo(
                    index=index + 1,
                    source=video_files[0],
                    kind="video",
                    overlay_sources=tuple(overlays),
                    overlay_layout=overlay_layout,
                    overlay_text=overlay_text,
                    duration=None,
                    visual_sources=tuple(video_files),
                )
            )
            index += 1
            continue

        image_sources = image_files if image_files else visuals
        duration_value: Optional[float] = (
            duration_override
            if duration_override is not None
            else (duration_overlay if overlay_text else duration_image)
        )

        plan.append(
            SegmentInfo(
                index=index + 1,
                source=image_sources[0],
                kind="image",
                overlay_sources=tuple(overlays),
                overlay_layout=overlay_layout,
                overlay_text=overlay_text,
                duration=duration_value,
                visual_sources=tuple(image_sources),
            )
        )
        index += 1

    return plan


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def list_dependencies(segment: SegmentInfo) -> List[Path]:
    deps_set: List[Path] = []
    visuals = list(segment.visual_sources)
    if visuals:
        deps_set.extend(visuals)
    else:
        deps_set.append(segment.source)
    for overlay in segment.overlay_sources:
        deps_set.append(overlay)
    return deps_set


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
    ffprobe_path: Optional[str],
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
    motion_plan = stv.get_motion_plan(segment.index, still_duration, fps)
    filter_graph, filter_output = stv.build_media_filter_graph(
        width,
        height,
        segment.overlay_text,
        label,
        debug_text,
        overlay_subtitle_path,
        motion_plan,
    )
    def _render_with_input(input_path: Path) -> None:
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
            str(input_path),
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

    visuals = list(segment.visual_sources) or [segment.source]
    if len(visuals) > 1:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            source_image = collage.build_collage(
                ffmpeg_path,
                ffprobe_path,
                visuals,
                width,
                height,
                tmp_dir,
            )
            _render_with_input(source_image)
    else:
        _render_with_input(visuals[0])


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
    ffprobe_path: Optional[str],
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
            ffprobe_path,
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
    segment_files: Sequence[Path],
    output_path: Path,
    segments_dir: Path,
    ffmpeg_path: str,
) -> None:
    concat_path = segments_dir / "concat.txt"
    with concat_path.open("w", encoding="utf-8") as handle:
        for segment_file in segment_files:
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


def mux_audio_tracks(
    ffmpeg_path: str,
    source_video: Path,
    target_video: Path,
    audio_paths: Sequence[Path],
    ffprobe_path: Optional[str],
    video_duration: Optional[float],
) -> None:
    existing_sources = [path for path in audio_paths if path.exists()]
    if not existing_sources:
        shutil.copyfile(source_video, target_video)
        return

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        combined_audio = tmp_dir / "combined_audio.m4a"
        adjusted_sources = stv._adjust_audio_tracks(
            ffmpeg_path,
            list(existing_sources),
            video_duration,
            ffprobe_path,
            tmp_dir,
        )

        if len(adjusted_sources) == 1:
            stv.run_ffmpeg(
                [
                    ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(adjusted_sources[0]),
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
            for source in adjusted_sources:
                concat_cmd.extend(["-i", str(source)])
            filter_inputs = "".join(f"[{idx}:a]" for idx in range(len(adjusted_sources)))
            filter_complex = (
                f"{filter_inputs}concat=n={len(adjusted_sources)}:v=0:a=1[aout]"
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
                str(source_video),
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
        shutil.move(tmp_video, target_video)


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


def run_build(args: argparse.Namespace, announce_audio: bool = True) -> BuildContext:
    config = load_config(args.config)
    stv.configure_motion(config)
    keep_temp = stv._config_bool(config, "keep_temp", False)

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

    def _font_size(value, fallback):
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    title_font_size = _font_size(
        config.get("title_font_size"), stv.DEFAULT_CONFIG["title_font_size"]
    )
    body_font_size = _font_size(
        config.get("body_font_size"), stv.DEFAULT_CONFIG["body_font_size"]
    )
    text_renderer.set_font_sizes(title_font_size, body_font_size)

    source_dir = Path(config.get("source_dir", stv.DEFAULT_CONFIG["source_dir"])).resolve()
    if not source_dir.exists():
        raise SystemExit(f"Source directory {source_dir} does not exist")

    input_paths: Set[Path] = {args.config.resolve(), source_dir}

    base_output = (
        args.output if args.output else Path(config.get("output", "slideshow.mp4"))
    ).resolve()
    if not base_output.suffix:
        base_output = base_output.with_suffix(".mp4")
    ensure_dir(base_output.parent)

    width, height = stv.parse_resolution(str(config.get("resolution", "1920x1080")))
    fps = int(config.get("fps", stv.DEFAULT_CONFIG["fps"]))
    duration_image = float(config.get("duration_image", stv.DEFAULT_CONFIG["duration_image"]))
    duration_overlay = float(
        config.get("duration_overlay", stv.DEFAULT_CONFIG["duration_overlay"])
    )
    duration_text = float(config.get("duration_text", stv.DEFAULT_CONFIG["duration_text"]))
    work_dir_root = Path(config.get("work_dir", "segments")).resolve()
    ensure_dir(work_dir_root)

    watch_paths: Set[Path] = {args.config.resolve(), source_dir}

    media_files = list_media(source_dir)
    if not media_files:
        print("No media files found; nothing to do.")
        return BuildContext(watch_paths, input_paths)

    plan = build_segment_plan(
        media_files,
        args.limit,
        duration_image,
        duration_overlay,
        duration_text,
    )
    if not plan:
        print("No convertible media files found.")
        return BuildContext(watch_paths, input_paths)

    if args.segments_dir:
        segments_dir = args.segments_dir.resolve()
    else:
        segments_dir = work_dir_root / "segments"
    if not keep_temp and not has_existing_output(base_output) and segments_dir.exists():
        shutil.rmtree(segments_dir)
    ensure_dir(segments_dir)
    subtitles_root = segments_dir / "subtitles"
    ensure_dir(subtitles_root)

    config_mtime = args.config.stat().st_mtime if args.config.exists() else time.time()
    script_mtime = Path(stv.__file__).stat().st_mtime
    additional_mtime = max(config_mtime, script_mtime)

    ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"
    ffprobe_path = shutil.which("ffprobe")

    force_rebuild = args.force or not has_existing_output(base_output)
    expected_segments: Set[Path] = set()
    segment_files: List[Path] = []

    for segment in plan:
        resolved_source = segment.source.resolve()
        watch_paths.add(resolved_source)
        input_paths.add(resolved_source)

        resolved_overlays = [dep.resolve() for dep in segment.overlay_sources]
        for resolved_dep in resolved_overlays:
            watch_paths.add(resolved_dep)
            input_paths.add(resolved_dep)

        output_segment = segment_output_path(segments_dir, segment)
        expected_segments.add(output_segment)
        segment_files.append(output_segment)

        deps = list_dependencies(segment)
        should_render = force_rebuild or needs_render(output_segment, deps, additional_mtime)

        if should_render:
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
                ffprobe_path,
                duration_image,
            )

    for segment_file in segments_dir.glob("*.mp4"):
        if segment_file not in expected_segments:
            try:
                segment_file.unlink()
            except OSError:
                pass

    final_output = next_versioned_path(base_output)
    intermediate_output = final_output.with_name(f"{final_output.stem}.video{final_output.suffix}")
    build_succeeded = False
    try:
        if args.verbose:
            print(f"concatenating into {intermediate_output.name}")
        if intermediate_output.exists():
            intermediate_output.unlink()
        concat_segments(segment_files, intermediate_output, segments_dir, ffmpeg_path)
        audio_paths = [Path(entry) for entry in config.get("audio_files", []) if entry]
        audio_attached = not audio_paths
        for audio in audio_paths:
            resolved_audio = audio.resolve()
            watch_paths.add(resolved_audio)
            watch_paths.add(resolved_audio.parent)
            input_paths.add(resolved_audio)
            input_paths.add(resolved_audio.parent)
        video_duration = stv.probe_media_duration(ffprobe_path, intermediate_output)
        if audio_paths:
            resolved, missing = stv.resolve_audio_files(audio_paths)
            for missing_path in missing:
                print(f"Warning: audio file {missing_path} not found; skipping.")
            if resolved:
                if announce_audio and args.verbose:
                    resolved_list = ", ".join(path.as_posix() for path in resolved)
                    print(f"using audio tracks: {resolved_list}")
                mux_audio_tracks(ffmpeg_path, intermediate_output, final_output, resolved, ffprobe_path, video_duration)
                audio_attached = True
                resolved_parents = {path.resolve().parent for path in resolved}
                watch_paths.update(path.resolve() for path in resolved)
                watch_paths.update(resolved_parents)
                input_paths.update(path.resolve() for path in resolved)
                input_paths.update(resolved_parents)
            else:
                shutil.copyfile(intermediate_output, final_output)
        else:
            shutil.copyfile(intermediate_output, final_output)
        if audio_attached:
            print(f"generated {final_output.name}")
        else:
            print(f"generated {final_output.name} (audio missing)")
        build_succeeded = True
    finally:
        if not build_succeeded and final_output.exists():
            try:
                final_output.unlink()
            except OSError:
                pass
        if not build_succeeded and intermediate_output.exists():
            try:
                intermediate_output.unlink()
            except OSError:
                pass

    return BuildContext(watch_paths, input_paths)


def watch_loop(args: argparse.Namespace) -> None:
    initial_config = load_config(args.config)
    stv.configure_motion(initial_config)
    audio_entries = [Path(entry) for entry in initial_config.get("audio_files", []) if entry]
    if audio_entries:
        resolved, _ = stv.resolve_audio_files(audio_entries)
        if resolved:
            resolved_list = ", ".join(path.as_posix() for path in resolved)
            print(f"using audio tracks: {resolved_list}")

    build_ctx = run_build(args, announce_audio=False)
    watch_paths = build_ctx.watch_paths
    input_paths = build_ctx.input_paths
    snapshot: Dict[Path, float] = collect_snapshot(watch_paths)

    print("Watching for changes... Press Ctrl+C to stop.")
    try:
        while True:
            changed_paths: List[Path] = []
            watch_dirs = {path if path.is_dir() else path.parent for path in watch_paths}
            if watchfiles_watch is not None and watch_dirs:
                for changes in watchfiles_watch(*{str(path) for path in watch_dirs}):
                    changed_paths = sorted({Path(p) for _change, p in changes})
                    break
            else:
                while True:
                    time.sleep(max(args.interval, 0.1))
                    current = collect_snapshot(watch_paths)
                    if current != snapshot:
                        changed_paths = diff_snapshot(snapshot, current)
                        snapshot = current
                        break
            relevant_changes = filter_relevant_changes(changed_paths, input_paths)
            if not relevant_changes:
                continue
            names = ", ".join(path.name for path in relevant_changes)
            print(f"detected change in {names}")
            build_ctx = run_build(args, announce_audio=False)
            watch_paths = build_ctx.watch_paths
            input_paths = build_ctx.input_paths
            snapshot = collect_snapshot(watch_paths)
    except KeyboardInterrupt:
        print("\nStopped watching.")


def main() -> None:
    args = parse_args()
    if args.output:
        if not args.output.suffix:
            args.output = args.output.with_suffix(".mp4")
    if args.watch:
        watch_loop(args)
    else:
        run_build(args)


if __name__ == "__main__":
    main()
