"""Unit tests for rendering an MR markdown description into Telegram HTML."""

from __future__ import annotations

from ai_agent.clients.telegram_client import _md_to_tg_html


def test_headings_bold_code_and_links_render() -> None:
    md = (
        "### Task: T-1 — fix\n"
        "Notion: https://n/1\n"
        "- `app/foo.py`\n"
        "- **bold** point\n"
        "[MR link](https://git/mr/1)\n"
        "---"
    )
    html = _md_to_tg_html(md)
    assert "<b>Task: T-1 — fix</b>" in html
    assert "• <code>app/foo.py</code>" in html
    assert "• <b>bold</b> point" in html
    assert '<a href="https://git/mr/1">MR link</a>' in html
    assert "➖➖➖" in html


def test_user_html_is_escaped() -> None:
    html = _md_to_tg_html("plain <script>alert(1)</script> & co")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp; co" in html


def test_long_body_is_truncated() -> None:
    html = _md_to_tg_html("x\n" * 5000)
    assert len(html) <= 3502
    assert html.endswith("…")
