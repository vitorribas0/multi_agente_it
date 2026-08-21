"""
Tool de RAG sobre Knowledge Bases da IARA.

Busca trechos relevantes nas KBs que o usuário ativou para a conversa
(via o botão "Bases de conhecimento" no composer). A lista de KBs ativas
fica em ``_session["active_kbs"]`` — cada item: {id, name, description}.

A busca usa similarity_search (cosine, top_k configurável). O resultado é
um texto formatado com trechos numerados, pronto para o LLM citar como [n].
"""
import os
from uuid import uuid4

from .registry import tool


def _iara_access_token() -> str | None:
    # O .env do servidor expõe ACCESS_TOKEN (sem prefixo IARA_); aceitamos os dois.
    return os.getenv("IARA_ACCESS_TOKEN") or os.getenv("ACCESS_TOKEN")


# Nomes que o SDK iaragenai já usou para a referência de KB no similarity_search.
# A classe é renomeada entre versões, então resolvemos em runtime pelo módulo de
# types, em vez de fixar um import que quebra a cada atualização do SDK.
_REF_CANDIDATES = (
    "SimilaritySearchKnowledgeBaseVersionReference",
    "KnowledgeBaseVersionReference",
    "SimilaritySearchKnowledgeBaseReference",
    "KnowledgeBaseReference",
)


def _kb_reference_class():
    """Localiza a classe de referência de KB no módulo de types do iaragenai.

    Tenta os nomes conhecidos e, se nenhum casar, faz um fallback por heurística
    (qualquer classe do módulo cujo nome contenha KnowledgeBase + Reference).
    """
    try:
        from iaragenai.apis.resources.datafoundation_api import types as _types
    except Exception:
        return None

    for name in _REF_CANDIDATES:
        cls = getattr(_types, name, None)
        if isinstance(cls, type):
            return cls

    for name in dir(_types):
        if "KnowledgeBase" in name and "Reference" in name:
            cls = getattr(_types, name, None)
            if isinstance(cls, type):
                return cls
    return None


def _make_ref(Ref: type, kb_id: str):
    """Instancia a referência tolerando diferenças de assinatura entre versões."""
    for kwargs in (
        {"knowledge_base_id": kb_id, "knowledge_base_version": None},
        {"knowledge_base_id": kb_id},
        {"id": kb_id},
    ):
        try:
            return Ref(**kwargs)
        except TypeError:
            continue
    # Último recurso: posicional.
    return Ref(kb_id)


@tool(
    description=(
        "Busca trechos relevantes nas Knowledge Bases (bases de conhecimento) "
        "ativas nesta conversa, via similarity search. Use SEMPRE que a pergunta "
        "exigir conteúdo de manuais, políticas, normas ou documentos anexados "
        "como base de conhecimento. Cite os trechos retornados no formato [n]."
    ),
    icon="📚",
)
def consultar_kb(consulta: str, _session: dict, top_k: int = 5) -> str:
    """Consulta as Knowledge Bases ativas e devolve trechos relevantes.

    Args:
        consulta: Pergunta ou termos de busca em linguagem natural.
        top_k: Número máximo de trechos a retornar (padrão 5).
    """
    consulta = (consulta or "").strip()
    if not consulta:
        return "❌ Parâmetro 'consulta' é obrigatório."

    active_kbs = _session.get("active_kbs") or []
    active_kbs = [k for k in active_kbs if isinstance(k, dict) and k.get("id")]
    if not active_kbs:
        return (
            "⚠️ Nenhuma base de conhecimento ativa nesta conversa. "
            "Peça ao usuário para selecionar uma no botão 📚 do chat."
        )

    try:
        from iaragenai import IaraGenAI

        Ref = _kb_reference_class()
        if Ref is None:
            # Sem classe de referência conhecida: tenta passar dicts crus.
            kb_refs = [
                {"knowledge_base_id": kb["id"], "knowledge_base_version": None}
                for kb in active_kbs
            ]
        else:
            kb_refs = [_make_ref(Ref, kb["id"]) for kb in active_kbs]

        client = IaraGenAI(
            client_id=os.getenv("IARA_CLIENT_ID"),
            client_secret=os.getenv("IARA_CLIENT_SECRET"),
            environment=os.getenv("IARA_ENVIRONMENT", "homol"),
            access_token=_iara_access_token(),
            correlation_id=str(uuid4()),
        )

        trechos = client.similarity_search.search(
            text=consulta,
            top_k=int(top_k or 5),
            strategy="cosine_similarity",
            knowledge_bases=kb_refs,
        ) or []

        if not trechos:
            return f"⚠️ Nenhum trecho relevante encontrado para: {consulta}"

        nomes = ", ".join(kb.get("name") or kb["id"] for kb in active_kbs)
        linhas = [
            f"📚 {len(trechos)} trecho(s) recuperado(s)",
            f"Consulta: {consulta}",
            f"Bases consultadas: {nomes}",
            "",
        ]
        for idx, item in enumerate(trechos, start=1):
            texto = (getattr(item, "text", "") or "").strip()
            if len(texto) > 700:
                texto = texto[:700] + " [...]"
            score = getattr(item, "score", None)
            document = getattr(item, "document", None)
            doc_name = getattr(document, "document_name", None) if document else None

            score_str = f" | Score: {score:.3f}" if isinstance(score, (int, float)) else ""
            linhas.append(f"[{idx}]{score_str}" + (f" — {doc_name}" if doc_name else ""))
            if texto:
                linhas.append(texto)
            linhas.append("")

        return "\n".join(linhas)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"❌ Erro ao consultar bases de conhecimento: {e}"
