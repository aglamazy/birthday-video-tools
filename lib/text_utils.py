from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

BULLET_PREFIXES = ("•", "-", "*")
RTL_HEBREW_START = ord("\u0590")
RTL_HEBREW_END = ord("\u05FF")


def _normalize_indent(raw_line: str) -> str:
    return raw_line.replace("\t", "  ")


def _indent_level(raw_line: str) -> int:
    normalized = _normalize_indent(raw_line)
    leading = len(normalized) - len(normalized.lstrip(" "))
    return leading // 2


def _extract_bullet(text: str) -> tuple[bool, str]:
    stripped = text.strip()
    if not stripped:
        return False, ""
    for marker in BULLET_PREFIXES:
        if stripped.startswith(marker):
            remainder = stripped[len(marker) :].strip()
            return True, remainder
    return False, stripped


def _is_rtl(text: str) -> bool:
    for char in text:
        code = ord(char)
        if RTL_HEBREW_START <= code <= RTL_HEBREW_END:
            return True
    return False


def is_rtl_text(text: str) -> bool:
    return _is_rtl(text)


def _line_alignment(text: str) -> str:
    return "right" if is_rtl_text(text) else "left"


@dataclass(frozen=True)
class LineInfo:
    kind: str  # "blank", "bullet", "text", "center", "top"
    level: int
    text: str
    display: str
    align: str  # "left", "right", "center", or "top"


@dataclass(frozen=True)
class TextLayout:
    title: str | None
    lines: List[LineInfo]
    metadata: dict[str, str]

    @property
    def body_lines(self) -> List[str]:
        return [line.display for line in self.lines]

    def overlay_text(self) -> str:
        parts: List[str] = []
        if self.title:
            parts.append(self.title)
        body = "\n".join(self.body_lines).strip()
        if body:
            parts.append(body)
        return "\n\n".join(part for part in parts if part).strip()

    def preview_text(self) -> str:
        if self.title:
            return self.title.strip()
        for line in self.lines:
            candidate = line.text.strip()
            if candidate:
                return candidate
        return ""


def load_text_layout(path: Path) -> TextLayout:
    try:
        raw_content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw_content = path.read_text(encoding="utf-8", errors="replace")

    title: str | None = None
    lines: List[LineInfo] = []

    metadata: dict[str, str] = {}
    content_started = False

    for raw_line in raw_content.splitlines():
        if not raw_line.strip():
            if content_started:
                lines.append(LineInfo("blank", 0, "", "", align="right"))
            continue

        stripped = raw_line.strip()
        if not content_started and stripped.startswith("@") and ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.lstrip("@").strip().lower()
            if key:
                metadata[key] = value.strip()
            continue

        content_started = True
        if stripped.startswith("#"):
            token = stripped.lstrip("#").strip()
            if title is None and token:
                title = token
                continue
            stripped = token
            if stripped:
                lines.append(LineInfo("top", 0, stripped, stripped, align="top"))
            continue

        level = _indent_level(raw_line)
        is_bullet, bullet_text = _extract_bullet(stripped)
        if is_bullet:
            text = bullet_text if bullet_text else "-"
            align = _line_alignment(text)
            if align == "right":
                display = f"{text}\u00A0•"
            else:
                display = f"•\u00A0{text}"
            lines.append(LineInfo("bullet", level, text, display, align=align))
        else:
            display = stripped
            align = _line_alignment(stripped)
            lines.append(LineInfo("text", level, stripped, display, align=align))

    if title is None and not any(line.text for line in lines if line.kind != "blank"):
        fallback = path.stem
        title = fallback
        lines = []
        fallback_align = _line_alignment(fallback)
        lines.append(LineInfo("text", 0, fallback, fallback, align=fallback_align))

    layout = TextLayout(title=title, lines=lines, metadata=metadata)

    return layout


def combine_overlay_texts(paths: Iterable[Path]) -> TextLayout:
    combined_lines: List[LineInfo] = []
    title: str | None = None
    metadata: dict[str, str] = {}
    for overlay_path in paths:
        layout = load_text_layout(overlay_path)
        if title is None and layout.title:
            title = layout.title
        for key, value in layout.metadata.items():
            if key not in metadata and value:
                metadata[key] = value
        for line in layout.lines:
            if line.kind == "blank":
                continue
            if not line.display.strip():
                continue
            combined_lines.append(line)
    return TextLayout(title=title, lines=combined_lines, metadata=metadata)
