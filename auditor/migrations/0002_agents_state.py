"""Adiciona Agent, ToolCall e estado em Conversation."""
from django.db import migrations, models
import django.db.models.deletion


def seed_agents(apps, schema_editor):
    """Cria agentes padrão lendo prompts da pasta prompts/."""
    from pathlib import Path
    Agent = apps.get_model("auditor", "Agent")

    base_dir = Path(__file__).resolve().parent.parent.parent
    prompts_dir = base_dir / "prompts"

    def _load(filename: str, fallback: str) -> str:
        path = prompts_dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return fallback

    seeds = [
        {
            "slug": "orquestrador",
            "name": "Orquestrador",
            "description": "Agente principal que coordena a investigação.",
            "icon": "🛡️",
            "system_prompt": _load("orquestrador.md", "Você é o Orquestrador."),
            "model": "gpt-4o",
            "temperature": 0.7,
            "tools_enabled": ["thinking", "ask_human", "query_fq_database"],
            "is_default": True,
        },
        {
            "slug": "analista_dados",
            "name": "Analista de Dados",
            "description": "Especialista em análise estatística de datasets.",
            "icon": "📊",
            "system_prompt": _load("analista_dados.md", "Você é o Analista."),
            "model": "gpt-4o",
            "temperature": 0.4,
            "tools_enabled": ["thinking", "ask_human"],
            "is_default": False,
        },
        {
            "slug": "gerador_sql",
            "name": "Gerador SQL",
            "description": "Especialista em queries Athena para a base FQ.",
            "icon": "🗄️",
            "system_prompt": _load("gerador_sql.md", "Você é o Gerador SQL."),
            "model": "gpt-4o",
            "temperature": 0.2,
            "tools_enabled": ["thinking", "ask_human", "query_fq_database"],
            "is_default": False,
        },
    ]
    for seed in seeds:
        Agent.objects.update_or_create(slug=seed["slug"], defaults=seed)


def unseed_agents(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    Agent.objects.filter(slug__in=["orquestrador", "analista_dados", "gerador_sql"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("auditor", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Agent",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(unique=True)),
                ("name", models.CharField(max_length=80)),
                ("description", models.CharField(blank=True, default="", max_length=240)),
                ("icon", models.CharField(default="🤖", max_length=8)),
                ("system_prompt", models.TextField()),
                ("model", models.CharField(default="gpt-4o", max_length=80)),
                ("temperature", models.FloatField(default=0.7)),
                ("tools_enabled", models.JSONField(default=list, help_text="Lista de slugs de tools habilitadas para este agente.")),
                ("is_default", models.BooleanField(default=False, help_text="Se True, é o agente usado quando nenhum é selecionado.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-is_default", "name"]},
        ),
        migrations.AddField(
            model_name="conversation",
            name="agent",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="conversations", to="auditor.agent"),
        ),
        migrations.AddField(
            model_name="conversation",
            name="state",
            field=models.JSONField(default=dict, help_text="Estado de sessão compartilhado entre tools (df, arquivos, etc)."),
        ),
        migrations.AddField(
            model_name="conversation",
            name="awaiting_human_input",
            field=models.BooleanField(default=False, help_text="True quando o agente está esperando resposta de ask_human."),
        ),
        migrations.AddField(
            model_name="conversation",
            name="pending_tool_calls",
            field=models.JSONField(default=list, help_text="Tool calls pendentes quando pausado por ask_human."),
        ),
        migrations.AlterField(
            model_name="message",
            name="content",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.CreateModel(
            name="ToolCall",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tool_name", models.CharField(max_length=80)),
                ("args", models.JSONField(default=dict)),
                ("result", models.TextField(blank=True, default="")),
                ("error", models.TextField(blank=True, default="")),
                ("duration_ms", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("message", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tool_calls", to="auditor.message")),
            ],
            options={"ordering": ["created_at"]},
        ),
        migrations.RunPython(seed_agents, reverse_code=unseed_agents),
    ]
