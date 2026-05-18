import tempfile
import unittest
from pathlib import Path

from src.generator.verification.oktmo_municipality_lookup import OktmoMunicipalityLookup, parse_oktmo_csv


class OktmoMunicipalityLookupTests(unittest.TestCase):
    def test_confirms_city_settlement_from_oktmo_rows(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
            path = Path(tmp_dir) / "oktmo.csv"
            path.write_text(
                "\n".join(
                    [
                        '"80";"000";"000";"000";"8";"1";"Муниципальные образования Республики Башкортостан";;;"000";"0";14.06.2013;01.01.2014',
                        '"80";"651";"000";"000";"7";"1";"Туймазинский муниципальный район";"г Туймазы";;"000";"0";14.06.2013;01.01.2014',
                        '"80";"651";"101";"000";"0";"1";"город Туймазы";"г Туймазы";;"000";"0";14.06.2013;01.01.2014',
                    ]
                ),
                encoding="utf-8",
            )

            entries = parse_oktmo_csv(path)
            self.assertEqual(entries[0].official_name, "Городское поселение город Туймазы")

            result = OktmoMunicipalityLookup(csv_path=path).confirm(
                {
                    "SUB_RF": "Республика Башкортостан",
                    "MUN_R_NAME": "Туймазинский район",
                },
                "Городское поселение город Туймазы",
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Городское поселение город Туймазы")
        self.assertEqual(result.oktmo_code, "806511010000")

    def test_confirms_possovet_as_urban_settlement(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
            path = Path(tmp_dir) / "oktmo.csv"
            path.write_text(
                "\n".join(
                    [
                        '"80";"000";"000";"000";"8";"1";"Муниципальные образования Республики Башкортостан";;;"000";"0";14.06.2013;01.01.2014',
                        '"80";"609";"000";"000";"5";"1";"Белебеевский муниципальный район";"г Белебей";;"000";"0";14.06.2013;01.01.2014',
                        '"80";"609";"165";"000";"5";"1";"Приютовский поссовет";"рп Приютово";;"000";"0";14.06.2013;01.01.2014',
                    ]
                ),
                encoding="utf-8",
            )

            result = OktmoMunicipalityLookup(csv_path=path).confirm(
                {
                    "SUB_RF": "Республика Башкортостан",
                    "MUN_R_NAME": "Белебеевский район",
                },
                "Городское поселение Приютовский Поссовет",
            )

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Городское поселение Приютовский Поссовет")


if __name__ == "__main__":
    unittest.main()
