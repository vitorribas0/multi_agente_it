"""
Atualiza o system_prompt do orquestrador a partir de prompts/orquestrador.md.
A reescrita inclui mapa de roteamento explícito, sinais de palavras-chave
e capacidades atualizadas dos sub-agentes (analise_massiva_llm,
executar_pandas, exportar_dataset).
"""
from pathlib import Path

from django.db import migrations


PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    path = PROMPTS_DIR / "orquestrador.md"
    if not path.exists():
        return
    new_prompt = path.read_text(encoding="utf-8").strip()
    Agent.objects.filter(slug="orquestrador").update(system_prompt=new_prompt)


def downgrade(apps, schema_editor):
    # Sem fallback automático — a versão anterior do prompt vivia só
    # no histórico do git.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("auditor", "0009_toolcall_nested_tool_calls"),
    ]

    operations = [
        migrations.RunPython(upgrade, reverse_code=downgrade),
    ]
