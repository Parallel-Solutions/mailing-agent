import unittest

from src.generator.generation.template_visual_audit import (
    _document_mode_has_kp,
    extract_json_object,
    normalize_ai_patch_plan,
)


class TemplateVisualAuditTests(unittest.TestCase):
    def test_extract_json_object_from_markdown(self):
        payload = extract_json_object('```json\n{"ok": false, "issues": ["two pages"]}\n```')
        self.assertEqual(payload, {"ok": False, "issues": ["two pages"]})

    def test_patch_plan_allows_only_safe_patch_types(self):
        patches = normalize_ai_patch_plan([
            {"type": "move_stamp", "half_points": 18, "reason": "unsafe"},
            {"type": "shrink_body", "half_points": 9, "reason": "too long"},
            {"type": "compact_body", "half_points": 21, "reason": "spacing"},
        ])
        self.assertEqual([patch.patch_type for patch in patches], ["shrink_body", "compact_body"])
        self.assertEqual([patch.half_points for patch in patches], [14, 20])

    def test_document_mode_has_kp(self):
        self.assertTrue(_document_mode_has_kp("kp"))
        self.assertTrue(_document_mode_has_kp("both"))
        self.assertTrue(_document_mode_has_kp("kp,contract"))
        self.assertFalse(_document_mode_has_kp("contract"))


if __name__ == "__main__":
    unittest.main()
