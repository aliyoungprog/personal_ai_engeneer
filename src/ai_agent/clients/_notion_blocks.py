"""Render Notion block trees to Markdown.

Covers the block types that actually appear in our Tasks DB pages
(headings, paragraphs, lists, todos, quotes, callouts, code, dividers).
Unknown block types are skipped silently.
"""

from __future__ import annotations

from typing import Any

# block_type -> (prefix, suffix) wrappers for the rendered rich-text line
_LINE_BLOCKS: dict[str, tuple[str, str]] = {
    "heading_1": ("# ", ""),
    "heading_2": ("## ", ""),
    "heading_3": ("### ", ""),
    "paragraph": ("", ""),
    "bulleted_list_item": ("- ", ""),
    "quote": ("> ", ""),
    "callout": ("> ", ""),
}


def _rich_text(block_inner: dict[str, Any]) -> str:
    parts = block_inner.get("rich_text", [])
    return "".join(p.get("plain_text", "") for p in parts)


def render_blocks(blocks: list[dict[str, Any]], depth: int = 0) -> list[str]:
    """Render a flat list of block dicts (with optional pre-attached _children)."""

    lines: list[str] = []
    numbered_idx = 0
    indent = "  " * depth

    for block in blocks:
        btype = block.get("type", "")
        inner = block.get(btype, {})

        if btype == "numbered_list_item":
            numbered_idx += 1
            text = _rich_text(inner)
            lines.append(f"{indent}{numbered_idx}. {text}")
        elif btype == "to_do":
            numbered_idx = 0
            checked = "x" if inner.get("checked") else " "
            lines.append(f"{indent}- [{checked}] {_rich_text(inner)}")
        elif btype == "code":
            numbered_idx = 0
            lang = inner.get("language", "")
            lines.append(f"{indent}```{lang}")
            lines.append(f"{indent}{_rich_text(inner)}")
            lines.append(f"{indent}```")
        elif btype == "divider":
            numbered_idx = 0
            lines.append(f"{indent}---")
        elif btype in _LINE_BLOCKS:
            numbered_idx = 0
            prefix, suffix = _LINE_BLOCKS[btype]
            text = _rich_text(inner)
            if text or btype == "paragraph":
                lines.append(f"{indent}{prefix}{text}{suffix}")
        else:
            numbered_idx = 0
            text = _rich_text(inner)
            if text:
                lines.append(f"{indent}{text}")

        children = block.get("_children")
        if children:
            lines.extend(render_blocks(children, depth + 1))

    return lines


def blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
    lines = render_blocks(blocks)
    # collapse 3+ blank lines to a single blank line
    out: list[str] = []
    blank = 0
    for line in lines:
        if line.strip() == "":
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(line)
    return "\n".join(out).strip()
