"""
Tools de análise de documentos (PDF, DOCX, imagens) usando docling.

Convenção: o documento "atual" da conversa fica em
`_session['documento_atual']` com a forma:
    {
        "filename": "relatorio.pdf",
        "markdown": "<conteúdo extraído>",
        "char_count": 12345,
        "page_count": 7,        # quando disponível
    }

O upload (auditor.views.upload_table) roteia por extensão e popula esse
campo. As tools aqui apenas leem/buscam — não chamam docling de novo.
"""
import json
import re

from .registry import tool


def _err(msg: str) -> str:
    return json.dumps({"erro": msg}, ensure_ascii=False)


def _get_doc(_session: dict) -> dict | None:
    doc = _session.get("documento_atual")
    if not doc or not doc.get("markdown"):
        return None
    return doc


# ── Tools ───────────────────────────────────────────────────────────

@tool(
    description=(
        "Descreve o documento corrente: filename, total de caracteres, "
        "número de páginas (quando disponível) e a lista de "
        "títulos/seções (headings markdown) detectados, mais uma amostra "
        "do início. "
        "USE: PRIMEIRO PASSO obrigatório de qualquer análise de "
        "documento — sem isso você não conhece a estrutura. "
        "NÃO use: se já chamou no turno (resultado já está no histórico)."
    ),
    icon="📄",
)
def descrever_documento(_session: dict) -> str:
    """Descreve o documento corrente da sessão."""
    doc = _get_doc(_session)
    if doc is None:
        return _err("Nenhum documento na sessão. Peça ao usuário para anexar um PDF/DOCX/imagem.")

    md = doc["markdown"]
    headings = [
        {"nivel": len(m.group(1)), "titulo": m.group(2).strip()}
        for m in re.finditer(r"^(#{1,6})\s+(.+)$", md, flags=re.MULTILINE)
    ]
    return json.dumps(
        {
            "filename": doc.get("filename"),
            "char_count": doc.get("char_count", len(md)),
            "page_count": doc.get("page_count"),
            "total_headings": len(headings),
            "headings": headings[:50],
            "amostra_inicio": md[:600],
        },
        ensure_ascii=False,
    )


@tool(
    description=(
        "Lê um trecho do documento corrente em markdown. Por padrão "
        "devolve até 4000 caracteres a partir de 'offset' (default 0). "
        "USE: para ler conteúdo direto após localizar via "
        "buscar_no_documento, ou para leitura corrida em blocos. "
        "Para documentos grandes, faça leituras encadeadas movendo "
        "'offset'. NÃO use para procurar termo específico (use "
        "buscar_no_documento, é muito mais barato)."
    ),
    icon="📖",
)
def ler_documento(_session: dict, offset: int = 0, tamanho: int = 4000) -> str:
    """Lê um trecho do documento corrente.

    Args:
        offset: Posição em caracteres onde começar a leitura (default 0).
        tamanho: Quantidade de caracteres a ler (default 4000, máx 20000).
    """
    doc = _get_doc(_session)
    if doc is None:
        return _err("Nenhum documento na sessão.")

    md = doc["markdown"]
    total = len(md)
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    try:
        tamanho = max(100, min(20_000, int(tamanho)))
    except (TypeError, ValueError):
        tamanho = 4000

    trecho = md[offset: offset + tamanho]
    return json.dumps(
        {
            "filename": doc.get("filename"),
            "offset": offset,
            "tamanho_retornado": len(trecho),
            "tem_mais": (offset + tamanho) < total,
            "total_caracteres": total,
            "conteudo": trecho,
        },
        ensure_ascii=False,
    )


@tool(
    description=(
        "Busca um termo (case-insensitive) no documento corrente e "
        "retorna até 'top_n' ocorrências com contexto (~120 chars antes/"
        "depois) e o offset de cada match. "
        "USE: para localizar onde um tema/número/nome/cláusula aparece "
        "sem precisar ler o doc todo. Pode ser chamado em paralelo para "
        "vários termos no mesmo turno."
    ),
    icon="🔎",
)
def buscar_no_documento(termo: str, _session: dict, top_n: int = 10) -> str:
    """Busca um termo no documento corrente e devolve trechos com contexto.

    Args:
        termo: Termo a buscar (case-insensitive).
        top_n: Quantas ocorrências retornar (default 10, máx 50).
    """
    doc = _get_doc(_session)
    if doc is None:
        return _err("Nenhum documento na sessão.")
    if not termo or not termo.strip():
        return _err("Termo de busca vazio.")

    md = doc["markdown"]
    try:
        top_n = max(1, min(50, int(top_n)))
    except (TypeError, ValueError):
        top_n = 10

    pattern = re.compile(re.escape(termo), flags=re.IGNORECASE)
    matches = []
    for m in pattern.finditer(md):
        start = max(0, m.start() - 120)
        end = min(len(md), m.end() + 120)
        contexto = md[start:end].replace("\n", " ")
        matches.append({
            "offset": m.start(),
            "contexto": contexto,
        })
        if len(matches) >= top_n:
            break

    total = sum(1 for _ in pattern.finditer(md))
    return json.dumps(
        {
            "termo": termo,
            "ocorrencias_total": total,
            "ocorrencias_retornadas": len(matches),
            "matches": matches,
        },
        ensure_ascii=False,
    )


@tool(
    description=(
        "Extrai as tabelas detectadas no documento corrente em formato "
        "markdown. Retorna até 'top_n' tabelas (default 10) com índice e "
        "o markdown da tabela. "
        "USE: quando o usuário pergunta sobre dados tabulares dentro do "
        "documento (indicadores, listas estruturadas)."
    ),
    icon="📊",
)
def extrair_tabelas_do_documento(_session: dict, top_n: int = 10) -> str:
    """Extrai as tabelas markdown detectadas no documento.

    Args:
        top_n: Quantas tabelas retornar (default 10, máx 50).
    """
    doc = _get_doc(_session)
    if doc is None:
        return _err("Nenhum documento na sessão.")

    md = doc["markdown"]
    try:
        top_n = max(1, min(50, int(top_n)))
    except (TypeError, ValueError):
        top_n = 10

    # Tabela markdown: bloco contíguo de linhas começando com "|"
    tabelas = []
    bloco = []
    for linha in md.splitlines():
        if linha.lstrip().startswith("|"):
            bloco.append(linha)
        else:
            if len(bloco) >= 2:
                tabelas.append("\n".join(bloco))
            bloco = []
    if len(bloco) >= 2:
        tabelas.append("\n".join(bloco))

    payload = [
        {"indice": i, "linhas": t.count("\n") + 1, "markdown": t}
        for i, t in enumerate(tabelas[:top_n])
    ]
    return json.dumps(
        {
            "tabelas_total": len(tabelas),
            "tabelas_retornadas": len(payload),
            "tabelas": payload,
        },
        ensure_ascii=False,
    )
