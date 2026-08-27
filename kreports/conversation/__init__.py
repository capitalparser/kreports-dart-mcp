"""Conversation orchestration for user-driven KReports workflows.

The conversation package is protocol-neutral. It keeps volatile chat history
out of analytical inputs, binds state to a trusted user/conversation identity,
and exposes deterministic interaction contracts that an MCP v2 adapter or a
custom web chatbot can render.
"""

from kreports.conversation.contracts import (
    ChoiceField,
    ChoiceOption,
    ConversationIdentity,
    ConversationState,
    ContextSnapshot,
    InteractionRequest,
    PeerSelectionPreferences,
    TaskState,
)
from kreports.conversation.orchestrator import (
    PeerConversationOrchestrator,
    PreparedPeerRequest,
)
from kreports.conversation.store import (
    InMemoryConversationStore,
    StateAccessError,
    StateExpiredError,
    StateHandleError,
)

__all__ = [
    "ChoiceField",
    "ChoiceOption",
    "ConversationIdentity",
    "ConversationState",
    "ContextSnapshot",
    "InMemoryConversationStore",
    "InteractionRequest",
    "PeerConversationOrchestrator",
    "PeerSelectionPreferences",
    "PreparedPeerRequest",
    "StateAccessError",
    "StateExpiredError",
    "StateHandleError",
    "TaskState",
]
