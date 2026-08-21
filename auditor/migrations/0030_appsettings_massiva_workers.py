"""
Adiciona AppSettings.massiva_workers: nº de linhas processadas em paralelo na
análise massiva por IA (antes ficava fixo em código, MAX_WORKERS=5). Editável
na tela de Configurações (Geral), limitado a 1–10.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0029_knowledge"),
    ]

    operations = [
        migrations.AddField(
            model_name="appsettings",
            name="massiva_workers",
            field=models.PositiveIntegerField(
                default=5,
                help_text=(
                    "Nº de linhas processadas em paralelo na análise massiva "
                    "por IA (1–10). Cada worker faz 1 chamada de LLM simultânea; "
                    "valores altos aceleram, mas aumentam o risco de rate limit "
                    "e concentram custo. 10 é o teto e é considerado arriscado."
                ),
            ),
        ),
    ]
