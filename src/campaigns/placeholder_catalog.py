"""Unified catalog of template placeholders for exact and semantic resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PlaceholderKind = Literal["system", "recipient"]


@dataclass(frozen=True)
class PlaceholderEntry:
    canonical: str
    kind: PlaceholderKind
    labels: tuple[str, ...]
    description: str
    aliases: tuple[str, ...] = ()


def _entry(
    canonical: str,
    kind: PlaceholderKind,
    description: str,
    *labels: str,
    aliases: tuple[str, ...] = (),
) -> PlaceholderEntry:
    unique_labels = tuple(dict.fromkeys([canonical, *labels, *aliases]))
    return PlaceholderEntry(
        canonical=canonical,
        kind=kind,
        labels=unique_labels,
        description=description,
        aliases=aliases,
    )


PLACEHOLDER_CATALOG: tuple[PlaceholderEntry, ...] = (
    _entry(
        "WORK_TITLE",
        "system",
        "Вид работ или услуги, выбирается в кампании из каталога компании",
        "Вид работ",
        "Вид_работ",
        "вид_работ",
        "вид деятельности",
        "work_title",
        aliases=("WORK_TITLE_1", "WORK_TITLE_NOMINATIVE"),
    ),
    _entry(
        "DOCUMENT_ID",
        "system",
        "Номер или идентификатор документа",
        "ид",
        "id",
        "номер",
        "номер документа",
        "идентификатор",
        "identifier",
        "docnumber",
        "documentnumber",
        "documentid",
        "ref",
        "reference",
        "regnumber",
    ),
    _entry(
        "DATE",
        "system",
        "Дата документа или письма",
        "date",
        "current_date",
        "CURRENT_DATE",
        "дата",
        "Дата документа",
        "текущая дата",
    ),
    _entry(
        "VALID_UNTIL",
        "system",
        "Срок действия предложения или документа",
        "valid_until",
        "VALID_UNTIL_DATE",
        "срок действия",
        "Срок действия",
    ),
    _entry(
        "OUTGOING_NUMBER",
        "system",
        "Исходящий или договорной номер",
        "outgoing_number",
        "contract_number",
        "CONTRACT_NUMBER",
        "исходящий номер",
        "номер исходящего",
    ),
    _entry(
        "DIRECTOR_NAME",
        "system",
        "Подписант или руководитель отправителя",
        "director_name",
        "Подписант",
        "генеральный директор",
        "директор",
        "руководитель отправителя",
    ),
    _entry(
        "PRICE_TOTAL",
        "system",
        "Стоимость работ или услуг",
        "price_total",
        "Стоимость",
        "стоимость",
        "цена",
        "сумма",
    ),
    _entry(
        "campaign_name",
        "system",
        "Название рассылки или кампании",
        "название рассылки",
        "название кампании",
    ),
    _entry(
        "MUN_R_SCOPE_FRAGMENT",
        "system",
        "Фрагмент текста о муниципальном образовании с падежом",
        "муниципальное образование",
        "МО",
        "MUN_R_SCOPE_FRAGMENT",
    ),
    _entry(
        "WORK_SCOPE_FRAGMENT",
        "system",
        "Фрагмент текста о виде работ с падежом",
    ),
    _entry(
        "HEAD_MO_FRAGMENT",
        "system",
        "Фрагмент текста о главе муниципального образования",
    ),
    _entry(
        "ADM_NAME",
        "recipient",
        "Полное название администрации или получателя",
        "ADM",
        "ADM_NAME_1",
        "Полное название администрации",
        "Администрация",
        "получатель",
        "adm_name",
    ),
    _entry(
        "MUN_NAME",
        "recipient",
        "Название муниципального образования",
        "MUN_NAME_1",
        "MUN_NAME_2",
        "Муниципальное образование",
        "МО",
        "mun_name",
    ),
    _entry(
        "MUN_R_NAME",
        "recipient",
        "Название муниципального района",
        "MUN_R_NAME_1",
        "Муниципальный район",
        "Район",
        "Округ",
        "mun_r_name",
    ),
    _entry(
        "SUB_RF",
        "recipient",
        "Субъект Российской Федерации",
        "SUB_RF_1",
        "Субъект РФ",
        "Регион",
        "Область",
        "Край",
        "Республика",
        "sub_rf",
    ),
    _entry(
        "HEAD_FIO",
        "recipient",
        "ФИО главы муниципального образования или контактного лица",
        "HEAD_FIO_1",
        "HEAD_FIO_2",
        "HEAD_FIO_SHORT",
        "Глава МО",
        "Руководитель",
        "ФИО",
        "head_fio",
        "контактное лицо",
    ),
    _entry(
        "EMAIL_OSN",
        "recipient",
        "Основной email получателя",
        "EMAIL",
        "E-MAIL",
        "Эл. Адрес (основной)",
        "Почта",
        "email_osn",
    ),
    _entry(
        "EMAIL_DOP",
        "recipient",
        "Дополнительный email получателя",
        "Эл. Адрес (доп)",
        "Доп почта",
        "Резерв",
        "email_dop",
    ),
    _entry(
        "TEL_OSN",
        "recipient",
        "Основной телефон получателя",
        "Телефон",
        "Телефон основной",
        "tel_osn",
    ),
    _entry(
        "TEL_DOP",
        "recipient",
        "Дополнительный телефон получателя",
        "Телефон (доп)",
        "Доп телефон",
        "tel_dop",
    ),
    _entry(
        "company",
        "recipient",
        "Компания или организация получателя",
        "компания",
        "Компания",
        "organization",
        "муниципальное образование",
    ),
    _entry(
        "contact_name",
        "recipient",
        "Контактное лицо получателя",
        "contact",
        "контакт",
        "контактное лицо",
    ),
    _entry(
        "CONTACT_FIRST_NAME",
        "recipient",
        "Имя контактного лица (из ФИО получателя)",
        "Имя",
        "имя",
        "first_name",
    ),
    _entry(
        "CONTACT_PATRONYMIC",
        "recipient",
        "Отчество контактного лица (из ФИО получателя)",
        "Отчество",
        "отчество",
        "patronymic",
    ),
    _entry(
        "CONTACT_SURNAME",
        "recipient",
        "Фамилия контактного лица (из ФИО получателя)",
        "Фамилия",
        "фамилия",
        "surname",
    ),
    _entry(
        "email",
        "recipient",
        "Email получателя",
        "e-mail",
        "почта",
    ),
    _entry(
        "email_fallback",
        "recipient",
        "Резервный email получателя",
        "email2",
        "email_fallback",
    ),
    _entry(
        "region",
        "recipient",
        "Регион получателя",
        "регион",
    ),
)


def catalog_by_canonical() -> dict[str, PlaceholderEntry]:
    return {entry.canonical: entry for entry in PLACEHOLDER_CATALOG}


def catalog_entries(*, kind: PlaceholderKind | None = None) -> list[PlaceholderEntry]:
    if kind is None:
        return list(PLACEHOLDER_CATALOG)
    return [entry for entry in PLACEHOLDER_CATALOG if entry.kind == kind]


def entry_search_text(entry: PlaceholderEntry) -> str:
    parts = [entry.canonical, entry.description, *entry.labels, *entry.aliases]
    return " ".join(str(part) for part in parts if part)
