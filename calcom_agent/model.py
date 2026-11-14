"""Provider-neutral model boundary. No settings objects or scheduling imports."""

from dataclasses import dataclass
from typing import Protocol


class ModelError(Exception):
    """Safe failure code; never contains a provider response or request."""

    def __init__(self, code: str, *, details: dict | None = None):
        super().__init__(code)
        self.details = details or {}


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict


@dataclass(frozen=True)
class ModelReply:
    content: str = ""
    calls: tuple[ToolCall, ...] = ()
    incomplete: bool = False


class ModelProvider(Protocol):
    def chat(self, messages: list[dict], tools: list[dict]) -> ModelReply: ...
