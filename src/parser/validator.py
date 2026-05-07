import re
from dataclasses import dataclass

from src.utils.logger import logger


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str]
    warnings: list[str]


class MoValidator:
    """
    Валидирует и нормализует данные об МО перед записью в data.xlsx.
    Отслеживает дубликаты по ИНН в рамках одного сеанса парсинга.
    """

    def __init__(self):
        self._seen_inns: set[str] = set()

    def validate_and_normalize(self, data: dict) -> tuple[dict, ValidationResult]:
        """
        Валидирует и нормализует словарь с данными об МО.

        Args:
            data: словарь с ключами из COLUMNS (ADM_NAME, EMAIL_OSN и т.д.)

        Returns:
            Кортеж (нормализованные данные, результат валидации)
        """
        errors: list[str] = []
        warnings: list[str] = []
        result = dict(data)

        # ИНН
        inn = _clean(result.get("REQUISITES_INN", ""))
        if inn:
            inn_normalized, inn_error = _validate_inn(inn)
            if inn_error:
                warnings.append(inn_error)
            else:
                # Проверка дубликатов
                if inn_normalized in self._seen_inns:
                    logger.warning(
                        "validator_duplicate_inn",
                        inn=inn_normalized,
                        mun_name=result.get("MUN_NAME", ""),
                    )
                    return {}, ValidationResult(
                        is_valid=False,
                        errors=["Дубликат ИНН — запись пропущена"],
                        warnings=[],
                    )
                self._seen_inns.add(inn_normalized)
            result["REQUISITES_INN"] = inn_normalized or inn

        # КПП
        kpp = _clean(result.get("REQUISITES_KPP", ""))
        if kpp:
            kpp_normalized, kpp_error = _validate_kpp(kpp)
            if kpp_error:
                warnings.append(kpp_error)
            result["REQUISITES_KPP"] = kpp_normalized or kpp

        # ОГРН
        ogrn = _clean(result.get("REQUISITES_OGRN", ""))
        if ogrn:
            ogrn_normalized, ogrn_error = _validate_ogrn(ogrn)
            if ogrn_error:
                warnings.append(ogrn_error)
            result["REQUISITES_OGRN"] = ogrn_normalized or ogrn

        # Email основной
        email_osn = _clean(result.get("EMAIL_OSN", ""))
        if email_osn:
            email_normalized, email_error = _validate_email(email_osn)
            if email_error:
                warnings.append(f"EMAIL_OSN: {email_error}")
                result["EMAIL_OSN"] = email_osn  # оставляем как есть
            else:
                result["EMAIL_OSN"] = email_normalized

        # Email дополнительный
        email_dop = _clean(result.get("EMAIL_DOP", ""))
        if email_dop:
            email_normalized, email_error = _validate_email(email_dop)
            if email_error:
                warnings.append(f"EMAIL_DOP: {email_error}")
                result["EMAIL_DOP"] = email_dop
            else:
                result["EMAIL_DOP"] = email_normalized

        # Телефон основной
        tel_osn = _clean(result.get("TEL_OSN", ""))
        if tel_osn:
            tel_normalized, tel_error = _normalize_phone(tel_osn)
            if tel_error:
                warnings.append(f"TEL_OSN: {tel_error}")
                result["TEL_OSN"] = tel_osn
            else:
                result["TEL_OSN"] = tel_normalized

        # Телефон дополнительный
        tel_dop = _clean(result.get("TEL_DOP", ""))
        if tel_dop:
            tel_normalized, tel_error = _normalize_phone(tel_dop)
            if tel_error:
                warnings.append(f"TEL_DOP: {tel_error}")
                result["TEL_DOP"] = tel_dop
            else:
                result["TEL_DOP"] = tel_normalized

        # Обязательные поля — только предупреждения, не блокируем запись
        for required_field in ("MUN_NAME", "SUB_RF"):
            if not result.get(required_field, "").strip():
                warnings.append(f"Пустое обязательное поле: {required_field}")

        is_valid = len(errors) == 0

        if warnings:
            logger.debug(
                "validator_warnings",
                mun_name=result.get("MUN_NAME", ""),
                warnings=warnings,
            )

        return result, ValidationResult(is_valid=is_valid, errors=errors, warnings=warnings)

    def reset(self) -> None:
        """Сбрасывает список виденных ИНН (для нового сеанса парсинга)."""
        self._seen_inns.clear()


# ------------------------------------------------------------------
# Вспомогательные функции
# ------------------------------------------------------------------

def _clean(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _validate_inn(inn: str) -> tuple[str, str]:
    """
    Валидирует ИНН. Для организаций — 10 цифр, для ИП — 12 цифр.
    Возвращает (нормализованный ИНН, сообщение об ошибке).
    """
    digits = re.sub(r"\D", "", inn)
    if len(digits) not in (10, 12):
        return "", f"ИНН должен содержать 10 или 12 цифр, получено {len(digits)}: '{inn}'"
    return digits, ""


def _validate_kpp(kpp: str) -> tuple[str, str]:
    """Валидирует КПП — 9 цифр."""
    digits = re.sub(r"\D", "", kpp)
    if len(digits) != 9:
        return "", f"КПП должен содержать 9 цифр, получено {len(digits)}: '{kpp}'"
    return digits, ""


def _validate_ogrn(ogrn: str) -> tuple[str, str]:
    """Валидирует ОГРН — 13 цифр (15 для ИП)."""
    digits = re.sub(r"\D", "", ogrn)
    if len(digits) not in (13, 15):
        return "", f"ОГРН должен содержать 13 или 15 цифр, получено {len(digits)}: '{ogrn}'"
    return digits, ""


def _validate_email(email: str) -> tuple[str, str]:
    """Валидирует email. Возвращает нижний регистр если валидный."""
    pattern = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
    normalized = email.lower().strip()
    if not pattern.match(normalized):
        return "", f"Некорректный email: '{email}'"
    return normalized, ""


def _normalize_phone(phone: str) -> tuple[str, str]:
    digits = re.sub(r"\D", "", phone)

    # Убираем ведущую 8 или 7
    if len(digits) == 11 and digits[0] in ("7", "8"):
        digits = digits[1:]

    if len(digits) != 10:
        return "", f"Не удалось нормализовать телефон: '{phone}' (получено {len(digits)} цифр)"

    normalized = f"+7{digits}"
    return normalized, ""