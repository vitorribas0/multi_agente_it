"""Habilita as tools de análise massiva em BATCH no agente analista_dados.

Espelha o padrão idempotente de 0004: adiciona os slugs a tools_enabled sem
duplicar. As tools novas são:
  - analise_massiva_batch: dispara a análise em lote (job no servidor do IARA).
  - buscar_resultado_batch: recupera o resultado de um job pelo job_id.

Recarrega também o system_prompt do analista_dados (prompts/analista_dados.md),
onde deve estar documentado quando oferecer batch vs. o massivo síncrono.
"""
from pathlib import Path

from django.db import migrations


NEW_TOOLS = [
    "analise_massiva_batch",
    "buscar_resultado_batch",
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
    fields = ["tools_enabled"]
    new_prompt = _load_prompt("analista_dados.md")
    if new_prompt and a.system_prompt != new_prompt:
        a.system_prompt = new_prompt
        fields.append("system_prompt")
    a.save(update_fields=fields)


def downgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    a = Agent.objects.filter(slug="analista_dados").first()
    if not a:
        return
    a.tools_enabled = [t for t in (a.tools_enabled or []) if t not in NEW_TOOLS]
    a.save(update_fields=["tools_enabled"])


class Migration(migrations.Migration):

    dependencies = [
        ("auditor", "0034_batchjob"),
    ]

    operations = [
        migrations.RunPython(upgrade, reverse_code=downgrade),
    ]
