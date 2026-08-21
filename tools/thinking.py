"""Tool de pensamento explícito — força o agente a raciocinar passo a passo."""
from .registry import tool


@tool(
    description=(
        "Registra um bloco de raciocínio antes de agir. "
        "USE quando: o pedido é complexo, ambíguo, exige várias tools "
        "encadeadas, ou você precisa decidir o que delegar e o que pode "
        "rodar em paralelo. NÃO use para perguntas triviais (1 chamada "
        "óbvia) — adiciona latência sem ganho. "
        "Não substitui a resposta final ao usuário; é um espaço de "
        "raciocínio visível na trilha de auditoria."
    ),
    icon="🧠",
)
def thinking(thought: str) -> str:
    """Registra o raciocínio passo a passo.

    Args:
        thought: O raciocínio: hipóteses, dados que tem, dados que faltam, plano de execução (incluindo o que vai em paralelo).
    """
    return f"💭 Pensamento registrado:\n\n{thought}"
