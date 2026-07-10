from __future__ import annotations

import shutil
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pypdf import PdfWriter

from src.generator.generation import generator_agent, pdf_converter, pdf_template_renderer
from src.generator.generation.pdf_quality import count_pdf_pages


# Tests in this module mock ``convert_docx_batch`` to write placeholder bytes
# instead of real PDFs. The production KP finalizer validates the produced PDF
# with ``pypdf`` (single-page check) and, on failure, runs an auto-refit loop
# that deletes the "invalid" file. Since the converter is already mocked, we
# also stub the validator so these placeholder files are treated as valid
# single-page KP PDFs; the refit path itself is covered by dedicated tests.
_VALID_KP_PDF_VALIDATION = {"ok": True, "reason": "", "message": "", "page_count": 1}


class PdfPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path("tmp_test_pdf_pipeline")
        self.tmp_dir.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)

    def _write_minimal_docx(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/></w:body></w:document>',
            )

    def _write_real_pdf(self, path: Path, pages: int = 1) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=595, height=842)
        with path.open("wb") as handle:
            writer.write(handle)

    def test_pdf_template_cmap_parser_does_not_invent_ranges_from_bfchar(self) -> None:
        cmap = b"""
        begincmap
        3 beginbfchar
        <0244> <041A>
        <0245> <041B>
        <0249> <041F>
        endbfchar
        endcmap
        """

        mapping = pdf_template_renderer._parse_to_unicode_cmap(cmap)

        self.assertEqual(mapping["\u041a"], "0244")
        self.assertEqual(mapping["\u041b"], "0245")
        self.assertEqual(mapping["\u041f"], "0249")
        self.assertNotIn("\u0444", mapping)

    def test_pdf_overlay_wrapped_text_does_not_draw_yellow_highlight(self) -> None:
        text = "hello world"
        chars = sorted(set(text))
        font = pdf_template_renderer.PdfTextFont(
            resource_name="/F1",
            cmap={char: f"{ord(char):02X}" for char in chars},
            widths={ord(char): 500 for char in chars},
            default_width=500,
            base_font="Test-Regular",
            subtype="/TrueType",
        )

        commands = pdf_template_renderer._draw_wrapped_text(
            [font],
            text,
            x=10,
            y=20,
            max_width=200,
            font_size=11,
            line_height=12,
            bold=False,
        )

        self.assertNotIn("1 1 0 rg", commands)
        self.assertFalse(any(" re f" in command for command in commands))

    def test_convert_docx_batch_reuses_existing_valid_pdf(self) -> None:
        docx_path = self.tmp_dir / "document.docx"
        output_dir = self.tmp_dir / "pdf"
        output_dir.mkdir()
        docx_path.write_bytes(b"fake-docx")
        existing_pdf = output_dir / "document.pdf"
        existing_pdf.write_bytes(b"%PDF fake")
        progress_calls = 0

        def progress() -> None:
            nonlocal progress_calls
            progress_calls += 1

        with patch.object(pdf_converter, "_run_backend") as run_backend:
            result = pdf_converter.convert_docx_batch([docx_path], output_dir, progress_callback=progress)

        run_backend.assert_not_called()
        self.assertEqual(result[docx_path], existing_pdf)
        self.assertEqual(progress_calls, 1)

    def test_gotenberg_backend_is_available(self) -> None:
        docx_path = self.tmp_dir / "document.docx"
        pdf_path = self.tmp_dir / "pdf" / "document.pdf"
        docx_path.write_bytes(b"fake-docx")

        timings = []

        with patch.object(pdf_converter, "_convert_with_gotenberg", return_value={docx_path: pdf_path}) as convert:
            result = pdf_converter._run_backend(
                "gotenberg",
                [docx_path],
                pdf_path.parent,
                chunk_size=10,
                worker_count=2,
                profiles_root=None,
                timing_callback=timings.append,
            )

        convert.assert_called_once()
        self.assertEqual(result[docx_path], pdf_path)
        self.assertEqual(timings[0]["backend"], "gotenberg")
        self.assertEqual(timings[0]["total"], 1)
        self.assertEqual(timings[0]["success_count"], 1)
        self.assertEqual(timings[0]["failed_count"], 0)
        self.assertIn("seconds", timings[0])

    def test_gotenberg_without_urls_returns_missing_pdf(self) -> None:
        docx_path = self.tmp_dir / "document.docx"
        output_dir = self.tmp_dir / "pdf"
        output_dir.mkdir()
        docx_path.write_bytes(b"fake-docx")

        with patch.object(pdf_converter, "GOTENBERG_BASE_URLS", ()):
            result = pdf_converter._convert_with_gotenberg([docx_path], output_dir, worker_count=1)

        self.assertIsNone(result[docx_path])

    def test_gotenberg_retries_next_url_after_failed_endpoint(self) -> None:
        docx_path = self.tmp_dir / "document.docx"
        output_dir = self.tmp_dir / "pdf"
        output_dir.mkdir()
        docx_path.write_bytes(b"fake-docx")
        calls = []

        class FakeResponse:
            def __init__(self, status_code: int, content: bytes) -> None:
                self.status_code = status_code
                self.content = content
                self.headers = {"content-type": "application/pdf"}

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise RuntimeError("bad endpoint")

        class FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def post(self, endpoint, files):
                calls.append(endpoint)
                if "bad" in endpoint:
                    return FakeResponse(500, b"")
                return FakeResponse(200, b"%PDF ok")

        with (
            patch.object(pdf_converter, "GOTENBERG_BASE_URLS", ("http://bad", "http://good")),
            patch.object(pdf_converter.httpx, "Client", FakeClient),
        ):
            source, pdf_path = pdf_converter._convert_gotenberg_single((0, docx_path, output_dir))

        self.assertEqual(source, docx_path)
        self.assertEqual(pdf_path, output_dir / "document.pdf")
        self.assertEqual(calls, [
            "http://bad/forms/libreoffice/convert",
            "http://good/forms/libreoffice/convert",
        ])
        self.assertEqual((output_dir / "document.pdf").read_bytes(), b"%PDF ok")

    def test_gotenberg_health_filters_unhealthy_endpoints(self) -> None:
        calls = []

        class FakeHealthResponse:
            def __init__(self, payload) -> None:
                self._payload = payload

            def raise_for_status(self) -> None:
                return None

            def json(self):
                return self._payload

        def fake_get(endpoint, **kwargs):
            calls.append(endpoint)
            if "bad" in endpoint:
                raise RuntimeError("down")
            return FakeHealthResponse({"status": "up", "details": {"libreoffice": {"status": "up"}}})

        with (
            patch.object(pdf_converter, "GOTENBERG_BASE_URLS", ("http://bad", "http://good")),
            patch.object(pdf_converter.httpx, "get", side_effect=fake_get),
        ):
            urls = pdf_converter._healthy_gotenberg_base_urls()

        self.assertEqual(urls, ("http://good",))
        self.assertEqual(calls, ["http://bad/health", "http://good/health"])

    def test_gotenberg_failed_conversion_does_not_report_progress(self) -> None:
        docx_path = self.tmp_dir / "document.docx"
        output_dir = self.tmp_dir / "pdf"
        output_dir.mkdir()
        docx_path.write_bytes(b"fake-docx")
        progress_calls = 0

        class FakeResponse:
            content = b""
            headers = {"content-type": "text/plain"}

            def raise_for_status(self) -> None:
                raise RuntimeError("conversion failed")

        class FakeClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

            def post(self, endpoint, files):
                return FakeResponse()

        def progress() -> None:
            nonlocal progress_calls
            progress_calls += 1

        with (
            patch.object(pdf_converter, "_healthy_gotenberg_base_urls", return_value=("http://bad",)),
            patch.object(pdf_converter.httpx, "Client", FakeClient),
        ):
            result = pdf_converter._convert_with_gotenberg([docx_path], output_dir, worker_count=1, progress_callback=progress)

        self.assertIsNone(result[docx_path])
        self.assertEqual(progress_calls, 0)

    def test_finalize_generated_files_recovers_missing_pdf_from_final_docx(self) -> None:
        final_dir = self.tmp_dir / "output" / "1_Test"
        batch_pdf_dir = self.tmp_dir / "batch_pdf"
        final_dir.mkdir(parents=True)
        final_docx = final_dir / "kp.docx"
        final_pdf = final_dir / "kp.pdf"
        self._write_minimal_docx(final_docx)
        results = [
            {
                "status": "ok",
                "result_index": 0,
                "generated_files": {
                    "kp": self.tmp_dir / "missing_staged.docx",
                    "kp_final_docx": final_docx,
                    "kp_final_pdf": final_pdf,
                },
            }
        ]

        def fake_convert(docx_paths, output_dir, **kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = output_dir / f"{docx_paths[0].stem}.pdf"
            pdf_path.write_bytes(b"%PDF recovered")
            return {docx_paths[0]: pdf_path}

        with (
            patch.object(generator_agent, "convert_docx_batch", side_effect=fake_convert),
            patch.object(generator_agent, "validate_kp_pdf", return_value=_VALID_KP_PDF_VALIDATION),
        ):
            generator_agent.finalize_generated_files(results, batch_pdf_dir=batch_pdf_dir, create_pdf=True)

        self.assertTrue(final_pdf.exists())
        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(results[0]["files"]["kp_final_pdf"], str(final_pdf))

    def test_finalize_generated_files_marks_missing_pdf_as_error(self) -> None:
        staged_docx = self.tmp_dir / "batch_docx" / "kp.docx"
        final_dir = self.tmp_dir / "output" / "1_Test"
        final_docx = final_dir / "kp.docx"
        final_pdf = final_dir / "kp.pdf"
        staged_docx.parent.mkdir(parents=True)
        self._write_minimal_docx(staged_docx)
        results = [
            {
                "status": "ok",
                "result_index": 0,
                "generated_files": {
                    "kp": staged_docx,
                    "kp_final_docx": final_docx,
                    "kp_final_pdf": final_pdf,
                },
            }
        ]

        with (
            patch.object(generator_agent, "convert_docx_batch", side_effect=lambda docx_paths, output_dir, **kwargs: {docx_paths[0]: None}),
        ):
            generator_agent.finalize_generated_files(results, batch_pdf_dir=self.tmp_dir / "batch_pdf", create_pdf=True)

        self.assertTrue(final_docx.exists())
        self.assertFalse(final_pdf.exists())
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("Не удалось создать PDF", results[0]["error"])

    def test_finalize_generated_files_uses_configured_pdf_chunk_size(self) -> None:
        staged_docx = self.tmp_dir / "batch_docx" / "kp.docx"
        final_dir = self.tmp_dir / "output" / "1_Test"
        final_docx = final_dir / "kp.docx"
        final_pdf = final_dir / "kp.pdf"
        staged_docx.parent.mkdir(parents=True)
        self._write_minimal_docx(staged_docx)
        results = [
            {
                "status": "ok",
                "result_index": 0,
                "generated_files": {
                    "kp": staged_docx,
                    "kp_final_docx": final_docx,
                    "kp_final_pdf": final_pdf,
                },
            }
        ]

        timings = []

        def fake_convert(docx_paths, output_dir, **kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            kwargs["timing_callback"](
                {
                    "backend": "fake",
                    "total": len(docx_paths),
                    "success_count": len(docx_paths),
                    "failed_count": 0,
                    "seconds": 0.123,
                    "workers": kwargs["worker_count"],
                    "chunk_size": kwargs["chunk_size"],
                }
            )
            pdf_path = output_dir / f"{docx_paths[0].stem}.pdf"
            pdf_path.write_bytes(b"%PDF recovered")
            return {docx_paths[0]: pdf_path}

        with (
            patch.object(generator_agent, "PDF_CHUNK_SIZE", 7),
            patch.object(generator_agent, "convert_docx_batch", side_effect=fake_convert) as convert_mock,
            patch.object(generator_agent, "validate_kp_pdf", return_value=_VALID_KP_PDF_VALIDATION),
        ):
            generator_agent.finalize_generated_files(
                results,
                batch_pdf_dir=self.tmp_dir / "batch_pdf",
                create_pdf=True,
                timing_callback=timings.append,
            )

        self.assertEqual(convert_mock.call_args.kwargs["chunk_size"], 7)
        self.assertEqual(timings[0]["backend"], "fake")
        self.assertEqual(timings[0]["seconds"], 0.123)


    def test_finalize_generated_files_skips_contract_pdf_when_kp_pdf_requested(self) -> None:
        staged_dir = self.tmp_dir / "batch_docx"
        final_dir = self.tmp_dir / "output" / "1_Test"
        staged_kp = staged_dir / "1_kp_Test.docx"
        staged_contract = staged_dir / "1_contract_Test.docx"
        final_kp_docx = final_dir / "КП_МНГП_Test.docx"
        final_kp_pdf = final_dir / "КП_МНГП_Test.pdf"
        final_contract_docx = final_dir / "Договор_МНГП_Test.docx"
        final_contract_pdf = final_dir / "Договор_МНГП_Test.pdf"
        self._write_minimal_docx(staged_kp)
        self._write_minimal_docx(staged_contract)
        results = [
            {
                "status": "ok",
                "result_index": 0,
                "generated_files": {
                    "kp": staged_kp,
                    "kp_final_docx": final_kp_docx,
                    "kp_final_pdf": final_kp_pdf,
                    "contract": staged_contract,
                    "contract_final_docx": final_contract_docx,
                    "contract_final_pdf": final_contract_pdf,
                },
            }
        ]
        converted_names = []

        def fake_convert(docx_paths, output_dir, **kwargs):
            converted_names.extend(path.name for path in docx_paths)
            output_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = output_dir / f"{docx_paths[0].stem}.pdf"
            pdf_path.write_bytes(b"%PDF kp")
            return {docx_paths[0]: pdf_path}

        with (
            patch.object(generator_agent, "convert_docx_batch", side_effect=fake_convert),
            patch.object(generator_agent, "validate_kp_pdf", return_value=_VALID_KP_PDF_VALIDATION),
        ):
            generator_agent.finalize_generated_files(results, batch_pdf_dir=self.tmp_dir / "batch_pdf", create_pdf=True)

        self.assertTrue(final_kp_docx.exists())
        self.assertTrue(final_kp_pdf.exists())
        self.assertTrue(final_contract_docx.exists())
        self.assertFalse(final_contract_pdf.exists())
        self.assertEqual(len(converted_names), 1)
        self.assertIn("kp", converted_names[0].lower())
        self.assertIn("kp_final_pdf", results[0]["files"])
        self.assertNotIn("contract_final_pdf", results[0]["files"])
        self.assertEqual(results[0]["status"], "ok")

    def test_finalize_generated_files_moves_direct_pdf_template_output(self) -> None:
        staged_pdf = self.tmp_dir / "batch_docx" / "1_kp_Test.pdf"
        final_pdf = self.tmp_dir / "output" / "1_Test" / "КП_Случайный_лес_Test.pdf"
        staged_pdf.parent.mkdir(parents=True)
        staged_pdf.write_bytes(b"%PDF direct")
        results = [
            {
                "status": "ok",
                "result_index": 0,
                "generated_files": {
                    "kp_pdf": staged_pdf,
                    "kp_final_pdf": final_pdf,
                },
            }
        ]
        progress_calls = 0

        def progress() -> None:
            nonlocal progress_calls
            progress_calls += 1

        with (
            patch.object(generator_agent, "convert_docx_batch") as convert_mock,
            patch.object(generator_agent, "validate_kp_pdf", return_value=_VALID_KP_PDF_VALIDATION),
        ):
            generator_agent.finalize_generated_files(
                results,
                batch_pdf_dir=self.tmp_dir / "batch_pdf",
                create_pdf=True,
                progress_callback=progress,
            )

        convert_mock.assert_not_called()
        self.assertFalse(staged_pdf.exists())
        self.assertTrue(final_pdf.exists())
        self.assertEqual(results[0]["files"]["kp_final_pdf"], str(final_pdf))
        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(progress_calls, 1)

    def test_finalize_output_pdfs_for_job_converts_only_kp_docx(self) -> None:
        output_dir = self.tmp_dir / "output"
        batch_pdf_dir = self.tmp_dir / "batch_pdf"
        folder = output_dir / "1_Test"
        kp_docx = folder / "КП_МНГП_Test.docx"
        contract_docx = folder / "Договор_МНГП_Test.docx"
        self._write_minimal_docx(kp_docx)
        self._write_minimal_docx(contract_docx)
        job_paths = SimpleNamespace(
            output_dir=output_dir,
            batch_pdf_dir=batch_pdf_dir,
            templates_dir=self.tmp_dir / "templates",
            uses_legacy_layout=False,
        )
        prepared_sources = []

        def fake_prepare(source_docx, target_docx, **kwargs):
            prepared_sources.append(Path(source_docx).name)
            shutil.copy2(source_docx, target_docx)
            return object()

        def fake_convert(docx_paths, output_dir, **kwargs):
            self.assertEqual(len(docx_paths), 1)
            output_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = output_dir / f"{docx_paths[0].stem}.pdf"
            pdf_path.write_bytes(b"%PDF kp")
            kwargs["progress_callback"]()
            return {docx_paths[0]: pdf_path}

        saved_states = []
        with (
            patch.object(generator_agent, "resolve_job_paths", return_value=job_paths),
            patch.object(generator_agent, "_load_generator_state", return_value={"status": "running", "document_mode": "both"}),
            patch.object(generator_agent, "_save_generator_state", side_effect=lambda state, job_id=None: saved_states.append(dict(state))),
            patch.object(generator_agent, "prepare_docx_for_pdf_export", side_effect=fake_prepare),
            patch.object(generator_agent, "apply_pdf_safe_postprocess", return_value=None),
            patch.object(generator_agent, "convert_docx_batch", side_effect=fake_convert),
            patch.object(generator_agent, "validate_kp_pdf", return_value=_VALID_KP_PDF_VALIDATION),
        ):
            state = generator_agent.finalize_output_pdfs_for_job("job-test")

        self.assertEqual(prepared_sources, [kp_docx.name])
        self.assertTrue(kp_docx.with_suffix(".pdf").exists())
        self.assertFalse(contract_docx.with_suffix(".pdf").exists())
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["pdf_total"], 1)
        self.assertEqual(state["pdf_processed"], 1)

    def test_try_refit_returns_none_for_non_kp_file_kind(self) -> None:
        source_docx = self.tmp_dir / "contract.docx"
        self._write_minimal_docx(source_docx)

        result = generator_agent._try_refit_one_page_kp_pdf(
            source_docx=source_docx,
            pdf_output_dir=self.tmp_dir / "pdf",
            work_docx_dir=self.tmp_dir / "fit",
            template_docx=None,
            file_kind="contract",
        )

        self.assertIsNone(result)

    def test_try_refit_returns_none_when_source_missing(self) -> None:
        result = generator_agent._try_refit_one_page_kp_pdf(
            source_docx=self.tmp_dir / "missing.docx",
            pdf_output_dir=self.tmp_dir / "pdf",
            work_docx_dir=self.tmp_dir / "fit",
            template_docx=None,
            file_kind="kp",
        )

        self.assertIsNone(result)

    def test_try_refit_succeeds_on_later_font_size(self) -> None:
        source_docx = self.tmp_dir / "kp.docx"
        self._write_minimal_docx(source_docx)
        pdf_output_dir = self.tmp_dir / "pdf"
        work_docx_dir = self.tmp_dir / "fit"

        def fake_prepare(src, target, **kwargs):
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_bytes(b"docx")
            return object()

        # Fail (2 pages) until the font shrinks to 15 half-points, then fit on 1 page.
        def fake_convert(docx_paths, output_dir, **kwargs):
            fit_docx = docx_paths[0]
            half_points = int(fit_docx.stem.rsplit("_", 1)[-1])
            pdf_path = Path(output_dir) / f"{fit_docx.stem}.pdf"
            self._write_real_pdf(pdf_path, pages=1 if half_points <= 15 else 2)
            return {fit_docx: pdf_path}

        with (
            patch.object(generator_agent, "prepare_docx_for_pdf_export", side_effect=fake_prepare),
            patch.object(generator_agent, "apply_pdf_safe_postprocess", return_value=None),
            patch.object(generator_agent, "convert_docx_batch", side_effect=fake_convert),
        ):
            result = generator_agent._try_refit_one_page_kp_pdf(
                source_docx=source_docx,
                pdf_output_dir=pdf_output_dir,
                work_docx_dir=work_docx_dir,
                template_docx=None,
                file_kind="kp",
            )

        self.assertIsNotNone(result)
        self.assertTrue(result.exists())
        self.assertTrue(generator_agent.validate_kp_pdf(result)["ok"])
        self.assertIn("_fit_15", result.stem)

    def test_try_refit_returns_none_when_all_attempts_fail(self) -> None:
        source_docx = self.tmp_dir / "kp.docx"
        self._write_minimal_docx(source_docx)

        def fake_prepare(src, target, **kwargs):
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_bytes(b"docx")
            return object()

        def fake_convert(docx_paths, output_dir, **kwargs):
            fit_docx = docx_paths[0]
            pdf_path = Path(output_dir) / f"{fit_docx.stem}.pdf"
            self._write_real_pdf(pdf_path, pages=2)
            return {fit_docx: pdf_path}

        with (
            patch.object(generator_agent, "prepare_docx_for_pdf_export", side_effect=fake_prepare),
            patch.object(generator_agent, "apply_pdf_safe_postprocess", return_value=None),
            patch.object(generator_agent, "convert_docx_batch", side_effect=fake_convert),
        ):
            result = generator_agent._try_refit_one_page_kp_pdf(
                source_docx=source_docx,
                pdf_output_dir=self.tmp_dir / "pdf",
                work_docx_dir=self.tmp_dir / "fit",
                template_docx=None,
                file_kind="kp",
            )

        self.assertIsNone(result)

    def test_try_refit_clamps_chunk_and_worker_counts(self) -> None:
        source_docx = self.tmp_dir / "kp.docx"
        self._write_minimal_docx(source_docx)
        captured = {}

        def fake_prepare(src, target, **kwargs):
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_bytes(b"docx")
            return object()

        def fake_convert(docx_paths, output_dir, **kwargs):
            captured["chunk_size"] = kwargs.get("chunk_size")
            captured["worker_count"] = kwargs.get("worker_count")
            fit_docx = docx_paths[0]
            pdf_path = Path(output_dir) / f"{fit_docx.stem}.pdf"
            self._write_real_pdf(pdf_path, pages=1)
            return {fit_docx: pdf_path}

        with (
            patch.object(generator_agent, "prepare_docx_for_pdf_export", side_effect=fake_prepare),
            patch.object(generator_agent, "apply_pdf_safe_postprocess", return_value=None),
            patch.object(generator_agent, "convert_docx_batch", side_effect=fake_convert),
        ):
            generator_agent._try_refit_one_page_kp_pdf(
                source_docx=source_docx,
                pdf_output_dir=self.tmp_dir / "pdf",
                work_docx_dir=self.tmp_dir / "fit",
                template_docx=None,
                file_kind="kp",
                chunk_size=0,
                worker_count=5,
            )

        self.assertEqual(captured["chunk_size"], 1)
        self.assertEqual(captured["worker_count"], 1)

    def test_finalize_output_pdfs_invokes_refit_on_page_count_failure(self) -> None:
        output_dir = self.tmp_dir / "output"
        batch_pdf_dir = self.tmp_dir / "batch_pdf"
        folder = output_dir / "1_Test"
        kp_docx = folder / "КП_МНГП_Test.docx"
        self._write_minimal_docx(kp_docx)
        job_paths = SimpleNamespace(
            output_dir=output_dir,
            batch_pdf_dir=batch_pdf_dir,
            templates_dir=self.tmp_dir / "templates",
            uses_legacy_layout=False,
        )

        def fake_prepare(source_docx, target_docx, **kwargs):
            shutil.copy2(source_docx, target_docx)
            return object()

        # Main conversion yields a 2-page (invalid) KP PDF, triggering refit.
        def fake_convert(docx_paths, output_dir, **kwargs):
            pdf_path = Path(output_dir) / f"{docx_paths[0].stem}.pdf"
            self._write_real_pdf(pdf_path, pages=2)
            cb = kwargs.get("progress_callback")
            if cb:
                cb()
            return {docx_paths[0]: pdf_path}

        def fake_refit(*, source_docx, pdf_output_dir, **kwargs):
            fitted = Path(pdf_output_dir) / "refit.pdf"
            self._write_real_pdf(fitted, pages=1)
            return fitted

        with (
            patch.object(generator_agent, "resolve_job_paths", return_value=job_paths),
            patch.object(generator_agent, "_load_generator_state", return_value={"status": "running", "document_mode": "both"}),
            patch.object(generator_agent, "_save_generator_state", return_value=None),
            patch.object(generator_agent, "prepare_docx_for_pdf_export", side_effect=fake_prepare),
            patch.object(generator_agent, "apply_pdf_safe_postprocess", return_value=None),
            patch.object(generator_agent, "convert_docx_batch", side_effect=fake_convert),
            patch.object(generator_agent, "_try_refit_one_page_kp_pdf", side_effect=fake_refit) as refit_mock,
        ):
            state = generator_agent.finalize_output_pdfs_for_job("job-test")

        refit_mock.assert_called_once()
        final_pdf = kp_docx.with_suffix(".pdf")
        self.assertTrue(final_pdf.exists())
        self.assertEqual(count_pdf_pages(final_pdf), 1)
        self.assertEqual(state["status"], "completed")


if __name__ == "__main__":
    unittest.main()

