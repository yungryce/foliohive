"""AI helpers package for shared module.

Keep imports light here — import heavy ML dependencies inside functions.
"""

from foliohive_shared.ai.ai_assistant import AIAssistant
from foliohive_shared.ai.summary_manager import SummaryManager

__all__ = ["AIAssistant", "SummaryManager", "ai_assistant", "summary_manager"]
