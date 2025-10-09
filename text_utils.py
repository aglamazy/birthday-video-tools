from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

HEBREW_RANGES = (
    (0x0590, 0x05FF),
    (0xFB1D, 0xFB4F),
)


def _is_hebrew_char(char: str) -> bool:
    if not char:
        return False
    codepoint = ord(char)
    for start, end in HEBREW_RANGES:
        if start <= codepoint <= end:
            return True
    return False


def _normalize_indent(raw_line: str) -> str:
    return raw_line.replace("\t", "  ")


def _indent_level(raw_line: str) -> int:
    normalized = _normalize_indent(raw_line)
    leading = len(normalized) - len(normalized.lstrip(" "))
    return leading // 2


def _strip_bullet(text: str) -> str:
    return text.lstrip("-").strip()


def detect_text_direction(text: str) -> str:
    hebrew_count = 0
    latin_count = 0
    for char in text:
        if _is_hebrew_char(char):
            hebrew_count += 1
        elif char.isalpha():
            latin_count += 1
    if hebrew_count > 0 and hebrew_count >= latin_count:
        return "rtl"
    return "ltr"


@dataclass(frozen=True)
class TextLayout:
    title: str | None
    body_lines: List[str]
    direction: str  # "ltr" or "rtl"

    def overlay_text(self) -> str:
        parts: List[str] = []
        if self.title:
            parts.append(self.title)
        if self.body_lines:
            parts.append("\n".join(self.body_lines))
        return "\n\n".join(part for part in parts if part).strip()

    def preview_text(self) -> str:
        if self.title:
            return self.title
        for line in self.body_lines:
            candidate = line.strip()
            if candidate:
                return candidate
        return ""


def load_text_layout(path: Path) -> TextLayout:
    try:
        raw_content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw_content = path.read_text(encoding="utf-8", errors="replace")

    title: str | None = None
    body_lines: List[str] = []

    for raw_line in raw_content.splitlines():
        if not raw_line.strip():
            body_lines.append("")
            continue
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            candidate = stripped.lstrip("#").strip()
            if candidate and title is None:
                title = candidate
                continue
            stripped = candidate or stripped.lstrip("#")
        indent = "  " * _indent_level(raw_line)
        if stripped.startswith("-"):
            item_text = _strip_bullet(stripped)
            if not item_text:
                item_text = "-"
            line = f"{indent}• {item_text}"
        else:
            line = f"{indent}{stripped}"
        body_lines.append(line)

    hydrated_text = " ".join(
        part for part in ([title] if title else []) + [line for line in body_lines if line]
    )

    if not title and not any(line.strip() for line in body_lines):
        fallback = path.stem
        title = fallback
        body_lines = []
        hydrated_text = fallback

    direction = detect_text_direction(hydrated_text)
    return TextLayout(title=title, body_lines=body_lines, direction=direction)


def combine_overlay_texts(paths: Iterable[Path]) -> str:
    blocks: List[str] = []
    for overlay_path in paths:
        layout = load_text_layout(overlay_path)
        text = layout.overlay_text()
        if text:
            blocks.append(text)
    return "\n\n".join(blocks).strip()
