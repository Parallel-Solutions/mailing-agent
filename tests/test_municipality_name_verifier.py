import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from src.generator.verification.municipality_name_verifier import (
    BaseMunicipalityEntry,
    _official_site_match_from_search_result,
    verify_municipality_name,
    verify_municipality_names_in_workbook,
)


class MunicipalityNameVerifierTests(unittest.TestCase):
    def test_extracts_quoted_official_name_from_administration(self) -> None:
        result = verify_municipality_name(
            {
                "MUN_NAME": "Городское поселение Яблоновский",
                "ADM_NAME": 'Администрация муниципального образования "Яблоновское городское поселение"',
            }
        )

        self.assertEqual(result.status, "verified")
        self.assertEqual(result.confidence, "high")
        self.assertEqual(result.official_name, "Яблоновское городское поселение")
        self.assertTrue(result.should_replace)

    def test_restores_readable_case_for_uppercase_official_name(self) -> None:
        result = verify_municipality_name(
            {
                "MUN_NAME": "Городское поселение Энем",
                "ADM_NAME": 'АДМИНИСТРАЦИЯ МУНИЦИПАЛЬНОГО ОБРАЗОВАНИЯ "ЭНЕМСКОЕ ГОРОДСКОЕ ПОСЕЛЕНИЕ"',
            }
        )

        self.assertEqual(result.official_name, "Энемское городское поселение")

    def test_restores_readable_case_for_city_type_name(self) -> None:
        result = verify_municipality_name(
            {
                "MUN_NAME": "Городское поселение город Белебей",
                "ADM_NAME": 'АДМИНИСТРАЦИЯ МУНИЦИПАЛЬНОГО ОБРАЗОВАНИЯ "ГОРОДСКОЕ ПОСЕЛЕНИЕ ГОРОД БЕЛЕБЕЙ"',
            }
        )

        self.assertEqual(result.official_name, "Городское поселение город Белебей")

    def test_rebuilds_city_settlement_from_contextual_quoted_locality(self) -> None:
        result = verify_municipality_name(
            {
                "MUN_NAME": "Городское поселение Поселок Онохой",
                "ADM_NAME": (
                    'АДМИНИСТРАЦИЯ МУНИЦИПАЛЬНОГО ОБРАЗОВАНИЯ '
                    'ГОРОДСКОГО ПОСЕЛЕНИЯ "ПОСЕЛОК ОНОХОЙ"'
                ),
            }
        )

        self.assertEqual(result.status, "verified")
        self.assertEqual(result.confidence, "high")
        self.assertEqual(result.official_name, "Городское поселение поселок Онохой")

    def test_rebuilds_city_settlement_from_contextual_quoted_adjective(self) -> None:
        result = verify_municipality_name(
            {
                "MUN_NAME": "Городское поселение Бабушкинское",
                "ADM_NAME": (
                    'АДМИНИСТРАЦИЯ МУНИЦИПАЛЬНОГО ОБРАЗОВАНИЯ '
                    'ГОРОДСКОГО ПОСЕЛЕНИЯ "БАБУШКИНСКОЕ" КАБАНСКОГО РАЙОНА'
                ),
            }
        )

        self.assertEqual(result.status, "verified")
        self.assertEqual(result.confidence, "high")
        self.assertEqual(result.official_name, "Городское поселение Бабушкинское")

    def test_does_not_replace_settlement_with_quoted_district(self) -> None:
        result = verify_municipality_name(
            {
                "MUN_NAME": "Городское поселение Поселок Заиграево",
                "ADM_NAME": 'АДМИНИСТРАЦИЯ МУНИЦИПАЛЬНОГО ОБРАЗОВАНИЯ "ЗАИГРАЕВСКИЙ РАЙОН"',
            }
        )

        self.assertEqual(result.status, "kept")
        self.assertFalse(result.should_replace)
        self.assertEqual(result.official_name, "Городское поселение Поселок Заиграево")

    def test_uses_base_xlsx_entries_before_misleading_administration_name(self) -> None:
        result = verify_municipality_name(
            {
                "SUB_RF": "Республика Бурятия",
                "MUN_R_NAME": "Заиграевский муниципальный район",
                "MUN_NAME": "Городское поселение Поселок Заиграево",
                "ADM_NAME": 'АДМИНИСТРАЦИЯ МУНИЦИПАЛЬНОГО ОБРАЗОВАНИЯ "ЗАИГРАЕВСКИЙ РАЙОН"',
            },
            base_entries=[
                BaseMunicipalityEntry(
                    sub_rf="Республика Бурятия",
                    mun_r_name="Заиграевский муниципальный район",
                    mun_name="Городское поселение Поселок Заиграево",
                )
            ],
        )

        self.assertEqual(result.status, "verified")
        self.assertEqual(result.confidence, "high")
        self.assertEqual(result.source, "base.xlsx")
        self.assertEqual(result.official_name, "Городское поселение Поселок Заиграево")

    def test_confirms_rural_administration_matches_current_municipality(self) -> None:
        result = verify_municipality_name(
            {
                "MUN_NAME": "Дубровское сельское поселение",
                "ADM_NAME": "ДУБРОВСКАЯ СЕЛЬСКАЯ АДМИНИСТРАЦИЯ",
            }
        )

        self.assertEqual(result.status, "verified")
        self.assertEqual(result.confidence, "medium")
        self.assertEqual(result.official_name, "Дубровское сельское поселение")
        self.assertFalse(result.should_replace)

    def test_confirms_tskoe_tskaya_adjective_pair(self) -> None:
        result = verify_municipality_name(
            {
                "MUN_NAME": "Стекляннорадицкое сельское поселение",
                "ADM_NAME": "СТЕКЛЯННОРАДИЦКАЯ СЕЛЬСКАЯ АДМИНИСТРАЦИЯ",
            }
        )

        self.assertEqual(result.status, "verified")
        self.assertEqual(result.confidence, "medium")

    def test_confirms_genitive_rural_settlement_administration(self) -> None:
        result = verify_municipality_name(
            {
                "MUN_NAME": "Сельское поселение Вертячинское",
                "ADM_NAME": "АДМИНИСТРАЦИЯ ВЕРТЯЧИНСКОГО СЕЛЬСКОГО ПОСЕЛЕНИЯ",
            }
        )

        self.assertEqual(result.status, "verified")
        self.assertEqual(result.confidence, "medium")

    def test_confirms_selsovet_administration(self) -> None:
        result = verify_municipality_name(
            {
                "MUN_NAME": "Сельское поселение Кирюшкинский сельсовет",
                "ADM_NAME": "АДМИНИСТРАЦИЯ КИРЮШКИНСКОГО СЕЛЬСОВЕТА БУГУРУСЛАНСКОГО РАЙОНА",
            }
        )

        self.assertEqual(result.status, "verified")
        self.assertEqual(result.confidence, "medium")

    def test_confirms_municipal_obrazovanie_dash_administration(self) -> None:
        result = verify_municipality_name(
            {
                "MUN_NAME": "Задубровское сельское поселение",
                "ADM_NAME": "АДМИНИСТРАЦИЯ МУНИЦИПАЛЬНОГО ОБРАЗОВАНИЯ - ЗАДУБРОВСКОЕ СЕЛЬСКОЕ ПОСЕЛЕНИЕ",
            }
        )

        self.assertEqual(result.status, "verified")
        self.assertEqual(result.confidence, "medium")

    def test_confirms_municipal_obrazovanie_rural_settlement_noun(self) -> None:
        result = verify_municipality_name(
            {
                "MUN_NAME": "Сельское поселение Ваеги",
                "ADM_NAME": "АДМИНИСТРАЦИЯ МУНИЦИПАЛЬНОГО ОБРАЗОВАНИЯ СЕЛЬСКОГО ПОСЕЛЕНИЯ ВАЕГИ",
            }
        )

        self.assertEqual(result.status, "verified")
        self.assertEqual(result.confidence, "medium")

    def test_accepts_official_like_site_search_result(self) -> None:
        match = _official_site_match_from_search_result(
            {
                "title": "Администрация Дубровского сельского поселения",
                "url": "https://adm-dubrovskoe.ru/",
                "content": "Официальный сайт администрации Дубровского сельского поселения",
            },
            {
                "MUN_NAME": "Дубровское сельское поселение",
                "ADM_NAME": "ДУБРОВСКАЯ СЕЛЬСКАЯ АДМИНИСТРАЦИЯ",
            },
        )

        self.assertIsNotNone(match)

    def test_rejects_registry_search_result_as_official_site(self) -> None:
        match = _official_site_match_from_search_result(
            {
                "title": "Дубровская сельская администрация — Rusprofile",
                "url": "https://www.rusprofile.ru/id/123",
                "content": "Карточка организации",
            },
            {
                "MUN_NAME": "Дубровское сельское поселение",
                "ADM_NAME": "ДУБРОВСКАЯ СЕЛЬСКАЯ АДМИНИСТРАЦИЯ",
            },
        )

        self.assertIsNone(match)

    def test_updates_workbook_and_preserves_original_name(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
            path = Path(tmp_dir) / "data.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.cell(row=2, column=1).value = "ID"
            worksheet.cell(row=2, column=2).value = "MUN_NAME"
            worksheet.cell(row=2, column=3).value = "ADM_NAME"
            worksheet.cell(row=3, column=1).value = 1
            worksheet.cell(row=3, column=2).value = "Городское поселение Яблоновский"
            worksheet.cell(row=3, column=3).value = (
                'Администрация муниципального образования "Яблоновское городское поселение"'
            )
            workbook.save(path)
            workbook.close()

            stats = verify_municipality_names_in_workbook(path)

            self.assertEqual(stats["updated_rows"], 1)
            self.assertEqual(len(stats["replacements"]), 1)
            self.assertEqual(stats["replacements"][0]["from"], "Городское поселение Яблоновский")
            self.assertEqual(stats["replacements"][0]["to"], "Яблоновское городское поселение")
            updated = load_workbook(path)
            sheet = updated.active
            headers = {sheet.cell(row=2, column=i).value: i for i in range(1, sheet.max_column + 1)}
            self.assertEqual(sheet.cell(row=3, column=headers["MUN_NAME"]).value, "Яблоновское городское поселение")
            self.assertEqual(
                sheet.cell(row=3, column=headers["MUN_NAME_SOURCE_ORIGINAL"]).value,
                "Городское поселение Яблоновский",
            )
            self.assertEqual(sheet.cell(row=3, column=headers["MUN_NAME_VERIFICATION_STATUS"]).value, "verified")
            updated.close()


if __name__ == "__main__":
    unittest.main()
