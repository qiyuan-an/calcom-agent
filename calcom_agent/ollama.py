"""Ollama /api/chat adapter; plain HTTPX avoids another SDK dependency.

Contract: https://docs.ollama.com/api/chat and /capabilities/tool-calling.
"""

import httpx
import time
import re

from .model import ModelError, ModelReply, ToolCall


class OllamaProvider:
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", *,
                 transport: httpx.BaseTransport | None = None, timeout_seconds: float = 120):
        self._model = model
        self._thinking = False
        self._max_output_tokens = 1024
        self._context_tokens = 8192
        self._generation_configured = False
        if type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 600:
            raise ValueError("Invalid model timeout")
        self._read_timeout = timeout_seconds
        # Accept common copied endpoint forms without constructing /api/api/chat.
        url = httpx.URL(base_url)
        path = url.path.rstrip("/")
        for suffix in ("/api/chat", "/api", "/v1"):
            if path.endswith(suffix):
                path = path[:-len(suffix)]
                break
        url = url.copy_with(path=path + "/")
        self._http = httpx.Client(base_url=url, timeout=httpx.Timeout(timeout_seconds, connect=5, pool=5, write=10),
                                  follow_redirects=False, trust_env=False, transport=transport)

    def close(self):
        self._http.close()

    def connection_settings(self) -> dict:
        url = self._http.base_url
        origin = httpx.URL(scheme=url.scheme, host=url.host, port=url.port)
        return {"model": self._model, "server": str(origin), "read_timeout_seconds": self._read_timeout}

    def runtime_snapshot(self) -> dict:
        """Selected model's residency only; omit all other model/server data."""
        try:
            response = self._http.get("api/ps", timeout=2)
            if response.status_code != 200:
                return {"runtime_check": "unavailable"}
            rows = response.json()["models"]
            if not isinstance(rows, list):
                return {"runtime_check": "unavailable"}
            for row in rows:
                if not isinstance(row, dict) or row.get("name") not in {self._model, self._model + ":latest"}:
                    continue
                size, vram = row.get("size"), row.get("size_vram")
                result = {"runtime_check": "available", "model_loaded": True}
                if type(size) is int and size > 0 and type(vram) is int and vram >= 0:
                    result.update(model_size_gib=round(size / 2**30, 2), vram_gib=round(vram / 2**30, 2),
                                  placement="cpu_only" if vram == 0 else "gpu_or_mixed")
                return result
            return {"runtime_check": "available", "model_loaded": False}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {"runtime_check": "unavailable"}

    def check_connection(self) -> dict:
        """Read model metadata from this session's actual client, without prompts."""
        result = self.connection_settings()
        failure = None
        try:
            response = self._http.get("api/tags", timeout=5)
        except httpx.TimeoutException:
            failure = "model_timeout"
        except httpx.HTTPError:
            failure = "model_connection_failed"
        if failure:
            return {**result, "status": failure}
        if response.status_code != 200:
            return {**result, "status": f"model_http_error_{response.status_code}"}
        try:
            rows = response.json()["models"]
            if not isinstance(rows, list) or not all(isinstance(row, dict) and isinstance(row.get("name"), str) for row in rows):
                raise ValueError()
            names = {row["name"] for row in rows}
            installed = self._model in names or self._model + ":latest" in names
        except (ValueError, KeyError, TypeError):
            return {**result, "status": "invalid_model_response"}
        return {**result, "status": "ready" if installed else "model_not_installed"}

    def _configure_generation(self):
        if self._generation_configured:
            return
        # This installed alias can point to a thinking-only Qwen release.
        # Respect server metadata instead of disabling unsupported thinking.
        if self._model.startswith("qwen3:30b-a3b"):
            try:
                response = self._http.post("api/show", json={"model": self._model}, timeout=5)
                if response.status_code == 200 and response.json().get("thinking", {}).get("values") == [True]:
                    self._thinking = True
                    self._max_output_tokens = 4096
                    self._context_tokens = 16384
            except (httpx.HTTPError, ValueError, AttributeError):
                pass
        self._generation_configured = True

    def chat(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        self._configure_generation()
        started = time.monotonic()
        details = {**self.connection_settings(), "method": "POST", "path": "/api/chat",
                   "connect_timeout_seconds": 5, "write_timeout_seconds": 10, "pool_timeout_seconds": 5,
                   "message_count": len(messages), "tool_count": len(tools),
                   "context_tokens": self._context_tokens, "max_output_tokens": self._max_output_tokens,
                   "streaming": False, "thinking": self._thinking, "automatic_retries": 0}
        failure = None
        try:
            response = self._http.post("api/chat", json={
                "model": self._model, "messages": messages, "tools": tools,
                "stream": False, "think": self._thinking,
                "options": {"temperature": 0, "num_ctx": self._context_tokens, "num_predict": self._max_output_tokens},
            })
        except httpx.TimeoutException as error:
            failure = "model_timeout"
            details["timeout_phase"] = {httpx.ConnectTimeout: "connect", httpx.ReadTimeout: "read",
                                        httpx.WriteTimeout: "write", httpx.PoolTimeout: "pool"}.get(type(error), "unknown")
        except httpx.ConnectError:
            failure = "model_connection_failed"
        except httpx.RemoteProtocolError:
            failure = "model_connection_dropped"
        except httpx.HTTPError:
            failure = "model_unavailable"
        if failure:
            details["elapsed_seconds"] = round(time.monotonic() - started, 2)
            details.update(self.runtime_snapshot())
            raise ModelError(failure, details=details)
        if response.status_code != 200:
            details["elapsed_seconds"] = round(time.monotonic() - started, 2)
            details["http_status"] = response.status_code
            # Inspect for fixed categories, never retain or display server text.
            try:
                raw_error = response.json().get("error", "")
            except (ValueError, AttributeError):
                raw_error = ""
            if isinstance(raw_error, str):
                categories = {"memory_allocation": r"out of memory|allocat|not enough.*memory",
                              "gpu_or_device": r"cuda|cublas|gpu|device",
                              "runner_process": r"runner|process.*exit|crash",
                              "context_limit": r"context.*(length|limit)|too many tokens"}
                details["server_error_categories"] = [key for key, pattern in categories.items()
                                                     if re.search(pattern, raw_error, re.I)]
            details.update(self.runtime_snapshot())
            if response.status_code == 404 and self._model_is_missing():
                raise ModelError("model_not_installed", details=details)
            raise ModelError(f"model_http_error_{response.status_code}", details=details)
        try:
            body = response.json()
            message = body["message"]
            if body.get("done") is not True or message.get("role") != "assistant":
                raise ValueError()
            content = message.get("content", "")
            calls = message.get("tool_calls", [])
            if not isinstance(content, str) or not isinstance(calls, list) or len(calls) > 8:
                raise ValueError()
            parsed = []
            for call in calls:
                function = call["function"]
                if not isinstance(function["name"], str) or not isinstance(function["arguments"], dict):
                    raise ValueError()
                parsed.append(ToolCall(function["name"], function["arguments"]))
            # Hidden thinking is discarded, including legacy inline tags.
            content = re.sub(r"<think>.*?(?:</think>|$)", "", content, flags=re.S).strip()
            result = ModelReply(content, tuple(parsed), body.get("done_reason") == "length")
        except (KeyError, TypeError, ValueError, AttributeError):
            result = None
        if result is None:
            details["elapsed_seconds"] = round(time.monotonic() - started, 2)
            raise ModelError("invalid_model_response", details=details)
        return result

    def _model_is_missing(self) -> bool:
        """Diagnose 404 using model metadata only; never echo provider content."""
        try:
            response = self._http.get("api/tags", timeout=5)
            if response.status_code != 200:
                return False
            models = response.json()["models"]
            if not isinstance(models, list) or not all(isinstance(row, dict) and isinstance(row.get("name"), str) for row in models):
                return False
            names = {row["name"] for row in models}
            return self._model not in names and self._model + ":latest" not in names
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return False
