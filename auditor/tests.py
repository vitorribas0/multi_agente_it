import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, TestCase, override_settings

from .codex_app_server import CodexAppServer
from .codex_views import (
    _artifact_snapshot,
    _codex_trace_record,
    _collect_generated_artifacts,
    _is_html_revision_request,
    _prepare_session_workspace,
    _prompt_with_history,
    _split_html_response,
    _strip_codex_file_citations,
)
from .models import Conversation, Message


class CodexAppServerEventTests(SimpleTestCase):
    def test_streams_complete_activity_lifecycle_and_command_output(self):
        client = object.__new__(CodexAppServer)
        client.cwd = Path("/tmp/chat/artefatos")
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
        self.assertFalse(sent[0]["params"]["sandboxPolicy"]["networkAccess"])
        self.assertEqual(events[0]["phase"], "started")
        self.assertEqual(events[0]["item"]["id"], "cmd-1")
        self.assertEqual(events[1]["type"], "activity_output")
        self.assertEqual(events[2]["phase"], "completed")
        self.assertEqual(events[3]["type"], "completed")


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


class CodexHtmlRevisionPromptTests(TestCase):
    def test_injects_latest_html_and_requires_complete_document(self):
        with TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir):
            export_dir = Path(temp_dir) / "exports"
            export_dir.mkdir()
            html = "<!doctype html><html><body>Relatório anterior</body></html>"
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

            prompt = _prompt_with_history(
                conv,
                "Nesse mesmo HTML inclua a metodologia.",
                has_codex_thread=True,
            )

        self.assertIn(html, prompt)
        self.assertIn("documento HTML completo", prompt)
        self.assertIn("Não devolva CSS isolado", prompt)
        self.assertTrue(prompt.endswith("para inserir ou substituir trechos."))


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
            artifacts_dir = Path(temp_dir) / "runtime" / "codex_sessions" / "7" / "artefatos"
            before = _artifact_snapshot(artifacts_dir)
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Resumo"
            sheet.append(["Mês", "Valor"])
            sheet.append(["Janeiro", 100])
            sheet.append(["Fevereiro", 120])
            generated = artifacts_dir / "orcamento.xlsx"
            workbook.save(generated)

            attachments = _collect_generated_artifacts(artifacts_dir, before, 7)

            self.assertTrue(generated.exists())
            self.assertEqual(len(attachments), 1)
            attachment = attachments[0]
            self.assertEqual(attachment["kind"], "export")
            self.assertEqual(attachment["formato"], "xlsx")
            self.assertEqual(attachment["filename"], "orcamento.xlsx")
            self.assertEqual(attachment["linhas"], 2)
            self.assertEqual(attachment["colunas"], 2)
            self.assertEqual(attachment["abas"], ["Resumo"])
            exported = Path(temp_dir) / "exports" / Path(attachment["download_url"]).name
            self.assertTrue(exported.exists())

    def test_does_not_republish_unchanged_artifact(self):
        with TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir):
            artifacts_dir = Path(temp_dir) / "artefatos"
            artifacts_dir.mkdir()
            (artifacts_dir / "dados.csv").write_text("a,b\n1,2\n", encoding="utf-8")
            before = _artifact_snapshot(artifacts_dir)

            attachments = _collect_generated_artifacts(artifacts_dir, before, 8)

            self.assertEqual(attachments, [])

    def test_manifest_lists_generated_artifacts(self):
        with TemporaryDirectory() as temp_dir, override_settings(BASE_DIR=temp_dir):
            conv = Conversation.objects.create(title="Artefatos")
            workspace = Path(temp_dir) / "runtime" / "codex_sessions" / str(conv.id)
            artifacts_dir = workspace / "artefatos"
            artifacts_dir.mkdir(parents=True)
            (artifacts_dir / "resultado.csv").write_text("id\n1\n", encoding="utf-8")

            _prepare_session_workspace(conv)

            manifest = json.loads(
                (workspace / "manifesto_sessao.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["artefatos_gerados"][0]["arquivo"],
                "artefatos/resultado.csv",
            )
