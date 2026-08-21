"""
Recarrega os system_prompts de todos os agentes a partir dos arquivos em
prompts/. Esta rodada eleva a "inteligência" do sistema multiagente:

- orquestrador: avalia criticamente o retorno dos sub-agentes, recupera de
  erros/resultados vazios e só entrega após validar (auto-correção em até
  2 ciclos).
- analista_dados: sanity check de números antes de responder.
- gerador_sql: trata resultado vazio como provável erro de predicado e faz
  query de diagnóstico; busca textual com LOWER(...) LIKE.
- analista_documentos: tenta variações/sinônimos antes de declarar ausência
  e nunca preenche lacuna com conhecimento geral.
"""
from pathlib import Path

from django.db import migrations


PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


SLUG_TO_PROMPT_FILE = {
    "orquestrador": "orquestrador.md",
    "analista_dados": "analista_dados.md",
    "analista_documentos": "analista_documentos.md",
    "gerador_sql": "gerador_sql.md",
}


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    for slug, filename in SLUG_TO_PROMPT_FILE.items():
        agent = Agent.objects.filter(slug=slug).first()
        if not agent:
            continue
        path = PROMPTS_DIR / filename
        if not path.exists():
            continue
        new_prompt = path.read_text(encoding="utf-8").strip()
        if agent.system_prompt != new_prompt:
            agent.system_prompt = new_prompt
            agent.save(update_fields=["system_prompt"])


def downgrade(apps, schema_editor):
    pass  # Não reverte prompts — versões anteriores vivem no git.


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0011_autonomous_agents"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
