from __future__ import annotations

from dataclasses import dataclass, replace


WORK_TYPE_MNGP_SETTLEMENTS = "mngp_settlements"
WORK_TYPE_MNGP_DISTRICTS = "mngp_districts"
WORK_TYPE_TERRITORIAL_ZONE_BOUNDARIES = "territorial_zone_boundaries"
WORK_TYPE_STP_MO = "stp_mo"
DEFAULT_WORK_TYPE = WORK_TYPE_MNGP_SETTLEMENTS


@dataclass(frozen=True)
class WorkTypeProfile:
    key: str
    label: str
    short_name: str
    filename_label: str
    service_title_prepositional: str
    service_title_nominative: str
    result_name: str
    mail_subject: str
    consent_subject_kp: str
    consent_subject_contract: str
    consent_subject_both: str
    consent_button_kp: str
    consent_button_contract: str
    consent_button_both: str
    consent_prepared_phrase: str


_MNGP_PROFILE = WorkTypeProfile(
    key=WORK_TYPE_MNGP_SETTLEMENTS,
    label="МНГП",
    short_name="МНГП",
    filename_label="МНГП",
    service_title_prepositional="разработке проекта местных нормативов градостроительного проектирования",
    service_title_nominative="разработка проекта местных нормативов градостроительного проектирования",
    result_name="проект местных нормативов градостроительного проектирования",
    mail_subject="Коммерческое предложение на разработку МНГП.",
    consent_subject_kp="МНГП для {MUN_R_NAME}: согласие на получение КП",
    consent_subject_contract="МНГП для {MUN_R_NAME}: согласие на получение проекта договора",
    consent_subject_both="МНГП для {MUN_R_NAME}: согласие на получение КП и проекта договора",
    consent_button_kp="Получить персонализированное коммерческое предложение по разработке МНГП.",
    consent_button_contract="Получить проект договора по разработке МНГП.",
    consent_button_both="Получить КП и проект договора по разработке МНГП.",
    consent_prepared_phrase=(
        "на разработку местных нормативов градостроительного проектирования (далее - МНГП)"
    ),
)


WORK_TYPE_PROFILES: dict[str, WorkTypeProfile] = {
    WORK_TYPE_MNGP_SETTLEMENTS: _MNGP_PROFILE,
    WORK_TYPE_MNGP_DISTRICTS: replace(_MNGP_PROFILE, key=WORK_TYPE_MNGP_DISTRICTS),
    WORK_TYPE_STP_MO: replace(
        _MNGP_PROFILE,
        key=WORK_TYPE_STP_MO,
        label="СТП МО",
        short_name="СТП МО",
        filename_label="СТП_МО",
        service_title_prepositional="разработке схемы территориального планирования муниципального образования",
        service_title_nominative="разработка схемы территориального планирования муниципального образования",
        result_name="схема территориального планирования муниципального образования",
        mail_subject="Коммерческое предложение на разработку СТП МО.",
        consent_subject_kp="СТП МО для {MUN_R_NAME}: согласие на получение КП",
        consent_subject_contract="СТП МО для {MUN_R_NAME}: согласие на получение проекта договора",
        consent_subject_both="СТП МО для {MUN_R_NAME}: согласие на получение КП и проекта договора",
        consent_button_kp="Получить персонализированное коммерческое предложение по разработке СТП МО.",
        consent_button_contract="Получить проект договора по разработке СТП МО.",
        consent_button_both="Получить КП и проект договора по разработке СТП МО.",
        consent_prepared_phrase=(
            "на разработку схемы территориального планирования муниципального образования (далее - СТП МО)"
        ),
    ),
    WORK_TYPE_TERRITORIAL_ZONE_BOUNDARIES: WorkTypeProfile(
        key=WORK_TYPE_TERRITORIAL_ZONE_BOUNDARIES,
        label="Описание местоположения границ территориальных зон",
        short_name="территориальные зоны",
        filename_label="Территориальные_зоны",
        service_title_prepositional="подготовке описания местоположения границ территориальных зон",
        service_title_nominative="подготовка описания местоположения границ территориальных зон",
        result_name="описание местоположения границ территориальных зон",
        mail_subject="Коммерческое предложение на подготовку описания границ территориальных зон.",
        consent_subject_kp="Территориальные зоны для {MUN_R_NAME}: согласие на получение КП",
        consent_subject_contract="Территориальные зоны для {MUN_R_NAME}: согласие на получение проекта договора",
        consent_subject_both="Территориальные зоны для {MUN_R_NAME}: согласие на получение КП и проекта договора",
        consent_button_kp="Получить коммерческое предложение по описанию границ территориальных зон.",
        consent_button_contract="Получить проект договора по описанию границ территориальных зон.",
        consent_button_both="Получить КП и проект договора по описанию границ территориальных зон.",
        consent_prepared_phrase="по подготовке описания местоположения границ территориальных зон",
    ),
}


def normalize_work_type(value: str | None) -> str:
    key = str(value or "").strip().lower()
    return key if key in WORK_TYPE_PROFILES else DEFAULT_WORK_TYPE


def get_work_type_profile(value: str | None) -> WorkTypeProfile:
    return WORK_TYPE_PROFILES[normalize_work_type(value)]


def build_work_type_context(value: str | None) -> dict[str, str]:
    profile = get_work_type_profile(value)
    return {
        "WORK_TYPE": profile.key,
        "WORK_TYPE_LABEL": profile.label,
        "WORK_SHORT_NAME": profile.short_name,
        "WORK_FILENAME_LABEL": profile.filename_label,
        "WORK_TITLE": profile.service_title_prepositional,
        "WORK_TITLE_1": profile.service_title_prepositional,
        "WORK_TITLE_NOMINATIVE": profile.service_title_nominative,
        "WORK_RESULT_NAME": profile.result_name,
        "MAIL_SUBJECT_DEFAULT": profile.mail_subject,
    }
