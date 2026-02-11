"""AI helpers package for shared module.

Keep imports light here — import heavy ML dependencies inside functions.
"""

from foliohive_shared.ai.ai_assistant import AIAssistant
from foliohive_shared.ai.summary_manager import SummaryManager, FILE_BUDGETS, get_file_budget

__all__ = ["AIAssistant", "SummaryManager", "FILE_BUDGETS", "get_file_budget", "ai_assistant", "summary_manager"]
