"""
Recarrega o system_prompt do analista_dados: a quantidade de linhas a
processar na analise_massiva_llm é decisão do USUÁRIO. Se ele não informou
o número, o agente deve perguntar (ask_human) "quantas linhas quer
processar? (máximo 8.000)" antes de rodar — em vez de processar o dataset
inteiro por conta própria.
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
        ("auditor", "0014_amostra_analista_dados"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
