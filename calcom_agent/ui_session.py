"""Session-owned UI state. No Streamlit dependency or global resource cache."""

from dataclasses import dataclass, field
from threading import Lock
from uuid import uuid4
import weakref

from .client import CalcomClient
from .config import load_settings
from .conversation import Conversation
from .ollama import OllamaProvider
from .diagnostics import diagnostic


@dataclass
class ChatSession:
    conversation: Conversation = field(repr=False)
    messages: list[dict] = field(default_factory=list, repr=False)
    _queued: tuple[str, str] | None = field(default=None, repr=False)
    _seen: set[str] = field(default_factory=set, repr=False)
    _processing: bool = field(default=False, repr=False)
    _lock: Lock = field(default_factory=Lock, repr=False)

    @property
    def busy(self):
        return self._queued is not None or self._processing

    @property
    def has_queued(self):
        return self._queued is not None

    def enqueue(self, text: str, *, request_id: str | None = None) -> bool:
        """Claim a submission once; a rerun must not re-enqueue an event."""
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            return False
        with self._lock:
            event_id = request_id or uuid4().hex
            if self.busy or event_id in self._seen:
                return False
            self._seen.add(event_id)
            self._queued = (event_id, text)
            self.messages.append({"role": "user", "content": text})
            return True

    def process(self) -> bool:
        with self._lock:
            if self._queued is None or self._processing:
                return False
            _, text = self._queued
            self._queued = None  # Consume before calling external code.
            self._processing = True
        answer = None
        details = []
        try:
            answer = self.conversation.turn(text)
            details = list(self.conversation.diagnostics)
        except Exception:
            # Never render/log arbitrary exceptions: they may include request data.
            self.conversation.reset()
            details = [diagnostic("application", "unexpected_error")]
            answer = "The request could not be completed. Conversation context was reset; check your bookings before trying again."
        finally:
            with self._lock:
                if answer is None:
                    self.conversation.reset()
                    details = [diagnostic("application", "request_interrupted")]
                    answer = "The request was interrupted and was not replayed. Conversation context was reset."
                self.messages.append({"role": "assistant", "content": answer, "diagnostics": details,
                                      "model_outputs": list(self.conversation.model_outputs)})
                self._processing = False
        return True

    def reset(self) -> bool:
        with self._lock:
            if self.busy:
                return False
            self.conversation.reset()
            self.messages.clear()
            # Retain consumed IDs for this browser session so old events cannot replay.
            return True


def create_session() -> ChatSession:
    """Read private configuration only when the app creates a browser session."""
    settings = load_settings()
    client = CalcomClient(settings)
    provider = OllamaProvider(settings.ollama_model, settings.ollama_base_url,
                              timeout_seconds=settings.ollama_timeout_seconds)
    session = ChatSession(Conversation(provider, client, executor=client, time_zone=settings.default_timezone))
    # Release session-owned connections when Streamlit releases the session.
    weakref.finalize(session, provider.close)
    weakref.finalize(session, client.close)
    return session
