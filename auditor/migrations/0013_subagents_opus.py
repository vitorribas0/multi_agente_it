"""
Eleva a inteligência de todos os agentes:

- Padroniza os 4 agentes (orquestrador, analista_dados, gerador_sql,
  analista_documentos) em Claude Opus 4.6. Em Claude o ai_service ativa
  thinking adaptive + effort high — os agentes deixam de rodar "crus"
  justamente nas tarefas pesadas (planejamento, pandas, SQL). Antes, os
  3 sub-agentes estavam em gpt-4o.
- Recarrega o system_prompt do analista_dados com a regra anti-alucinação
  (não afirmar transformação sem o retorno da tool comprovar).

Contexto: um analista_dados em gpt-4o pulou o executar_pandas, exportou o
dataset original intacto (104 colunas) e narrou "agora contém 3 colunas".
"""
from pathlib import Path

from django.db import migrations


PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

OPUS = "anthropic.claude-opus-4-6-v1"

ALL_SLUGS = ["orquestrador", "analista_dados", "gerador_sql", "analista_documentos"]


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")

    # 1) Todos os agentes -> Opus 4.6
    Agent.objects.filter(slug__in=ALL_SLUGS).update(model=OPUS)

    # 2) Recarrega o prompt do analista_dados (regra anti-alucinação)
    agent = Agent.objects.filter(slug="analista_dados").first()
    if agent:
        path = PROMPTS_DIR / "analista_dados.md"
        if path.exists():
            new_prompt = path.read_text(encoding="utf-8").strip()
            if agent.system_prompt != new_prompt:
                agent.system_prompt = new_prompt
                agent.save(update_fields=["system_prompt"])


def downgrade(apps, schema_editor):
    # Estado anterior: orquestrador em gpt-4o; sub-agentes em gpt-4o.
    Agent = apps.get_model("auditor", "Agent")
    Agent.objects.filter(slug__in=ALL_SLUGS).update(model="gpt-4o")


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0012_smarter_agents"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
