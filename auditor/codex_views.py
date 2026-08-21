"""Endpoints SSE que adaptam o Codex App Server ao contrato do frontend."""

from __future__ import annotations

import json
import queue
import re
import threading
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .codex_app_server import CodexAppServer, CodexAppServerError
from .models import Message
from .views import _message_payload, _resolve_conversation


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
    session_hint = (
        "Os dados anexados a esta conversa, quando existirem, estão materializados "
        "no diretório atual. Leia primeiro manifesto_sessao.json; ele aponta para "
        "dataset_atual.json e para todas as abas/datasets nomeados."
    )
    if has_codex_thread:
        return f"{session_hint}\n\n{text}"
    previous = list(conv.messages.order_by("created_at").values("role", "content"))[-12:]
    if not previous:
        return f"{session_hint}\n\n{text}"
    history = "\n".join(
        f"{('Usuário' if row['role'] == 'user' else 'Assistente')}: {row['content']}"
        for row in previous if row["content"]
    )
    return (
        "Considere este histórico importado da interface antiga apenas como contexto:\n"
        f"<historico>\n{history}\n</historico>\n\n"
        f"{session_hint}\n\nPedido atual do usuário: {text}"
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
    prompt = _prompt_with_history(conv, text, bool(old_thread_id))
    Message.objects.create(conversation=conv, role="user", content=text)

    events: "queue.Queue[dict]" = queue.Queue()
    sentinel = object()

    def worker() -> None:
        answer_parts: list[str] = []
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

                activity_labels = {
                    "commandExecution": "Executando análise no sandbox",
                    "mcpToolCall": "Consultando ferramenta autorizada",
                    "webSearch": "Pesquisando fonte externa",
                }
                for event in client.turn(thread_id, prompt):
                    if event["type"] == "delta" and event.get("phase") != "commentary":
                        answer_parts.append(event["text"])
                    elif event["type"] == "activity":
                        label = activity_labels.get(event["activity"], "Processando")
                        events.put({"type": "progress", "stage": "tool", "icon": "⚙️", "text": label})
                    elif event["type"] == "completed" and event.get("status") != "completed":
                        raise CodexAppServerError(str(event.get("error") or "Turno não concluído."))

            answer = "".join(answer_parts).strip()
            if not answer:
                answer = "O Codex concluiu o turno sem produzir uma mensagem de texto."
            assistant = Message.objects.create(conversation=conv, role="assistant", content=answer)
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
