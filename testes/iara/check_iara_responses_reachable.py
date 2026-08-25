"""Verifica se o endpoint Responses do IARA é alcançável deste ambiente.

O adaptador do Codex faz passthrough para ``client.responses`` do SDK iaragenai,
que roteia ``/responses`` pelo gateway SSE (``base_url_sse`` = agent-gateway).
Em alguns ambientes de desenvolvimento esse host não é resolvível/alcançável,
embora o gateway padrão (chat.completions) seja. Rode este script no ambiente
onde o app vai rodar para confirmar que o caminho Codex↔IARA funcionará.

    set -a && source .env && set +a
    python -m testes.iara.check_iara_responses_reachable

Saída 0 = Responses alcançável (Codex↔IARA vai funcionar).
Saída 1 = Responses inalcançável (ver a causa impressa).
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

from auditor import iara_adapter as A


def main() -> int:
    # Carrega o .env do projeto para funcionar em qualquer shell (inclusive
    # cmd/PowerShell no Windows), sem depender de `source .env`.
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except Exception:
        pass

    if not (os.environ.get("IARA_CLIENT_ID") and os.environ.get("IARA_CLIENT_SECRET")):
        print("ERRO: IARA_CLIENT_ID/IARA_CLIENT_SECRET ausentes (carregue o .env).")
        return 2

    model = os.getenv("IARA_MODEL", "gpt-5.6-terra")
    provider = A._provider_for(model)
    print(f"environment={os.getenv('IARA_ENVIRONMENT', 'homol')} "
          f"provider={provider} model={model}")

    client = A._make_iara_client(provider)
    gc = client.responses.client  # client genai_api interno do SDK
    base = gc.base_url
    base_sse = getattr(gc, "base_url_sse", base)
    print(f"base_url     (chat)      : {base}")
    print(f"base_url_sse (responses) : {base_sse}")

    # 1) DNS do host do gateway SSE (onde /responses vive)
    host = urlparse(str(base_sse)).hostname or ""
    try:
        ip = socket.gethostbyname(host)
        print(f"DNS {host} -> {ip}  ✓")
    except Exception as e:
        print(f"DNS {host} FALHOU: {e}  ✗")
        print("\nRESULTADO: Responses INALCANÇÁVEL (o host do gateway SSE não "
              "resolve neste ambiente). O Codex↔IARA só funcionará onde esse "
              "host for alcançável (rede Itaú/ECS).")
        return 1

    # 2) Chamada real mínima ao /responses (não-streaming)
    try:
        resp = client.responses.create(model=model, input="responda apenas: pong",
                                        stream=False, enable_polling=False)
        text = (getattr(resp, "output_text", "") or "")[:60]
        print(f"responses.create OK; output_text={text!r}  ✓")
        print("\nRESULTADO: Responses ALCANÇÁVEL — o Codex↔IARA vai funcionar aqui ✅")
        return 0
    except Exception as e:
        cause = getattr(e, "__cause__", None)
        print(f"responses.create FALHOU: {type(e).__name__}: {str(e)[:120]}  ✗")
        if cause is not None:
            print(f"  causa: {type(cause).__name__}: {str(cause)[:160]}")
        print("\nRESULTADO: Responses INALCANÇÁVEL a partir daqui.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
