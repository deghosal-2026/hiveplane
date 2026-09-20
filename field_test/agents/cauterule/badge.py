"""Shields.io-style SVG badge for 'N rules learned'."""

from __future__ import annotations

BADGE_TEMPLATE = """\
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20">
  <linearGradient id="smooth" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="100%" stop-opacity=".1"/>
  </linearGradient>
  <mask id="rnd"><rect width="{width}" height="20" rx="3" fill="#fff"/></mask>
  <g mask="url(#rnd)">
    <rect width="{left_w}" height="20" fill="#555"/>
    <rect x="{left_w}" width="{right_w}" height="20" fill="#4c1"/>
    <rect width="{width}" height="20" fill="url(#smooth)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana" font-size="11">
    <text x="{left_cx}" y="14">{label}</text>
    <text x="{right_cx_abs}" y="14">{count}</text>
  </g>
</svg>"""


def badge_svg(count: int, label: str = "rules") -> str:
    """Return an SVG badge showing the number of learned rules.

    Args:
        count: Number of rules learned.
        label: Left-side label.

    Returns:
        SVG string suitable for shields.io-style badges.
    """
    label = label or "rules"
    label_w = _text_width(label)
    count_str = str(count) if count >= 0 else "0"
    count_w = _text_width(count_str)
    padding = 6
    left_w = label_w + padding
    right_w = count_w + padding
    width = left_w + right_w
    left_cx = left_w // 2
    right_cx = right_w // 2

    return BADGE_TEMPLATE.format(
        width=width,
        left_w=left_w,
        right_w=right_w,
        left_cx=left_cx,
        right_cx_abs=left_w + right_cx,
        label=label,
        count=count_str,
    )


def _text_width(text: str) -> int:
    """Approximate pixel width of text in Verdana 11px."""
    # rough: each char ~6px on average
    return max(len(text) * 6, 10)
