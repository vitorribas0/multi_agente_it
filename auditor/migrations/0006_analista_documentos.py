"""Cria o agente Analista de Documentos com tools de OCR (docling)."""
from pathlib import Path

from django.db import migrations


DOC_TOOLS = [
    "thinking",
    "ask_human",
    "descrever_documento",
    "ler_documento",
    "buscar_no_documento",
    "extrair_tabelas_do_documento",
]


def _load_prompt(filename: str) -> str:
    base = Path(__file__).resolve().parent.parent.parent / "prompts" / filename
    if base.exists():
        return base.read_text(encoding="utf-8").strip()
    return "Você é o Analista de Documentos."


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    Agent.objects.update_or_create(
        slug="analista_documentos",
        defaults={
            "name": "Analista de Documentos",
            "description": "Especialista em leitura e busca em PDFs/DOCX/imagens (docling).",
            "icon": "📄",
            "system_prompt": _load_prompt("analista_documentos.md"),
            "model": "gpt-4o",
            "temperature": 0.3,
            "tools_enabled": DOC_TOOLS,
            "is_default": False,
        },
    )

    # Permite que o orquestrador delegue para o novo analista via call_agent.
    orq = Agent.objects.filter(slug="orquestrador").first()
    if orq:
        tools = list(orq.tools_enabled or [])
        if "call_agent" not in tools:
            tools.append("call_agent")
            orq.tools_enabled = tools
            orq.save(update_fields=["tools_enabled"])


def downgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    Agent.objects.filter(slug="analista_documentos").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("auditor", "0005_message_attachment_alter_agent_id_alter_toolcall_id"),
    ]

    operations = [
        migrations.RunPython(upgrade, reverse_code=downgrade),
    ]
