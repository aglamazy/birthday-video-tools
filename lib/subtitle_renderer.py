from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import ImageFont

from . import text_renderer
from .text_utils import TextLayout, is_rtl_text


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


def _line_height() -> int:
    return text_renderer.BODY_FONT_SIZE + text_renderer.BODY_LINE_SPACING


def create_ass_subtitle(
    layout: TextLayout,
    width: int,
    height: int,
    font_path: Optional[Path],
    output_dir: Path,
    duration: Optional[float] = None,
) -> Path:
    has_title = bool(layout.title and layout.title.strip())
    lines = list(layout.lines)
    has_line_content = any(
        line.display.strip() for line in lines if line.kind != "blank"
    )
    if not has_title and not has_line_content:
        raise ValueError("Cannot create ASS subtitle from empty layout.")

    output_dir.mkdir(parents=True, exist_ok=True)
    subtitle_path = _next_output_path(output_dir, "overlay", ".ass")

    font_name = _resolve_font_name(font_path)
    end_time = _format_ass_time(duration)
    line_height = _line_height()

    dialogues: list[str] = []

    def add_dialogue(
        alignment: int,
        x_pos: int,
        y_pos: int,
        text: str,
        direction: Optional[str] = None,
        font_size: Optional[int] = None,
    ) -> None:
        if not text:
            return
        escaped = _escape_ass_text(text)
        overrides = [f"\\an{alignment}", f"\\pos({x_pos},{y_pos})"]
        if direction == "rtl":
            overrides.append("\\rtl")
        elif direction == "ltr":
            overrides.append("\\ltr")
        if font_size:
            overrides.append(f"\\fs{font_size}")
        override_block = "".join(overrides)
        dialogues.append(
            f"Dialogue: 0,0:00:00.00,{end_time},Overlay,,0,0,0,,"
            f"{{{override_block}}}{escaped}"
        )

    top_lines = [line for line in lines if line.align == "top" and line.display.strip()]
    body_lines = [line for line in lines if line.align != "top"]

    if has_title:
        title_direction = "rtl" if is_rtl_text(layout.title) else "ltr"
        add_dialogue(
            8,
            width // 2,
            text_renderer.TOP_MARGIN,
            layout.title.strip(),
            title_direction,
            text_renderer.TITLE_FONT_SIZE,
        )
        top_base_y = (
            text_renderer.TOP_MARGIN
            + text_renderer.TITLE_FONT_SIZE
            + text_renderer.TOP_LINE_SPACING
        )
    else:
        top_base_y = text_renderer.TOP_MARGIN

    for index, line in enumerate(top_lines):
        y_pos = top_base_y + index * line_height
        line_text = line.text if line.text else line.display
        direction = "rtl" if is_rtl_text(line_text) else "ltr"
        add_dialogue(
            9,
            width - text_renderer.RIGHT_MARGIN,
            y_pos,
            line.display.strip(),
            direction,
            text_renderer.BODY_FONT_SIZE,
        )

    body_start_y = top_base_y + len(top_lines) * line_height
    if has_title or top_lines:
        body_start_y += 40

    current_y = body_start_y
    for line in body_lines:
        if line.kind == "blank":
            current_y += line_height
            continue
        if not line.display.strip():
            current_y += line_height
            continue
        align = line.align
        line_text = line.text if line.text else line.display
        direction = "rtl" if is_rtl_text(line_text) else "ltr"
        if align == "center":
            alignment = 8
            x_pos = width // 2
        elif align == "left":
            alignment = 7
            x_pos = text_renderer.LEFT_MARGIN + line.level * text_renderer.INDENT_WIDTH
        else:  # treat "right" and any fallback as right-aligned
            alignment = 9
            x_pos = (
                width - text_renderer.RIGHT_MARGIN - line.level * text_renderer.INDENT_WIDTH
            )
        add_dialogue(
            alignment,
            x_pos,
            current_y,
            line.display.strip(),
            direction,
            text_renderer.BODY_FONT_SIZE,
        )
        current_y += line_height

    if not dialogues:
        raise ValueError("Cannot create ASS subtitle from empty layout.")

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
                f"Style: Overlay,{font_name},{text_renderer.BODY_FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
                "0,0,0,0,100,100,0,0,1,3,0,8,40,120,80,0"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *dialogues,
        ]
    )

    subtitle_path.write_text(subtitle_contents, encoding="utf-8")
    return subtitle_path
