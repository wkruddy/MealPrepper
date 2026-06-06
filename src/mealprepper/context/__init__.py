from mealprepper.context.budget import CallType, ContextBudget, load_context_budget

__all__ = [
    "CallType",
    "ContextBudget",
    "load_context_budget",
]


def __getattr__(name: str):
    """Lazy exports avoid circular imports with llm.ollama_client."""
    if name == "ContextCompressor":
        from mealprepper.context.compressor import ContextCompressor

        return ContextCompressor
    if name in ("PromptBuilder", "PromptSection"):
        from mealprepper.context.prompt_builder import PromptBuilder, PromptSection

        return PromptBuilder if name == "PromptBuilder" else PromptSection
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
