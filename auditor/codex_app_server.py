"""Cliente mínimo do Codex App Server para o chat Angular.

Cada turno abre uma conexão stdio curta. O thread do Codex é persistido pelo
próprio Codex e seu id fica em ``Conversation.state``; assim o Django pode
reiniciar sem perder o contexto e não precisa manter um subprocesso global.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Iterator


_TRACE_ITEM_TYPES = {
    "commandExecution",
    "fileChange",
    "mcpToolCall",
    "dynamicToolCall",
    "collabToolCall",
    "webSearch",
    "imageView",
    "contextCompaction",
}


class CodexAppServerError(RuntimeError):
    pass


_DEVELOPER_INSTRUCTIONS = (
    "Atue como assistente de auditoria. Responda em português do Brasil e use "
    "as skills do repositório quando forem pertinentes. Não altere arquivos do "
    "projeto durante este chat. Converse naturalmente em saudações e perguntas "
    "gerais. Não mencione diretório de trabalho, manifesto, nomes de arquivos "
    "internos, sandbox ou infraestrutura, salvo quando o usuário perguntar ou "
    "quando isso for indispensável para explicar uma análise solicitada. Só "
    "inspecione os arquivos da sessão quando o pedido envolver dados anexados. "
    "Quando o usuário pedir relatório ou dashboard HTML, entregue um documento "
    "HTML completo, standalone e sem recursos externos após um resumo curto; a "
    "aplicação converterá o documento automaticamente em artefato visualizável. "
    "Quando o pedido for para alterar, ampliar ou continuar um HTML já criado, "
    "reescreva e devolva o documento HTML completo atualizado. Nunca responda com "
    "CSS isolado, fragmentos, diffs ou instruções de edição manual."
)


class CodexAppServer:
    def __init__(self, cwd: Path):
        executable = shutil.which("codex")
        if not executable:
            raise CodexAppServerError("Executável 'codex' não encontrado no PATH.")
        self.cwd = cwd.resolve()
        self.process = subprocess.Popen(
            [executable, "app-server", "--stdio"],
            cwd=str(self.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=os.environ.copy(),
        )
        self._stderr: list[str] = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        if not self.process.stderr:
            return
        for line in self.process.stderr:
            self._stderr.append(line.rstrip())
            if len(self._stderr) > 30:
                self._stderr.pop(0)

    def _send(self, payload: dict) -> None:
        if not self.process.stdin:
            raise CodexAppServerError("Canal de entrada do Codex indisponível.")
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _messages(self) -> Iterator[dict]:
        if not self.process.stdout:
            raise CodexAppServerError("Canal de saída do Codex indisponível.")
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
        detail = "\n".join(self._stderr[-5:])
        raise CodexAppServerError(detail or "Codex App Server encerrou inesperadamente.")

    def _wait_response(self, request_id: int) -> dict:
        for message in self._messages():
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                raise CodexAppServerError(error.get("message", str(error)))
            return message.get("result") or {}
        raise CodexAppServerError("Resposta não recebida do Codex App Server.")

    def initialize(self) -> None:
        self._send({
            "method": "initialize",
            "id": 1,
            "params": {
                "clientInfo": {
                    "name": "multi_agente_it",
                    "title": "Multi-Agentes Auditoria",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": True},
            },
        })
        self._wait_response(1)
        self._send({"method": "initialized", "params": {}})

    def open_thread(self, thread_id: str | None = None) -> str:
        if thread_id:
            self._send({
                "method": "thread/resume",
                "id": 2,
                "params": {
                    "threadId": thread_id,
                    "cwd": str(self.cwd),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "developerInstructions": _DEVELOPER_INSTRUCTIONS,
                },
            })
            try:
                result = self._wait_response(2)
                return result["thread"]["id"]
            except (CodexAppServerError, KeyError, TypeError):
                # Thread removido/incompatível: inicia outro sem derrubar o chat.
                pass

        self._send({
            "method": "thread/start",
            "id": 3,
            "params": {
                "cwd": str(self.cwd),
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "developerInstructions": _DEVELOPER_INSTRUCTIONS,
            },
        })
        result = self._wait_response(3)
        try:
            return result["thread"]["id"]
        except (KeyError, TypeError) as exc:
            raise CodexAppServerError("Resposta de thread/start inválida.") from exc

    def turn(self, thread_id: str, prompt: str) -> Iterator[dict]:
        self._send({
            "method": "turn/start",
            "id": 4,
            "params": {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
            },
        })

        message_phases: dict[str, str | None] = {}
        for message in self._messages():
            if message.get("id") == 4 and "error" in message:
                error = message["error"]
                raise CodexAppServerError(error.get("message", str(error)))

            method = message.get("method")
            params = message.get("params") or {}
            if method == "item/agentMessage/delta":
                delta = params.get("delta") or ""
                if delta:
                    yield {
                        "type": "delta",
                        "text": delta,
                        "phase": message_phases.get(params.get("itemId")),
                    }
            elif method == "item/started":
                item = params.get("item") or {}
                item_type = item.get("type")
                if item_type == "agentMessage":
                    message_phases[item.get("id", "")] = item.get("phase")
                if item_type in _TRACE_ITEM_TYPES:
                    yield {
                        "type": "activity",
                        "phase": "started",
                        "activity": item_type,
                        "item": item,
                    }
            elif method == "item/completed":
                item = params.get("item") or {}
                item_type = item.get("type")
                if item_type == "agentMessage":
                    message_phases[item.get("id", "")] = item.get("phase")
                if item_type in _TRACE_ITEM_TYPES:
                    yield {
                        "type": "activity",
                        "phase": "completed",
                        "activity": item_type,
                        "item": item,
                    }
            elif method == "item/commandExecution/outputDelta":
                delta = params.get("delta") or ""
                if delta:
                    yield {
                        "type": "activity_output",
                        "item_id": params.get("itemId"),
                        "text": delta,
                    }
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                yield {
                    "type": "completed",
                    "status": turn.get("status"),
                    "error": turn.get("error"),
                }
                return

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def __enter__(self) -> "CodexAppServer":
        return self

    def __exit__(self, *_args) -> None:
        self.close()
