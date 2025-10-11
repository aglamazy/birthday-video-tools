#!/usr/bin/env python3
"""Render a single slideshow segment based on the configured settings."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import incremental_builder as ib
import sequence_to_video as stv
from lib import collage, text_renderer
from lib.text_utils import combine_overlay_texts




def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Stem of the files inside the sequence directory.")
    parser.add_argument("--config", type=Path, default=ib.CONFIG_PATH, help="Path to config.json (default: config.json).")
    parser.add_argument("--segments-dir", type=Path, default=Path("segments"), help="Directory to write segment files.")
    parser.add_argument("--output", type=Path, required=True, help="Path to the output segment mp4.")
    parser.add_argument(
        "--subtitles-dir",
        type=Path,
        help="Directory root for generated ASS subtitles (defaults to segments/subtitles/<base>).",
    )
    parser.add_argument("--verbose", action="store_true", help="Print ffmpeg commands.")
    return parser.parse_args()


def configure_environment(config: dict[str, object], verbose: bool) -> None:
    stv.VERBOSE = verbose
    stv.FFMPEG_DEBUG = False
    stv.SHOW_FILENAME = bool(config.get("debug_filename", False))
    stv.LABEL_YEAR = bool(config.get("label_year", False))
    stv.configure_motion(config)

    label_font = config.get("label_font")
    if label_font:
        font_path = Path(label_font)
        if font_path.exists():
            stv.FONT_PATH = font_path
    if stv.FONT_PATH is None:
        default_font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if default_font.exists():
            stv.FONT_PATH = default_font

    def _font_size(value: object, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    title_font_size = _font_size(config.get("title_font_size"), stv.DEFAULT_CONFIG["title_font_size"])
    body_font_size = _font_size(config.get("body_font_size"), stv.DEFAULT_CONFIG["body_font_size"])
    text_renderer.set_font_sizes(title_font_size, body_font_size)


def ensure_mp4(path: Path) -> Path:
    if path.suffix:
        return path
    return path.with_suffix(".mp4")


def main() -> None:
    args = parse_args()
    config = ib.load_config(args.config)
    configure_environment(config, args.verbose)

    source_dir = Path(config.get("source_dir", stv.DEFAULT_CONFIG["source_dir"])).resolve()
    if not source_dir.exists():
        raise SystemExit(f"Source directory {source_dir} does not exist.")

    visuals, text_files = collage.collect_assets(source_dir, args.base)
    if not visuals:
        raise SystemExit(f"No visual files found for base '{args.base}' in {source_dir}.")

    image_files = [path for path in visuals if path.suffix.lower() in stv.IMAGE_EXTENSIONS]
    video_files = [path for path in visuals if path.suffix.lower() in stv.VIDEO_EXTENSIONS]

    duration_image = float(config.get("duration_image", stv.DEFAULT_CONFIG["duration_image"]))
    duration_overlay = float(config.get("duration_overlay", stv.DEFAULT_CONFIG["duration_overlay"]))

    overlay_layout = None
    overlay_text = None
    if text_files:
        combined_layout = combine_overlay_texts(text_files)
        if combined_layout.lines or combined_layout.title or combined_layout.metadata:
            overlay_layout = combined_layout
            overlay_text = combined_layout.overlay_text()
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

    segments_dir = args.segments_dir.resolve()
    ib.ensure_dir(segments_dir)

    output_path = ensure_mp4(args.output).resolve()
    ib.ensure_dir(output_path.parent)

    subtitles_root = args.subtitles_dir.resolve() if args.subtitles_dir else segments_dir / "subtitles" / args.base
    ib.ensure_dir(subtitles_root)

    ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"
    ffprobe_path = shutil.which("ffprobe")
    width, height = stv.parse_resolution(str(config.get("resolution", "1920x1080")))
    fps = int(config.get("fps", stv.DEFAULT_CONFIG["fps"]))

    is_video = bool(video_files) and not image_files
    segment_duration = None if is_video else (
        duration_override if duration_override is not None else (duration_overlay if overlay_text else duration_image)
    )

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        temp_dir = Path(tmp_dir_str)
        if is_video:
            source_path = video_files[0]
            visual_sources = tuple(video_files)
            segment_kind = "video"
        else:
            source_images = image_files if image_files else visuals
            if len(source_images) > 1:
                source_path = collage.build_collage(ffmpeg_path, ffprobe_path, source_images, width, height, temp_dir)
            else:
                source_path = source_images[0]
            visual_sources = tuple(source_images)
            segment_kind = "image"

        segment = ib.SegmentInfo(
            index=1,
            source=source_path,
            kind=segment_kind,
            overlay_sources=tuple(text_files),
            overlay_layout=overlay_layout,
            overlay_text=overlay_text,
            duration=segment_duration,
            visual_sources=visual_sources,
        )

        ib.render_segment(segment, output_path, subtitles_root, width, height, fps, ffmpeg_path, ffprobe_path, duration_image)


if __name__ == "__main__":
    main()
