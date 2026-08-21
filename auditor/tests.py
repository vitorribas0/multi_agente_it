from django.test import TestCase

# Create your tests here.
from django.test import SimpleTestCase

from .codex_views import _split_html_response


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
