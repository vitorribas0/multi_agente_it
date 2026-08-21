"""
Cria o modelo Playbook (pipeline multi-agente autorado num canvas) e o FK
Conversation.playbook que vincula uma conversa a um playbook.

Ambas as adições são nullable/default — sem data migration.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0032_gerar_html"),
    ]

    operations = [
        migrations.CreateModel(
            name="Playbook",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("description", models.CharField(blank=True, default="", max_length=240)),
                ("icon", models.CharField(default="📘", max_length=8)),
                ("nodes", models.JSONField(default=list, help_text="Lista de nós-agente do grafo (ver shape na docstring).")),
                ("edges", models.JSONField(default=list, help_text="Arestas direcionadas de delegação: [{source, target}].")),
                ("suggestions", models.JSONField(blank=True, default=list, help_text="Cards de sugestão da tela de boas-vindas: [{title, text}].")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Playbook",
                "verbose_name_plural": "Playbooks",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="conversation",
            name="playbook",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="conversations",
                to="auditor.playbook",
                help_text=(
                    "Se definido, a conversa roda o grafo deste playbook (o nó root "
                    "vira o orquestrador e call_agent fica isolado aos nós do "
                    "playbook) em vez do agente global. Nulo = comportamento padrão."
                ),
            ),
        ),
    ]
