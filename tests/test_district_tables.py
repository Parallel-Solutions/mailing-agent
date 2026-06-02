import shutil
import unittest
from pathlib import Path

from openpyxl import Workbook

from src.generator.generation.document_builder import (
    build_contract_filename,
    build_contract_replacements,
    build_kp_replacements,
)
from src.generator.generation.excel_io import load_rows
from src.generator.generation.transforms import build_document_context, build_output_folder_name
from src.generator.verification.municipality_name_verifier import verify_municipality_names_in_workbook


class DistrictTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path("tmp_test_district_tables")
        self.tmp_dir.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir)

    def _district_xlsx(self) -> Path:
        path = self.tmp_dir / "district.xlsx"
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.cell(row=1, column=1).value = "N"
        headers = [
            "ID",
            "SUB_RF",
            "MUN_R_NAME",
            "MUN_NAME",
            "ADM_NAME",
            "ADRES",
            "HEAD_FIO",
            "POPULATION",
            "EMAIL_OSN",
            "REQUISITES_OKTNO",
            "STATUS",
        ]
        for column_index, header in enumerate(headers, start=1):
            worksheet.cell(row=2, column=column_index).value = header
        values = [
            1,
            "Краснодарский край",
            "Новокубанский район",
            None,
            "АДМИНИСТРАЦИЯ МУНИЦИПАЛЬНОГО ОБРАЗОВАНИЯ НОВОКУБАНСКИЙ РАЙОН",
            "352240, Краснодарский край, Новокубанский район",
            "Гомодин Александр Владимирович",
            81734,
            "omsiknk@mail.ru",
            "3634101001",
            "ОК",
        ]
        for column_index, value in enumerate(values, start=1):
            worksheet.cell(row=3, column=column_index).value = value
        source_values = ["Источники:", 'Файл "БАЗА МО 2025 год"', 'Файл "БАЗА МО 2025 год"']
        for column_index, value in enumerate(source_values, start=1):
            worksheet.cell(row=4, column=column_index).value = value
        workbook.save(path)
        workbook.close()
        return path

    def test_load_rows_skips_source_row_and_keeps_oktmo_alias(self) -> None:
        _, _, rows = load_rows(self._district_xlsx())

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["MUN_R_NAME"], "Новокубанский район")
        self.assertEqual(rows[0]["REQUISITES_OKTMO"], "3634101001")

    def test_district_table_verification_is_accepted_without_mun_name(self) -> None:
        result = verify_municipality_names_in_workbook(self._district_xlsx(), use_oktmo=True)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["table_mode"], "district")
        self.assertEqual(result["total_rows"], 1)
        self.assertEqual(result["kept_rows"], 1)

    def test_district_context_uses_mun_r_name_as_main_entity(self) -> None:
        _, _, rows = load_rows(self._district_xlsx())
        context = build_document_context(rows[0], outgoing_number=101)
        replacements = dict(build_kp_replacements(context))
        contract_replacements = dict(build_contract_replacements(context))

        self.assertEqual(context["DOCUMENT_ENTITY_TYPE"], "district")
        self.assertEqual(context["MUN_NAME"], "Новокубанский район")
        self.assertEqual(context["MUN_R_NAME"], "Новокубанский район")
        self.assertIn("Новокубанского", context["MUN_R_NAME_1"])
        self.assertIn("Новокубанского", context["WORK_SCOPE_FRAGMENT"])
        self.assertEqual(replacements["MUN_R_NAME"], "Новокубанский район")
        self.assertEqual(replacements["SUB_RF"], "Краснодарский край")
        self.assertEqual(contract_replacements["Глава MUN_NAME"], "Глава Новокубанского района")
        self.assertIn("Новокубанский", build_contract_filename(rows[0]))
        self.assertIn("Новокубанский", build_output_folder_name(rows[0]))

    def test_municipality_contract_replacements_inflect_district_scope(self) -> None:
        row = {
            "ID": 1,
            "SUB_RF": "Иркутская область",
            "MUN_R_NAME": "Усть-Кутский район",
            "MUN_NAME": "Нийское сельское поселение",
            "ADM_NAME": 'АДМИНИСТРАЦИЯ НИЙСКОГО СЕЛЬСКОГО ПОСЕЛЕНИЯ УСТЬ-КУТСКОГО МУНИЦИПАЛЬНОГО РАЙОНА ИРКУТСКОЙ ОБЛАСТИ',
            "HEAD_FIO": "Дудник Евгения Викторовна",
        }
        context = build_document_context(row, outgoing_number=101)
        replacements = dict(build_contract_replacements(context))

        self.assertEqual(context["MUN_R_NAME_1"], "Усть-Кутского района")
        self.assertEqual(context["SUB_RF_1"], "Иркутской области")
        self.assertEqual(replacements["MUN_R_NAME SUB_RF"], "Усть-Кутского муниципального района Иркутской области")
        self.assertEqual(replacements["Глава MUN_NAME"], "Глава Нийского сельского поселения")


if __name__ == "__main__":
    unittest.main()
