"""Compat shim para substituir o pacote `iaragenai` por chamadas ao OpenAI.

Este módulo fornece uma implementação mínima das interfaces usadas pelo
projeto (IaraGenAI, BatchPresignedUrlEnvironment, submódulos de types) e
encaminha chamadas de completions para a API OpenAI usando a chave
`OPENAI_ADMIN_KEY` (fallbacks: `OPENAI_API_KEY`).

Observação: este shim tenta importar o pacote oficial `openai`. Se não
estiver instalado, o erro explicará como instalar.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Any

try:
    import openai
except Exception:  # pragma: no cover - runtime dependency
    openai = None


def _openai_api_key() -> str | None:
    return os.getenv("OPENAI_ADMIN_KEY") or os.getenv("OPENAI_API_KEY")


class _Function:
    """Formato mínimo de function call esperado pelo motor de agentes."""

    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    """Normaliza a tool call retornada pelo SDK oficial da OpenAI."""

    def __init__(self, call_id: str, name: str, arguments: str):
        self.id = call_id
        self.function = _Function(name, arguments)


class _Message:
    def __init__(self, content: str | None, tool_calls: list[_ToolCall] | None = None):
        self.content = content
        self.tool_calls = tool_calls or []


class _Choice:
    def __init__(self, message: _Message):
        self.message = message


class _Response:
    def __init__(self, choices: list[_Choice]):
        self.choices = choices


class _Completions:
    def create(self, **kwargs: Any) -> _Response:
        # Minimal adapter: support both old and new openai client APIs.
        if openai is None:
            raise ImportError(
                "openai package is required by the local iaragenai shim. "
                "Install it with `pip install openai` and set OPENAI_ADMIN_KEY."
            )

        api_key = _openai_api_key()
        if not api_key:
            raise EnvironmentError(
                "OPENAI_ADMIN_KEY or OPENAI_API_KEY must be set in the environment"
            )

        model = kwargs.get("model")
        # Esta instalação usa a API direta da OpenAI. Alguns perfis antigos
        # do banco ainda apontam para IDs Bedrock/Claude; sem o gateway Iara
        # esses IDs retornariam 404. Executa-os com um modelo OpenAI adequado.
        if "claude" in str(model or "").lower() or str(model or "").lower().startswith("anthropic."):
            model = os.getenv("OPENAI_FALLBACK_MODEL", "gpt-5-mini")
        messages = kwargs.get("messages")
        temperature = kwargs.get("temperature")
        # Encaminha somente os parâmetros compatíveis com Chat Completions.
        # ``thinking`` e ``output_config`` são opções do caminho Bedrock e não
        # existem na API direta da OpenAI.
        call_kwargs = {"model": model, "messages": messages}
        # GPT-5 (incluindo gpt-5-mini) só aceita a temperatura padrão no
        # endpoint de Chat Completions. Os agentes guardam temperaturas por
        # perfil (ex.: 0.4 para analista_dados), então omitimos esse parâmetro
        # para que a API aplique o default compatível.
        if temperature is not None and not str(model or "").lower().startswith("gpt-5"):
            call_kwargs["temperature"] = temperature
        if kwargs.get("max_tokens") is not None:
            call_kwargs["max_tokens"] = kwargs.get("max_tokens")
        if kwargs.get("tools"):
            call_kwargs["tools"] = kwargs["tools"]
            call_kwargs["tool_choice"] = kwargs.get("tool_choice", "auto")

        # Use new client when available (openai.OpenAI), else fallback to
        # legacy `openai.ChatCompletion.create`.
        resp = None
        try:
            # Ensure environment and legacy global key are set for both APIs.
            # New OpenAI SDKs read OPENAI_API_KEY from env; legacy uses openai.api_key.
            try:
                os.environ.setdefault("OPENAI_ADMIN_KEY", api_key)
                os.environ.setdefault("OPENAI_API_KEY", api_key)
            except Exception:
                pass
            try:
                openai.api_key = api_key
            except Exception:
                pass

            if hasattr(openai, "OpenAI"):
                # Pass api_key explicitly to new client to satisfy auth resolution.
                # Um timeout explícito evita que uma chamada de chat bloqueie
                # indefinidamente a thread SSE quando a rede estiver instável.
                # Relatórios com várias tools podem exigir uma síntese longa
                # após as análises. Três minutos evitam abortar essa etapa,
                # sem deixar a requisição aberta indefinidamente.
                client = openai.OpenAI(api_key=api_key, timeout=180.0, max_retries=1)
                resp = client.chat.completions.create(**call_kwargs)
            else:
                resp = openai.ChatCompletion.create(**call_kwargs)
        except Exception as e:
            # Wrap common network/auth issues with clearer guidance
            msg = (
                f"OpenAI request failed: {e}.\n"
                "Check network/proxy settings and that OPENAI_ADMIN_KEY/OPENAI_API_KEY is valid."
            )
            raise ConnectionError(msg) from e

        # Normalize response to attribute-style access expected by code
        try:
            # resp may be a mapping-like or object with attributes
            choices_raw = None
            if hasattr(resp, "get"):
                choices_raw = resp.get("choices")
            else:
                choices_raw = getattr(resp, "choices", None)

            choices = []
            if isinstance(choices_raw, list):
                for c in choices_raw:
                    # c may be mapping-like or object
                    msg = c.get("message") if isinstance(c, dict) else getattr(c, "message", None)
                    if msg is None:
                        # Some clients return nested dict under 'message'
                        msg = c
                    content = None
                    if isinstance(msg, dict):
                        content = msg.get("content")
                        raw_calls = msg.get("tool_calls") or []
                    else:
                        content = getattr(msg, "content", None)
                        raw_calls = getattr(msg, "tool_calls", None) or []

                    tool_calls = []
                    for tc in raw_calls:
                        if isinstance(tc, dict):
                            fn = tc.get("function") or {}
                            call_id = tc.get("id", "")
                            name = fn.get("name", "")
                            arguments = fn.get("arguments", "{}")
                        else:
                            fn = getattr(tc, "function", None)
                            call_id = getattr(tc, "id", "")
                            name = getattr(fn, "name", "")
                            arguments = getattr(fn, "arguments", "{}")
                        if name:
                            tool_calls.append(_ToolCall(call_id, name, arguments))

                    choices.append(_Choice(_Message(content, tool_calls)))
            return _Response(choices)
        except Exception:
            return _Response([])


class _Chat:
    def __init__(self) -> None:
        self.completions = _Completions()


class _Models:
    def list(self):
        # Try to return OpenAI model list using either new or old API
        if openai is None:
            return []
        api_key = _openai_api_key()
        try:
            try:
                os.environ.setdefault("OPENAI_ADMIN_KEY", api_key)
                os.environ.setdefault("OPENAI_API_KEY", api_key)
            except Exception:
                pass
            try:
                openai.api_key = api_key
            except Exception:
                pass
            if hasattr(openai, "OpenAI"):
                client = openai.OpenAI(api_key=api_key)
                return client.models.list()
            return openai.Model.list()
        except Exception:
            return []


class IaraGenAI:
    """Shim replacement for the original IaraGenAI SDK class.

    Signature attempts to be compatible with calls in the project. Most
    parameters are accepted but ignored; this object exposes `.chat` and
    `.models` with minimal behavior.
    """

    def __init__(self, *args, **kwargs) -> None:
        self.chat = _Chat()
        self.models = _Models()


class BatchPresignedUrlEnvironment(Enum):
    LOCAL = "LOCAL"
    CLOUD = "CLOUD"


# Expose a tiny apis.resources.datafoundation_api.types compatibility module
# used by some tools; real functionality is not necessary for the import
# (the code only needs reference classes that can be instantiated).
class _KBRefBase:
    def __init__(self, *args, **kwargs):
        # accept any signature
        pass


class SimilaritySearchKnowledgeBaseVersionReference(_KBRefBase):
    pass


class KnowledgeBaseVersionReference(_KBRefBase):
    pass


class SimilaritySearchKnowledgeBaseReference(_KBRefBase):
    pass


class KnowledgeBaseReference(_KBRefBase):
    pass


# Provide a small attribute used by other modules when they `from ... import types`
class _TypesModule:
    SimilaritySearchKnowledgeBaseVersionReference = SimilaritySearchKnowledgeBaseVersionReference
    KnowledgeBaseVersionReference = KnowledgeBaseVersionReference
    SimilaritySearchKnowledgeBaseReference = SimilaritySearchKnowledgeBaseReference
    KnowledgeBaseReference = KnowledgeBaseReference


# Make `from iaragenai.apis.resources.datafoundation_api import types` work by
# injecting a package-like object into this module under the attribute
# `apis`/`resources`/`datafoundation_api` at import time if code asks for it.
class _DataFoundationAPI:
    types = _TypesModule()


# Minimal `apis` package object
class _APIs:
    def __init__(self):
        self.resources = type("R", (), {"datafoundation_api": _DataFoundationAPI()})()


# Expose a top-level `apis` attribute so imports like
# `from iaragenai.apis.resources.datafoundation_api import types as _types`
# succeed (they import the module and then access `.types`).
apis = _APIs()
