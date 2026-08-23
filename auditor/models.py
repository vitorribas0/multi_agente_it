import uuid

from django.db import models


# ════════════════════════════════════════════════════════════════════════
# Configurações globais da aplicação (editáveis pela tela)
# ════════════════════════════════════════════════════════════════════════

class AppSettings(models.Model):
    """Configurações globais únicas (singleton) ajustáveis em Configurações.

    Há sempre uma única linha (pk=1). Use ``AppSettings.get_solo()`` para
    lê-la/criá-la sem precisar saber o id.
    """
    max_iterations = models.PositiveIntegerField(
        default=18,
        help_text=(
            "Nº máximo de passos com ferramentas que um agente pode dar em "
            "um único turno antes de ser forçado a concluir."
        ),
    )
    massiva_workers = models.PositiveIntegerField(
        default=5,
        help_text=(
            "Nº de linhas processadas em paralelo na análise massiva por IA "
            "(1–10). Cada worker faz 1 chamada de LLM simultânea; valores "
            "altos aceleram, mas aumentam o risco de rate limit e concentram "
            "custo. 10 é o teto e é considerado arriscado."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuração da aplicação"
        verbose_name_plural = "Configurações da aplicação"

    def __str__(self):
        return f"AppSettings(max_iterations={self.max_iterations})"

    @classmethod
    def get_solo(cls) -> "AppSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class BatchJob(models.Model):
    """Job de análise massiva em BATCH (Batch API do IARA).

    Persiste o ``job_id`` e o mapeamento necessário para casar os resultados de
    volta no dataset (``meta.id_to_index``/``vazias``/``colunas_saida``). O ponto
    é sobreviver a queda de conexão / restart: o job roda no servidor do IARA e,
    enquanto tivermos o job_id salvo, ``buscar_resultado_batch`` recupera o
    resultado depois — mesmo que o poll automático tenha expirado.

    Segue o padrão da casa (estado em JSONField único; ver Agent.tools_enabled,
    Playbook.nodes) — o blob de merge vive em ``meta``.
    """
    job_id = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=24, default="PENDING")
    meta = models.JSONField(
        default=dict,
        help_text=(
            "Contexto do job para casar resultados de volta: modelo, "
            "coluna_texto, colunas_saida, id_to_index (custom_id→índice), "
            "vazias, total, presigned_env."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Job de batch"
        verbose_name_plural = "Jobs de batch"

    def __str__(self):
        return f"BatchJob({self.job_id}, {self.status})"


# ════════════════════════════════════════════════════════════════════════
# Agentes (configuráveis pela tela)
# ════════════════════════════════════════════════════════════════════════

class Agent(models.Model):
    """Configuração de um agente: prompt, modelo e tools habilitadas."""
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=240, blank=True, default="")
    icon = models.CharField(max_length=8, default="🤖")
    system_prompt = models.TextField()
    model = models.CharField(max_length=80, default="gpt-4o")
    temperature = models.FloatField(default=0.7)
    tools_enabled = models.JSONField(
        default=list,
        help_text="Lista de slugs de tools habilitadas para este agente.",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Se True, é o agente usado quando nenhum é selecionado.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "name"]

    def __str__(self):
        return self.name


# ════════════════════════════════════════════════════════════════════════
# Conhecimentos (prompts de especialista, cadastráveis pela tela)
# ════════════════════════════════════════════════════════════════════════

class Knowledge(models.Model):
    """Um "conhecimento" reutilizável: um prompt de especialista/processo.

    Diferente de Agent (que é um executor com tools) e de SessionAgent (que
    vive só numa conversa), um Knowledge é um bloco de instruções nomeado —
    contexto de especialista, processo, bases de análise etc. — cadastrado na
    tela de Configurações. No chat, o usuário ativa um ou mais conhecimentos e
    o conteúdo é injetado no contexto do orquestrador, ampliando o que ele
    sabe fazer sem editar prompt de agente.
    """
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=240, blank=True, default="")
    icon = models.CharField(max_length=8, default="📚")
    prompt = models.TextField(
        help_text=(
            "Prompt completo do conhecimento: contexto de especialista, "
            "processo, bases de análise etc. Injetado no contexto do "
            "orquestrador quando ativado na conversa."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Conhecimento"
        verbose_name_plural = "Conhecimentos"

    def __str__(self):
        return self.name


# ════════════════════════════════════════════════════════════════════════
# Playbooks (pipelines multi-agente autorados num canvas visual)
# ════════════════════════════════════════════════════════════════════════

class Playbook(models.Model):
    """Um pipeline multi-agente nomeado, autorado num canvas visual.

    Guarda um grafo de nós-agente + arestas direcionadas de delegação, com
    exatamente um nó ROOT (orquestrador). Ao rodar numa conversa, o ROOT vira
    o orquestrador e ``call_agent`` resolve SOMENTE os nós deste playbook
    (isolado da tabela Agent global), restrito aos especialistas alcançáveis
    por aresta a partir de cada nó.

    O grafo inteiro vive em JSON num único registro (segue o padrão da casa —
    Agent.tools_enabled, SessionAgent.documents, Conversation.state já são
    JSONField). O canvas salva o blob {nodes, edges, suggestions} de forma
    atômica; não há ganho em normalizar em tabelas-filhas.

    Shape de um nó (duck-types a superfície que RuntimeAgent/run_agent/
    call_agent consomem — slug/name/system_prompt/model/temperature/
    tools_enabled)::

        {"slug": "orquestrador", "name": "Orquestrador",
         "system_prompt": "...", "model": "gpt-4o", "temperature": 0.7,
         "tools_enabled": ["call_agent", "gerar_sql"], "is_root": true,
         "icon": "🧭", "description": "...", "canvas": {"x": 120, "y": 80}}

    O slug é gerado/mantido pelo backend no save (estável em rename, único no
    playbook). ``canvas`` é layout opaco, ignorado pelo motor.

    Aresta: ``{"source": "<slug>", "target": "<slug>"}`` — indica que
    ``source`` pode delegar para ``target`` via call_agent.

    Sugestão: ``{"title": "...", "text": "..."}`` — card da tela de
    boas-vindas do chat quando este playbook está ativo.
    """
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=240, blank=True, default="")
    icon = models.CharField(max_length=8, default="📘")
    nodes = models.JSONField(
        default=list,
        help_text="Lista de nós-agente do grafo (ver shape na docstring).",
    )
    edges = models.JSONField(
        default=list,
        help_text="Arestas direcionadas de delegação: [{source, target}].",
    )
    suggestions = models.JSONField(
        default=list, blank=True,
        help_text="Cards de sugestão da tela de boas-vindas: [{title, text}].",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Playbook"
        verbose_name_plural = "Playbooks"

    def __str__(self):
        return self.name


# ════════════════════════════════════════════════════════════════════════
# Conversas e mensagens
# ════════════════════════════════════════════════════════════════════════

class Conversation(models.Model):
    """Uma conversa de auditoria."""
    title = models.CharField(max_length=120, default="Nova conversa")
    agent = models.ForeignKey(
        Agent, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="conversations",
    )
    playbook = models.ForeignKey(
        "Playbook", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="conversations",
        help_text=(
            "Se definido, a conversa roda o grafo deste playbook (o nó root "
            "vira o orquestrador e call_agent fica isolado aos nós do "
            "playbook) em vez do agente global. Nulo = comportamento padrão."
        ),
    )
    state = models.JSONField(
        default=dict,
        help_text="Estado de sessão compartilhado entre tools (df, arquivos, etc).",
    )
    awaiting_human_input = models.BooleanField(
        default=False,
        help_text="True quando o agente está esperando resposta de ask_human.",
    )
    pending_tool_calls = models.JSONField(
        default=list,
        help_text="Tool calls pendentes quando pausado por ask_human.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title


class Execution(models.Model):
    """Ciclo persistente de uma execução do agente em uma conversa.

    O registro independe do transporte SSE e é a base para recuperar progresso,
    perguntas e cancelamento depois de recarregar a interface. O backend
    ``local`` usa uma thread por enquanto; futuros backends podem guardar ARN
    de task ECS e continuar usando o mesmo contrato.
    """

    STATUS_CHOICES = (
        ("queued", "Na fila"),
        ("starting", "Iniciando"),
        ("running", "Executando"),
        ("waiting_user", "Aguardando usuário"),
        ("stopping", "Interrompendo"),
        ("completed", "Concluída"),
        ("stopped", "Interrompida"),
        ("failed", "Falhou"),
    )
    ACTIVE_STATUSES = ("queued", "starting", "running", "waiting_user", "stopping")
    TERMINAL_STATUSES = ("completed", "stopped", "failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        related_name="executions",
        on_delete=models.CASCADE,
    )
    engine = models.CharField(max_length=40, default="codex-app-server")
    backend = models.CharField(max_length=24, default="local")
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="queued", db_index=True)
    runtime_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    thread_id = models.CharField(max_length=160, blank=True, default="")
    turn_id = models.CharField(max_length=160, blank=True, default="")
    events = models.JSONField(default=list, blank=True)
    plan = models.JSONField(default=list, blank=True)
    plan_explanation = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    stop_requested_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation"],
                condition=models.Q(
                    status__in=["queued", "starting", "running", "waiting_user", "stopping"]
                ),
                name="unique_active_execution_per_conversation",
            ),
        ]

    def __str__(self):
        return f"Execution({self.id}, {self.status}, conv={self.conversation_id})"


class SessionAgent(models.Model):
    """Agente criado SÓ para uma conversa (não é democratizado).

    Diferente de Agent (global, configurado na tela de Configurações), o
    SessionAgent existe apenas no escopo de uma Conversation. O orquestrador
    daquela conversa o enxerga como um especialista chamável via call_agent
    (slug reservado 'agente_sessao'). Pode ser exportado/importado como JSON
    para reuso em outro chat.
    """
    conversation = models.OneToOneField(
        Conversation, on_delete=models.CASCADE, related_name="session_agent",
    )
    name = models.CharField(max_length=80, default="Meu agente")
    icon = models.CharField(max_length=8, default="🤖")
    system_prompt = models.TextField(
        blank=True, default="",
        help_text="Prompt do usuário. Combinado por trás com boas práticas + guardrails.",
    )
    model = models.CharField(max_length=80, default="gpt-4o")
    temperature = models.FloatField(default=0.7)
    tools_enabled = models.JSONField(
        default=list,
        help_text="Lista de slugs de tools habilitadas para este agente da sessão.",
    )
    guardrails = models.TextField(
        blank=True, default="",
        help_text="Regras/limites que o agente deve sempre respeitar.",
    )
    documents = models.JSONField(
        default=list, blank=True,
        help_text=(
            "Documentos anexados ao agente (PDF/TXT de política etc.), já "
            "extraídos como markdown. Cada item: "
            "{filename, markdown, char_count, page_count}. O conteúdo é "
            "injetado no contexto do agente em toda execução."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Slug reservado pelo qual o orquestrador delega para este agente.
    SLUG = "agente_sessao"

    def __str__(self):
        return f"{self.name} (conv {self.conversation_id})"


class Message(models.Model):
    """Uma mensagem dentro de uma conversa."""
    ROLE_CHOICES = (
        ("user", "Usuário"),
        ("assistant", "Assistente"),
    )

    conversation = models.ForeignKey(
        Conversation, related_name="messages", on_delete=models.CASCADE
    )
    role = models.CharField(max_length=12, choices=ROLE_CHOICES)
    content = models.TextField(blank=True, default="")
    attachment = models.JSONField(
        default=dict, blank=True,
        help_text="Anexo opcional (ex.: tabela carregada): kind, filename, columns, rows, dtypes, sample.",
    )
    attachments = models.JSONField(
        default=list, blank=True,
        help_text=(
            "Lista de cards de artefato produzidos NO turno (export, chart, "
            "mermaid, table). Permite vários por mensagem — ex.: PDF + Excel "
            "ou dois gráficos. `attachment` (singular) segue para o anexo "
            "único do upload do usuário."
        ),
    )
    input_tokens = models.IntegerField(
        default=0,
        help_text="Tokens de entrada consumidos nesta mensagem.",
    )
    output_tokens = models.IntegerField(
        default=0,
        help_text="Tokens de saída gerados nesta mensagem.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"


class ToolCall(models.Model):
    """Registro de uma tool call: nome, args, resultado."""
    message = models.ForeignKey(
        Message, related_name="tool_calls", on_delete=models.CASCADE
    )
    tool_name = models.CharField(max_length=80)
    args = models.JSONField(default=dict)
    result = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    duration_ms = models.IntegerField(default=0)
    nested_tool_calls = models.JSONField(
        default=list, blank=True,
        help_text="Tool calls do sub-agente quando esta tool é call_agent.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.tool_name}({len(self.args)} args)"
