from __future__ import annotations

import unittest

from src.campaigns.delivery_filename_service import (
    infer_static_delivery_filename,
    infer_template_display_name,
)


class DeliveryFilenameServiceTests(unittest.TestCase):
    def test_kp_and_stp_from_content(self) -> None:
        text = (
            "Коммерческое предложение\n"
            "на разработку схемы территориального планирования муниципального образования"
        )
        filename = infer_static_delivery_filename(text=text, upload_filename="document.docx")
        self.assertEqual(filename, "КП_СТП.pdf")

    def test_cleans_copy_suffixes_from_upload_stem(self) -> None:
        filename = infer_static_delivery_filename(
            text="",
            upload_filename="КП_СТП_районы (1) (1).docx",
        )
        self.assertEqual(filename, "КП_СТП_районы.pdf")

    def test_contract_from_content(self) -> None:
        text = "Договор оказания услуг по разработке МНГП"
        filename = infer_static_delivery_filename(text=text, upload_filename="template.docx")
        self.assertEqual(filename, "Договор_МНГП.pdf")

    def test_fallback_to_cleaned_stem(self) -> None:
        filename = infer_static_delivery_filename(text="", upload_filename="custom-offer (2).pdf")
        self.assertEqual(filename, "custom-offer.pdf")

    def test_infer_template_display_name(self) -> None:
        self.assertEqual(infer_template_display_name("КП_СТП_районы.pdf"), "КП СТП районы")

    def test_normalize_delivery_filename(self) -> None:
        from src.campaigns.delivery_filename_service import normalize_delivery_filename

        self.assertEqual(normalize_delivery_filename("КП_СТП"), "КП_СТП.pdf")
        self.assertEqual(normalize_delivery_filename("КП_СТП.pdf"), "КП_СТП.pdf")
        with self.assertRaises(ValueError):
            normalize_delivery_filename("   ")


if __name__ == "__main__":
    unittest.main()
