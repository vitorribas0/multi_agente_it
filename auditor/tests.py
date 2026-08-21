from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, TestCase, override_settings

from .codex_views import (
    _is_html_revision_request,
    _prompt_with_history,
    _split_html_response,
)
from .models import Conversation, Message


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
