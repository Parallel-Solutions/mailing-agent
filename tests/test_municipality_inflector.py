import unittest

from src.generator.inflect import inflect_mun_name_genitive
from src.generator.inflect import inflect_mun_name_prepositional
from src.generator.inflect import inflect_mun_r_name_genitive
from src.generator.inflect import inflect_sub_rf_genitive


class MunicipalityInflectorTests(unittest.TestCase):
    def test_city_component_is_inflected(self) -> None:
        self.assertEqual(
            inflect_mun_name_genitive("Городское поселение город Белебей").value,
            "Городского поселения города Белебея",
        )
        self.assertEqual(
            inflect_mun_name_genitive("Городское поселение город Баймак").value,
            "Городского поселения города Баймака",
        )

    def test_risky_localities_are_preserved(self) -> None:
        self.assertEqual(
            inflect_mun_name_genitive("Городское поселение Энем").value,
            "Городского поселения Энем",
        )
        self.assertEqual(
            inflect_mun_name_genitive("Сельское поселение село Болхуны").value,
            "Сельского поселения села Болхуны",
        )
        self.assertEqual(
            inflect_mun_name_genitive("Городское поселение город Учалы").value,
            "Городского поселения города Учалы",
        )

    def test_suffix_municipality_names_are_inflected(self) -> None:
        self.assertEqual(
            inflect_mun_name_genitive("Яблоновское городское поселение").value,
            "Яблоновского городского поселения",
        )
        self.assertEqual(
            inflect_mun_name_prepositional("Энемское городское поселение").value,
            "Энемском городском поселении",
        )

    def test_district_and_subject_are_preserved_as_components(self) -> None:
        self.assertEqual(
            inflect_mun_r_name_genitive("Тахтамукайский муниципальный район").value,
            "Тахтамукайского муниципального района",
        )
        self.assertEqual(
            inflect_mun_r_name_genitive("Белебеевский район").value,
            "Белебеевского района",
        )
        self.assertEqual(
            inflect_sub_rf_genitive("Республика Башкортостан").value,
            "Республики Башкортостан",
        )


if __name__ == "__main__":
    unittest.main()
