from __future__ import annotations

import pytest

from src.campaigns.pdf_overlay_service import _normalize_official_recipient_name
from src.campaigns.substitution_engine import render_text
from src.generator.generation.document_builder import format_kp_recipient
from src.generator.generation.recipient_normalization import (
    extract_administration_entity_name,
    is_district_level_entity_name,
    normalize_administration_recipient,
)
from src.generator.generation.structured_kp import build_structured_kp_model
from src.generator.generation.transforms import build_document_context


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "Администрации муниципального образования "
            "«Администрация Любимского муниципального округа Ярославской области»",
            "Администрации Любимского муниципального округа Ярославской области",
        ),
        (
            'Администрация муниципального образования "Администрация Дятьковского района"',
            "Администрация Дятьковского района",
        ),
        (
            "Администрации муниципального образования "
            "«Администрация Яблоновского городского поселения»",
            "Администрации Яблоновского городского поселения",
        ),
        (
            "Администрации муниципального образования "
            "“Администрация Любимского муниципального округа”",
            "Администрации Любимского муниципального округа",
        ),
        (
            "Администрации муниципального образования Администрации Невского округа",
            "Администрации Невского округа",
        ),
    ],
)
def test_nested_administration_is_removed(source: str, expected: str) -> None:
    assert normalize_administration_recipient(source) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Любимский муниципальный округ", True),
        ("Одинцовский городской округ", True),
        ("Дятьковский муниципальный район", True),
        ("Яблоновское городское поселение", False),
        ("Нийское сельское поселение Усть-Кутского района", False),
        ("Красногорский сельсовет", False),
    ],
)
def test_district_level_classifier_covers_entity_types(value: str, expected: bool) -> None:
    assert is_district_level_entity_name(value) is expected


def test_nested_administration_entity_is_extracted_without_losing_region() -> None:
    source = (
        "Администрации муниципального образования "
        "«Администрация Любимского муниципального округа Ярославской области»"
    )

    assert (
        extract_administration_entity_name(source)
        == "Любимского муниципального округа Ярославской области"
    )


@pytest.mark.parametrize(
    ("mun_r_name", "mun_name", "adm_name", "expected_mun_name", "expected_recipient"),
    [
        (
            "Любимский муниципальный округ",
            "Администрация Любимского муниципального округа",
            (
                "Администрация муниципального образования "
                "«Администрация Любимского муниципального округа Ярославской области»"
            ),
            "Любимский муниципальный округ",
            "администрации Любимского муниципального округа",
        ),
        (
            "Дятьковский район",
            "Администрация Дятьковского района",
            (
                "Администрация муниципального образования "
                "«Администрация Дятьковского района»"
            ),
            "Дятьковский муниципальный район",
            "администрации Дятьковского муниципального района",
        ),
        (
            "Одинцовский городской округ",
            "",
            "",
            "Одинцовский городской округ",
            "администрации Одинцовского городского округа",
        ),
    ],
)
def test_document_context_canonicalizes_all_district_level_entities(
    mun_r_name: str,
    mun_name: str,
    adm_name: str,
    expected_mun_name: str,
    expected_recipient: str,
) -> None:
    context = build_document_context(
        {
            "MUN_R_NAME": mun_r_name,
            "MUN_NAME": mun_name,
            "ADM_NAME": adm_name,
            "SUB_RF": "Ярославская область",
        },
        outgoing_number=1,
    )

    assert context["DOCUMENT_ENTITY_TYPE"] == "district"
    assert context["MUN_NAME"] == expected_mun_name
    assert context["ADM_NAME_1"] == expected_recipient
    assert "муниципального образования «Администрация" not in context["ADM_NAME_1"]


def test_settlement_stays_municipality_and_loses_only_nested_administration() -> None:
    context = build_document_context(
        {
            "MUN_R_NAME": "Тахтамукайский район",
            "MUN_NAME": "Яблоновское городское поселение",
            "ADM_NAME": (
                "Администрация муниципального образования "
                "«Администрация Яблоновского городского поселения»"
            ),
            "SUB_RF": "Республика Адыгея",
        },
        outgoing_number=1,
    )

    assert context["DOCUMENT_ENTITY_TYPE"] == "municipality"
    assert context["MUN_NAME"] == "Яблоновское городское поселение"
    assert context["ADM_NAME_1"] == "администрации Яблоновского городского поселения"


def test_all_renderers_protect_legacy_nested_recipient_values() -> None:
    legacy_value = (
        "администрации муниципального образования "
        "«Администрация Любимского муниципального округа Ярославской области»"
    )

    assert format_kp_recipient(legacy_value) == (
        "Администрации Любимского муниципального округа Ярославской области"
    )
    assert _normalize_official_recipient_name(legacy_value) == (
        "Администрации Любимского муниципального округа Ярославской области"
    )
    model = build_structured_kp_model(
        {
            "ADM_NAME_1": legacy_value,
            "OUTGOING_NUMBER": "1",
            "DATE": "29.07.2026",
        }
    )
    assert model.recipient == (
        "Администрации Любимского муниципального округа Ярославской области"
    )


def test_rendered_correspondence_applies_safe_text_corrections() -> None:
    rendered = render_text(
        (
            '<p style="color : red">Для администрация муниципального '
            'образования"яблоновское городское поселение" ,, предмета нормирование.</p>'
        ),
        {},
    )

    assert 'style="color : red"' in rendered
    assert "Для администрации" in rendered
    assert 'муниципального образования "яблоновское' in rendered
    assert ",," not in rendered
    assert "предмета нормирования" in rendered
