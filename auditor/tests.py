import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from .codex_app_server import CodexAppServer
from .codex_views import (
    _CODEX_RUNTIME_ID,
    _ExecutionEventQueue,
    _PENDING_INTERACTIONS,
    _PENDING_INTERACTIONS_LOCK,
    _PendingCodexInteraction,
    _advance_live_plan,
    _artifact_snapshot,
    _codex_trace_record,
    _collect_generated_artifacts,
    _is_html_revision_request,
    _latest_html_artifact,
    _prepare_session_workspace,
    _prompt_with_history,
    _split_html_response,
    _strip_codex_file_citations,
    _register_codex_execution,
    _recover_orphaned_local_executions,
    _unregister_codex_execution,
    request_codex_execution_stop,
)
from .models import Agent, Conversation, Execution, ExecutionInteraction, Message
from tools.gerar_html import gerar_html


class ApiOnlyBoundaryTests(TestCase):
    def test_legacy_django_frontend_routes_are_not_exposed(self):
        for path in ("/", "/manual/", "/settings/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_admin_and_api_remain_available(self):
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        self.assertEqual(self.client.get("/api/codex/status/").status_code, 200)


class CodexAppServerEventTests(SimpleTestCase):
    @staticmethod
    def _client() -> CodexAppServer:
        client = object.__new__(CodexAppServer)
        client.cwd = Path("/tmp/chat/artefatos")
        client._turn_lock = threading.Lock()
        client._active_thread_id = None
        client._active_turn_id = None
        client._interrupt_requested = threading.Event()
        client._interrupt_sent = threading.Event()
        return client

    def test_streams_complete_activity_lifecycle_and_command_output(self):
        client = self._client()
        sent = []
        client._send = lambda payload: sent.append(payload)
        client._messages = lambda: iter([
            {
                "method": "item/started",
                "params": {"item": {
                    "type": "commandExecution",
                    "id": "cmd-1",
                    "command": "python analisar.py",
                    "cwd": "/tmp/chat",
                    "status": "inProgress",
                }},
            },
            {
                "method": "item/commandExecution/outputDelta",
                "params": {"itemId": "cmd-1", "delta": "processando\n"},
            },
            {
                "method": "item/completed",
                "params": {"item": {
                    "type": "commandExecution",
                    "id": "cmd-1",
                    "command": "python analisar.py",
                    "cwd": "/tmp/chat",
                    "status": "completed",
                    "exitCode": 0,
                    "durationMs": 125,
                    "aggregatedOutput": "concluído",
                }},
            },
            {
                "method": "turn/completed",
                "params": {"turn": {"status": "completed", "error": None}},
            },
        ])

        events = list(client.turn("thread-1", "analise"))

        self.assertEqual(sent[0]["method"], "turn/start")
        self.assertEqual(sent[0]["params"]["sandboxPolicy"]["type"], "workspaceWrite")
        self.assertEqual(
            sent[0]["params"]["sandboxPolicy"]["writableRoots"],
            ["/tmp/chat/artefatos"],
        )
        self.assertTrue(sent[0]["params"]["sandboxPolicy"]["networkAccess"])
        self.assertEqual(events[0]["phase"], "started")
        self.assertEqual(events[0]["item"]["id"], "cmd-1")
        self.assertEqual(events[1]["type"], "activity_output")
        self.assertEqual(events[2]["phase"], "completed")
        self.assertEqual(events[3]["type"], "completed")

    def test_routes_interactive_request_and_live_plan(self):
        client = self._client()
        sent = []
        client._send = lambda payload: sent.append(payload)
        client._messages = lambda: iter([
            {
                "id": 77,
                "method": "item/tool/requestUserInput",
                "params": {"questions": [{"id": "scope", "question": "Qual escopo?"}]},
            },
            {
                "method": "turn/plan/updated",
                "params": {
                    "explanation": "Plano inicial",
                    "plan": [{"step": "Validar escopo", "status": "inProgress"}],
                },
            },
            {
                "method": "turn/completed",
                "params": {"turn": {"status": "completed", "error": None}},
            },
        ])

        events = list(client.turn(
            "thread-1",
            "analise",
            lambda method, params: {
                "answers": {params["questions"][0]["id"]: {"answers": ["2026"]}}
            },
        ))

        self.assertEqual(sent[0]["params"]["approvalPolicy"], "untrusted")
        self.assertEqual(sent[1]["id"], 77)
        self.assertEqual(sent[1]["result"]["answers"]["scope"]["answers"], ["2026"])
        self.assertEqual(events[0]["type"], "plan")
        self.assertEqual(events[0]["plan"][0]["status"], "inProgress")

    def test_interrupt_uses_the_active_thread_and_turn_ids(self):
        client = self._client()
        sent = []
        started_turns = []
        client._send = lambda payload: sent.append(payload)

        def messages():
            yield {
                "id": 4,
                "result": {"turn": {"id": "turn-9", "status": "inProgress", "items": []}},
            }
            self.assertTrue(client.interrupt())
            yield {
                "method": "turn/completed",
                "params": {"turn": {"id": "turn-9", "status": "interrupted", "items": []}},
            }

        client._messages = messages

        events = list(client.turn(
            "thread-7",
            "analise longa",
            turn_started_handler=started_turns.append,
        ))

        self.assertEqual(sent[1]["method"], "turn/interrupt")
        self.assertEqual(sent[1]["params"], {"threadId": "thread-7", "turnId": "turn-9"})
        self.assertEqual(started_turns, ["turn-9"])
        self.assertEqual(events[-1]["status"], "interrupted")


class LivePlanProgressTests(SimpleTestCase):
    def test_advances_the_active_step_after_a_completed_action(self):
        plan = [
            {"step": "Ler dados", "status": "inProgress"},
            {"step": "Analisar", "status": "pending"},
            {"step": "Gerar relatório", "status": "pending"},
        ]

        advanced = _advance_live_plan(plan)

        self.assertEqual([item["status"] for item in advanced], ["completed", "inProgress", "pending"])
        self.assertEqual(plan[0]["status"], "inProgress")

    def test_does_not_advance_a_completed_plan(self):
        self.assertIsNone(_advance_live_plan([{"step": "Concluído", "status": "completed"}]))


class CodexInteractionEndpointTests(TestCase):
    def tearDown(self):
        with _PENDING_INTERACTIONS_LOCK:
            _PENDING_INTERACTIONS.clear()

    def test_stop_endpoint_interrupts_an_active_codex_execution(self):
        conversation = Conversation.objects.create(title="Execução longa")
        execution_record = Execution.objects.create(
            conversation=conversation,
            status="running",
            runtime_id=_CODEX_RUNTIME_ID,
        )
        execution = _register_codex_execution(
            conversation.id,
            str(execution_record.id),
        )

        class FakeClient:
            interrupted = False
            closed = False

            def interrupt(self):
                self.interrupted = True
                return True

            def close(self):
                self.closed = True

        fake_client = FakeClient()
        with execution.lock:
            execution.client = fake_client
        try:
            response = self.client.post(f"/api/conversations/{conversation.id}/stop/")
            payload = response.json()

            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["stopped"])
            self.assertTrue(payload["codex_stopped"])
            self.assertTrue(execution.stop_event.is_set())
            self.assertTrue(fake_client.interrupted)
            execution_record.refresh_from_db()
            self.assertEqual(execution_record.status, "stopping")
        finally:
            _unregister_codex_execution(execution)

    def test_execution_events_survive_the_stream_connection(self):
        conversation = Conversation.objects.create(title="Persistência")
        execution = Execution.objects.create(
            conversation=conversation,
            status="starting",
            runtime_id=_CODEX_RUNTIME_ID,
        )
        events = _ExecutionEventQueue(execution.id)

        events.put({"type": "progress", "stage": "thinking", "text": "Analisando"})
        events.put({
            "type": "plan",
            "explanation": "Plano",
            "plan": [{"step": "Ler dados", "status": "inProgress"}],
        })

        execution.refresh_from_db()
        self.assertEqual(execution.status, "running")
        self.assertEqual(len(execution.events), 2)
        self.assertEqual([event["sequence"] for event in execution.events], [1, 2])
        self.assertEqual(execution.plan[0]["step"], "Ler dados")

    def test_late_progress_does_not_reactivate_a_stopping_execution(self):
        conversation = Conversation.objects.create(title="Interrompendo")
        execution = Execution.objects.create(
            conversation=conversation,
            status="stopping",
            runtime_id=_CODEX_RUNTIME_ID,
        )

        _ExecutionEventQueue(execution.id).put({
            "type": "progress",
            "stage": "thinking",
            "text": "Evento atrasado",
        })

        execution.refresh_from_db()
        self.assertEqual(execution.status, "stopping")
        self.assertEqual(execution.events[0]["text"], "Evento atrasado")

    def test_execution_status_marks_an_old_local_runtime_as_failed(self):
        conversation = Conversation.objects.create(title="Órfã")
        execution = Execution.objects.create(
            conversation=conversation,
            status="running",
            runtime_id="processo-anterior",
        )

        changed = _recover_orphaned_local_executions(conversation.id)

        execution.refresh_from_db()
        self.assertEqual(changed, 1)
        self.assertEqual(execution.status, "failed")
        self.assertIn("reiniciado", execution.error)

    def test_conversation_detail_exposes_the_active_execution(self):
        conversation = Conversation.objects.create(title="Recuperável")
        execution = Execution.objects.create(
            conversation=conversation,
            status="waiting_user",
            runtime_id=_CODEX_RUNTIME_ID,
            events=[{"type": "interaction", "interaction": {"token": "abc"}}],
        )

        response = self.client.get(f"/api/conversations/{conversation.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["active_execution"]["id"], str(execution.id))
        self.assertEqual(response.json()["active_execution"]["status"], "waiting_user")

    def test_database_rejects_two_active_executions_for_the_same_chat(self):
        conversation = Conversation.objects.create(title="Sem duplicidade")
        Execution.objects.create(conversation=conversation, status="running")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Execution.objects.create(conversation=conversation, status="queued")

    def test_chat_request_is_persisted_for_the_local_worker(self):
        agent = Agent.objects.create(name="Atena", slug="atena", is_default=True)
        conversation = Conversation.objects.create(title="Fila durável", agent=agent)

        response = self.client.post(
            "/api/codex/chat/stream/",
            data=json.dumps({
                "conversation_id": conversation.id,
                "message": "Analise este conjunto de dados",
            }),
            content_type="application/json",
        )
        list(response.streaming_content)

        self.assertEqual(response.status_code, 200)
        execution = Execution.objects.get(pk=response["X-Execution-Id"])
        self.assertEqual(execution.backend, "local-worker")
        self.assertEqual(execution.status, "queued")
        self.assertEqual(
            execution.request_payload["message"],
            "Analise este conjunto de dados",
        )
        self.assertTrue(execution.request_payload["_prepared_prompt"])
        self.assertEqual(
            conversation.messages.filter(role="user").values_list("content", flat=True).get(),
            "Analise este conjunto de dados",
        )

    def test_a_queued_worker_execution_can_be_stopped_before_claim(self):
        conversation = Conversation.objects.create(title="Cancelar fila")
        execution = Execution.objects.create(
            conversation=conversation,
            backend="local-worker",
            status="queued",
            request_payload={"message": "Tarefa longa"},
        )

        stopped = request_codex_execution_stop(execution)

        execution.refresh_from_db()
        self.assertTrue(stopped)
        self.assertEqual(execution.status, "stopped")
        self.assertIsNotNone(execution.stop_requested_at)

    def test_durable_interaction_is_answered_across_processes(self):
        conversation = Conversation.objects.create(title="Pergunta durável")
        execution = Execution.objects.create(
            conversation=conversation,
            backend="local-worker",
            status="waiting_user",
        )
        requested = {"network": {"enabled": True}}
        interaction = ExecutionInteraction.objects.create(
            execution=execution,
            method="item/permissions/requestApproval",
            params={"permissions": requested},
        )

        response = self.client.post(
            f"/api/codex/interactions/{interaction.token}/respond/",
            data=json.dumps({"approve": True, "scope": "turn"}),
            content_type="application/json",
        )

        interaction.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(interaction.status, "responded")
        self.assertEqual(
            interaction.response,
            {"permissions": requested, "scope": "turn"},
        )

    def test_worker_claims_a_queued_execution_in_a_separate_command(self):
        conversation = Conversation.objects.create(title="Worker separado")
        execution = Execution.objects.create(
            conversation=conversation,
            backend="local-worker",
            status="queued",
            request_payload={"message": "Processar"},
        )
        with TemporaryDirectory() as temporary_dir, override_settings(
            BASE_DIR=Path(temporary_dir)
        ), patch(
            "auditor.management.commands.run_agent_worker.run_queued_codex_execution"
        ) as runner:
            call_command("run_agent_worker", "--once", verbosity=0)

        execution.refresh_from_db()
        runner.assert_called_once()
        self.assertEqual(execution.status, "starting")
        self.assertEqual(execution.attempts, 1)
        self.assertTrue(execution.runtime_id.startswith("local-worker-"))

    def test_submits_all_question_answers(self):
        pending = _PendingCodexInteraction(
            conversation_id=1,
            method="item/tool/requestUserInput",
            params={"questions": [{"id": "periodo"}, {"id": "formato"}]},
        )
        with _PENDING_INTERACTIONS_LOCK:
            _PENDING_INTERACTIONS["question-token"] = pending

        response = self.client.post(
            "/api/codex/interactions/question-token/respond/",
            data=json.dumps({"answers": {"periodo": ["2026"], "formato": ["Excel"]}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(pending.ready.is_set())
        self.assertEqual(pending.response["answers"]["periodo"]["answers"], ["2026"])

    def test_grants_only_requested_permissions_for_turn(self):
        requested = {"network": {"enabled": True}, "fileSystem": None}
        pending = _PendingCodexInteraction(
            conversation_id=1,
            method="item/permissions/requestApproval",
            params={"permissions": requested},
        )
        with _PENDING_INTERACTIONS_LOCK:
            _PENDING_INTERACTIONS["permission-token"] = pending

        response = self.client.post(
            "/api/codex/interactions/permission-token/respond/",
            data=json.dumps({"approve": True, "scope": "turn"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending.response, {"permissions": requested, "scope": "turn"})

    def test_cancel_releases_a_pending_question(self):
        pending = _PendingCodexInteraction(
            conversation_id=1,
            method="item/tool/requestUserInput",
            params={"questions": [{"id": "periodo"}]},
        )
        with _PENDING_INTERACTIONS_LOCK:
            _PENDING_INTERACTIONS["cancel-token"] = pending

        response = self.client.post(
            "/api/codex/interactions/cancel-token/respond/",
            data=json.dumps({"cancel": True}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending.response, {"answers": {}})
        self.assertTrue(pending.ready.is_set())

    def test_approve_all_accepts_current_command_and_marks_the_turn(self):
        pending = _PendingCodexInteraction(
            conversation_id=1,
            method="item/commandExecution/requestApproval",
            params={"command": "python analisar.py"},
        )
        with _PENDING_INTERACTIONS_LOCK:
            _PENDING_INTERACTIONS["approve-all-token"] = pending

        response = self.client.post(
            "/api/codex/interactions/approve-all-token/respond/",
            data=json.dumps({"approve_all": True}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(pending.response["decision"], "accept")
        self.assertTrue(pending.response["__approve_all_for_turn__"])
        self.assertTrue(pending.ready.is_set())


class CodexTraceMappingTests(SimpleTestCase):
    def test_maps_command_to_persistable_trace(self):
        record = _codex_trace_record({
            "type": "commandExecution",
            "id": "cmd-2",
            "command": "python analisar.py",
            "cwd": "/tmp/chat-12",
            "status": "completed",
            "aggregatedOutput": "42 registros",
            "exitCode": 0,
            "durationMs": 240,
        })

        self.assertEqual(record["tool"], "codex_command")
        self.assertEqual(record["args"]["command"], "python analisar.py")
        self.assertEqual(record["result"], "42 registros")
        self.assertEqual(record["duration_ms"], 240)

    def test_redacts_api_key_from_trace(self):
        record = _codex_trace_record({
            "type": "commandExecution",
            "id": "cmd-3",
            "command": "OPENAI_API_KEY=secret-value comando",
            "status": "completed",
            "aggregatedOutput": "ok",
        })

        self.assertNotIn("secret-value", record["args"]["command"])


class CodexHtmlArtifactTests(SimpleTestCase):
    def test_extracts_fenced_standalone_html(self):
        answer = "Resumo executivo.\n\n```html\n<!doctype html><html><body>OK</body></html>\n```"

        visible, html = _split_html_response(answer)

        self.assertEqual(visible, "Resumo executivo.")
        self.assertEqual(html, "<!doctype html><html><body>OK</body></html>")

    def test_preserves_plain_text(self):
        answer = "Resposta comum, sem relatório."

        visible, html = _split_html_response(answer)

        self.assertEqual(visible, answer)
        self.assertIsNone(html)

    def test_does_not_extract_incomplete_html(self):
        answer = "<html><body>documento interrompido"

        visible, html = _split_html_response(answer)

        self.assertEqual(visible, answer)
        self.assertIsNone(html)

    def test_recognizes_explicit_reference_to_existing_html(self):
        self.assertTrue(
            _is_html_revision_request(
                "Nesse mesmo HTML crie uma página com o processo realizado."
            )
        )

    def test_does_not_treat_new_report_as_revision(self):
        self.assertFalse(_is_html_revision_request("Crie um relatório HTML mensal."))


class SessionArtifactStorageTests(SimpleTestCase):
    def test_html_tool_saves_into_the_conversation_box(self):
        with TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir):
            result = json.loads(gerar_html(
                _session={"__conversation_id": 23},
                html="<!doctype html><html><head><title>Teste</title></head><body>ok</body></html>",
                titulo="Teste",
            ))

            self.assertTrue(result["download_url"].startswith("/api/conversations/23/artifacts/"))
            artifact = (
                Path(temp_dir) / "runtime" / "codex_sessions" / "23" / "saida" / result["filename"]
            )
            self.assertTrue(artifact.exists())
            self.assertFalse((Path(temp_dir) / "exports" / result["filename"]).exists())


class CodexHtmlRevisionPromptTests(TestCase):
    def test_injects_latest_html_and_requires_complete_document(self):
        with TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir):
            conv = Conversation.objects.create(title="Teste")
            artifacts_dir = Path(temp_dir) / "runtime" / "codex_sessions" / str(conv.id) / "artefatos"
            artifacts_dir.mkdir(parents=True)
            html = "<!doctype html><html><body>Relatório anterior</body></html>"
            (artifacts_dir / "relatorio.html").write_text(html, encoding="utf-8")
            Message.objects.create(
                conversation=conv,
                role="assistant",
                content="Relatório gerado.",
                attachments=[{
                    "kind": "export",
                    "formato": "html",
                    "download_url": f"/api/conversations/{conv.id}/artifacts/relatorio.html",
                }],
            )

            prompt = _prompt_with_history(
                conv,
                "Nesse mesmo HTML inclua a metodologia.",
                has_codex_thread=True,
            )

        self.assertIn(html, prompt)
        self.assertIn("documento HTML completo", prompt)
        self.assertIn("Não devolva CSS isolado", prompt)
        self.assertTrue(prompt.endswith("para inserir ou substituir trechos."))

    def test_keeps_reading_legacy_exports_for_existing_history(self):
        with TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir):
            export_dir = Path(temp_dir) / "exports"
            export_dir.mkdir()
            html = "<!doctype html><html><body>Versão antiga</body></html>"
            (export_dir / "relatorio.html").write_text(html, encoding="utf-8")
            conv = Conversation.objects.create(title="Teste")
            Message.objects.create(
                conversation=conv,
                role="assistant",
                content="Relatório gerado.",
                attachments=[{
                    "kind": "export",
                    "formato": "html",
                    "download_url": "/api/exports/relatorio.html",
                }],
            )

            self.assertEqual(_latest_html_artifact(conv), html)


class CodexGeneratedArtifactTests(TestCase):
    def test_strips_internal_file_citation_from_visible_answer(self):
        answer = (
            "Planilha criada. "
            ':codex-file-citation{path="/tmp/planilha.xlsx" purpose="output"}'
        )

        self.assertEqual(_strip_codex_file_citations(answer), "Planilha criada.")

    def test_publishes_new_excel_and_keeps_it_in_chat_workspace(self):
        from openpyxl import Workbook

        with TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir):
            workspace = Path(temp_dir) / "runtime" / "codex_sessions" / "7"
            working_dir = workspace / "trabalho"
            before = _artifact_snapshot(working_dir)
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Resumo"
            sheet.append(["Mês", "Valor"])
            sheet.append(["Janeiro", 100])
            sheet.append(["Fevereiro", 120])
            working_dir.mkdir(parents=True, exist_ok=True)
            generated = working_dir / "orcamento.xlsx"
            workbook.save(generated)

            attachments = _collect_generated_artifacts(working_dir, before, 7)

            self.assertTrue(generated.exists())
            self.assertEqual(len(attachments), 1)
            attachment = attachments[0]
            self.assertEqual(attachment["kind"], "export")
            self.assertEqual(attachment["formato"], "xlsx")
            self.assertEqual(attachment["filename"], "orcamento.xlsx")
            self.assertEqual(attachment["linhas"], 2)
            self.assertEqual(attachment["colunas"], 2)
            self.assertEqual(attachment["abas"], ["Resumo"])
            self.assertEqual(
                attachment["download_url"].split("/artifacts/")[0],
                "/api/conversations/7",
            )
            exported = workspace / "saida" / Path(attachment["download_url"]).name
            self.assertTrue(exported.exists())

    def test_serves_artifact_only_from_its_conversation(self):
        with TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir):
            artifacts_dir = Path(temp_dir) / "runtime" / "codex_sessions" / "12" / "saida"
            artifacts_dir.mkdir(parents=True)
            (artifacts_dir / "relatorio.html").write_text("<html>ok</html>", encoding="utf-8")

            response = self.client.get("/api/conversations/12/artifacts/relatorio.html")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), b"<html>ok</html>")
            self.assertEqual(
                self.client.get("/api/conversations/13/artifacts/relatorio.html").status_code,
                404,
            )

    def test_archives_previous_output_when_filename_is_reused(self):
        with TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir):
            workspace = Path(temp_dir) / "runtime" / "codex_sessions" / "15"
            working_dir = workspace / "trabalho"
            working_dir.mkdir(parents=True)
            generated = working_dir / "resultado.csv"
            generated.write_text("id,valor\n1,10\n", encoding="utf-8")
            _collect_generated_artifacts(working_dir, {}, 15)

            before = _artifact_snapshot(working_dir)
            generated.write_text("id,valor\n1,20\n", encoding="utf-8")
            attachments = _collect_generated_artifacts(working_dir, before, 15)

            self.assertEqual(len(attachments), 1)
            self.assertEqual(
                (workspace / "saida" / "resultado.csv").read_text(encoding="utf-8"),
                "id,valor\n1,20\n",
            )
            archived = list((workspace / "versoes" / "resultado").glob("*.csv"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_text(encoding="utf-8"), "id,valor\n1,10\n")

    def test_does_not_republish_unchanged_artifact(self):
        with TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir):
            artifacts_dir = Path(temp_dir) / "trabalho"
            artifacts_dir.mkdir()
            (artifacts_dir / "dados.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            before = _artifact_snapshot(artifacts_dir)

            attachments = _collect_generated_artifacts(artifacts_dir, before, 8)

            self.assertEqual(attachments, [])

    def test_manifest_lists_generated_artifacts(self):
        with TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir):
            conv = Conversation.objects.create(title="Artefatos")
            workspace = Path(temp_dir) / "runtime" / "codex_sessions" / str(conv.id)
            artifacts_dir = workspace / "saida"
            artifacts_dir.mkdir(parents=True)
            (artifacts_dir / "resultado.csv").write_text("id\n1\n", encoding="utf-8")

            _prepare_session_workspace(conv)

            manifest = json.loads(
                (workspace / "manifesto_sessao.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["artefatos_gerados"][0]["arquivo"],
                "saida/resultado.csv",
            )

    def test_workspace_separates_input_data_from_outputs(self):
        with TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir):
            conv = Conversation.objects.create(
                title="Entradas",
                state={
                    "athena_last_result": [{"id": 1, "valor": 20}],
                    "athena_last_columns": ["id", "valor"],
                    "named_datasets": {"base de teste": [{"id": 1}]},
                    "documento_atual": {
                        "filename": "evidencia.pdf",
                        "markdown": "# Evidência",
                        "page_count": 1,
                    },
                },
            )

            workspace = _prepare_session_workspace(conv)
            manifest = json.loads(
                (workspace / "manifesto_sessao.json").read_text(encoding="utf-8")
            )

            self.assertTrue((workspace / "entrada" / "dataset_atual.json").exists())
            self.assertTrue((workspace / "entrada" / "datasets" / "001_base_de_teste.json").exists())
            self.assertTrue((workspace / "entrada" / "documentos" / "evidencia.md").exists())
            self.assertTrue((workspace / "trabalho").is_dir())
            self.assertTrue((workspace / "saida").is_dir())
            self.assertTrue((workspace / "evidencias" / "fontes.json").is_file())
            self.assertTrue((workspace / "versoes").is_dir())
            self.assertEqual(manifest["dataset_atual"], "entrada/dataset_atual.json")
            self.assertEqual(manifest["documento_atual"]["arquivo"], "entrada/documentos/evidencia.md")
            self.assertEqual(manifest["estrutura"]["saida"], "saida")
