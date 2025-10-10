#!/usr/bin/env python3
"""Render a single slideshow segment based on the configured settings."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import incremental_builder as ib
import sequence_to_video as stv
from lib import text_renderer


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


def main() -> None:
    args = parse_args()
    config = ib.load_config(args.config)
    configure_environment(config, args.verbose)

    source_dir = Path(config.get("source_dir", stv.DEFAULT_CONFIG["source_dir"])).resolve()
    if not source_dir.exists():
        raise SystemExit(f"Source directory {source_dir} does not exist.")

    base_files = sorted(path for path in source_dir.glob(f"{args.base}.*") if path.is_file())
    if not base_files:
        raise SystemExit(f"No files found for base '{args.base}' in {source_dir}.")

    duration_image = float(config.get("duration_image", stv.DEFAULT_CONFIG["duration_image"]))
    duration_overlay = float(config.get("duration_overlay", stv.DEFAULT_CONFIG["duration_overlay"]))
    duration_text = float(config.get("duration_text", stv.DEFAULT_CONFIG["duration_text"]))

    plan = ib.build_segment_plan(base_files, None, duration_image, duration_overlay, duration_text)
    if not plan:
        raise SystemExit(f"Unable to build segment plan for base '{args.base}'.")

    segment = next((item for item in plan if item.source.stem == args.base), None)
    if segment is None:
        segment = plan[0]
        if len(plan) > 1:
            print(f"Warning: multiple segments detected for base '{args.base}', using the first.", flush=True)

    segments_dir = args.segments_dir.resolve()
    ib.ensure_dir(segments_dir)

    output_path = args.output.resolve()
    ib.ensure_dir(output_path.parent)

    subtitles_root = args.subtitles_dir.resolve() if args.subtitles_dir else segments_dir / "subtitles" / args.base
    ib.ensure_dir(subtitles_root)

    ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"
    width, height = stv.parse_resolution(str(config.get("resolution", "1920x1080")))
    fps = int(config.get("fps", stv.DEFAULT_CONFIG["fps"]))

    ib.render_segment(segment, output_path, subtitles_root, width, height, fps, ffmpeg_path, duration_image)


if __name__ == "__main__":
    main()
