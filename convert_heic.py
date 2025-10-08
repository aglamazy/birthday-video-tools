#!/usr/bin/env python3
"""Convert all HEIC/HEIF files beneath the sequence folder to JPEG or WebP."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional

try:
    from pillow_heif import register_heif_opener  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    register_heif_opener = None

try:
    from PIL import Image  # type: ignore
except ImportError:  # pragma: no cover - Pillow is optional but recommended
    Image = None  # type: ignore

HEIC_EXTENSIONS = {".heic", ".heif"}


class ConversionError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("sequence"),
        help="Directory to scan recursively for HEIC/HEIF files (default: sequence).",
    )
    parser.add_argument(
        "--format",
        choices=("jpg", "webp"),
        default="jpg",
        help="Target image format (default: jpg).",
    )
    parser.add_argument(
        "--quality",
        type=int,
        help="Quality setting for the output format (e.g. 90). Uses Pillow defaults if omitted.",
    )
    parser.add_argument(
        "--remove-original",
        action="store_true",
        help="Remove the source HEIC file after successful conversion.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the planned conversions without writing files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_dir = args.source_dir.resolve()
    if not source_dir.exists():
        raise SystemExit(f"Source directory {source_dir} does not exist")
    if not source_dir.is_dir():
        raise SystemExit(f"Source path {source_dir} is not a directory")

    heic_files = list(find_heic_files(source_dir))
    if not heic_files:
        print("No HEIC/HEIF files found.")
        return

    pillow_available = prepare_pillow()
    external_tool = None if pillow_available else detect_external_tool(args.format)

    if not pillow_available and external_tool is None:
        raise SystemExit(
            "No HEIC conversion backend available. Install pillow-heif & Pillow, ImageMagick, or heif-convert."
        )

    for src in heic_files:
        dst = destination_path(src, args.format)
        print(f"{src.relative_to(source_dir)} -> {dst.relative_to(source_dir)}")
        if args.dry_run:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        renamed = False
        try:
            if pillow_available:
                convert_with_pillow(src, dst, args.format, args.quality)
            else:
                convert_with_external(src, dst, external_tool)
        except ConversionError as exc:  # pragma: no cover - runtime failure path
            message = str(exc)
            if args.format == "jpg" and "JPEG image" in message:
                if dst.exists():
                    print(
                        f"Skipping rename for {src.name}: destination {dst.name} already exists",
                        file=sys.stderr,
                    )
                    continue
                src.rename(dst)
                renamed = True
                print(f"Renamed mislabelled JPEG {src.name} -> {dst.name}")
            else:
                print(f"Failed to convert {src}: {message}", file=sys.stderr)
                continue
        if args.remove_original and not renamed:
            src.unlink(missing_ok=True)


def find_heic_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in HEIC_EXTENSIONS:
            yield path


def destination_path(src: Path, target_format: str) -> Path:
    return src.with_suffix(f".{target_format}")


def prepare_pillow() -> bool:
    if register_heif_opener and Image:
        register_heif_opener()
        return True
    return False


def convert_with_pillow(src: Path, dst: Path, target_format: str, quality: Optional[int]) -> None:
    if Image is None:
        raise ConversionError("Pillow is not installed")
    with Image.open(src) as img:
        if img.format and img.format.upper() == "JPEG" and target_format == "jpg":
            src.rename(dst)
            return
        rgb = img.convert("RGB")
        save_kwargs = {}
        if quality is not None:
            if target_format == "jpg":
                save_kwargs["quality"] = quality
            elif target_format == "webp":
                save_kwargs["quality"] = quality
        rgb.save(dst, format=target_format.upper(), **save_kwargs)


def detect_external_tool(target_format: str) -> Optional[list[str]]:
    magick = shutil.which("magick")
    convert = shutil.which("convert")
    heif_convert = shutil.which("heif-convert")

    if magick:
        return [magick]
    if convert:
        return [convert]
    if target_format == "jpg" and heif_convert:
        return [heif_convert]
    return None


def convert_with_external(src: Path, dst: Path, tool_cmd: Optional[list[str]]) -> None:
    if not tool_cmd:
        raise ConversionError("No external tool available")

    cmd = list(tool_cmd)
    if Path(tool_cmd[0]).name in {"magick", "convert"}:
        cmd.extend([str(src), str(dst)])
    else:  # heif-convert
        cmd.extend([str(src), str(dst)])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ConversionError(result.stderr.strip() or "conversion failed")


if __name__ == "__main__":
    main()
