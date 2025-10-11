"""Helpers for building image collages from grouped sequence assets."""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

VISUAL_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".heic",
    ".heif",
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".mkv",
    ".hevc",
    ".mpg",
    ".mpeg",
    ".wmv",
}

TEXT_EXTENSIONS = {".txt", ".pug"}


def collect_assets(source_dir: Path, prefix: str) -> tuple[list[Path], list[Path]]:
    visuals: list[Path] = []
    overlays: list[Path] = []
    for candidate in sorted(source_dir.glob(f"{prefix}*")):
        if not candidate.is_file():
            continue
        suffix = candidate.suffix.lower()
        stem = candidate.stem
        if not stem.startswith(prefix):
            continue
        if suffix in VISUAL_EXTENSIONS:
            visuals.append(candidate)
        elif suffix in TEXT_EXTENSIONS:
            overlays.append(candidate)
    return visuals, overlays


def probe_dimensions(path: Path, ffprobe_path: Optional[str]) -> Optional[tuple[int, int]]:
    if not ffprobe_path:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    parts = result.stdout.strip().split("x")
    if len(parts) != 2:
        return None
    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError:
        return None
    return width, height


def classify_orientation(path: Path, ffprobe_path: Optional[str]) -> str:
    dims = probe_dimensions(path, ffprobe_path)
    if not dims:
        return "unknown"
    width, height = dims
    if height > width * 1.1:
        return "portrait"
    if width > height * 1.1:
        return "landscape"
    return "square"


def _compute_cell_size(width: int, height: int, cols: int, rows: int, padding: int) -> tuple[int, int]:
    usable_w = width - padding * (cols + 1)
    usable_h = height - padding * (rows + 1)
    cell_w = max(1, usable_w // max(cols, 1))
    cell_h = max(1, usable_h // max(rows, 1))
    return cell_w, cell_h


def _layout_positions(
    count: int,
    cols: int,
    rows: int,
    cell_w: int,
    cell_h: int,
    padding: int,
    orientation: Sequence[str],
) -> List[tuple[int, int]]:
    def col_x(col_index: int) -> int:
        return padding + col_index * (cell_w + padding)

    def row_y(row_index: int) -> int:
        return padding + row_index * (cell_h + padding)

    if count == 1:
        return [(col_x(0), row_y(0))]

    if count == 2:
        if rows == 1:
            return [(col_x(0), row_y(0)), (col_x(1), row_y(0))]
        return [(col_x(0), row_y(0)), (col_x(0), row_y(1))]

    if count == 3:
        top = [(col_x(0), row_y(0)), (col_x(1), row_y(0))]
        content_width = cols * cell_w + (cols - 1) * padding
        center_x = padding + (content_width - cell_w) // 2
        bottom = (center_x, row_y(1))
        return top + [bottom]

    positions: list[tuple[int, int]] = []
    for idx in range(count):
        col = idx % cols
        row = idx // cols
        positions.append((col_x(col), row_y(row)))
    return positions


def build_collage(
    ffmpeg_path: str,
    ffprobe_path: Optional[str],
    images: Sequence[Path],
    width: int,
    height: int,
    workspace: Path,
    padding: Optional[int] = None,
) -> Path:
    if len(images) < 2:
        raise ValueError("Collage requires at least two images")

    padding_value = padding if padding is not None else max(20, min(width, height) // 40)
    orientations = [classify_orientation(path, ffprobe_path) for path in images]

    if len(images) == 2:
        same_orientation = orientations[0] == orientations[1]
        if same_orientation and orientations[0] == "landscape":
            cols, rows = 1, 2
        else:
            cols, rows = 2, 1
    elif len(images) == 3:
        cols, rows = 2, 2
    elif len(images) == 4:
        cols, rows = 2, 2
    else:
        cols = min(3, len(images))
        rows = math.ceil(len(images) / cols)

    cell_w, cell_h = _compute_cell_size(width, height, cols, rows, padding_value)
    positions = _layout_positions(len(images), cols, rows, cell_w, cell_h, padding_value, orientations)

    filter_parts: list[str] = []
    labels: list[str] = []
    for idx in range(len(images)):
        label = f"p{idx}"
        filter_parts.append(
            f"[{idx}:v]scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
            f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2,format=rgba[{label}]"
        )
        labels.append(f"[{label}]")

    layout_entries = [f"{x}_{y}" for (x, y) in positions]
    filter_parts.append(
        "".join(labels) + f"xstack=inputs={len(images)}:layout={'|'.join(layout_entries)}:fill=black[stack]"
    )
    filter_parts.append("[stack]format=rgba[out]")
    filter_graph = ";".join(filter_parts)

    collage_path = workspace / "collage.png"
    cmd: list[str] = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y"]
    for path in images:
        cmd.extend(["-i", str(path)])
    cmd.extend(
        [
            "-filter_complex",
            filter_graph,
            "-map",
            "[out]",
            "-frames:v",
            "1",
            str(collage_path),
        ]
    )
    subprocess.run(cmd, check=True)
    return collage_path
