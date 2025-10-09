from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import ImageFont


def _escape_ass_text(text: str) -> str:
    escaped = text.replace("\\", r"\\")
    escaped = escaped.replace("{", r"\{")
    escaped = escaped.replace("}", r"\}")
    return escaped.replace("\n", r"\N")


def _format_ass_time(duration: Optional[float]) -> str:
    if duration is None or duration <= 0:
        # Run effectively until the segment finishes; one hour is a safe upper bound.
        return "0:59:59.00"

    total_centiseconds = int(round(duration * 100))
    # Clamp rounding overflow (e.g., 59.999 -> 60.00).
    seconds, centiseconds = divmod(total_centiseconds, 100)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _resolve_font_name(font_path: Optional[Path]) -> str:
    if font_path and font_path.exists():
        try:
            font = ImageFont.truetype(str(font_path), size=64)
            family, _ = font.getname()
            if family:
                return family
        except OSError:
            stem = font_path.stem
            if stem:
                return stem
    return "DejaVu Sans"


def _next_output_path(output_dir: Path, prefix: str, suffix: str) -> Path:
    counter = 0
    while True:
        candidate = output_dir / f"{prefix}_{counter:04d}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def create_ass_subtitle(
    text: str,
    width: int,
    height: int,
    font_path: Optional[Path],
    output_dir: Path,
    duration: Optional[float] = None,
) -> Path:
    normalized = text.strip()
    if not normalized:
        raise ValueError("Cannot create ASS subtitle from empty text.")

    output_dir.mkdir(parents=True, exist_ok=True)
    subtitle_path = _next_output_path(output_dir, "overlay", ".ass")

    font_name = _resolve_font_name(font_path)
    ass_text = _escape_ass_text(normalized)
    end_time = _format_ass_time(duration)

    subtitle_contents = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            (
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
                "MarginL, MarginR, MarginV, Encoding"
            ),
            (
                f"Style: Overlay,{font_name},52,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
                "0,0,0,0,100,100,0,0,1,3,0,8,40,120,80,0"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            f"Dialogue: 0,0:00:00.00,{end_time},Overlay,,0,0,0,,{{\\an8}}{ass_text}",
        ]
    )

    subtitle_path.write_text(subtitle_contents, encoding="utf-8")
    return subtitle_path

