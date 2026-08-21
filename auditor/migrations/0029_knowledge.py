"""
Cria o model Knowledge: conhecimentos reutilizáveis (prompts de especialista /
processo) cadastráveis na tela de Configurações e ativáveis por conversa no
chat. Quando ativos, seu conteúdo é injetado no contexto do orquestrador.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0028_rename_query_fq_database_to_consulta_aws"),
    ]

    operations = [
        migrations.CreateModel(
            name="Knowledge",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("description", models.CharField(blank=True, default="", max_length=240)),
                ("icon", models.CharField(default="📚", max_length=8)),
                ("prompt", models.TextField(
                    help_text=(
                        "Prompt completo do conhecimento: contexto de especialista, "
                        "processo, bases de análise etc. Injetado no contexto do "
                        "orquestrador quando ativado na conversa."
                    ),
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Conhecimento",
                "verbose_name_plural": "Conhecimentos",
                "ordering": ["name"],
            },
        ),
    ]
