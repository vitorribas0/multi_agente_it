"""
Recarrega o system_prompt do analista_dados: a `analise_massiva_llm` agora
exige confirmação EXPLÍCITA do usuário antes de executar (sempre), reforçada
por uma trava de código (parâmetro `confirmado`). O agente deve apresentar o
plano via ask_human e só rodar com confirmado=true após o aval.
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
        ("auditor", "0023_cientista_dados_completo"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
