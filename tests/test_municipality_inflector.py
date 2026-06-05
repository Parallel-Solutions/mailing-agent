import unittest

from src.generator.inflection.inflect import inflect_mun_name_genitive
from src.generator.inflection.inflect import inflect_mun_name_prepositional
from src.generator.inflection.inflect import inflect_mun_r_name_genitive
from src.generator.inflection.inflect import inflect_sub_rf_genitive


class MunicipalityInflectorTests(unittest.TestCase):
    def test_city_component_is_preserved_inside_official_urban_settlement_name(self) -> None:
        self.assertEqual(
            inflect_mun_name_genitive("Городское поселение город Белебей").value,
            "городского поселения город Белебей",
        )
        self.assertEqual(
            inflect_mun_name_genitive("Городское поселение город Баймак").value,
            "городского поселения город Баймак",
        )

    def test_risky_localities_are_preserved(self) -> None:
        self.assertEqual(
            inflect_mun_name_genitive("Городское поселение Энем").value,
            "городского поселения Энем",
        )
        self.assertEqual(
            inflect_mun_name_genitive("Сельское поселение село Болхуны").value,
            "сельского поселения села Болхуны",
        )
        self.assertEqual(
            inflect_mun_name_genitive("Городское поселение город Учалы").value,
            "городского поселения город Учалы",
        )
        self.assertEqual(
            inflect_mun_name_genitive("Городское поселение Благовещенск").value,
            "городского поселения Благовещенск",
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

    def test_hyphenated_selsovet_tail_is_inflected(self) -> None:
        self.assertEqual(
            inflect_mun_name_genitive("Сельское поселение Кшлау-Елгинский сельсовет").value,
            "сельского поселения Кшлау-Елгинского сельсовета",
        )
        self.assertEqual(
            inflect_mun_name_prepositional("Сельское поселение Кшлау-Елгинский сельсовет").value,
            "сельском поселении Кшлау-Елгинском сельсовете",
        )

    def test_hyphenated_proper_adjectives_preserve_name_components(self) -> None:
        self.assertEqual(
            inflect_mun_name_genitive("Мухор-Тархатинское сельское поселение").value,
            "Мухор-Тархатинского сельского поселения",
        )
        self.assertEqual(
            inflect_mun_name_prepositional("Усть-Мунинское сельское поселение").value,
            "Усть-Мунинском сельском поселении",
        )
        self.assertEqual(
            inflect_mun_r_name_genitive("Кош-Агачский муниципальный район").value,
            "Кош-Агачского муниципального района",
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
            inflect_mun_r_name_genitive("Усть-Кутский район").value,
            "Усть-Кутского района",
        )
        self.assertEqual(
            inflect_mun_r_name_genitive("Муниципальный район Баймакский район").value,
            "муниципального района Баймакский район",
        )
        self.assertEqual(
            inflect_mun_r_name_genitive("Городской округ города Махачкалы").value,
            "городского округа города Махачкалы",
        )
        self.assertEqual(
            inflect_sub_rf_genitive("Республика Башкортостан").value,
            "Республики Башкортостан",
        )


if __name__ == "__main__":
    unittest.main()
