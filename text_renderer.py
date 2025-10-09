from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from text_utils import LineInfo, TextLayout

RIGHT_MARGIN = 120
TOP_MARGIN = 80
TOP_LINE_SPACING = 20
BODY_LINE_SPACING = 22
TITLE_FONT_SIZE = 72
BODY_FONT_SIZE = 56
BACKGROUND_COLOR = (16, 16, 16, 255)
TEXT_COLOR = (255, 255, 255, 255)
BOX_COLOR = (0, 0, 0, 136)
OUTLINE_COLOR = (0, 0, 0, 255)


def _get_font(font_path: Optional[Path], size: int) -> ImageFont.FreeTypeFont:
    if font_path and font_path.exists():
        return ImageFont.truetype(str(font_path), size)
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _draw_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    position: Tuple[int, int],
    anchor: str,
) -> None:
    x, y = position
    box = draw.textbbox((x, y), text, font=font, anchor=anchor)
    draw.rectangle(box, fill=BOX_COLOR)
    draw.text((x, y), text, font=font, fill=TEXT_COLOR, anchor=anchor, stroke_width=3, stroke_fill=OUTLINE_COLOR)


def render_text_panel(
    layout: TextLayout,
    width: int,
    height: int,
    font_path: Optional[Path],
    background: bool,
    output_dir: Path,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    if background:
        image = Image.new("RGBA", (width, height), BACKGROUND_COLOR)
    else:
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    draw = ImageDraw.Draw(image)
    title_font = _get_font(font_path, TITLE_FONT_SIZE)
    body_font = _get_font(font_path, BODY_FONT_SIZE)

    line_height = BODY_FONT_SIZE + BODY_LINE_SPACING

    top_lines: list[LineInfo] = [line for line in layout.lines if line.align == "top"]
    body_lines: list[LineInfo] = [line for line in layout.lines if line.align != "top"]

    if layout.title:
        _draw_text(
            draw,
            layout.title,
            title_font,
            (width - RIGHT_MARGIN, TOP_MARGIN),
            anchor="ra",
        )
        top_start_y = TOP_MARGIN + TITLE_FONT_SIZE + TOP_LINE_SPACING
    else:
        top_start_y = TOP_MARGIN

    for idx, line in enumerate(top_lines):
        line_y = top_start_y + idx * line_height
        _draw_text(
            draw,
            line.display,
            body_font,
            (width - RIGHT_MARGIN, line_y),
            anchor="ra",
        )

    body_start_y = top_start_y + len(top_lines) * line_height + (40 if layout.title or top_lines else 0)
    current_y = body_start_y
    for line in body_lines:
        if not line.display.strip():
            current_y += line_height
            continue
        if line.align == "center":
            anchor = "ma"
            pos = (width // 2, current_y)
        else:
            anchor = "ra"
            pos = (width - RIGHT_MARGIN - line.level * 70, current_y)
        _draw_text(draw, line.display, body_font, pos, anchor)
        current_y += line_height

    output_path = output_dir / f"panel_{len(list(output_dir.iterdir())):04d}.png"
    image.save(output_path, "PNG")
    return output_path
