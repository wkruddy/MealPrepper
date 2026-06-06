from __future__ import annotations

import logging
from dataclasses import dataclass, field

from mealprepper.context.budget import CallType, ContextBudget

logger = logging.getLogger(__name__)


@dataclass
class PromptSection:
    name: str
    content: str
    priority: int = 50  # lower = kept first when trimming


@dataclass
class PromptBuilder:
    """Assemble minimal prompts: system + task + retrieved chunks within budget."""

    budget: ContextBudget
    call_type: CallType = CallType.DEFAULT
    system: str = ""
    task: str = ""
    sections: list[PromptSection] = field(default_factory=list)

    def add_section(self, name: str, content: str, *, priority: int = 50) -> None:
        if content.strip():
            self.sections.append(PromptSection(name=name, content=content.strip(), priority=priority))

    def build_messages(self) -> list[dict[str, str]]:
        user_content = self._assemble_user()
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": user_content},
        ]

    def build_user_prompt(self) -> str:
        return self._assemble_user()

    def total_chars(self) -> int:
        return len(self.system) + len(self._assemble_user())

    def _assemble_user(self) -> str:
        limit = self.budget.limit_for(self.call_type)
        system_reserve = min(len(self.system), limit // 4)
        user_limit = max(256, limit - system_reserve)

        parts: list[str] = []
        if self.task:
            parts.append(self.task)

        sorted_sections = sorted(self.sections, key=lambda s: s.priority)
        for section in sorted_sections:
            block = f"\n\n## {section.name}\n{section.content}"
            if sum(len(p) for p in parts) + len(block) <= user_limit:
                parts.append(block)
            else:
                remaining = user_limit - sum(len(p) for p in parts) - len(f"\n\n## {section.name}\n")
                if remaining > 80:
                    trimmed = section.content[: remaining - 20] + "\n...[section trimmed]"
                    parts.append(f"\n\n## {section.name}\n{trimmed}")
                logger.debug("Dropped/truncated section %s for %s", section.name, self.call_type.value)

        result = "".join(parts).strip()
        if len(result) > user_limit:
            result = self.budget.truncate(result, self.call_type, label="user prompt")
        return result
