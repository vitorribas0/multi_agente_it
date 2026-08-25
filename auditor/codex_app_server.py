"""Cliente mínimo do Codex App Server para o chat Angular.

Cada turno abre uma conexão stdio curta. O thread do Codex é persistido pelo
próprio Codex e seu id fica em ``Conversation.state``; assim o Django pode
reiniciar sem perder o contexto e não precisa manter um subprocesso global.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Iterator


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

_APPROVAL_POLICY = "untrusted"


def _env_timeout(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


_STARTUP_TIMEOUT_SECONDS = _env_timeout(
    "ATENA_CODEX_STARTUP_TIMEOUT_SECONDS", 30, 5
)
_TURN_IDLE_TIMEOUT_SECONDS = _env_timeout(
    "ATENA_CODEX_IDLE_TIMEOUT_SECONDS", 300, 30
)
_SERVER_REQUEST_METHODS = {
    "item/tool/requestUserInput",
    "tool/requestUserInput",  # compatibilidade com versões anteriores
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
}


class CodexAppServerError(RuntimeError):
    pass


_DEVELOPER_INSTRUCTIONS = (
    "Atue como assistente de auditoria. Responda em português do Brasil e use "
    "as skills do repositório quando forem pertinentes. O diretório de trabalho "
    "é a área isolada e temporária desta conversa: quando o usuário pedir um "
    "arquivo, crie de fato o arquivo final diretamente nesse diretório; a aplicação "
    "o publicará na pasta de saída da conversa, em vez "
    "de apenas mostrar código ou instruções. Você pode gerar XLSX, CSV, PDF e "
    "HTML. Para planilhas, siga a skill Spreadsheets e use o @oai/artifact-tool; "
    "o node_modules e o container_tools oficiais já estão disponíveis no diretório "
    "atual. Não use openpyxl, xlsxwriter ou pandas.ExcelWriter. Não altere arquivos "
    "fora do diretório atual. Os dados de entrada da sessão, quando existirem, "
    "podem ser localizados em ../manifesto_sessao.json e são somente leitura. "
    "Converse naturalmente em saudações e perguntas "
    "gerais. Não mencione diretório de trabalho, manifesto, nomes de arquivos "
    "internos, sandbox ou infraestrutura, salvo quando o usuário perguntar ou "
    "quando isso for indispensável para explicar uma análise solicitada. Só "
    "inspecione os arquivos da sessão quando o pedido envolver dados anexados. "
    "Quando o usuário pedir relatório ou dashboard HTML, entregue um documento "
    "HTML completo, standalone e sem recursos externos, salve o .html no diretório "
    "atual e responda com um resumo curto; a aplicação publicará o arquivo como "
    "artefato visualizável. "
    "Quando o pedido for para alterar, ampliar ou continuar um HTML já criado, "
    "reescreva e devolva o documento HTML completo atualizado. Nunca responda com "
    "CSS isolado, fragmentos, diffs ou instruções de edição manual. Para tarefas "
    "com várias etapas, publique um plano curto e atualize o andamento durante a "
    "execução. Quando faltar uma escolha necessária do usuário, use a pergunta "
    "interativa em vez de encerrar o turno pedindo esclarecimento em texto."
)


class CodexAppServer:
    def __init__(self, cwd: Path):
        executable = shutil.which("codex")
        if not executable:
            raise CodexAppServerError("Executável 'codex' não encontrado no PATH.")
        self.cwd = cwd.resolve()
        runtime_root = (
            Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime"
        )
        dependencies = runtime_root / "dependencies"
        node_bin = dependencies / "node" / "bin"
        node_modules = dependencies / "node" / "node_modules"
        bundled_python = dependencies / "python" / "bin" / "python3"
        spreadsheet_tools = (
            runtime_root
            / "plugins"
            / "openai-primary-runtime"
            / "plugins"
            / "spreadsheets"
            / "skills"
            / "spreadsheets"
            / "container_tools"
        )
        self._send_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._active_thread_id: str | None = None
        self._active_turn_id: str | None = None
        self._interrupt_requested = threading.Event()
        self._interrupt_sent = threading.Event()
        self._ensure_runtime_link("node_modules", node_modules)
        self._ensure_runtime_link("container_tools", spreadsheet_tools)
        process_env = os.environ.copy()
        if node_bin.is_dir():
            process_env["PATH"] = f"{node_bin}{os.pathsep}{process_env.get('PATH', '')}"
        if node_modules.is_dir():
            process_env["NODE_PATH"] = str(node_modules)
        process_env["AUDITOR_ARTIFACT_PYTHON"] = str(
            bundled_python if bundled_python.is_file() else Path(sys.executable)
        )
        self.process = subprocess.Popen(
            [executable, "app-server", "--stdio"],
            cwd=str(self.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=process_env,
        )
        self._stderr: list[str] = []
        self._stdout_messages: "queue.Queue[dict | object]" = queue.Queue()
        self._stdout_closed = object()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        threading.Thread(target=self._drain_stdout, daemon=True).start()

    def _ensure_runtime_link(self, name: str, target: Path) -> None:
        """Expõe dependências oficiais no workspace sem copiá-las ou alterá-las."""
        if not target.is_dir():
            return
        link = self.cwd / name
        if link.exists():
            return
        if link.is_symlink():
            try:
                link.unlink()
            except OSError:
                return
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            pass

    def _drain_stderr(self) -> None:
        if not self.process.stderr:
            return
        for line in self.process.stderr:
            self._stderr.append(line.rstrip())
            if len(self._stderr) > 30:
                self._stderr.pop(0)

    def _drain_stdout(self) -> None:
        """Lê o protocolo sem permitir que uma resposta ausente bloqueie para sempre."""
        if not self.process.stdout:
            self._stdout_messages.put(self._stdout_closed)
            return
        try:
            for line in self.process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._stdout_messages.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
        finally:
            self._stdout_messages.put(self._stdout_closed)

    def _send(self, payload: dict) -> None:
        with self._send_lock:
            if self.process.poll() is not None or not self.process.stdin:
                raise CodexAppServerError("Canal de entrada do Codex indisponível.")
            try:
                self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise CodexAppServerError(
                    "Codex App Server encerrou antes de receber a solicitação."
                ) from exc

    def _remember_active_turn(self, thread_id: str, turn: dict) -> str | None:
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not turn_id:
            return None
        turn_id = str(turn_id)
        with self._turn_lock:
            self._active_thread_id = thread_id
            self._active_turn_id = turn_id
        self._send_interrupt_if_ready()
        return turn_id

    def _send_interrupt_if_ready(self) -> bool:
        with self._turn_lock:
            if (
                not self._interrupt_requested.is_set()
                or self._interrupt_sent.is_set()
                or not self._active_thread_id
                or not self._active_turn_id
            ):
                return False
            thread_id = self._active_thread_id
            turn_id = self._active_turn_id
            self._interrupt_sent.set()
        try:
            self._send({
                "method": "turn/interrupt",
                "id": 5,
                "params": {"threadId": thread_id, "turnId": turn_id},
            })
        except Exception:
            self._interrupt_sent.clear()
            raise
        return True

    def interrupt(self) -> bool:
        """Solicita a interrupção do turno ativo pelo protocolo do App Server.

        Se o clique chegar antes da resposta de ``turn/start``, a intenção fica
        registrada e a interrupção é enviada assim que o ``turnId`` aparecer.
        """
        self._interrupt_requested.set()
        return self._send_interrupt_if_ready()

    def _messages(self, timeout_seconds: int | None = None) -> Iterator[dict]:
        timeout_seconds = timeout_seconds or _TURN_IDLE_TIMEOUT_SECONDS
        while True:
            try:
                message = self._stdout_messages.get(timeout=timeout_seconds)
            except queue.Empty as exc:
                raise CodexAppServerError(
                    "A Atena não recebeu resposta do modelo dentro do tempo limite. "
                    "Verifique a credencial OpenAI e a conexão de rede."
                ) from exc
            if message is self._stdout_closed:
                detail = "\n".join(self._stderr[-5:])
                raise CodexAppServerError(
                    detail or "Codex App Server encerrou inesperadamente."
                )
            if isinstance(message, dict):
                yield message

    def _wait_response(self, request_id: int) -> dict:
        for message in self._messages(_STARTUP_TIMEOUT_SECONDS):
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
                    "approvalPolicy": _APPROVAL_POLICY,
                    "sandbox": "workspace-write",
                    "config": {"features.default_mode_request_user_input": True},
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
                "approvalPolicy": _APPROVAL_POLICY,
                "sandbox": "workspace-write",
                "config": {"features.default_mode_request_user_input": True},
                "developerInstructions": _DEVELOPER_INSTRUCTIONS,
            },
        })
        result = self._wait_response(3)
        try:
            return result["thread"]["id"]
        except (KeyError, TypeError) as exc:
            raise CodexAppServerError("Resposta de thread/start inválida.") from exc

    def turn(
        self,
        thread_id: str,
        prompt: str,
        server_request_handler: Callable[[str, dict], dict] | None = None,
        turn_started_handler: Callable[[str], None] | None = None,
    ) -> Iterator[dict]:
        with self._turn_lock:
            self._active_thread_id = thread_id
            self._active_turn_id = None
            self._interrupt_sent.clear()
        self._send({
            "method": "turn/start",
            "id": 4,
            "params": {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
                "cwd": str(self.cwd),
                "approvalPolicy": _APPROVAL_POLICY,
                "sandboxPolicy": {
                    "type": "workspaceWrite",
                    "writableRoots": [str(self.cwd)],
                    "networkAccess": True,
                },
            },
        })

        message_phases: dict[str, str | None] = {}
        try:
            for message in self._messages():
                if message.get("id") == 4:
                    if "error" in message:
                        error = message["error"]
                        raise CodexAppServerError(error.get("message", str(error)))
                    result = message.get("result") or {}
                    turn_id = self._remember_active_turn(
                        thread_id, result.get("turn") or {}
                    )
                    if turn_id and turn_started_handler is not None:
                        turn_started_handler(turn_id)

                method = message.get("method")
                params = message.get("params") or {}
                if method == "turn/started":
                    turn_id = self._remember_active_turn(
                        thread_id, params.get("turn") or {}
                    )
                    if turn_id and turn_started_handler is not None:
                        turn_started_handler(turn_id)
                elif message.get("id") is not None and method in _SERVER_REQUEST_METHODS:
                    if server_request_handler is None:
                        if method == "item/permissions/requestApproval":
                            result = {"permissions": {}, "scope": "turn"}
                        elif method in {"item/tool/requestUserInput", "tool/requestUserInput"}:
                            result = {"answers": {}}
                        else:
                            result = {"decision": "decline"}
                    else:
                        result = server_request_handler(method, params)
                    self._send({"id": message["id"], "result": result})
                elif method == "turn/plan/updated":
                    yield {
                        "type": "plan",
                        "explanation": params.get("explanation") or "",
                        "plan": params.get("plan") or [],
                    }
                elif method == "item/agentMessage/delta":
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
        finally:
            with self._turn_lock:
                self._active_thread_id = None
                self._active_turn_id = None

    def close(self) -> None:
        with self._close_lock:
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
