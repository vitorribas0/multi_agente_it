"""Teste ponta a ponta REAL com USO DE FERRAMENTAS: Codex -> adaptador -> IARA.

Prova o caminho completo COM tool use (o que faltava): pede ao Codex para
criar um arquivo e verifica no disco que o arquivo foi realmente escrito.

Isso exercita o "code mode" nativo do Codex (tool ``functions.exec`` com
gramática Lark), que só funciona porque o adaptador agora repassa a Responses
API do IARA verbatim (``client.responses``), sem tradução.

Uso:
    set -a && source .env && set +a
    export ATENA_IARA_ENABLED=true
    export ATENA_IARA_ADAPTER_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(24))")
    python -m testes.iara.e2e_codex_iara_tools
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from auditor import codex_app_server as C
from auditor import iara_adapter as A

_TARGET = "ola_iara.txt"
_CONTENT = "oi do IARA via Codex"


def main() -> int:
    if not C._iara_enabled():
        print("ERRO: defina ATENA_IARA_ENABLED=true antes de rodar.")
        return 2
    if not (os.environ.get("IARA_CLIENT_ID") and os.environ.get("IARA_CLIENT_SECRET")):
        print("ERRO: IARA_CLIENT_ID/IARA_CLIENT_SECRET ausentes (carregue o .env).")
        return 2
    if not C.codex_runtime_available():
        print("ERRO: runtime do Codex indisponível (pip install openai-codex).")
        return 2

    print(f"Modelo: {C._configured_model()} | provider: "
          f"{A._provider_for(C._configured_model())}")

    workdir = Path(tempfile.mkdtemp(prefix="atena_e2e_tools_"))
    print(f"Área de trabalho da sessão: {workdir}\n")

    prompt = (f"Crie um arquivo chamado {_TARGET} no diretório de trabalho atual "
              f"com exatamente este conteúdo: {_CONTENT}\n"
              "Use suas ferramentas para escrever o arquivo. "
              "Ao terminar, responda apenas: PRONTO.")

    try:
        server = C.CodexAppServer(workdir)
    except C.CodexAppServerError as e:
        print(f"FALHA ao iniciar o Codex: {e}")
        return 1

    cfg = C._managed_config_path(C._codex_home_path())
    if cfg.is_file():
        print("=== config.toml gerado ===")
        print(cfg.read_text())
        print("==========================\n")

    deltas: list[str] = []
    status = None
    error = None
    try:
        with server:
            server.initialize()
            print("initialize OK")
            thread_id = server.open_thread()
            print(f"thread aberto: {thread_id}\n")
            print("--- streaming do turno ---")
            for evt in server.turn(thread_id, prompt):
                t = evt.get("type")
                if t == "delta":
                    deltas.append(evt.get("text", ""))
                    sys.stdout.write(evt.get("text", ""))
                    sys.stdout.flush()
                elif t == "activity":
                    item = evt.get("item") or {}
                    detail = item.get("command") or item.get("path") or item.get("type")
                    print(f"\n[atividade {evt.get('phase')}: {evt.get('activity')} :: {detail}]")
                elif t == "activity_output":
                    sys.stdout.write(evt.get("text", ""))
                    sys.stdout.flush()
                elif t == "completed":
                    status = evt.get("status")
                    error = evt.get("error")
    except C.CodexAppServerError as e:
        print(f"\nFALHA no turno: {e}")
        return 1

    print("\n--- fim ---")
    print(f"status do turno: {status}")
    if error:
        print(f"erro do turno: {error}")

    target = workdir / _TARGET
    exists = target.is_file()
    written = target.read_text(encoding="utf-8", errors="replace").strip() if exists else ""
    print(f"arquivo {_TARGET} existe? {exists}")
    if exists:
        print(f"conteúdo escrito: {written!r}")

    ok = status == "completed" and exists and _CONTENT in written
    print("\n" + ("OK: Codex USOU FERRAMENTAS via IARA e criou o arquivo ✅"
                  if ok else "ATENÇÃO: arquivo não foi criado como esperado."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
