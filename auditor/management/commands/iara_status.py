"""Diagnóstico do provedor efetivo do Codex (OpenAI vs IARA).

Roda dentro do Django, então reflete EXATAMENTE o que a aplicação enxerga
(inclui o carregamento do .env feito em settings.py). Não imprime segredos —
apenas se as credenciais estão presentes.

    python manage.py iara_status
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from auditor import codex_app_server as C


class Command(BaseCommand):
    help = "Mostra qual provedor o Codex vai usar e por quê (sem expor segredos)."

    def handle(self, *args, **options):
        import os

        raw = os.environ.get("ATENA_IARA_ENABLED")
        enabled = C._iara_enabled()

        self.stdout.write("── Diagnóstico do provedor da Atena/Codex ──")
        self.stdout.write(f"ATENA_IARA_ENABLED (bruto) : {raw!r}")
        self.stdout.write(f"IARA habilitado?           : {enabled}")
        self.stdout.write(
            "Caminho ativo              : "
            + ("IARA (adaptador local)" if enabled else "OpenAI direto")
        )
        self.stdout.write(f"Modelo configurado         : {C._configured_model()}")
        self.stdout.write(f"Runtime Codex disponível?  : {C.codex_runtime_available()}")

        self.stdout.write("")
        self.stdout.write("Credenciais presentes (sem valores):")
        self.stdout.write(f"  IARA_CLIENT_ID           : {bool(os.environ.get('IARA_CLIENT_ID'))}")
        self.stdout.write(f"  IARA_CLIENT_SECRET       : {bool(os.environ.get('IARA_CLIENT_SECRET'))}")
        self.stdout.write(f"  OPENAI_API_KEY           : {bool(os.environ.get('OPENAI_API_KEY'))}")
        self.stdout.write(f"  IARA_ENVIRONMENT         : {os.environ.get('IARA_ENVIRONMENT') or '(vazio)'}")

        if enabled:
            self.stdout.write("")
            self.stdout.write(f"Adaptador local            : {C._iara_adapter_base_url()}")
            codex_home = C._codex_home_path()
            config_path = C._managed_config_path(codex_home)
            self.stdout.write(f"CODEX_HOME                 : {codex_home}")
            self.stdout.write(f"config.toml gerenciado     : {config_path}")
            if config_path.is_file():
                content = config_path.read_text(encoding="utf-8", errors="replace")
                self.stdout.write("  conteúdo (não contém segredos):")
                for line in content.splitlines():
                    self.stdout.write(f"    {line}")
            else:
                self.stdout.write(
                    "  (ainda não escrito — é gerado ao iniciar um turno; "
                    "abra um chat uma vez e rode este comando de novo)"
                )

            self.stdout.write("")
            try:
                import iaragenai

                self.stdout.write(f"SDK iaragenai importado de : {getattr(iaragenai, '__file__', '?')}")
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(self.style.ERROR(f"SDK iaragenai NÃO importa: {exc}"))

        self.stdout.write("")
        if enabled and not (
            os.environ.get("IARA_CLIENT_ID") and os.environ.get("IARA_CLIENT_SECRET")
        ):
            self.stdout.write(self.style.ERROR(
                "IARA habilitado mas sem credenciais — preencha IARA_CLIENT_ID/"
                "IARA_CLIENT_SECRET no .env."
            ))
        elif not enabled:
            self.stdout.write(self.style.WARNING(
                "OpenAI está ativo. Para usar o IARA, defina ATENA_IARA_ENABLED=true "
                "no .env e REINICIE o servidor (editar .env não recarrega sozinho)."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                "IARA habilitado e com credenciais. Se ainda cair em api.openai.com, "
                "reinicie o servidor para reler o .env."
            ))
