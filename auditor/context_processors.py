from .models import Conversation


def sidebar_history(request):
    """Injeta a lista de conversas em TODAS as páginas.

    Assim a sidebar de histórico já vem renderizada no HTML (server-side),
    aparecendo instantaneamente no reload — sem o "flash" de lista vazia
    enquanto o fetch de /api/conversations/ (feito pelo main.js) não responde.
    O main.js continua atualizando a lista via JS depois do load.
    """
    convs = Conversation.objects.select_related("agent", "session_agent")
    active_id = request.GET.get("c")
    return {
        "sidebar_conversations": convs,
        "sidebar_active_conversation_id": active_id,
    }
