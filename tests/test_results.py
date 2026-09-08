from __future__ import annotations

from pathlib import Path
import site
import sys
from tempfile import TemporaryDirectory
import unittest
import zipfile
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
USER_SITE = Path(site.getusersitepackages())
for candidate in (ROOT, SRC, USER_SITE):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from generador_horarios.models import MatchRecord
from generador_horarios.results import (
    ParsedResultEntry,
    build_match_key,
    match_results_to_records,
    parse_results_doc,
)


def create_test_docx(path: Path, paragraphs: list[str]) -> None:
    body = []
    for paragraph in paragraphs:
        body.append(f"<w:p><w:r><w:t>{escape(paragraph)}</w:t></w:r></w:p>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>'
        + ''.join(body)
        + '</w:body></w:document>'
    )
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', xml)


class ResultsTests(unittest.TestCase):
    def test_parse_results_doc_extracts_only_score_lines(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            docx_path = Path(tmp_dir) / 'results.docx'
            create_test_docx(
                docx_path,
                [
                    'JUNIOR PREFERENTE FEMENINO PRIMERA DIVISIÓN, GRUPO 3',
                    'SAN JOSÉ DEL PARQUE 49-45 BASKET ARANJUEZ ESENCIAL SALUD',
                    'Parciales: 10-15, 12-15, 16-6, 11-9',
                    'En el tiempo extra, el mayor acierto visitante terminó decantando el encuentro con el 48-54 final.',
                ],
            )

            entries = parse_results_doc(docx_path)

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].local_team_raw, 'SAN JOSÉ DEL PARQUE')
            self.assertEqual(entries[0].visitante_team_raw, 'BASKET ARANJUEZ ESENCIAL SALUD')
            self.assertEqual(entries[0].local_score, 49)
            self.assertEqual(entries[0].visitante_score, 45)

    def test_match_results_to_records_handles_aliases(self) -> None:
        record = MatchRecord(
            row_index=2,
            categoria='Infantil Fem. Pref.',
            local_original='BASKET ARANJUEZ ESENCIAL SALUD',
            visitante_original='CABRINI A.P.A. VERDE',
            fecha=None,
            hora=None,
            campo_original='LAS OLIVAS, CIUDAD DEPORTIVA',
            match_key=build_match_key(None, 'Infantil Fem. Pref.', 'BASKET ARANJUEZ ESENCIAL SALUD', 'CABRINI A.P.A. VERDE'),
        )

        entries = [
            ParsedResultEntry(
                category_context='INFANTIL PREFERENTE FEMENINO PRIMERA DIVISIÓN, GRUPO 2',
                score_line='BASKET ARANJUEZ ESENCIAL SALUD 74-35 CABRINI VERDE',
                local_team_raw='BASKET ARANJUEZ ESENCIAL SALUD',
                visitante_team_raw='CABRINI VERDE',
                local_score=74,
                visitante_score=35,
            )
        ]

        matches = match_results_to_records([record], entries)

        self.assertIn(record.match_key, matches)
        self.assertEqual(matches[record.match_key].visitante_score, 35)


if __name__ == '__main__':
    unittest.main()
