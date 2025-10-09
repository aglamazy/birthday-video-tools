from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

BULLET_PREFIXES = ("•", "-", "*")


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


@dataclass(frozen=True)
class LineInfo:
    kind: str  # "blank", "bullet", "text", "center", "top"
    level: int
    text: str
    display: str
    align: str  # "right", "center", or "top"


@dataclass(frozen=True)
class TextLayout:
    title: str | None
    lines: List[LineInfo]

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

    for raw_line in raw_content.splitlines():
        if not raw_line.strip():
            lines.append(LineInfo("blank", 0, "", "", align="right"))
            continue

        stripped = raw_line.strip()
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
            display = f"{text}\u00A0•"
            lines.append(LineInfo("bullet", level, text, display, align="right"))
        else:
            display = stripped
            lines.append(LineInfo("text", level, stripped, display, align="right"))

    if title is None and not any(line.text for line in lines if line.kind != "blank"):
        fallback = path.stem
        title = fallback
        lines = []
        lines.append(LineInfo("text", 0, fallback, fallback, align="right"))

    layout = TextLayout(title=title, lines=lines)

    return layout


def combine_overlay_texts(paths: Iterable[Path]) -> TextLayout:
    combined_lines: List[LineInfo] = []
    for overlay_path in paths:
        layout = load_text_layout(overlay_path)
        for line in layout.lines:
            if line.kind == "blank":
                continue
            if not line.display.strip():
                continue
            combined_lines.append(line)
    return TextLayout(title=None, lines=combined_lines)
