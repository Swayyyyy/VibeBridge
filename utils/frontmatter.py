"""Frontmatter parser — port of server/utils/frontmatter.js.

Uses a minimal YAML-only parser instead of pulling in gray-matter.
"""
import re
from typing import Tuple

import yaml


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_frontmatter(content: str) -> Tuple[dict, str]:
    """Return (metadata_dict, body_content) from a markdown file with optional YAML frontmatter."""
    m = _FM_RE.match(content)
    if not m:
        return {}, content

    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        data = {}

    body = content[m.end():]
    return data, body
