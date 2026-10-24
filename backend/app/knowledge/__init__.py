"""
정적 지식팩（한국 시장·정책 컨텍스트 등）로더
"""

import os
from functools import lru_cache

_KNOWLEDGE_DIR = os.path.dirname(__file__)


@lru_cache(maxsize=None)
def load_korea_business_context() -> str:
    """한국 시장·정책 컨텍스트 Markdown을 읽는다（없으면 빈 문자열）"""
    path = os.path.join(_KNOWLEDGE_DIR, "korea_business_context.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""
