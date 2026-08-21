"""
Tool de geração de fluxograma (diagrama Mermaid) do processo executado.

O agente escreve o código Mermaid; esta tool valida levemente e publica
o diagrama como attachment da mensagem (`kind: "mermaid"`). O frontend
renderiza um card bonito com o diagrama, botão de expandir e download
em PNG / SVG / .mmd — análogo ao card-tabela e ao card de export.
"""
import json
import re

from .registry import tool, publish_attachment


# Tipos de diagrama Mermaid reconhecidos no início do código. Usado só para
# uma validação leve (ajuda o agente a perceber quando esqueceu o cabeçalho),
# nunca para bloquear — o render de verdade é no navegador.
_MERMAID_HEADS = (
    "graph", "flowchart", "sequencediagram", "classdiagram", "statediagram",
    "erdiagram", "journey", "gantt", "pie", "mindmap", "timeline",
    "gitgraph", "quadrantchart", "requirementdiagram", "c4context",
)


def _err(msg: str) -> str:
    return json.dumps({"erro": msg}, ensure_ascii=False)


def _strip_fences(code: str) -> str:
    """Remove cercas markdown ```mermaid ... ``` que o modelo às vezes inclui."""
    code = code.strip()
    if code.startswith("```"):
        # tira a primeira linha (```mermaid ou ```) e a última cerca
        code = re.sub(r"^```[a-zA-Z]*\s*\n", "", code)
        code = re.sub(r"\n?```\s*$", "", code)
    return code.strip()


@tool(
    description=(
        "Gera um FLUXOGRAMA do processo/análise e o exibe no chat como um "
        "diagrama bonito, com botão de expandir e download (PNG, SVG e .mmd).\n\n"
        "USE quando o usuário pedir um fluxograma, diagrama, mapa do processo "
        "ou 'desenha o passo a passo do que você fez'. Resuma visualmente o "
        "fluxo executado (etapas, decisões, ramificações).\n\n"
        "O parâmetro `mermaid` recebe o código Mermaid COMPLETO e válido. "
        "Comece com o tipo do diagrama — normalmente `flowchart TD` (top-down) "
        "ou `flowchart LR` (left-right). Exemplo:\n"
        "flowchart TD\n"
        "  A[Início] --> B{Tem dataset?}\n"
        "  B -- Sim --> C[Filtra dados]\n"
        "  B -- Não --> D[Consulta base FQ]\n"
        "  C --> E[Exporta resultado]\n\n"
        "DICAS para ficar bonito: rótulos curtos e claros; use {losango} para "
        "decisões e [retângulo] para etapas; não cole o link nem repita o "
        "código na resposta — o card aparece sozinho no chat."
    ),
    icon="🗺️",
)
def gerar_fluxograma(_session: dict, mermaid: str, titulo: str = "") -> str:
    """Publica um fluxograma Mermaid como card no chat.

    Args:
        mermaid: Código Mermaid completo e válido (ex.: começando com 'flowchart TD').
        titulo: Título curto exibido no topo do card. Default: 'Fluxograma do processo'.
    """
    code = _strip_fences(mermaid or "")
    if not code:
        return _err("Código Mermaid vazio. Forneça o diagrama em `mermaid`.")

    head = code.lstrip().split(None, 1)[0].lower().replace("-", "")
    if head not in _MERMAID_HEADS:
        return _err(
            "O código não começa com um tipo de diagrama Mermaid válido "
            f"(ex.: 'flowchart TD'). Recebido: '{code.splitlines()[0][:40]}'."
        )

    titulo = (titulo or "").strip() or "Fluxograma do processo"

    payload = {
        "ok": True,
        "titulo": titulo,
        "linhas": len(code.splitlines()),
    }

    # Publica como attachment: o frontend renderiza o diagrama (mermaid.js),
    # fora do bloco colapsável da tool, igual ao card-tabela / card-export.
    publish_attachment(_session, {
        "kind": "mermaid",
        "code": code,
        **payload,
    })

    return json.dumps(payload, ensure_ascii=False)
