"""Extension registries for professional MCP presentation surfaces."""

from .audit_effort import DETAIL_RENDERERS as AUDIT_EFFORT_DETAILS
from .audit_effort import PACK_BUILDERS as AUDIT_EFFORT_PACKS
from .audit_effort import CONCLUSION_OVERRIDES as AUDIT_EFFORT_CONCLUSIONS
from .auditor import DETAIL_RENDERERS as AUDITOR_DETAILS
from .auditor import PACK_BUILDERS as AUDITOR_PACKS
from .investor import DETAIL_RENDERERS as INVESTOR_DETAILS
from .investor import PACK_BUILDERS as INVESTOR_PACKS

PACK_BUILDERS = {
    **AUDIT_EFFORT_PACKS,
    **AUDITOR_PACKS,
    **INVESTOR_PACKS,
}
DETAIL_RENDERERS = {
    **AUDIT_EFFORT_DETAILS,
    **AUDITOR_DETAILS,
    **INVESTOR_DETAILS,
}
CONCLUSION_OVERRIDES = {
    **AUDIT_EFFORT_CONCLUSIONS,
}
