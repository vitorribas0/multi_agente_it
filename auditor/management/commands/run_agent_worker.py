"""Worker local durável para execuções da Atena."""

from __future__ import annotations

import fcntl
import time
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, transaction
from django.db.models import F
from django.utils import timezone

from auditor.codex_views import _persist_execution_event, run_queued_codex_execution
from auditor.models import Execution, ExecutionInteraction


class Command(BaseCommand):
    help = "Processa a fila persistida de execuções da Atena em um processo separado."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Processa no máximo uma execução e encerra.",
        )
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=0.5,
            help="Intervalo de consulta da fila, em segundos (padrão: 0,5).",
        )
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=0,
            help="Encerra após N execuções; zero mantém o worker ativo.",
        )

    def handle(self, *args, **options):
        poll_interval = max(0.1, float(options["poll_interval"]))
        max_jobs = max(0, int(options["max_jobs"]))
        once = bool(options["once"])
        worker_id = f"local-worker-{uuid4().hex[:24]}"
        lock_path = Path(settings.BASE_DIR) / "runtime" / "agent-worker.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with lock_path.open("a+") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CommandError("Já existe um worker local da Atena em execução.") from exc

            recovered = self._recover_orphaned_worker_executions()
            if recovered:
                self.stdout.write(
                    self.style.WARNING(f"{recovered} execução(ões) órfã(s) reconciliada(s).")
                )
            self.stdout.write(self.style.SUCCESS(f"Worker Atena ativo: {worker_id}"))

            processed = 0
            try:
                while True:
                    close_old_connections()
                    execution = self._claim_next(worker_id)
                    if execution is None:
                        if once or (max_jobs and processed >= max_jobs):
                            break
                        time.sleep(poll_interval)
                        continue

                    self.stdout.write(
                        f"Executando {execution.id} (conversa {execution.conversation_id})"
                    )
                    self._run(execution)
                    processed += 1
                    if once or (max_jobs and processed >= max_jobs):
                        break
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING("Worker Atena encerrado pelo operador."))
            finally:
                close_old_connections()

    @staticmethod
    def _claim_next(worker_id: str) -> Execution | None:
        with transaction.atomic():
            execution = (
                Execution.objects.select_for_update()
                .filter(backend="local-worker", status="queued")
                .order_by("created_at")
                .first()
            )
            if execution is None:
                return None
            now = timezone.now()
            changed = Execution.objects.filter(
                pk=execution.pk,
                status="queued",
            ).update(
                status="starting",
                runtime_id=worker_id,
                claimed_at=now,
                attempts=F("attempts") + 1,
                last_heartbeat_at=now,
                updated_at=now,
            )
            if not changed:
                return None
            execution.refresh_from_db()
            return execution

    @staticmethod
    def _run(execution: Execution) -> None:
        try:
            run_queued_codex_execution(execution)
        except Exception as exc:
            execution.refresh_from_db()
            if execution.status == "stopping":
                _persist_execution_event(execution.id, {
                    "type": "done",
                    "payload": {"stopped": True},
                })
                return
            if execution.status not in Execution.TERMINAL_STATUSES:
                _persist_execution_event(execution.id, {
                    "type": "error",
                    "message": f"Worker Atena: {exc}",
                })

    @staticmethod
    def _recover_orphaned_worker_executions() -> int:
        """Reconcilia trabalhos abandonados antes de aceitar uma nova fila.

        O lock exclusivo garante que não exista outro worker local saudável ao
        executar esta rotina. Não repetimos automaticamente um turno iniciado,
        pois comandos externos podem não ser idempotentes.
        """
        now = timezone.now()
        active = Execution.objects.filter(
            backend="local-worker",
            status__in=("starting", "running", "waiting_user", "stopping"),
        )
        ids = list(active.values_list("id", flat=True))
        if not ids:
            return 0
        ExecutionInteraction.objects.filter(
            execution_id__in=ids,
            status="pending",
        ).update(
            status="cancelled",
            response={},
            responded_at=now,
            updated_at=now,
        )
        stopped = active.filter(status="stopping").update(
            status="stopped",
            error="",
            finished_at=now,
            last_heartbeat_at=now,
            updated_at=now,
        )
        failed = active.exclude(status="stopping").update(
            status="failed",
            error="Execução encerrada porque o worker local foi reiniciado.",
            finished_at=now,
            last_heartbeat_at=now,
            updated_at=now,
        )
        return stopped + failed
