"""Endpoints SSE que adaptam o Codex App Server ao contrato do frontend."""

from __future__ import annotations

import json
import queue
import re
import threading
import time
import unicodedata
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .codex_app_server import CodexAppServer, CodexAppServerError
from .models import Message, ToolCall
from .views import _message_payload, _resolve_conversation
from tools.gerar_html import gerar_html


_HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HTML_EXPORT_PREFIX = "/api/exports/"
_TRACE_TEXT_LIMIT = 6000
_TRACE_RESULT_PREVIEW_LIMIT = 1200
_TRACE_META = {
    "commandExecution": ("codex_command", "⌨️"),
    "fileChange": ("codex_file_change", "📝"),
    "mcpToolCall": ("codex_mcp", "🔌"),
    "dynamicToolCall": ("codex_tool", "⚙️"),
    "collabToolCall": ("codex_subagent", "🤝"),
    "webSearch": ("codex_web_search", "🌐"),
    "imageView": ("codex_image_view", "🖼️"),
    "contextCompaction": ("codex_compaction", "🧠"),
}


def _sanitize_trace_text(value, limit: int = _TRACE_TEXT_LIMIT) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            value = str(value)
    value = re.sub(
        r"(?i)((?:OPENAI|AWS|AZURE|IARA)[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*[=:]\s*)([^\s,;]+)",
        r"\1[REDACTED]",
        value,
    )
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", value)
    return value if len(value) <= limit else value[:limit] + "…"


def _bounded_trace_value(value):
    """Mantém args estruturados pequenos e transforma payloads grandes em prévia."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_trace_text(value, 4000)
    serialized = _sanitize_trace_text(value, 8000)
    try:
        parsed = json.loads(serialized)
    except json.JSONDecodeError:
        return serialized
    return parsed


def _codex_trace_args(item: dict) -> dict:
    item_type = item.get("type")
    if item_type == "commandExecution":
        return {
            "command": _sanitize_trace_text(item.get("command"), 4000),
            "cwd": Path(str(item.get("cwd") or ".")).name,
        }
    if item_type == "mcpToolCall":
        return {
            "server": item.get("server") or "",
            "tool": item.get("tool") or "",
            "arguments": _bounded_trace_value(item.get("arguments") or {}),
        }
    if item_type == "dynamicToolCall":
        return {
            "tool": item.get("tool") or "",
            "arguments": _bounded_trace_value(item.get("arguments") or {}),
        }
    if item_type == "collabToolCall":
        return {
            "tool": item.get("tool") or "",
            "prompt": _sanitize_trace_text(item.get("prompt"), 3000),
        }
    if item_type == "webSearch":
        return {
            "query": _sanitize_trace_text(item.get("query"), 2000),
            "action": _bounded_trace_value(item.get("action") or {}),
        }
    if item_type == "fileChange":
        return {
            "changes": [
                {"path": change.get("path"), "kind": change.get("kind")}
                for change in (item.get("changes") or [])[:50]
                if isinstance(change, dict)
            ]
        }
    if item_type == "imageView":
        return {"path": Path(str(item.get("path") or "imagem")).name}
    return {}


def _codex_trace_label(item: dict) -> str:
    item_type = item.get("type")
    if item_type == "commandExecution":
        command = _sanitize_trace_text(item.get("command"), 100).splitlines()[0]
        return f"Executando no sandbox · {command}" if command else "Executando no sandbox"
    if item_type == "mcpToolCall":
        target = " · ".join(filter(None, [str(item.get("server") or ""), str(item.get("tool") or "")]))
        return f"Consultando ferramenta · {target}" if target else "Consultando ferramenta"
    if item_type == "dynamicToolCall":
        return f"Executando ferramenta · {item.get('tool') or 'Codex'}"
    if item_type == "collabToolCall":
        return f"Coordenando subagente · {item.get('tool') or 'Codex'}"
    if item_type == "webSearch":
        query = _sanitize_trace_text(item.get("query"), 100)
        return f"Pesquisando na web · {query}" if query else "Pesquisando na web"
    if item_type == "fileChange":
        return f"Preparando alterações em {len(item.get('changes') or [])} arquivo(s)"
    if item_type == "imageView":
        return f"Inspecionando imagem · {Path(str(item.get('path') or 'imagem')).name}"
    if item_type == "contextCompaction":
        return "Organizando o contexto da conversa"
    return "Executando atividade do Codex"


def _codex_trace_result(item: dict, streamed_output: str = "") -> tuple[str, str]:
    item_type = item.get("type")
    status = str(item.get("status") or "completed").lower()
    error_value = item.get("error")
    error = _sanitize_trace_text(error_value)
    if status in {"failed", "declined", "cancelled", "canceled"} and not error:
        error = f"Atividade encerrada com status {status}."

    if item_type == "commandExecution":
        result = item.get("aggregatedOutput") or streamed_output
        exit_code = item.get("exitCode")
        if exit_code not in (None, 0) and not error:
            error = f"Comando encerrado com código {exit_code}."
        return _sanitize_trace_text(result), error
    if item_type == "mcpToolCall":
        return _sanitize_trace_text(item.get("result")), error
    if item_type == "dynamicToolCall":
        return _sanitize_trace_text(item.get("contentItems") or item.get("success")), error
    if item_type == "collabToolCall":
        return _sanitize_trace_text(item.get("agentStatus") or status), error
    if item_type == "webSearch":
        return "Pesquisa concluída.", error
    if item_type == "fileChange":
        return f"{len(item.get('changes') or [])} alteração(ões) processada(s).", error
    if item_type == "imageView":
        return "Imagem inspecionada.", error
    if item_type == "contextCompaction":
        return "Contexto da conversa organizado.", error
    return _sanitize_trace_text(status), error


def _codex_trace_record(item: dict, streamed_output: str = "", elapsed_ms: int = 0) -> dict | None:
    item_type = item.get("type")
    meta = _TRACE_META.get(item_type)
    item_id = str(item.get("id") or "")
    if not meta or not item_id:
        return None
    result, error = _codex_trace_result(item, streamed_output)
    duration = item.get("durationMs")
    try:
        duration_ms = int(duration if duration is not None else elapsed_ms)
    except (TypeError, ValueError):
        duration_ms = elapsed_ms
    return {
        "id": item_id,
        "item_type": item_type,
        "tool": meta[0],
        "icon": meta[1],
        "label": _codex_trace_label(item),
        "args": _codex_trace_args(item),
        "result": result,
        "error": error,
        "duration_ms": max(0, duration_ms),
    }


def _split_html_response(answer: str) -> tuple[str, str | None]:
    """Separa texto explicativo de um documento HTML completo retornado inline."""
    lowered = answer.lower()
    starts = [pos for pos in (lowered.find("<!doctype html"), lowered.find("<html")) if pos >= 0]
    if not starts:
        return answer, None
    start = min(starts)
    end = lowered.rfind("</html>")
    if end < start:
        return answer, None
    end += len("</html>")

    html = answer[start:end].strip()
    before = answer[:start].strip()
    after = answer[end:].strip()
    # Remove somente as cercas externas que normalmente envolvem o documento.
    before = re.sub(r"```(?:html|htm)?\s*$", "", before, flags=re.IGNORECASE).strip()
    after = re.sub(r"^```", "", after).strip()
    visible_text = "\n\n".join(part for part in (before, after) if part).strip()
    return visible_text, html


def _materialize_html_response(answer: str) -> tuple[str, list[dict]]:
    """Converte HTML inline do Codex no attachment já entendido pelo Angular."""
    visible_text, html = _split_html_response(answer)
    if html is None:
        return answer, []

    title_match = _HTML_TITLE_RE.search(html)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else "Relatório"
    session: dict = {}
    raw_result = gerar_html(
        _session=session,
        html=html,
        titulo=title or "Relatório",
        nome_arquivo="relatorio_codex",
    )
    try:
        result = json.loads(raw_result)
    except (TypeError, json.JSONDecodeError):
        result = {"erro": "Resposta inválida ao salvar HTML."}
    if result.get("erro"):
        return answer, []

    attachments = session.get("__pending_attachments") or []
    if not visible_text:
        visible_text = "Relatório HTML gerado. Use **Visualizar** para abrir o painel interativo."
    return visible_text, attachments


def _normalize_request(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _is_html_revision_request(text: str) -> bool:
    """Reconhece referências explícitas a um artefato HTML já criado."""
    normalized = _normalize_request(text)
    artifact = r"(?:html|relatorio|dashboard|pagina|artefato)"
    reference = r"(?:nesse|neste|naquele|no\s+mesmo|o\s+mesmo|mesmo|anterior|acima|existente|ultimo)"
    return bool(
        re.search(rf"\b{reference}\b.{{0,60}}\b{artifact}\b", normalized)
        or re.search(rf"\b{artifact}\b.{{0,60}}\b{reference}\b", normalized)
    )


def _latest_html_artifact(conv) -> str | None:
    """Lê com segurança o HTML do attachment mais recente da conversa."""
    export_dir = (Path(settings.BASE_DIR) / "exports").resolve()
    for message in conv.messages.order_by("-created_at"):
        for attachment in reversed(message.attachments or []):
            if attachment.get("kind") != "export" or attachment.get("formato") != "html":
                continue
            download_url = str(attachment.get("download_url") or "")
            if not download_url.startswith(_HTML_EXPORT_PREFIX):
                continue
            filename = Path(download_url.removeprefix(_HTML_EXPORT_PREFIX)).name
            candidate = (export_dir / filename).resolve()
            if candidate.parent != export_dir or candidate.suffix.lower() not in {".html", ".htm"}:
                continue
            try:
                return candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
    return None


def _html_revision_context(conv, text: str) -> str:
    if not _is_html_revision_request(text):
        return ""
    html = _latest_html_artifact(conv)
    if not html:
        return ""
    return (
        "Você está editando o último artefato HTML desta conversa. Aplique o pedido "
        "ao documento abaixo e devolva obrigatoriamente o documento HTML completo e "
        "standalone, do <!doctype html> até </html>. Não devolva CSS isolado, trechos, "
        "diff, tutorial ou instruções para o usuário editar manualmente. Preserve o "
        "conteúdo existente que não foi alvo do pedido.\n\n"
        "<artefato_html_anterior>\n"
        f"{html}\n"
        "</artefato_html_anterior>"
    )


def _revision_output_requirement() -> str:
    return (
        "REQUISITO OBRIGATÓRIO DE SAÍDA: responda com o HTML atualizado inteiro, "
        "começando por <!doctype html> e terminando em </html>. Não diga ao usuário "
        "para inserir ou substituir trechos."
    )


def _prepare_session_workspace(conv) -> Path:
    """Materializa datasets do Django como arquivos legíveis pelo sandbox."""
    workspace = Path(settings.BASE_DIR) / "runtime" / "codex_sessions" / str(conv.id)
    datasets_dir = workspace / "datasets_nomeados"
    datasets_dir.mkdir(parents=True, exist_ok=True)

    state = conv.state or {}
    manifest = {
        "conversation_id": conv.id,
        "fonte_atual": state.get("athena_last_source") or {},
        "colunas_atuais": state.get("athena_last_columns") or [],
        "workbooks": state.get("excel_workbooks") or {},
        "dataset_atual": None,
        "datasets_nomeados": {},
    }
    current = state.get("athena_last_result")
    if current is not None:
        current_path = workspace / "dataset_atual.json"
        current_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        manifest["dataset_atual"] = current_path.name

    for index, (name, rows) in enumerate((state.get("named_datasets") or {}).items(), start=1):
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name)).strip("._")[:80]
        filename = f"{index:03d}_{safe_name or 'dataset'}.json"
        path = datasets_dir / filename
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        manifest["datasets_nomeados"][str(name)] = str(path.relative_to(workspace))

    (workspace / "manifesto_sessao.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return workspace


def _prompt_with_history(conv, text: str, has_codex_thread: bool) -> str:
    state = conv.state or {}
    has_attached_data = any((
        state.get("athena_last_result") is not None,
        bool(state.get("named_datasets")),
        bool(state.get("excel_workbooks")),
    ))
    hints: list[str] = []
    if has_attached_data:
        hints.append(
            "Há dados anexados disponíveis na sessão. Somente se o pedido atual "
            "exigir esses dados, consulte manifesto_sessao.json para localizar o "
            "dataset atual e as abas/datasets nomeados. Não mencione essa estrutura "
            "interna na resposta; apresente apenas a análise e suas evidências."
        )

    revision_context = _html_revision_context(conv, text)
    if revision_context:
        hints.append(revision_context)

    session_hint = "\n\n".join(hints)

    def with_hint(value: str) -> str:
        prompted = f"{session_hint}\n\n{value}" if session_hint else value
        if revision_context:
            prompted = f"{prompted}\n\n{_revision_output_requirement()}"
        return prompted

    if has_codex_thread:
        return with_hint(text)
    previous = list(conv.messages.order_by("created_at").values("role", "content"))[-12:]
    if not previous:
        return with_hint(text)
    history = "\n".join(
        f"{('Usuário' if row['role'] == 'user' else 'Assistente')}: {row['content']}"
        for row in previous if row["content"]
    )
    return (
        "Considere este histórico importado da interface antiga apenas como contexto:\n"
        f"<historico>\n{history}\n</historico>\n\n"
        + with_hint(f"Pedido atual do usuário: {text}")
    )


@require_GET
def codex_status(_request):
    from shutil import which

    return JsonResponse({
        "status": "ready" if which("codex") else "unavailable",
        "engine": "codex-app-server",
        "sandbox": "read-only",
        "skills": [
            "auditoria-interna",
            "aws-athena",
            "ciencia-dados",
            "analise-documentos",
            "documentacao-auditoria",
        ],
    })


@csrf_exempt
def codex_chat_stream(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)

    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "JSON inválido."}, status=400)

    text = (data.get("message") or "").strip()
    if not text:
        return JsonResponse({"status": "error", "message": "Mensagem vazia."}, status=400)

    conv, error = _resolve_conversation(data.get("conversation_id"), data.get("agent_slug"), text)
    if error:
        return JsonResponse({"status": "error", "message": error}, status=400)

    state = dict(conv.state or {})
    old_thread_id = state.get("codex_thread_id")
    revision_html = (
        _latest_html_artifact(conv) if _is_html_revision_request(text) else None
    )
    prompt = _prompt_with_history(conv, text, bool(old_thread_id))
    Message.objects.create(conversation=conv, role="user", content=text)

    events: "queue.Queue[dict]" = queue.Queue()
    sentinel = object()

    def worker() -> None:
        answer_parts: list[str] = []
        trace_records: dict[str, dict] = {}
        trace_started_at: dict[str, float] = {}
        trace_output: dict[str, str] = {}
        trace_completed: set[str] = set()
        try:
            session_workspace = _prepare_session_workspace(conv)
            with CodexAppServer(session_workspace) as client:
                events.put({"type": "progress", "stage": "thinking", "icon": "◈", "text": "Conectando ao Codex App Server"})
                client.initialize()
                thread_id = client.open_thread(old_thread_id)
                state["codex_thread_id"] = thread_id
                state["chat_engine"] = "codex-app-server"
                conv.state = state
                conv.save(update_fields=["state", "updated_at"])
                events.put({"type": "progress", "stage": "thinking", "icon": "🛡️", "text": "Sandbox somente leitura · skills de auditoria carregadas"})

                def collect_turn(turn_prompt: str) -> str:
                    parts: list[str] = []
                    for event in client.turn(thread_id, turn_prompt):
                        if event["type"] == "delta" and event.get("phase") != "commentary":
                            parts.append(event["text"])
                        elif event["type"] == "activity":
                            item = event.get("item") or {}
                            item_id = str(item.get("id") or "")
                            phase = event.get("phase")
                            if phase == "started":
                                record = _codex_trace_record(item)
                                if record:
                                    trace_records[item_id] = record
                                    trace_started_at[item_id] = time.monotonic()
                                    events.put({
                                        "type": "progress",
                                        "stage": "tool",
                                        "agent": "codex",
                                        "tool": record["tool"],
                                        "tool_call_id": item_id,
                                        "args": record["args"],
                                        "icon": record["icon"],
                                        "text": record["label"],
                                    })
                            elif phase == "completed":
                                started_at = trace_started_at.get(item_id)
                                elapsed_ms = (
                                    round((time.monotonic() - started_at) * 1000)
                                    if started_at is not None else 0
                                )
                                record = _codex_trace_record(
                                    item,
                                    trace_output.get(item_id, ""),
                                    elapsed_ms,
                                )
                                if record:
                                    # Alguns clientes podem receber somente o completed;
                                    # ainda assim criamos o nó antes de encerrá-lo.
                                    if item_id not in trace_records:
                                        events.put({
                                            "type": "progress",
                                            "stage": "tool",
                                            "agent": "codex",
                                            "tool": record["tool"],
                                            "tool_call_id": item_id,
                                            "args": record["args"],
                                            "icon": record["icon"],
                                            "text": record["label"],
                                        })
                                    trace_records[item_id] = record
                                    trace_completed.add(item_id)
                                    events.put({
                                        "type": "progress",
                                        "stage": "tool_result",
                                        "agent": "codex",
                                        "tool": record["tool"],
                                        "tool_call_id": item_id,
                                        "error": record["error"],
                                        "duration_ms": record["duration_ms"],
                                        "result_preview": _sanitize_trace_text(
                                            record["result"], _TRACE_RESULT_PREVIEW_LIMIT
                                        ),
                                    })
                        elif event["type"] == "activity_output":
                            item_id = str(event.get("item_id") or "")
                            if item_id:
                                current = trace_output.get(item_id, "")
                                trace_output[item_id] = _sanitize_trace_text(
                                    current + str(event.get("text") or "")
                                )
                        elif event["type"] == "completed" and event.get("status") != "completed":
                            raise CodexAppServerError(
                                str(event.get("error") or "Turno não concluído.")
                            )
                    return "".join(parts).strip()

                answer = collect_turn(prompt)
                if revision_html and _split_html_response(answer)[1] is None:
                    events.put({
                        "type": "progress",
                        "stage": "thinking",
                        "icon": "◇",
                        "text": "Consolidando a nova versão completa do HTML",
                    })
                    repair_prompt = (
                        "A resposta anterior ficou incompleta porque trouxe apenas "
                        "instruções ou fragmentos. Refaça agora o pedido original: "
                        f"{text}\n\n{_revision_output_requirement()}\n\n"
                        "Use este documento como base e preserve o restante:\n"
                        "<artefato_html_anterior>\n"
                        f"{revision_html}\n"
                        "</artefato_html_anterior>"
                    )
                    answer = collect_turn(repair_prompt)

                answer_parts.append(answer)

            for item_id, record in trace_records.items():
                if item_id not in trace_completed:
                    started_at = trace_started_at.get(item_id)
                    if started_at is not None:
                        record["duration_ms"] = round(
                            (time.monotonic() - started_at) * 1000
                        )
                    record["result"] = record["result"] or "Atividade concluída com o turno."

            answer = "".join(answer_parts).strip()
            if not answer:
                answer = "O Codex concluiu o turno sem produzir uma mensagem de texto."
            answer, attachments = _materialize_html_response(answer)
            assistant = Message.objects.create(
                conversation=conv,
                role="assistant",
                content=answer,
                attachments=attachments,
            )
            for record in trace_records.values():
                ToolCall.objects.create(
                    message=assistant,
                    tool_name=record["tool"],
                    args=record["args"],
                    result=record["result"],
                    error=record["error"],
                    duration_ms=record["duration_ms"],
                )
            events.put({
                "type": "done",
                "payload": {
                    "status": "success",
                    "conversation_id": conv.id,
                    "conversation_title": conv.title,
                    "agent_slug": conv.agent.slug if conv.agent else None,
                    "engine": "codex-app-server",
                    "reply": _message_payload(assistant),
                },
            })
        except Exception as exc:
            events.put({"type": "error", "message": f"Codex App Server: {exc}"})
        finally:
            from django.db import connection

            connection.close()
            events.put(sentinel)

    def stream():
        yield ": stream start\n\n"
        threading.Thread(target=worker, daemon=True).start()
        while True:
            try:
                event = events.get(timeout=15)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if event is sentinel:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
