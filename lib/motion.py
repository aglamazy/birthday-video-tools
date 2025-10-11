#!/usr/bin/env python3
"""Utility helpers for deterministic gentle motion on still images."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

EFFECT_SEQUENCE = [
    "zoom_in",
    "zoom_out",
    "pan_right",
    "pan_left",
    "pan_up",
    "pan_down",
    "pan_diag_up_right",
    "pan_diag_down_left",
    "pan_diag_up_left",
    "pan_diag_down_right",
]


@dataclass(frozen=True)
class MotionPlan:
    effect: str
    zoom_start: float
    zoom_end: float
    offset_x_start: float
    offset_x_end: float
    offset_y_start: float
    offset_y_end: float
    duration: float
    fps: int


def select_motion(
    index: int,
    duration: float,
    fps: int,
    effects: Optional[Sequence[str]] = None,
) -> Optional[MotionPlan]:
    if duration <= 0 or fps <= 0:
        return None
    playlist = [effect for effect in (effects or EFFECT_SEQUENCE) if effect in EFFECT_SEQUENCE]
    if not playlist:
        return None
    effect = playlist[(index - 1) % len(playlist)]
    return _plan_for_effect(effect, duration, fps)


def _plan_for_effect(effect: str, duration: float, fps: int) -> Optional[MotionPlan]:
    base_zoom = 1.045
    medium_zoom = 1.055
    gentle_shift = 0.35  # fraction of available margin (0..1)
    diagonal_shift = 0.25

    if effect == "zoom_in":
        return MotionPlan(effect, 1.0, base_zoom, 0.0, 0.0, 0.0, 0.0, duration, fps)
    if effect == "zoom_out":
        return MotionPlan(effect, base_zoom, 1.0, 0.0, 0.0, 0.0, 0.0, duration, fps)
    if effect == "pan_right":
        return MotionPlan(effect, medium_zoom, medium_zoom, -gentle_shift, gentle_shift, 0.0, 0.0, duration, fps)
    if effect == "pan_left":
        return MotionPlan(effect, medium_zoom, medium_zoom, gentle_shift, -gentle_shift, 0.0, 0.0, duration, fps)
    if effect == "pan_up":
        return MotionPlan(effect, medium_zoom, medium_zoom, 0.0, 0.0, gentle_shift, -gentle_shift, duration, fps)
    if effect == "pan_down":
        return MotionPlan(effect, medium_zoom, medium_zoom, 0.0, 0.0, -gentle_shift, gentle_shift, duration, fps)
    if effect == "pan_diag_up_right":
        return MotionPlan(effect, medium_zoom, medium_zoom, -diagonal_shift, diagonal_shift, diagonal_shift, -diagonal_shift, duration, fps)
    if effect == "pan_diag_down_left":
        return MotionPlan(effect, medium_zoom, medium_zoom, diagonal_shift, -diagonal_shift, -diagonal_shift, diagonal_shift, duration, fps)
    if effect == "pan_diag_up_left":
        return MotionPlan(effect, medium_zoom, medium_zoom, diagonal_shift, -diagonal_shift, diagonal_shift, -diagonal_shift, duration, fps)
    if effect == "pan_diag_down_right":
        return MotionPlan(effect, medium_zoom, medium_zoom, -diagonal_shift, diagonal_shift, -diagonal_shift, diagonal_shift, duration, fps)
    # Fallback
    return MotionPlan(effect, 1.0, base_zoom, 0.0, 0.0, 0.0, 0.0, duration, fps)


def build_motion_filter(plan: MotionPlan, width: int, height: int) -> Optional[str]:
    total_frames = max(int(round(plan.duration * plan.fps)), 1)
    progress_frames = max(total_frames - 1, 1)
    progress_expr = f"if(gte(on,{progress_frames}),1,(1-cos(PI*on/{progress_frames}))/(2))"

    zoom_delta = plan.zoom_end - plan.zoom_start
    if abs(zoom_delta) < 1e-4:
        zoom_expr = f"{plan.zoom_start:.6f}"
    else:
        zoom_expr = f"{plan.zoom_start:.6f} + ({zoom_delta:.6f})*{progress_expr}"

    def _offset_expression(start: float, end: float) -> str:
        delta = end - start
        if abs(delta) < 1e-4:
            if abs(start) < 1e-4:
                return "0"
            return f"{start:.6f}"
        body = f"{start:.6f} + ({delta:.6f})*{progress_expr}"
        return f"{body}"

    offset_x_expr = _offset_expression(plan.offset_x_start, plan.offset_x_end)
    offset_y_expr = _offset_expression(plan.offset_y_start, plan.offset_y_end)

    x_expr = f"(iw*zoom-{width})/2 + ({offset_x_expr})*(iw*zoom-{width})"
    y_expr = f"(ih*zoom-{height})/2 + ({offset_y_expr})*(ih*zoom-{height})"

    return (
        "zoompan="
        f"z='{zoom_expr}':"
        f"x='{x_expr}':"
        f"y='{y_expr}':"
        "d=1:"
        f"s={width}x{height}:"
        f"fps={plan.fps}"
    )
