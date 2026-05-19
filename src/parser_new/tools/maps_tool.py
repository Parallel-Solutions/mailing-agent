"""
tools/maps_tool.py — геопоиск и работа с картами.

Три источника:
  - Nominatim (OpenStreetMap) — геокодинг адресов, бесплатно
  - Overpass API (OpenStreetMap) — поиск объектов в радиусе, бесплатно
  - 2GIS API — организации в России/СНГ, точнее OSM для РФ

Инструменты:
  - geocode_tool        — адрес → координаты и обратно
  - search_nearby_tool  — найти объекты рядом с адресом
  - search_2gis_tool    — найти организации через 2GIS (для России)
"""
from __future__ import annotations

import time
import httpx
from langchain.tools import tool
from tenacity import retry, stop_after_attempt, wait_fixed

from src.parser_new import config
from src.parser_new.logger import logger


# ==============================
# ОБЩИЕ НАСТРОЙКИ
# ==============================

HEADERS = {
    "User-Agent": "ParserAgent/1.0 (data collection bot)",
    "Accept-Language": "ru,en",
}

# Небольшая задержка между запросами к Nominatim — требование их политики
NOMINATIM_DELAY = 1.1


# ==============================
# NOMINATIM — геокодинг
# ==============================

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _nominatim_geocode(address: str) -> list[dict]:
    """Адрес → список вариантов с координатами."""
    time.sleep(NOMINATIM_DELAY)
    response = httpx.get(
        "https://nominatim.openstreetmap.org/search",
        params={
            "q":              address,
            "format":         "json",
            "addressdetails": 1,
            "limit":          5,
            "accept-language": "ru",
        },
        headers=HEADERS,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _nominatim_reverse(lat: float, lon: float) -> dict:
    """Координаты → адрес."""
    time.sleep(NOMINATIM_DELAY)
    response = httpx.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={
            "lat":    lat,
            "lon":    lon,
            "format": "json",
            "accept-language": "ru",
        },
        headers=HEADERS,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def geocode(address: str) -> dict:
    """
    Переводит адрес в координаты.
    Возвращает лучший вариант + все найденные альтернативы.
    """
    try:
        results = _nominatim_geocode(address)
        if not results:
            return {"success": False, "error": f"Адрес не найден: {address}"}

        best = results[0]
        parsed = {
            "success":     True,
            "lat":         float(best["lat"]),
            "lon":         float(best["lon"]),
            "display":     best.get("display_name", ""),
            "type":        best.get("type", ""),
            "importance":  best.get("importance", 0),
            "alternatives": [
                {
                    "display": r.get("display_name", ""),
                    "lat":     float(r["lat"]),
                    "lon":     float(r["lon"]),
                }
                for r in results[1:]
            ],
        }
        logger.info(f"[maps/geocode] '{address}' → {parsed['lat']}, {parsed['lon']}")
        return parsed

    except httpx.TimeoutException:
        return {"success": False, "error": "Таймаут Nominatim"}
    except Exception as e:
        logger.error(f"[maps/geocode] Ошибка: {e}")
        return {"success": False, "error": str(e)}


def reverse_geocode(lat: float, lon: float) -> dict:
    """Переводит координаты в человекочитаемый адрес."""
    try:
        result = _nominatim_reverse(lat, lon)
        return {
            "success": True,
            "address": result.get("display_name", ""),
            "city":    result.get("address", {}).get("city") or
                       result.get("address", {}).get("town") or
                       result.get("address", {}).get("village", ""),
            "region":  result.get("address", {}).get("state", ""),
            "country": result.get("address", {}).get("country", ""),
        }
    except Exception as e:
        logger.error(f"[maps/reverse] Ошибка: {e}")
        return {"success": False, "error": str(e)}


# ==============================
# OVERPASS API — поиск объектов в радиусе
# ==============================

# Словарь типов заведений для Overpass
PLACE_TYPES = {
    "цветочный":     'shop="florist"',
    "магазин":       'shop',
    "кафе":          'amenity="cafe"',
    "ресторан":      'amenity="restaurant"',
    "аптека":        'amenity="pharmacy"',
    "банк":          'amenity="bank"',
    "больница":      'amenity="hospital"',
    "школа":         'amenity="school"',
    "гостиница":     'tourism="hotel"',
    "парковка":      'amenity="parking"',
    "заправка":      'amenity="fuel"',
    "администрация": 'amenity="townhall"',
    "офис":          'office',
}


@retry(stop=stop_after_attempt(3), wait=wait_fixed(3))
def _overpass_query(query: str) -> dict:
    response = httpx.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def search_nearby(
    lat: float,
    lon: float,
    place_type: str,
    radius_m: int = 1000,
) -> dict:
    """
    Ищет объекты заданного типа в радиусе от точки.

    Args:
        lat, lon:    координаты центра поиска
        place_type:  тип объекта на русском или тег OSM
        radius_m:    радиус поиска в метрах
    """
    # Определяем тег OSM
    osm_tag = None
    for keyword, tag in PLACE_TYPES.items():
        if keyword in place_type.lower():
            osm_tag = tag
            break
    if not osm_tag:
        osm_tag = f'name~"{place_type}"'  # ищем по имени если тип неизвестен

    query = f"""
    [out:json][timeout:25];
    (
      node[{osm_tag}](around:{radius_m},{lat},{lon});
      way[{osm_tag}](around:{radius_m},{lat},{lon});
    );
    out body;
    """

    try:
        data    = _overpass_query(query)
        elements = data.get("elements", [])

        places = []
        for el in elements:
            tags = el.get("tags", {})
            name = tags.get("name") or tags.get("name:ru")
            if not name:
                continue

            # Координаты
            el_lat = el.get("lat") or (el.get("center", {}) or {}).get("lat")
            el_lon = el.get("lon") or (el.get("center", {}) or {}).get("lon")

            # Расстояние от центра (приблизительно)
            dist = None
            if el_lat and el_lon:
                dist = int(_haversine(lat, lon, el_lat, el_lon))

            places.append({
                "name":    name,
                "address": tags.get("addr:full") or _build_address(tags),
                "phone":   tags.get("phone") or tags.get("contact:phone", ""),
                "website": tags.get("website") or tags.get("contact:website", ""),
                "lat":     el_lat,
                "lon":     el_lon,
                "dist_m":  dist,
            })

        # Сортируем по расстоянию
        places.sort(key=lambda p: p["dist_m"] or 9999)

        logger.info(f"[maps/nearby] Найдено {len(places)} объектов типа '{place_type}'")
        return {"success": True, "places": places, "total": len(places)}

    except httpx.TimeoutException:
        return {"success": False, "error": "Таймаут Overpass API — попробуй уменьшить радиус"}
    except Exception as e:
        logger.error(f"[maps/nearby] Ошибка: {e}")
        return {"success": False, "error": str(e)}


def _build_address(tags: dict) -> str:
    """Собирает адрес из отдельных тегов OSM."""
    parts = [
        tags.get("addr:street", ""),
        tags.get("addr:housenumber", ""),
        tags.get("addr:city", ""),
    ]
    return ", ".join(p for p in parts if p)


def _haversine(lat1, lon1, lat2, lon2) -> float:
    """Расстояние между двумя точками в метрах."""
    from math import radians, sin, cos, sqrt, atan2
    R = 6371000
    φ1, φ2 = radians(lat1), radians(lat2)
    Δφ = radians(lat2 - lat1)
    Δλ = radians(lon2 - lon1)
    a = sin(Δφ/2)**2 + cos(φ1)*cos(φ2)*sin(Δλ/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


# ==============================
# 2GIS API — организации в России
# ==============================

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _twogis_request(params: dict) -> dict:
    response = httpx.get(
        "https://catalog.api.2gis.com/3.0/items",
        params={"key": config.TWOGIS_API_KEY, **params},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def search_2gis(query: str, location: str, radius_m: int = 1000) -> dict:
    """
    Ищет организации через 2GIS.
    Лучше OSM для российских организаций — больше данных, актуальнее.

    Args:
        query:     название или тип организации
        location:  адрес или название города
        radius_m:  радиус поиска в метрах
    """
    if not config.TWOGIS_API_KEY:
        return {"success": False, "error": "TWOGIS_API_KEY не задан в .env"}

    try:
        # Сначала геокодируем location
        geo = geocode(location)
        if not geo["success"]:
            return {"success": False, "error": f"Не удалось найти локацию: {location}"}

        params = {
            "q":       query,
            "point":   f"{geo['lon']},{geo['lat']}",
            "radius":  radius_m,
            "fields":  "items.point,items.address,items.contact_groups,items.schedule,items.rating",
            "locale":  "ru_RU",
            "page_size": 20,
        }

        data  = _twogis_request(params)
        items = data.get("result", {}).get("items", [])

        places = []
        for item in items:
            # Контакты
            phones   = []
            websites = []
            for group in item.get("contact_groups", []):
                for contact in group.get("contacts", []):
                    if contact["type"] == "phone":
                        phones.append(contact.get("text", ""))
                    elif contact["type"] in ("website", "url"):
                        websites.append(contact.get("value", ""))

            # Координаты и расстояние
            point = item.get("point", {})
            el_lat, el_lon = point.get("lat"), point.get("lon")
            dist = int(_haversine(geo["lat"], geo["lon"], el_lat, el_lon)) \
                if el_lat and el_lon else None

            places.append({
                "name":    item.get("name", ""),
                "address": item.get("address", {}).get("name", ""),
                "phones":  phones,
                "website": websites[0] if websites else "",
                "rating":  item.get("rating", {}).get("score"),
                "lat":     el_lat,
                "lon":     el_lon,
                "dist_m":  dist,
                "source":  "2GIS",
            })

        places.sort(key=lambda p: p["dist_m"] or 9999)
        logger.info(f"[maps/2gis] '{query}' в '{location}': {len(places)} результатов")
        return {"success": True, "places": places, "total": len(places)}

    except httpx.TimeoutException:
        return {"success": False, "error": "Таймаут 2GIS"}
    except Exception as e:
        logger.error(f"[maps/2gis] Ошибка: {e}")
        return {"success": False, "error": str(e)}


# ==============================
# ФОРМАТИРОВАНИЕ ДЛЯ АГЕНТА
# ==============================

def _format_places(places: list[dict], max_results: int = 10) -> str:
    if not places:
        return "Ничего не найдено."
    lines = []
    for i, p in enumerate(places[:max_results], 1):
        dist  = f"{p['dist_m']} м" if p.get("dist_m") else ""
        phone = ", ".join(p["phones"]) if isinstance(p.get("phones"), list) \
                else p.get("phone", "") or p.get("phones", "")
        lines += [
            f"{i}. {p['name']} {f'({dist})' if dist else ''}",
            f"   📍 {p.get('address', '')}",
            f"   📞 {phone}" if phone else "",
            f"   🌐 {p.get('website', '')}" if p.get("website") else "",
            f"   ⭐ {p.get('rating', '')}" if p.get("rating") else "",
            "",
        ]
    return "\n".join(l for l in lines if l is not None)


# ==============================
# ИНСТРУМЕНТЫ ДЛЯ АГЕНТА
# ==============================

@tool
def geocode_tool(address: str) -> str:
    """
    Переводит текстовый адрес в координаты (широта, долгота).
    Также работает в обратную сторону — если передать координаты
    в формате 'lat,lon' вернёт адрес.
    Используй когда нужно найти координаты места перед геопоиском,
    или когда есть координаты и нужен адрес.
    """
    # Проверяем не координаты ли это
    parts = address.replace(" ", "").split(",")
    if len(parts) == 2:
        try:
            lat, lon = float(parts[0]), float(parts[1])
            result = reverse_geocode(lat, lon)
            if result["success"]:
                return f"Адрес: {result['address']}"
        except ValueError:
            pass

    result = geocode(address)
    if not result["success"]:
        return f"Не удалось найти: {result['error']}"

    lines = [
        f"Адрес: {result['display']}",
        f"Координаты: {result['lat']}, {result['lon']}",
    ]
    if result["alternatives"]:
        lines.append("\nДругие варианты:")
        for alt in result["alternatives"]:
            lines.append(f"  • {alt['display']} ({alt['lat']}, {alt['lon']})")
    return "\n".join(lines)


@tool
def search_nearby_tool(query: str, address: str, radius_m: int = 1000) -> str:
    """
    Ищет объекты (заведения, организации) рядом с указанным адресом.
    Использует OpenStreetMap — работает для любой страны, бесплатно.
    Используй для поиска типов мест: кафе, магазины, больницы, школы и т.д.

    Параметры:
      query    — что искать (например 'цветочный магазин', 'аптека', 'кафе')
      address  — адрес или название места откуда искать
      radius_m — радиус поиска в метрах (по умолчанию 1000)
    """
    geo = geocode(address)
    if not geo["success"]:
        return f"Не удалось найти адрес '{address}': {geo['error']}"

    result = search_nearby(geo["lat"], geo["lon"], query, radius_m)
    if not result["success"]:
        return f"Поиск не удался: {result['error']}"

    if not result["places"]:
        return f"По запросу '{query}' в радиусе {radius_m}м от '{address}' ничего не найдено."

    header = f"Найдено {result['total']} объектов '{query}' в радиусе {radius_m}м от '{address}':\n"
    return header + _format_places(result["places"])


@tool
def search_2gis_tool(query: str, location: str, radius_m: int = 1000) -> str:
    """
    Ищет организации через 2GIS — лучший источник для России и СНГ.
    Даёт более точные данные чем OpenStreetMap для российских организаций:
    телефоны, сайты, рейтинги, актуальные адреса.
    Используй для поиска конкретных организаций в российских городах.

    Параметры:
      query    — название или тип организации ('цветочный магазин', 'администрация')
      location — город или адрес ('Москва', 'Уфа, улица Ленина 1')
      radius_m — радиус поиска в метрах (по умолчанию 1000)
    """
    result = search_2gis(query, location, radius_m)
    if not result["success"]:
        return f"Поиск в 2GIS не удался: {result['error']}"

    if not result["places"]:
        return f"2GIS: по запросу '{query}' в '{location}' ничего не найдено."

    header = f"2GIS — найдено {result['total']} результатов '{query}' в '{location}':\n"
    return header + _format_places(result["places"])
