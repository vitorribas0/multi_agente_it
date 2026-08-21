"""
Recarrega o system_prompt do analista_dados com a regra "RESPEITE
QUANTIDADES E AMOSTRAS": quando o usuário pede N casos / uma amostra, o
agente deve reduzir o dataset antes de rodar análise custosa (ex.: passar
`limite` na analise_massiva_llm) em vez de processar o dataset inteiro.

Contexto: usuário pediu "amostra de 20 casos" e o analista rodou a análise
massiva nas 2.623 linhas inteiras.
"""
from pathlib import Path

from django.db import migrations


PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    agent = Agent.objects.filter(slug="analista_dados").first()
    if not agent:
        return
    path = PROMPTS_DIR / "analista_dados.md"
    if not path.exists():
        return
    new_prompt = path.read_text(encoding="utf-8").strip()
    if agent.system_prompt != new_prompt:
        agent.system_prompt = new_prompt
        agent.save(update_fields=["system_prompt"])


def downgrade(apps, schema_editor):
    pass  # Não reverte prompts.


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0013_subagents_opus"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
