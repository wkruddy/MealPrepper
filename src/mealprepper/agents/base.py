from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from mealprepper.llm.ollama_client import OllamaClient

logger = logging.getLogger(__name__)


ToolFn = Callable[..., Any]


@dataclass
class AgentTool:
    name: str
    description: str
    handler: ToolFn


@dataclass
class AgentResult:
    success: bool
    message: str
    data: Any = None


class BaseAgent:
    name: str = "base"
    system_prompt: str = ""

    def __init__(self, llm: OllamaClient | None = None) -> None:
        self.llm = llm or OllamaClient()
        self.tools: dict[str, AgentTool] = {}
        self._register_tools()

    def _register_tools(self) -> None:
        pass

    def register_tool(self, name: str, description: str, handler: ToolFn) -> None:
        self.tools[name] = AgentTool(name=name, description=description, handler=handler)

    def run_tool(self, name: str, **kwargs: Any) -> Any:
        if name not in self.tools:
            raise KeyError(f"Unknown tool: {name}")
        logger.info("[%s] running tool: %s", self.name, name)
        return self.tools[name].handler(**kwargs)

    def tool_descriptions(self) -> str:
        return "\n".join(f"- {t.name}: {t.description}" for t in self.tools.values())

    def think(self, user_message: str) -> str:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message},
        ]
        return self.llm.chat(messages, temperature=0.3)
