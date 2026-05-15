from pathlib import Path
import unittest

from src.generator.philologist.night_training import _safe_folder_part, _training_relative_path


class PhilologistNightTrainingTests(unittest.TestCase):
    def test_flat_batch_docx_is_grouped_by_row_id_for_philologist(self) -> None:
        source_dir = Path("job") / "_batch_docx"
        docx_path = source_dir / "878_contract_Сельское поселение Чувалкиповский сельсовет.docx"

        relative = _training_relative_path(source_dir=source_dir, docx_path=docx_path)

        self.assertEqual(
            relative,
            Path("878_Сельское поселение Чувалкиповский сельсовет") / docx_path.name,
        )

    def test_nested_output_docx_keeps_relative_path(self) -> None:
        source_dir = Path("job") / "output"
        docx_path = source_dir / "878_Сельское поселение Чувалкиповский сельсовет" / "Договор.docx"

        relative = _training_relative_path(source_dir=source_dir, docx_path=docx_path)

        self.assertEqual(relative, Path("878_Сельское поселение Чувалкиповский сельсовет") / "Договор.docx")

    def test_folder_part_removes_windows_forbidden_chars(self) -> None:
        self.assertEqual(_safe_folder_part('МО:"Тест"/Путь?.docx'), "МО Тест Путь")


if __name__ == "__main__":
    unittest.main()
