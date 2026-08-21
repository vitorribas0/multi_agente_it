"""
Tool de busca na web (grounded) via Vertex/Gemini.

Encapsula a tool NATIVA ``enterpriseWebSearch`` da Vertex dentro de uma
function-tool normal. Assim o orquestrador (que roda em Claude/Bedrock) pode
"puxar" a busca sob demanda — exatamente como chama qualquer outra tool —
enquanto a ``enterpriseWebSearch`` roda ISOLADA, numa request Vertex própria,
sem se misturar com as demais function tools do turno (o que a Vertex não
permite) nem travar o provider do auditor.

SI: a tool ``googleSearch`` NÃO é mais recomendada (não aderente aos
requisitos de SI). Usamos ``enterpriseWebSearch``.
Parecer: https://iconectados.sharepoint.com/sites/AIRTCyber/SitePages/GCP-VertexAI---Gemini.aspx
"""
import os
from uuid import uuid4

from .registry import tool


# Modelo Gemini usado internamente na busca. flash-lite foi o validado nos
# testes (rápido/barato) e suficiente para sumarizar resultados de web.
_WEB_SEARCH_MODEL = os.getenv("IARA_WEB_SEARCH_MODEL", "gemini-2.5-flash-lite")

# Teto do conteúdo devolvido ao orquestrador — evita despejar a web inteira no
# contexto (ver gargalo de janela de contexto em MELHORIAS_FUTURAS.txt).
_MAX_CONTENT_CHARS = 6000

# Quantas fontes (citações) listar no máximo.
_MAX_SOURCES = 10


def _vertex_client():
    """Cliente IaraGenAI no provider Vertex (mesmas credenciais do servidor)."""
    from iaragenai import IaraGenAI
    return IaraGenAI(
        client_id=os.getenv("IARA_CLIENT_ID"),
        client_secret=os.getenv("IARA_CLIENT_SECRET"),
        environment=os.getenv("IARA_ENVIRONMENT", "homol"),
        provider="vertex",
        correlation_id=str(uuid4()),
    )


def _extract_sources(message) -> list[str]:
    """Extrai URLs/títulos das fontes do grounding, tolerando variações de SDK.

    O enterpriseWebSearch devolve as fontes em metadados de grounding cujo
    nome/estrutura muda entre versões do iaragenai/litellm. Procuramos os
    formatos conhecidos e degradamos para lista vazia se nada casar — a busca
    continua útil mesmo sem citações estruturadas.
    """
    sources: list[str] = []

    def _add(title, uri):
        title = (title or "").strip()
        uri = (uri or "").strip()
        if uri:
            sources.append(f"{title} — {uri}" if title else uri)
        elif title:
            sources.append(title)

    # Possíveis localizações dos metadados de grounding.
    candidates = []
    for attr in ("grounding_metadata", "groundingMetadata"):
        meta = getattr(message, attr, None)
        if meta is not None:
            candidates.append(meta)
    # Alguns SDKs penduram em model_extra / dict cru.
    extra = getattr(message, "model_extra", None)
    if isinstance(extra, dict):
        for key in ("grounding_metadata", "groundingMetadata", "citation_metadata"):
            if extra.get(key):
                candidates.append(extra[key])

    for meta in candidates:
        chunks = (
            getattr(meta, "grounding_chunks", None)
            or getattr(meta, "groundingChunks", None)
            or (meta.get("grounding_chunks") if isinstance(meta, dict) else None)
            or (meta.get("groundingChunks") if isinstance(meta, dict) else None)
            or []
        )
        for ch in chunks:
            web = (
                getattr(ch, "web", None)
                or (ch.get("web") if isinstance(ch, dict) else None)
            )
            if web is None:
                continue
            title = (
                getattr(web, "title", None)
                or (web.get("title") if isinstance(web, dict) else None)
            )
            uri = (
                getattr(web, "uri", None)
                or (web.get("uri") if isinstance(web, dict) else None)
            )
            _add(title, uri)
            if len(sources) >= _MAX_SOURCES:
                break
        if sources:
            break

    # Dedup preservando ordem.
    seen = set()
    deduped = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped


@tool(
    description=(
        "Pesquisa informações ATUAIS ou EXTERNAS na web (notícias, eventos "
        "recentes, normas/regulações públicas, políticas, fatos posteriores ao "
        "treino do modelo). USE quando: a pergunta exigir dado recente ou que "
        "não esteja no dataset, documentos anexados ou Knowledge Bases da "
        "sessão. NÃO use: para dados que JÁ estão no dataset/documento/KB da "
        "conversa (use as tools próprias para isso) nem para cálculos. "
        "Sempre cite as fontes retornadas no formato [n]."
    ),
    icon="🌐",
)
def buscar_na_web(consulta: str) -> str:
    """Busca na web com grounding via Vertex (enterpriseWebSearch).

    Args:
        consulta: O que pesquisar, em linguagem natural — inclua datas/contexto quando ajudar (ex.: "ultima versao da resolucao X do BACEN" ou "noticias sobre incidente Y em 2026").
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return "❌ Parâmetro 'consulta' é obrigatório."

    try:
        client = _vertex_client()
        resp = client.with_options(
            correlation_id=str(uuid4())
        ).chat.completions.create(
            messages=[{"role": "user", "content": consulta}],
            tools=[{"enterpriseWebSearch": {}}],
            model=_WEB_SEARCH_MODEL,
            max_tokens=20000,
            temperature=0.3,
            top_p=0.95,
        )

        message = resp.choices[0].message
        content = (getattr(message, "content", "") or "").strip()
        if not content:
            return f"⚠️ A busca não retornou conteúdo para: {consulta}"

        truncated = False
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + " [...]"
            truncated = True

        linhas = [
            "🌐 Resultado da busca na web",
            f"Consulta: {consulta}",
            "",
            content,
        ]
        if truncated:
            linhas.append("\n> ⚠️ Conteúdo truncado por limite de tamanho.")

        sources = _extract_sources(message)
        if sources:
            linhas.append("\nFontes:")
            for idx, src in enumerate(sources, start=1):
                linhas.append(f"[{idx}] {src}")

        return "\n".join(linhas)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"❌ Erro ao buscar na web: {e}"
