"""Habilita as tools de análise de dados no agente analista_dados
e atualiza seu system_prompt a partir de prompts/analista_dados.md."""
from pathlib import Path

from django.db import migrations


NEW_TOOLS = [
    "descrever_dataset",
    "normalizar_coluna",
    "filtrar_por_termo",
    "contar_keywords",
    "contem_termo",
    "agrupar",
    "regex_extrair",
]


def _load_prompt(filename: str) -> str:
    base = Path(__file__).resolve().parent.parent.parent / "prompts" / filename
    if base.exists():
        return base.read_text(encoding="utf-8").strip()
    return ""


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    a = Agent.objects.filter(slug="analista_dados").first()
    if not a:
        return
    tools = list(a.tools_enabled or [])
    for slug in NEW_TOOLS:
        if slug not in tools:
            tools.append(slug)
    a.tools_enabled = tools
    new_prompt = _load_prompt("analista_dados.md")
    if new_prompt:
        a.system_prompt = new_prompt
    a.save(update_fields=["tools_enabled", "system_prompt"])


def downgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    a = Agent.objects.filter(slug="analista_dados").first()
    if not a:
        return
    a.tools_enabled = [t for t in (a.tools_enabled or []) if t not in NEW_TOOLS]
    a.save(update_fields=["tools_enabled"])


class Migration(migrations.Migration):

    dependencies = [
        ("auditor", "0003_orquestrador_call_agent"),
    ]

    operations = [
        migrations.RunPython(upgrade, reverse_code=downgrade),
    ]
