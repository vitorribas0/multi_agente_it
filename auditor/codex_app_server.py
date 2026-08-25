"""Cliente mínimo do Codex App Server para o chat Angular.

Cada turno abre uma conexão stdio curta. O thread do Codex é persistido pelo
próprio Codex e seu id fica em ``Conversation.state``; assim o Django pode
reiniciar sem perder o contexto e não precisa manter um subprocesso global.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from hashlib import sha256
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
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CODEX_HOME = _PROJECT_ROOT / "runtime" / "codex_home"
_AUTH_FINGERPRINT_FILENAME = ".atena-openai-key.sha256"
_DEFAULT_MODEL = "gpt-5.6-terra"
_DEFAULT_REASONING_EFFORT = "medium"

# Provedor IARA (via adaptador Responses local). Ver readm_implementar_iara.md
# e auditor/iara_adapter.py. Config gerada dinamicamente em runtime/codex_home.
_IARA_PROVIDER_ID = "iara"
_MANAGED_CONFIG_MARKER = "# managed-by: atena-iara"
_MANAGED_CONFIG_FILENAME = "config.toml"


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


def _bundled_codex_runtime() -> tuple[Path, Path | None]:
    """Resolve o runtime versionado instalado com o SDK oficial.

    O pacote ``openai-codex`` depende de ``openai-codex-cli-bin`` na mesma
    versão. Isso mantém o binário fora do Git, mas o torna uma dependência
    reproduzível do projeto em vez de reutilizar o Codex do ChatGPT Desktop.
    """
    try:
        from codex_cli_bin import bundled_codex_path, bundled_path_dir
    except ImportError as exc:
        raise CodexAppServerError(
            "Runtime portátil do Codex não instalado. "
            "Execute 'pip install -r requirements.txt'."
        ) from exc

    executable = Path(bundled_codex_path()).resolve()
    if not executable.is_file():
        raise CodexAppServerError(
            "O pacote openai-codex está instalado, mas seu runtime não foi encontrado."
        )
    runtime_path = bundled_path_dir()
    return executable, Path(runtime_path).resolve() if runtime_path else None


# ── Provedor IARA (opcional, gated por ATENA_IARA_ENABLED) ───────────────

_iara_adapter_started = False
_iara_adapter_lock = threading.Lock()


def _iara_enabled() -> bool:
    return (os.environ.get("ATENA_IARA_ENABLED") or "").strip().lower() in {
        "1", "true", "yes", "on", "sim",
    }


def _iara_adapter_host_port() -> tuple[str, int]:
    host = (os.environ.get("ATENA_IARA_ADAPTER_HOST") or "127.0.0.1").strip()
    try:
        port = int(os.environ.get("ATENA_IARA_ADAPTER_PORT") or "8799")
    except ValueError:
        port = 8799
    return host, port


def _iara_adapter_base_url() -> str:
    host, port = _iara_adapter_host_port()
    return f"http://{host}:{port}/v1"


def _ensure_iara_adapter_running() -> None:
    """Sobe o adaptador Responses⇄IARA uma vez por processo (idempotente).

    Se a porta já estiver em uso (adaptador iniciado externamente ou por outra
    thread), assume que está no ar e segue — o Codex se conecta via HTTP.
    """
    global _iara_adapter_started
    with _iara_adapter_lock:
        if _iara_adapter_started:
            return
        host, port = _iara_adapter_host_port()
        try:
            from auditor.iara_adapter import start_in_thread

            start_in_thread(host, port)
        except OSError:
            # Porta ocupada => provavelmente já rodando; não é erro fatal.
            pass
        except Exception as exc:  # import/config inesperada
            raise CodexAppServerError(
                "Não foi possível iniciar o adaptador IARA local."
            ) from exc
        _iara_adapter_started = True


def _managed_config_path(codex_home: Path) -> Path:
    return codex_home / _MANAGED_CONFIG_FILENAME


def _toml_key(value: str) -> str:
    """Formata ``value`` como chave TOML entre aspas.

    Usa string literal (aspas simples) por padrão — essencial em Windows, onde
    ``C:\\Users\\...`` tem barras invertidas que quebrariam uma string básica.
    Só cai para string básica (com escape) se o valor contiver aspas simples.
    """
    if "'" not in value:
        return f"'{value}'"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write_iara_provider_config(codex_home: Path, project_cwd: Path) -> None:
    """Escreve config.toml apontando o Codex para o adaptador IARA local.

    O arquivo é totalmente gerenciado pela Atena (marcado no cabeçalho); só é
    sobrescrito/removido quando tem o marcador, preservando um config manual.

    Também declara o projeto (``project_cwd``) como ``trusted``. O Codex é
    lançado com esse mesmo ``cwd``; se a confiança já estiver no config ele não
    precisa reescrever o arquivo por conta própria — o que apagaria o marcador
    e derrubaria a próxima inicialização com "config existe e não é gerenciado".
    """
    token = (os.environ.get("ATENA_IARA_ADAPTER_TOKEN") or "").strip()
    lines = [
        _MANAGED_CONFIG_MARKER + " — gerado automaticamente; não edite à mão.",
        f'model = "{_configured_model()}"',
        f'model_provider = "{_IARA_PROVIDER_ID}"',
        f'model_reasoning_effort = "{_configured_reasoning_effort()}"',
        "",
        f"[model_providers.{_IARA_PROVIDER_ID}]",
        'name = "IARA GenAI (adaptador local)"',
        f'base_url = "{_iara_adapter_base_url()}"',
        'wire_api = "responses"',
        # Resiliência: retenta stream/request interrompidos e tolera janelas
        # longas sem bytes (modelos com raciocínio demoram a emitir a saída).
        "request_max_retries = 2",
        "stream_max_retries = 2",
        "stream_idle_timeout_ms = 300000",
        "",
        f"[projects.{_toml_key(str(project_cwd))}]",
        'trust_level = "trusted"',
    ]
    if token:
        # Codex envia Authorization: Bearer $ATENA_IARA_ADAPTER_TOKEN.
        lines.insert(9, 'env_key = "ATENA_IARA_ADAPTER_TOKEN"')
    content = "\n".join(lines) + "\n"

    config_path = _managed_config_path(codex_home)
    if config_path.exists():
        existing = config_path.read_text(encoding="utf-8", errors="replace")
        if _MANAGED_CONFIG_MARKER not in existing.splitlines()[:1] and \
                _MANAGED_CONFIG_MARKER not in existing[:120]:
            raise CodexAppServerError(
                f"{config_path} existe e não é gerenciado pela Atena; "
                "remova-o ou desative ATENA_IARA_ENABLED."
            )
    config_path.write_text(content, encoding="utf-8")
    try:
        config_path.chmod(0o600)
    except OSError:
        pass


def _remove_managed_config(codex_home: Path) -> None:
    """Remove o config.toml gerenciado (caminho OpenAI). Preserva config manual."""
    config_path = _managed_config_path(codex_home)
    try:
        if not config_path.is_file():
            return
        head = config_path.read_text(encoding="utf-8", errors="replace")[:120]
        if _MANAGED_CONFIG_MARKER in head:
            config_path.unlink()
    except OSError:
        pass


def codex_runtime_available() -> bool:
    """Indica se o runtime empacotado e uma autenticação possível existem."""
    try:
        _bundled_codex_runtime()
    except CodexAppServerError:
        return False
    if _iara_enabled():
        # No caminho IARA a autenticação é feita pelo SDK (client_id/secret).
        return bool(
            os.environ.get("IARA_CLIENT_ID") and os.environ.get("IARA_CLIENT_SECRET")
        )
    codex_home = _codex_home_path()
    return bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENAI_ADMIN_KEY")
        or (codex_home / "auth.json").is_file()
    )


def _codex_home_path() -> Path:
    configured = (os.environ.get("ATENA_CODEX_HOME") or "").strip()
    if not configured:
        return _DEFAULT_CODEX_HOME
    path = Path(configured).expanduser()
    return (path if path.is_absolute() else _PROJECT_ROOT / path).resolve()


def _prepare_codex_home() -> Path:
    codex_home = _codex_home_path()
    codex_home.mkdir(parents=True, exist_ok=True)
    try:
        codex_home.chmod(0o700)
    except OSError:
        pass
    return codex_home


def _thread_config() -> dict:
    """Config passada em thread/start e thread/resume.

    Com o provedor IARA usamos o adaptador em modo *passthrough* (repassa a
    Responses API nativa para ``client.responses`` do SDK iaragenai), então as
    ferramentas nativas do Codex — inclusive o ``functions.exec`` — funcionam
    sem tradução e não é preciso mexer no "code mode".
    """
    return {"features.default_mode_request_user_input": True}


def _configured_model() -> str:
    if _iara_enabled():
        # O id vai ao adaptador, que deriva o provider IARA a partir dele.
        model = (
            os.environ.get("IARA_MODEL")
            or os.environ.get("ATENA_CODEX_MODEL")
            or _DEFAULT_MODEL
        )
        return model.strip()
    return (os.environ.get("ATENA_CODEX_MODEL") or _DEFAULT_MODEL).strip()


def _configured_reasoning_effort() -> str:
    configured = (
        os.environ.get("ATENA_CODEX_REASONING_EFFORT")
        or _DEFAULT_REASONING_EFFORT
    ).strip().lower()
    allowed = {"minimal", "low", "medium", "high", "xhigh"}
    return configured if configured in allowed else _DEFAULT_REASONING_EFFORT


def _ensure_api_key_auth(
    executable: Path,
    process_env: dict[str, str],
    codex_home: Path,
) -> None:
    """Inicializa a autenticação própria da Atena sem reutilizar ``~/.codex``."""
    auth_file = codex_home / "auth.json"
    api_key = (
        process_env.get("OPENAI_API_KEY")
        or process_env.get("OPENAI_ADMIN_KEY")
        or ""
    ).strip()
    if not api_key:
        if auth_file.is_file():
            return
        raise CodexAppServerError(
            "Credencial OpenAI ausente. Configure OPENAI_API_KEY no arquivo .env."
        )

    fingerprint = sha256(api_key.encode("utf-8")).hexdigest()
    fingerprint_file = codex_home / _AUTH_FINGERPRINT_FILENAME
    try:
        cached_fingerprint = fingerprint_file.read_text(encoding="utf-8").strip()
    except OSError:
        cached_fingerprint = ""
    if auth_file.is_file() and cached_fingerprint == fingerprint:
        return

    try:
        completed = subprocess.run(
            [str(executable), "login", "--with-api-key"],
            input=f"{api_key}\n",
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=process_env,
            timeout=_STARTUP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexAppServerError(
            "Não foi possível inicializar a autenticação OpenAI da Atena."
        ) from exc
    if completed.returncode != 0 or not auth_file.is_file():
        raise CodexAppServerError(
            "A credencial OpenAI não pôde ser registrada pelo runtime da Atena."
        )
    try:
        fingerprint_file.write_text(fingerprint, encoding="utf-8")
        fingerprint_file.chmod(0o600)
    except OSError:
        pass


_DEVELOPER_INSTRUCTIONS = (
    "Atue como assistente de auditoria. Responda em português do Brasil e use "
    "as skills do repositório quando forem pertinentes. O diretório de trabalho "
    "é a área isolada e temporária desta conversa: quando o usuário pedir um "
    "arquivo, crie de fato o arquivo final diretamente nesse diretório; a aplicação "
    "o publicará na pasta de saída da conversa, em vez "
    "de apenas mostrar código ou instruções. Você pode gerar XLSX, CSV, PDF e "
    "HTML. Para planilhas, use as bibliotecas Python instaladas com o projeto, como "
    "openpyxl, xlsxwriter ou pandas, e valide o arquivo antes de entregá-lo. Não "
    "dependa de node_modules, container_tools ou arquivos fora deste projeto. Não altere arquivos "
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
        executable, runtime_path = _bundled_codex_runtime()
        self.cwd = cwd.resolve()
        self._send_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._active_thread_id: str | None = None
        self._active_turn_id: str | None = None
        self._interrupt_requested = threading.Event()
        self._interrupt_sent = threading.Event()
        process_env = os.environ.copy()
        codex_home = _prepare_codex_home()
        process_env["CODEX_HOME"] = str(codex_home)
        path_entries = [str(Path(sys.executable).resolve().parent)]
        if runtime_path and runtime_path.is_dir():
            path_entries.insert(0, str(runtime_path))
        process_env["PATH"] = os.pathsep.join(
            [*path_entries, process_env.get("PATH", "")]
        )
        process_env["ATENA_PYTHON_EXECUTABLE"] = str(Path(sys.executable).resolve())
        if _iara_enabled():
            # Provedor IARA: Codex fala com o adaptador Responses local, que
            # traduz para o SDK iaragenai (autenticação via client_id/secret).
            _ensure_iara_adapter_running()
            _write_iara_provider_config(codex_home, self.cwd)
        else:
            # Caminho OpenAI padrão (default). Garante que uma config IARA
            # gerenciada anterior não redirecione o Codex indevidamente.
            _remove_managed_config(codex_home)
            _ensure_api_key_auth(executable, process_env, codex_home)
        # Canal binário: o protocolo JSONL do Codex exige UTF-8 estrito e linhas
        # separadas por "\n". Em modo texto o Windows usaria a codepage local
        # (cp1252) — quebrando em acentos/emojis ("stream did not contain valid
        # UTF-8") — e traduziria "\n" em "\r\n", corrompendo o enquadramento.
        # Fazemos encode/decode UTF-8 nós mesmos para um comportamento idêntico
        # em Windows e Unix.
        self.process = subprocess.Popen(
            [str(executable), "app-server", "--listen", "stdio://"],
            cwd=str(self.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=process_env,
        )
        self._stderr: list[str] = []
        self._stdout_messages: "queue.Queue[dict | object]" = queue.Queue()
        self._stdout_closed = object()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        threading.Thread(target=self._drain_stdout, daemon=True).start()

    def _drain_stderr(self) -> None:
        if not self.process.stderr:
            return
        for raw in self.process.stderr:
            line = raw.decode("utf-8", "replace")
            self._stderr.append(line.rstrip())
            if len(self._stderr) > 30:
                self._stderr.pop(0)

    def _drain_stdout(self) -> None:
        """Lê o protocolo sem permitir que uma resposta ausente bloqueie para sempre."""
        if not self.process.stdout:
            self._stdout_messages.put(self._stdout_closed)
            return
        try:
            for raw in self.process.stdout:
                line = raw.decode("utf-8", "replace").strip()
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
                data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
                self.process.stdin.write(data)
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
                    "model": _configured_model(),
                    "config": _thread_config(),
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
                "model": _configured_model(),
                "config": _thread_config(),
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
                "model": _configured_model(),
                "effort": _configured_reasoning_effort(),
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
