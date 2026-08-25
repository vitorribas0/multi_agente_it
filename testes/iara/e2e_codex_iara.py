"""Teste ponta a ponta REAL: Codex App Server -> adaptador -> IARA.

Sobe o CodexAppServer com ATENA_IARA_ENABLED=true, o que:
  1. inicia o adaptador Responses local,
  2. gera runtime/codex_home/config.toml (model_provider=iara, wire_api=responses),
  3. lança o binário openai-codex apontando para o adaptador,
  4. roda um turno e imprime a resposta que veio do IARA através do Codex.

Pré-requisitos (ambiente do usuário):
  - pip install openai-codex  (binário codex-cli-bin)
  - pip install iara_genai_sdk
  - .env com IARA_CLIENT_ID/SECRET reais
  - SEM a pasta shim ./iaragenai/ sombreando o SDK real

Uso:
    set -a && source .env && set +a
    export ATENA_IARA_ENABLED=true
    export ATENA_IARA_ADAPTER_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(24))")
    python -m testes.iara.e2e_codex_iara
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from auditor import codex_app_server as C
from auditor import iara_adapter as A


def main() -> int:
    if not C._iara_enabled():
        print("ERRO: defina ATENA_IARA_ENABLED=true antes de rodar.")
        return 2
    if not (os.environ.get("IARA_CLIENT_ID") and os.environ.get("IARA_CLIENT_SECRET")):
        print("ERRO: IARA_CLIENT_ID/IARA_CLIENT_SECRET ausentes (carregue o .env).")
        return 2
    if not C.codex_runtime_available():
        print("ERRO: runtime do Codex indisponível "
              "(pip install openai-codex) ou credenciais IARA ausentes.")
        return 2

    print(f"Modelo: {C._configured_model()} | provider derivado: "
          f"{A._provider_for(C._configured_model())}")
    print(f"Adaptador: {C._iara_adapter_base_url()} | "
          f"token setado? {'sim' if os.environ.get('ATENA_IARA_ADAPTER_TOKEN') else 'nao'}")

    workdir = Path(tempfile.mkdtemp(prefix="atena_e2e_"))
    print(f"Área de trabalho da sessão: {workdir}\n")

    prompt = ("Responda em uma única linha, sem ferramentas, exatamente: "
              "Codex conectado ao IARA com sucesso.")

    deltas: list[str] = []
    status = None
    error = None
    try:
        server = C.CodexAppServer(workdir)
    except C.CodexAppServerError as e:
        print(f"FALHA ao iniciar o Codex: {e}")
        return 1

    # mostra a config gerada para o provider
    cfg = C._managed_config_path(C._codex_home_path())
    if cfg.is_file():
        print("=== config.toml gerado ===")
        print(cfg.read_text())
        print("==========================\n")

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
                    print(f"\n[atividade {evt.get('phase')}: {evt.get('activity')}]")
                elif t == "completed":
                    status = evt.get("status")
                    error = evt.get("error")
    except C.CodexAppServerError as e:
        print(f"\nFALHA no turno: {e}")
        return 1

    print("\n--- fim ---")
    text = "".join(deltas).strip()
    print(f"status do turno: {status}")
    if error:
        print(f"erro do turno: {error}")
    print(f"texto agregado do agente:\n  {text or '(vazio)'}")

    ok = status == "completed" and bool(text)
    print("\n" + ("OK: Codex -> adaptador -> IARA funcionando ponta a ponta ✅"
                  if ok else "ATENÇÃO: turno não completou com texto."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
