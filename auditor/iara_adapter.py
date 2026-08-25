"""Adaptador HTTP: Responses API do Codex  ⇄  SDK IaraGenAI (``client.responses``).

Por quê existe
--------------
O runtime do Codex (``openai-codex``) só sabe conversar com um provedor de
modelo pela **Responses API** (``wire_api = "responses"``) sobre HTTP, apontando
para um ``base_url``. O IARA GenAI é entregue como um **SDK Python**
(``iaragenai``), mas — crucialmente — expõe a **Responses API nativa** em
``client.responses`` (além de ``client.chat.completions``). Este módulo é a
ponte HTTP → SDK:

    Codex  ──POST /v1/responses (SSE)──▶  IaraAdapter  ──iaragenai.client.responses──▶  IARA

Passthrough, não tradução
-------------------------
O adaptador **repassa** o corpo Responses do Codex para ``client.responses`` e
devolve o SSE do IARA **verbatim** (byte-a-byte). Isso é obrigatório: o Codex
0.147 opera em "code mode" e envia sua ferramenta principal como
``functions.exec`` do tipo ``custom`` com ``format: {type: "grammar"}`` (Lark),
dentro de um item ``additional_tools`` no ``input`` — um recurso **nativo da
Responses API** que o Chat Completions **não** consegue representar. Traduzir
para chat.completions quebraria as ferramentas do Codex; o passthrough as
preserva sem tocar.

    (Verificado: mesmo com ``code_mode``/``code_mode_only``/``code_mode_host``
    desligados, o Codex 0.147 continua enviando a tool grammar.)

Requisito de rede
-----------------
O SDK roteia ``/responses`` pelo gateway SSE (``base_url_sse`` = agent-gateway).
O adaptador precisa que esse host seja alcançável a partir de onde o app roda.
As funções puras de tradução (``responses_input_to_messages`` etc.) permanecem
apenas para os testes de unidade; o servidor HTTP usa exclusivamente o
passthrough (``open_iara_responses_stream`` / ``create_iara_response``).

Segurança
---------
- Liga só em ``127.0.0.1`` por padrão.
- Se ``ATENA_IARA_ADAPTER_TOKEN`` estiver setado, exige
  ``Authorization: Bearer <token>`` igual — autenticação interna (guia §9).
- Nunca loga credenciais nem corpo de mensagens (dump de diagnóstico só quando
  ``ATENA_IARA_DEBUG_DUMP`` aponta explicitamente para um arquivo).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterator
from uuid import uuid4


# ════════════════════════════════════════════════════════════════════════
# Tradução Responses  →  Chat Completions   (funções puras / testáveis)
# ════════════════════════════════════════════════════════════════════════


def _content_parts_to_text(content: Any) -> str:
    """Concatena as partes textuais de um ``content`` da Responses API.

    ``content`` pode ser uma string simples ou uma lista de partes como
    ``{"type": "input_text"|"output_text"|"text", "text": "..."}``.
    Partes não-textuais (imagem/áudio) são ignoradas nesta versão.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict):
                if part.get("type") in (None, "input_text", "output_text", "text"):
                    txt = part.get("text")
                    if isinstance(txt, str):
                        out.append(txt)
        return "".join(out)
    return ""


def responses_input_to_messages(
    body: dict,
) -> tuple[list[dict], list[dict] | None, str | None]:
    """Converte o corpo de ``POST /v1/responses`` em (messages, tools, tool_choice).

    Lida com:
      - ``instructions`` (str)                -> system message
      - ``input`` (str)                       -> user message
      - ``input`` (list) com itens:
          * message (role + content)          -> {role, content}
          * function_call                     -> assistant + tool_calls
          * function_call_output              -> {role: "tool", ...}
      - ``tools`` (FunctionTool da Responses)  -> tools Chat Completions
      - ``tool_choice``                        -> repassado quando aplicável
    """
    messages: list[dict] = []

    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})

    raw_input = body.get("input")
    if isinstance(raw_input, str):
        messages.append({"role": "user", "content": raw_input})
    elif isinstance(raw_input, list):
        # Junta function_calls consecutivas numa única assistant message,
        # como o formato Chat Completions espera.
        pending_tool_calls: list[dict] = []

        def _flush_tool_calls() -> None:
            if pending_tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": list(pending_tool_calls),
                })
                pending_tool_calls.clear()

        for item in raw_input:
            if not isinstance(item, dict):
                continue
            itype = item.get("type")

            if itype == "function_call":
                pending_tool_calls.append({
                    "id": item.get("call_id") or item.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": item.get("name") or "",
                        "arguments": item.get("arguments") or "{}",
                    },
                })
                continue

            _flush_tool_calls()

            if itype == "function_call_output":
                messages.append({
                    "role": "tool",
                    "tool_call_id": item.get("call_id") or item.get("id") or "",
                    "content": _stringify(item.get("output")),
                })
            elif itype in (None, "message"):
                role = item.get("role") or "user"
                text = _content_parts_to_text(item.get("content"))
                # Responses usa role "developer" para instruções de sistema.
                if role == "developer":
                    role = "system"
                messages.append({"role": role, "content": text})

        _flush_tool_calls()

    tools = _responses_tools_to_chat(body.get("tools"))
    tool_choice = _normalize_tool_choice(body.get("tool_choice"), tools)
    return messages, tools, tool_choice


def _responses_tools_to_chat(tools: Any) -> list[dict] | None:
    """Converte FunctionTools da Responses (flat) p/ o formato aninhado do Chat."""
    if not isinstance(tools, list) or not tools:
        return None
    out: list[dict] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") != "function":
            # Só function tools são suportadas nesta ponte.
            continue
        # Responses: {type, name, description, parameters, strict}
        # Chat:      {type: function, function: {name, description, parameters}}
        if "function" in t and isinstance(t["function"], dict):
            fn = t["function"]  # já no formato Chat
        else:
            fn = {
                "name": t.get("name") or "",
                "description": t.get("description") or "",
                "parameters": t.get("parameters") or {"type": "object", "properties": {}},
            }
        out.append({"type": "function", "function": fn})
    return out or None


def _normalize_tool_choice(tool_choice: Any, tools: list[dict] | None) -> str | dict | None:
    if tools is None:
        return None
    if tool_choice in (None, "auto", "none", "required"):
        return tool_choice
    if isinstance(tool_choice, dict):
        # Responses: {type:"function", name:"x"}  -> Chat: {type:"function", function:{name}}
        if tool_choice.get("type") == "function" and "name" in tool_choice:
            return {"type": "function", "function": {"name": tool_choice["name"]}}
        return tool_choice
    return "auto"


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


# ════════════════════════════════════════════════════════════════════════
# Resultado normalizado do IARA  →  eventos SSE da Responses API
# ════════════════════════════════════════════════════════════════════════


@dataclass
class ChatResult:
    """Resultado normalizado de uma chamada chat.completions (não-streaming)."""
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # {id,name,arguments}
    finish_reason: str = "stop"


def normalize_chat_response(response: Any) -> ChatResult:
    """Extrai texto + tool_calls de uma resposta do SDK (attr- ou dict-like)."""
    try:
        choice = response.choices[0]
        msg = choice.message
    except Exception:
        return ChatResult()

    content = getattr(msg, "content", None)
    if isinstance(content, list):  # alguns providers devolvem partes
        content = _content_parts_to_text(content)

    tool_calls = []
    for tc in (getattr(msg, "tool_calls", None) or []):
        fn = getattr(tc, "function", None)
        tool_calls.append({
            "id": getattr(tc, "id", None) or f"call_{uuid4().hex[:24]}",
            "name": getattr(fn, "name", "") if fn else "",
            "arguments": getattr(fn, "arguments", "") if fn else "",
        })

    finish = getattr(choice, "finish_reason", None) or ("tool_calls" if tool_calls else "stop")
    return ChatResult(text=content or "", tool_calls=tool_calls, finish_reason=finish)


def _build_response_object(response_id: str, model: str, status: str,
                           output: list[dict], body: dict) -> dict:
    """Monta o objeto ``response`` (created/completed) com defaults seguros."""
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "output": output,
        "error": None,
        "incomplete_details": None,
        "instructions": body.get("instructions"),
        "metadata": body.get("metadata") or {},
        "parallel_tool_calls": bool(body.get("parallel_tool_calls", True)),
        "temperature": body.get("temperature"),
        "top_p": body.get("top_p"),
        "tool_choice": body.get("tool_choice", "auto"),
        "tools": body.get("tools") or [],
        "max_output_tokens": body.get("max_output_tokens"),
        "previous_response_id": body.get("previous_response_id"),
        "reasoning": body.get("reasoning"),
        "text": body.get("text") or {"format": {"type": "text"}},
        "truncation": body.get("truncation") or "disabled",
        "usage": None,
        "user": body.get("user"),
    }


def iter_response_events(result: ChatResult, model: str, body: dict) -> Iterator[dict]:
    """Gera a sequência de eventos SSE da Responses API a partir do resultado.

    Ordem (por item): output_item.added → (deltas) → *.done → output_item.done.
    Fecha com response.completed. Cada evento é um dict pronto p/ serialização;
    o servidor adiciona ``sequence_number`` e formata o frame SSE.
    """
    response_id = f"resp_{uuid4().hex}"
    created = _build_response_object(response_id, model, "in_progress", [], body)

    yield {"type": "response.created", "response": created}
    yield {"type": "response.in_progress", "response": created}

    output_items: list[dict] = []
    output_index = 0

    # ── Item de texto (se houver) ────────────────────────────────────────
    if result.text:
        item_id = f"msg_{uuid4().hex[:24]}"
        message_item = {
            "id": item_id,
            "type": "message",
            "role": "assistant",
            "status": "in_progress",
            "content": [],
        }
        yield {"type": "response.output_item.added",
               "output_index": output_index, "item": message_item}
        yield {"type": "response.content_part.added",
               "item_id": item_id, "output_index": output_index, "content_index": 0,
               "part": {"type": "output_text", "text": "", "annotations": []}}
        yield {"type": "response.output_text.delta",
               "item_id": item_id, "output_index": output_index, "content_index": 0,
               "delta": result.text, "logprobs": []}
        yield {"type": "response.output_text.done",
               "item_id": item_id, "output_index": output_index, "content_index": 0,
               "text": result.text, "logprobs": []}
        yield {"type": "response.content_part.done",
               "item_id": item_id, "output_index": output_index, "content_index": 0,
               "part": {"type": "output_text", "text": result.text, "annotations": []}}
        done_item = {**message_item, "status": "completed",
                     "content": [{"type": "output_text", "text": result.text,
                                  "annotations": []}]}
        yield {"type": "response.output_item.done",
               "output_index": output_index, "item": done_item}
        output_items.append(done_item)
        output_index += 1

    # ── Itens de function_call ───────────────────────────────────────────
    for tc in result.tool_calls:
        item_id = f"fc_{uuid4().hex[:24]}"
        call_id = tc["id"]
        fc_item = {
            "id": item_id,
            "type": "function_call",
            "call_id": call_id,
            "name": tc["name"],
            "arguments": "",
            "status": "in_progress",
        }
        yield {"type": "response.output_item.added",
               "output_index": output_index, "item": fc_item}
        yield {"type": "response.function_call_arguments.delta",
               "item_id": item_id, "output_index": output_index,
               "delta": tc["arguments"] or ""}
        yield {"type": "response.function_call_arguments.done",
               "item_id": item_id, "output_index": output_index,
               "arguments": tc["arguments"] or ""}
        done_item = {**fc_item, "arguments": tc["arguments"] or "", "status": "completed"}
        yield {"type": "response.output_item.done",
               "output_index": output_index, "item": done_item}
        output_items.append(done_item)
        output_index += 1

    completed = _build_response_object(response_id, model, "completed", output_items, body)
    yield {"type": "response.completed", "response": completed}


def iter_events_from_response_object(resp: dict) -> Iterator[dict]:
    """Sintetiza os eventos SSE da Responses API a partir de um objeto
    ``response`` **completo** (retorno de ``responses.create`` bloqueante).

    Usado quando o streaming SSE nativo do IARA (``responses.stream``) não é
    utilizável — no Windows ele quebra o socket com ``[WinError 10038]``,
    enquanto a chamada bloqueante funciona (provado pelo checker
    ``check_iara_responses_reachable`` e pelo caminho Legado). Reconstruímos aqui
    a sequência de eventos que o Codex espera, repassando os **itens de saída
    verbatim** (texto, ``function_call``, tool call custom, ``reasoning``), de
    modo que as ferramentas nativas do Codex são preservadas.

    Ordem: response.created → response.in_progress → (por item)
    output_item.added → deltas específicos → output_item.done →
    response.completed com o objeto final.
    """
    if not isinstance(resp, dict):
        resp = {}
    response_id = resp.get("id") or f"resp_{uuid4().hex}"
    output = resp.get("output") or []

    created = {**resp, "id": response_id, "object": "response",
               "status": "in_progress", "output": []}
    yield {"type": "response.created", "response": created}
    yield {"type": "response.in_progress", "response": created}

    for idx, item in enumerate(output):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id") or f"item_{uuid4().hex[:24]}"
        item = {**item, "id": item_id}
        yield {"type": "response.output_item.added",
               "output_index": idx, "item": {**item, "status": "in_progress"}}

        itype = item.get("type")
        if itype == "message":
            for cidx, part in enumerate(item.get("content") or []):
                if not isinstance(part, dict) or part.get("type") not in (
                        "output_text", "text"):
                    continue
                text = part.get("text") or ""
                ann = part.get("annotations") or []
                yield {"type": "response.content_part.added",
                       "item_id": item_id, "output_index": idx, "content_index": cidx,
                       "part": {"type": "output_text", "text": "", "annotations": ann}}
                yield {"type": "response.output_text.delta",
                       "item_id": item_id, "output_index": idx, "content_index": cidx,
                       "delta": text, "logprobs": []}
                yield {"type": "response.output_text.done",
                       "item_id": item_id, "output_index": idx, "content_index": cidx,
                       "text": text, "logprobs": []}
                yield {"type": "response.content_part.done",
                       "item_id": item_id, "output_index": idx, "content_index": cidx,
                       "part": {"type": "output_text", "text": text, "annotations": ann}}
        elif itype == "function_call":
            args = item.get("arguments") or ""
            yield {"type": "response.function_call_arguments.delta",
                   "item_id": item_id, "output_index": idx, "delta": args}
            yield {"type": "response.function_call_arguments.done",
                   "item_id": item_id, "output_index": idx, "arguments": args}

        yield {"type": "response.output_item.done",
               "output_index": idx, "item": {**item, "status": "completed"}}

    completed = {**resp, "id": response_id, "object": "response",
                 "status": resp.get("status") or "completed"}
    if not completed.get("output"):
        completed["output"] = output
    yield {"type": "response.completed", "response": completed}


# ════════════════════════════════════════════════════════════════════════
# Chamada ao IARA
# ════════════════════════════════════════════════════════════════════════


def _is_claude(model: str) -> bool:
    return bool(model) and "claude" in (model or "").lower()


def _provider_for(model: str) -> str:
    """Deriva o provider do iaragenai pelo id do modelo (espelha ai_service.py)."""
    m = (model or "").lower()
    if not m:
        return os.getenv("IARA_PROVIDER", "azure_openai")
    if "anthropic." in m or "claude" in m:
        return "bedrock"
    if m.startswith("gemini") or m.startswith("vertex"):
        return "vertex"
    if (m.startswith("gpt") or m.startswith("o1") or m.startswith("o3")
            or m.startswith("o4") or m.startswith("openai.")):
        return "azure_openai"
    if m.split(".")[0] in {"amazon", "meta", "mistral", "qwen", "google", "deepseek"}:
        return "bedrock"
    return os.getenv("IARA_PROVIDER", "azure_openai")


def build_completion_kwargs(model: str, messages: list[dict],
                            tools: list[dict] | None, tool_choice: Any,
                            temperature: float | None) -> dict:
    """Monta kwargs de chat.completions.create adaptando por provider."""
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
    if _is_claude(model):
        # Claude no Bedrock: thinking adaptive + effort high (ver ai_service.py).
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": "high"}
        kwargs["max_tokens"] = 64000
        kwargs["temperature"] = 1.0
    elif temperature is not None:
        kwargs["temperature"] = temperature
    return kwargs


def _make_iara_client(provider: str):
    from iaragenai import IaraGenAI
    return IaraGenAI(
        client_id=os.getenv("IARA_CLIENT_ID"),
        client_secret=os.getenv("IARA_CLIENT_SECRET"),
        environment=os.getenv("IARA_ENVIRONMENT", "homol"),
        provider=provider,
        correlation_id=str(uuid4()),
    )


def run_iara_completion(body: dict) -> tuple[ChatResult, str]:
    """Traduz o request, chama o IARA (bloqueante) e devolve (ChatResult, model).

    LEGADO: caminho Chat Completions (tradução). Mantido apenas para os testes
    de tradução pura; o servidor HTTP usa o *passthrough* Responses (abaixo),
    que preserva as ferramentas nativas do Codex.
    """
    model = body.get("model") or os.getenv("IARA_MODEL", "gpt-5.6-terra")
    messages, tools, tool_choice = responses_input_to_messages(body)
    provider = _provider_for(model)
    client = _make_iara_client(provider)
    kwargs = build_completion_kwargs(
        model, messages, tools, tool_choice, body.get("temperature")
    )
    response = client.chat.completions.create(**kwargs)
    return normalize_chat_response(response), model


# ════════════════════════════════════════════════════════════════════════
# Passthrough Responses  (Codex Responses API  →  iaragenai client.responses)
# ════════════════════════════════════════════════════════════════════════
#
# Por que passthrough e não tradução: o Codex 0.147 opera em "code mode" e
# envia suas ferramentas dentro do array ``input`` como um item
# ``additional_tools`` cujo tool primário ``functions.exec`` é ``type:"custom"``
# com gramática Lark — um recurso NATIVO da Responses API que o Chat Completions
# não consegue representar. O SDK iaragenai expõe ``client.responses`` (Responses
# API nativa), então repassamos o corpo inteiro do Codex e devolvemos o SSE do
# IARA verbatim. Assim as ferramentas do Codex funcionam sem tradução.

# Chaves que NÃO são kwargs de client.responses.* (tratadas à parte).
_RESPONSES_META_KEYS = {"model", "input", "stream"}


def _responses_kwargs(body: dict) -> dict:
    """Extrai os kwargs a repassar para ``client.responses.*`` (tudo menos meta)."""
    return {k: v for k, v in body.items() if k not in _RESPONSES_META_KEYS}


def _client_and_model(body: dict):
    """Cria o client IARA (provider derivado do modelo) e resolve o modelo."""
    model = body.get("model") or os.getenv("IARA_MODEL", "gpt-5.6-terra")
    client = _make_iara_client(_provider_for(model))
    return client, model


def open_iara_responses_stream(body: dict):
    """Abre o stream Responses do IARA e devolve o objeto ``ResponsesSSEStream``.

    O objeto já está *entered* (``__enter__`` executado): a conexão HTTP com o
    IARA foi aberta e o status validado. O chamador deve iterar
    ``stream._resp.iter_bytes()`` para repassar o SSE cru e, ao final, chamar
    ``stream.__exit__(None, None, None)``.
    """
    client, model = _client_and_model(body)
    sse = client.responses.stream(model=model, input=body.get("input"),
                                  **_responses_kwargs(body))
    sse.__enter__()  # abre a conexão; levanta APIConnectionError em erro HTTP
    return sse


def create_iara_response(body: dict) -> dict:
    """Chamada Responses não-streaming; devolve o objeto ``response`` como dict."""
    client, model = _client_and_model(body)
    resp = client.responses.create(model=model, input=body.get("input"),
                                   stream=False, **_responses_kwargs(body))
    try:
        return resp.model_dump(mode="json")
    except Exception:
        return resp if isinstance(resp, dict) else {}


# ════════════════════════════════════════════════════════════════════════
# Servidor HTTP
# ════════════════════════════════════════════════════════════════════════


class _Handler(BaseHTTPRequestHandler):
    server_version = "AtenaIaraAdapter/1.0"

    # Silencia o log padrão (não vaza corpo/headers sensíveis no stderr).
    def log_message(self, *_args) -> None:  # noqa: D401
        return

    def _auth_ok(self) -> bool:
        expected = (os.getenv("ATENA_IARA_ADAPTER_TOKEN") or "").strip()
        if not expected:
            return True  # sem token configurado = sem exigência (loopback)
        header = self.headers.get("Authorization", "")
        return header.strip() == f"Bearer {expected}"

    def _send_json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse_write(self, event: dict, seq: int) -> None:
        event = {**event, "sequence_number": seq}
        frame = f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        self.wfile.write(frame.encode("utf-8"))
        self.wfile.flush()

    def do_GET(self) -> None:  # health check
        if self.path.rstrip("/") in ("/health", "/healthz"):
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        if self.path.rstrip("/") not in ("/v1/responses", "/responses"):
            self._send_json(404, {"error": {"message": "not found"}})
            return
        if not self._auth_ok():
            self._send_json(401, {"error": {"message": "unauthorized"}})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send_json(400, {"error": {"message": "invalid JSON body"}})
            return

        _debug_dump_body(body)

        stream = bool(body.get("stream"))
        if stream:
            self._handle_stream(body)
        else:
            self._handle_blocking(body)

    def _handle_stream(self, body: dict) -> None:
        """Atende um request com ``stream: true`` do Codex.

        Por padrão usa o modo **bloqueante + síntese** (``_handle_stream_blocking``):
        chama ``responses.create`` e reconstrói os eventos SSE aqui. Isso é o
        default porque o streaming SSE nativo do IARA (``responses.stream``)
        quebra o socket no Windows (``[WinError 10038]``), enquanto a chamada
        bloqueante funciona — provado pelo checker e pelo caminho Legado.

        Se ``ATENA_IARA_STREAM_PASSTHROUGH`` estiver setado, usa o passthrough
        nativo (``_handle_stream_passthrough``), útil em plataformas onde o
        streaming do SDK funciona e se deseja o streaming de tokens real.
        """
        if _stream_passthrough_enabled():
            self._handle_stream_passthrough(body)
        else:
            self._handle_stream_blocking(body)

    def _handle_stream_blocking(self, body: dict) -> None:
        """Chama o IARA bloqueante e sintetiza o SSE para o Codex.

        Preserva as ferramentas nativas: o objeto Responses volta inteiro (com
        os itens ``function_call``/tool call custom/``reasoning``) e é
        re-emitido verbatim por ``iter_events_from_response_object``.
        """
        try:
            final = create_iara_response(body)
        except Exception as e:
            _log_stream_error("gateway->adaptador (responses.create bloqueante)",
                              e, 0, False)
            self._send_json(502, {"error": {"message": _redact(str(e))}})
            return

        tap = _open_sse_tap()
        self.close_connection = True
        seq = 0
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            for event in iter_events_from_response_object(final):
                seq += 1
                self._sse_write(event, seq)
                if tap is not None:
                    try:
                        tap.write((json.dumps(event, ensure_ascii=False) + "\n")
                                  .encode("utf-8"))
                    except Exception:
                        pass
        except Exception as e:
            _log_stream_error("adaptador->Codex (escrita do SSE sintetizado)",
                              e, seq, False)
        finally:
            if tap is not None:
                try:
                    tap.close()
                except Exception:
                    pass

    def _handle_stream_passthrough(self, body: dict) -> None:
        """Repassa o SSE Responses do IARA verbatim para o Codex.

        Duas salvaguardas sobre o passthrough puro:

        * Detecta se o stream do IARA terminou **sem** um evento terminal
          (``response.completed``/``failed``/``incomplete``). O Codex, ao ver
          EOF sem esse evento, aborta com "stream closed before
          response.completed" — uma mensagem opaca. Nesse caso emitimos um
          ``response.failed`` explícito, para o usuário saber que o gateway
          cortou a resposta (provável timeout upstream).
        * Se ``ATENA_IARA_SSE_TAP`` apontar para um arquivo, grava os bytes
          crus do SSE ali (diagnóstico) — o corpo Responses não contém
          credenciais.
        """
        # Abre o stream ANTES de enviar cabeçalhos, para poder devolver um
        # status de erro adequado se a conexão com o IARA falhar.
        try:
            sse = open_iara_responses_stream(body)
        except Exception as e:
            self._send_json(502, {"error": {"message": _redact(str(e))}})
            return

        tap = _open_sse_tap()
        # Marcadores de evento terminal da Responses API. Buscamos no fluxo
        # bruto mantendo uma pequena "cauda" entre chunks (o marcador pode
        # cair na fronteira de dois chunks).
        terminal_markers = (b"response.completed", b"response.failed",
                            b"response.incomplete")
        tail = b""
        saw_terminal = False
        seq = 0

        # Fecha a conexão ao fim do stream para sinalizar EOF (SSE não tem
        # Content-Length).
        self.close_connection = True
        bytes_sent = 0  # bytes já repassados ao Codex (atribui a perna do erro)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            # Iteramos manualmente (em vez de ``for``) para separar a leitura do
            # gateway (perna IARA→adaptador) da escrita para o Codex (perna
            # adaptador→Codex). Assim, ao dar erro, sabemos QUAL socket caiu — o
            # ``[WinError 10038]`` (WSAENOTSOCK) do Windows não diz de qual lado
            # veio, e o Codex só mostra "stream disconnected before completion".
            iterator = sse._resp.iter_bytes()
            while True:
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                except Exception as e:
                    # Perna IARA→adaptador: o gateway cortou/expirou o SSE no
                    # meio. O socket do Codex ainda está vivo, então dá pra
                    # sinalizar a falha por um evento.
                    _log_stream_error("gateway->adaptador (leitura do SSE do IARA)",
                                      e, bytes_sent, saw_terminal)
                    self._sse_write(
                        {"type": "response.failed",
                         "response": {"status": "failed", "error": {"message":
                             "IARA interrompeu o stream (" + _redact(str(e)) + "); "
                             "resposta possivelmente truncada. bytes_recebidos="
                             + str(bytes_sent)}}},
                        seq + 1)
                    return
                if not chunk:
                    continue
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except Exception as e:
                    # Perna adaptador→Codex: o Codex fechou a conexão (timeout de
                    # idle, interrupção, etc.). Não dá pra sinalizar por evento —
                    # o socket de destino já morreu; só registramos.
                    _log_stream_error("adaptador->Codex (escrita do SSE)",
                                      e, bytes_sent, saw_terminal)
                    return
                bytes_sent += len(chunk)
                seq += chunk.count(b"\n\n")  # ~1 por frame SSE
                if tap is not None:
                    try:
                        tap.write(chunk)
                    except Exception:
                        pass
                if not saw_terminal:
                    window = tail + chunk
                    if any(m in window for m in terminal_markers):
                        saw_terminal = True
                    tail = window[-32:]  # cobre a fronteira de chunk
            if not saw_terminal:
                # EOF limpo sem evento terminal: o gateway cortou a resposta.
                _log_stream_error("gateway->adaptador (EOF sem evento terminal)",
                                  None, bytes_sent, saw_terminal)
                self._sse_write(
                    {"type": "response.failed",
                     "response": {"status": "failed", "error": {
                         "message": "IARA encerrou o stream sem response.completed "
                                    "(provável timeout do gateway; a resposta pode "
                                    "ter sido truncada)."}}},
                    seq + 1)
        except Exception as e:
            # Rede de segurança: erro fora das pernas acima (ex.: envio dos
            # cabeçalhos). Já enviamos 200; só dá pra sinalizar por um evento.
            _log_stream_error("desconhecida", e, bytes_sent, saw_terminal)
            try:
                self._sse_write({"type": "response.failed",
                                 "response": {"status": "failed",
                                              "error": {"message": _redact(str(e))}}},
                                seq + 1)
            except Exception:
                pass
        finally:
            if tap is not None:
                try:
                    tap.close()
                except Exception:
                    pass
            try:
                sse.__exit__(None, None, None)
            except Exception:
                pass

    def _handle_blocking(self, body: dict) -> None:
        """Resposta não-streaming: devolve o objeto ``response`` final."""
        try:
            final = create_iara_response(body)
        except Exception as e:
            self._send_json(502, {"error": {"message": _redact(str(e))}})
            return
        self._send_json(200, final or {})


def _open_sse_tap():
    """Abre o arquivo de captura do SSE cru se ``ATENA_IARA_SSE_TAP`` estiver setado.

    Diagnóstico apenas: permite inspecionar exatamente o que o gateway IARA
    devolve (inclusive se manda ou não ``response.completed``). Retorna um
    objeto de arquivo binário aberto em modo append, ou ``None``.
    """
    path = (os.getenv("ATENA_IARA_SSE_TAP") or "").strip()
    if not path:
        return None
    try:
        return open(path, "ab")
    except Exception:
        return None


def _stream_passthrough_enabled() -> bool:
    """Se setado, usa o streaming SSE nativo do IARA em vez de bloquear+sintetizar.

    O default é bloqueante porque ``responses.stream`` quebra o socket no Windows
    (``[WinError 10038]``). Ligue só onde o streaming do SDK comprovadamente
    funciona (ex.: Linux/ECS) e quiser streaming de tokens em tempo real.
    """
    return (os.getenv("ATENA_IARA_STREAM_PASSTHROUGH") or "").strip().lower() in {
        "1", "true", "yes", "on", "sim",
    }


def _log_stream_error(leg: str, exc: BaseException | None,
                      bytes_sent: int, saw_terminal: bool) -> None:
    """Registra qual perna do passthrough falhou e por quê (diagnóstico).

    O ``[WinError 10038]`` (WSAENOTSOCK) chega ao Codex como um opaco "stream
    disconnected before completion" sem dizer de que lado o socket caiu. Aqui
    atribuímos a perna (``gateway->adaptador`` = leitura do SSE do IARA;
    ``adaptador->Codex`` = escrita para o runtime) e o tipo/mensagem da exceção.

    Sempre emite uma linha curta no stderr (visível no console do app, sem corpo
    nem credenciais). Se ``ATENA_IARA_ERROR_LOG`` apontar para um arquivo, grava
    também o traceback completo (redigido) para inspeção posterior.
    """
    etype = type(exc).__name__ if exc is not None else "EOF"
    emsg = _redact(str(exc)) if exc is not None else "(sem exceção)"
    summary = (f"[iara_adapter] stream ABORTADO na perna {leg}: {etype}: {emsg} "
               f"| bytes_recebidos={bytes_sent} evento_terminal_visto={saw_terminal}")
    try:
        print(summary, file=__import__("sys").stderr, flush=True)
    except Exception:
        pass

    path = (os.getenv("ATENA_IARA_ERROR_LOG") or "").strip()
    if not path:
        return
    try:
        import traceback
        block = summary + "\n"
        if exc is not None:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            block += _redact(tb) + "\n"
        block += "-" * 60 + "\n"
        with open(path, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(block)
    except Exception:
        pass


def _debug_dump_body(body: dict) -> None:
    """Se ATENA_IARA_DEBUG_DUMP apontar p/ um arquivo, grava o request (JSONL).

    Diagnóstico apenas: permite inspecionar o que o Codex envia (tools/input)
    sem depender do IARA responder. Não grava credenciais (o corpo Responses
    não as contém).
    """
    path = (os.getenv("ATENA_IARA_DEBUG_DUMP") or "").strip()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(body, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _redact(text: str) -> str:
    """Remove padrões que pareçam segredos de uma mensagem de erro."""
    import re
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1[REDACTED]", text)
    text = re.sub(r"(client_secret|secret|token|api[_-]?key)\S*",
                  "[REDACTED]", text, flags=re.IGNORECASE)
    return text[:500]


def serve(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    host = host or os.getenv("ATENA_IARA_ADAPTER_HOST", "127.0.0.1")
    port = int(port or os.getenv("ATENA_IARA_ADAPTER_PORT", "8799"))
    httpd = ThreadingHTTPServer((host, port), _Handler)
    return httpd


def serve_forever(host: str | None = None, port: int | None = None) -> None:
    httpd = serve(host, port)
    h, p = httpd.server_address
    print(f"[iara_adapter] escutando em http://{h}:{p}/v1/responses", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


def start_in_thread(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    """Sobe o adaptador numa thread daemon e retorna o servidor (p/ embutir no Django)."""
    httpd = serve(host, port)
    threading.Thread(target=httpd.serve_forever, name="iara-adapter",
                     daemon=True).start()
    return httpd


if __name__ == "__main__":
    serve_forever()
