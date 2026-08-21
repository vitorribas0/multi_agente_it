"""Tool human-in-the-loop — pausa o agente para perguntar ao usuário."""
from .registry import tool


@tool(
    description=(
        "Pausa o loop e pergunta algo ao usuário. "
        "USE quando: faltar dado essencial e não-inferível (período, "
        "escopo, palavras-chave de busca em texto, critério de outlier). "
        "NÃO use para pedir permissão, confirmar coisa óbvia, ou quando "
        "puder agir e mostrar o resultado. "
        "IMPORTANTE: se você emitir esta tool no mesmo turno que outras "
        "tools, só esta será respeitada — as demais serão descartadas. "
        "Faça no máximo 1-2 perguntas curtas e específicas por chamada."
    ),
    icon="❓",
    is_human_in_loop=True,
)
def ask_human(question: str) -> str:
    """Pergunta algo ao usuário e pausa o loop.

    Args:
        question: Pergunta clara, objetiva e curta. Ex.: "Qual período devo analisar?", "Quais palavras representam 'fraude' nos relatos?".
    """
    # O resultado real é tratado pelo runtime: ele pausa e
    # injeta a resposta humana na próxima rodada.
    return f"⏸️ Aguardando resposta do usuário: {question}"
