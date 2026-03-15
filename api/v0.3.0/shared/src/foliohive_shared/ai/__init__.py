"""AI helpers package for shared module.

Keep imports light here — import heavy ML dependencies inside functions.
"""

__all__ = ["AIAssistant", "AIServiceError", "SummaryManager", "AIUsageTracker", "FILE_BUDGETS", "get_file_budget"]


def __getattr__(name: str):
    """Lazy loading for AI modules to avoid early dependency loading."""
    if name == "AIAssistant":
        from foliohive_shared.ai.ai_assistant import AIAssistant
        return AIAssistant
    if name == "AIServiceError":
        from foliohive_shared.ai.ai_assistant import AIServiceError
        return AIServiceError
    if name == "SummaryManager":
        from foliohive_shared.ai.summary_manager import SummaryManager
        return SummaryManager
    if name == "AIUsageTracker":
        from foliohive_shared.ai.api_usage import AIUsageTracker
        return AIUsageTracker
    if name == "FILE_BUDGETS":
        from foliohive_shared.ai.summary_manager import FILE_BUDGETS
        return FILE_BUDGETS
    if name == "get_file_budget":
        from foliohive_shared.ai.summary_manager import get_file_budget
        return get_file_budget
    raise AttributeError(f"module 'foliohive_shared.ai' has no attribute '{name}'")
