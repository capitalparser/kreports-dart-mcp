from collections.abc import Callable
from typing import Any

PackBuilder = Callable[[dict[str, Any]], dict[str, Any] | None]
DetailRenderer = Callable[[dict[str, Any]], str]

PACK_BUILDERS: dict[str, PackBuilder] = {}
DETAIL_RENDERERS: dict[str, DetailRenderer] = {}
