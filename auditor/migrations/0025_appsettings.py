"""
Adiciona AppSettings: configurações globais (singleton) editáveis na tela de
Configurações. Por ora guarda `max_iterations` — o nº máximo de passos com
ferramentas que um agente pode dar num turno (antes ficava fixo em código).
"""
from django.db import migrations, models


def create_singleton(apps, schema_editor):
    AppSettings = apps.get_model("auditor", "AppSettings")
    AppSettings.objects.get_or_create(pk=1, defaults={"max_iterations": 18})


def delete_singleton(apps, schema_editor):
    AppSettings = apps.get_model("auditor", "AppSettings")
    AppSettings.objects.filter(pk=1).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0024_confirmar_massiva"),
    ]

    operations = [
        migrations.CreateModel(
            name="AppSettings",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("max_iterations", models.PositiveIntegerField(
                    default=18,
                    help_text=(
                        "Nº máximo de passos com ferramentas que um agente pode "
                        "dar em um único turno antes de ser forçado a concluir."
                    ),
                )),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Configuração da aplicação",
                "verbose_name_plural": "Configurações da aplicação",
            },
        ),
        migrations.RunPython(create_singleton, delete_singleton),
    ]
