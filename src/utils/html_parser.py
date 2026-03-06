from __future__ import annotations

from lxml import html


def parse_html_tree(content: str):
    return html.fromstring(content)

