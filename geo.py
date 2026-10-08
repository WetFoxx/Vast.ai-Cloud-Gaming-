# geo.py — оценка задержки до машины по расстоянию.
#
# Замерить пинг с компьютера до хоста напрямую нельзя: многие хосты не отвечают на ping, а VPN
# (например, в режиме TUN) сам отвечает на соединения — замер показывает время VPN, а не хоста
# (проверено у Алексея 2026-09-25: все хосты «отвечали» за 1–14 мс, включая Китай и Аргентину).
# Поэтому: где я — по часовому поясу компьютера (координаты из zone.tab), где хост — по региону,
# который отдаёт Vast («Quebec, CA»). Это ОЦЕНКА: реальную задержку меряет latency.py после игры.

import math
import os
import re
import sys
from pathlib import Path

# Крупные узлы сети в регионе (город с дата-центрами), не географический центр — так точнее.
# Ключ — как в geolocation у Vast: «Регион, КОД» (регион) или «КОД» (страна целиком).
REGIONS = {
    # США — штаты
    "Alabama, US": (33.5, -86.8), "Arizona, US": (33.4, -112.1), "Arkansas, US": (34.7, -92.3),
    "California, US": (37.4, -121.9), "Colorado, US": (39.7, -105.0), "Connecticut, US": (41.8, -72.7),
    "Delaware, US": (39.7, -75.6), "Florida, US": (25.8, -80.2), "Georgia, US": (33.7, -84.4),
    "Idaho, US": (43.6, -116.2), "Illinois, US": (41.9, -87.6), "Indiana, US": (39.8, -86.2),
    "Iowa, US": (41.6, -93.6), "Kansas, US": (39.1, -94.6), "Kentucky, US": (38.3, -85.8),
    "Louisiana, US": (30.0, -90.1), "Maine, US": (43.7, -70.3), "Maryland, US": (39.3, -76.6),
    "Massachusetts, US": (42.4, -71.1), "Michigan, US": (42.3, -83.0), "Minnesota, US": (45.0, -93.3),
    "Mississippi, US": (32.3, -90.2), "Missouri, US": (38.6, -90.2), "Montana, US": (45.8, -108.5),
    "Nebraska, US": (41.3, -96.0), "Nevada, US": (36.2, -115.1), "New Hampshire, US": (43.0, -71.5),
    "New Jersey, US": (40.7, -74.2), "New Mexico, US": (35.1, -106.6), "New York, US": (40.7, -74.0),
    "North Carolina, US": (35.2, -80.8), "North Dakota, US": (46.9, -96.8), "Ohio, US": (40.0, -83.0),
    "Oklahoma, US": (35.5, -97.5), "Oregon, US": (45.5, -122.7), "Pennsylvania, US": (40.0, -75.2),
    "Rhode Island, US": (41.8, -71.4), "South Carolina, US": (34.0, -81.0), "South Dakota, US": (43.5, -96.7),
    "Tennessee, US": (36.2, -86.8), "Texas, US": (32.8, -96.8), "Utah, US": (40.8, -111.9),
    "Vermont, US": (44.5, -73.2), "Virginia, US": (39.0, -77.5), "Washington, US": (47.6, -122.3),
    "West Virginia, US": (38.4, -81.6), "Wisconsin, US": (43.0, -87.9), "Wyoming, US": (41.1, -104.8),
    "Hawaii, US": (21.3, -157.9), "Alaska, US": (61.2, -149.9),
    # Канада — провинции
    "Quebec, CA": (45.5, -73.6), "Ontario, CA": (43.7, -79.4), "British Columbia, CA": (49.3, -123.1),
    "Alberta, CA": (51.0, -114.1), "Manitoba, CA": (49.9, -97.1), "Saskatchewan, CA": (52.1, -106.7),
    "Nova Scotia, CA": (44.6, -63.6), "New Brunswick, CA": (45.9, -66.6),
    # Китай — провинции, где бывают хосты
    "Guangxi Zhuangzu, CN": (22.8, 108.3), "Chongqing, CN": (29.6, 106.5), "Guangdong, CN": (23.1, 113.3),
    "Beijing, CN": (39.9, 116.4), "Shanghai, CN": (31.2, 121.5), "Zhejiang, CN": (30.3, 120.2),
    "Jiangsu, CN": (32.1, 118.8), "Sichuan, CN": (30.7, 104.1), "Hubei, CN": (30.6, 114.3),
    "Fujian, CN": (26.1, 119.3), "Shandong, CN": (36.7, 117.0), "Henan, CN": (34.8, 113.7),
    "Hebei, CN": (38.0, 114.5), "Hunan, CN": (28.2, 113.0), "Anhui, CN": (31.9, 117.3),
    "Shaanxi, CN": (34.3, 108.9), "Tianjin, CN": (39.1, 117.2), "Liaoning, CN": (41.8, 123.4),
}
COUNTRIES = {
    "US": (39.0, -77.5), "CA": (43.7, -79.4), "MX": (19.4, -99.1), "BR": (-23.5, -46.6), "AR": (-34.6, -58.4),
    "CL": (-33.4, -70.6), "CO": (4.7, -74.1), "PE": (-12.0, -77.0),
    "GB": (51.5, -0.1), "IE": (53.3, -6.3), "FR": (48.9, 2.3), "DE": (50.1, 8.7), "NL": (52.4, 4.9),
    "BE": (50.8, 4.4), "LU": (49.6, 6.1), "CH": (47.4, 8.5), "AT": (48.2, 16.4), "IT": (45.5, 9.2),
    "ES": (40.4, -3.7), "PT": (38.7, -9.1), "DK": (55.7, 12.6), "SE": (59.3, 18.1), "NO": (59.9, 10.8),
    "FI": (60.2, 24.9), "IS": (64.1, -21.9), "PL": (52.2, 21.0), "CZ": (50.1, 14.4), "SK": (48.1, 17.1),
    "HU": (47.5, 19.0), "RO": (44.4, 26.1), "BG": (42.7, 23.3), "GR": (38.0, 23.7), "HR": (45.8, 16.0),
    "SI": (46.1, 14.5), "RS": (44.8, 20.5), "UA": (50.5, 30.5), "BY": (53.9, 27.6), "LT": (54.7, 25.3),
    "LV": (56.9, 24.1), "EE": (59.4, 24.8), "MD": (47.0, 28.9), "RU": (55.8, 37.6), "TR": (41.0, 29.0),
    "IL": (32.1, 34.8), "AE": (25.2, 55.3), "SA": (24.7, 46.7), "QA": (25.3, 51.5), "KZ": (43.2, 76.9),
    "GE": (41.7, 44.8), "AM": (40.2, 44.5), "IN": (19.1, 72.9), "PK": (24.9, 67.0), "TH": (13.8, 100.5),
    "VN": (10.8, 106.7), "MY": (3.1, 101.7), "SG": (1.3, 103.8), "ID": (-6.2, 106.8), "PH": (14.6, 121.0),
    "HK": (22.3, 114.2), "TW": (25.0, 121.5), "CN": (31.2, 121.5), "KR": (37.6, 127.0), "JP": (35.7, 139.7),
    "AU": (-33.9, 151.2), "NZ": (-36.8, 174.8), "ZA": (-26.2, 28.0), "EG": (30.0, 31.2), "NG": (6.5, 3.4),
    "KE": (-1.3, 36.8), "MA": (33.6, -7.6),
}

# Оценка времени туда-обратно: свет в оптоволокне — ~100 км на 1 мс туда-обратно по прямой; реальные
# трассы длиннее прямой (ROUTE) и добавляют постоянную задержку оборудования (BASE_MS).
ROUTE = 1.6
BASE_MS = 15


def _parse_coord(s):
    """«+5601+09250» из zone.tab → (56.02, 92.83). Градусы, минуты и, бывает, секунды."""
    m = re.fullmatch(r"([+-]\d{2})(\d{2})(\d{2})?([+-]\d{3})(\d{2})(\d{2})?", s)
    if not m:
        return None
    def dm(deg, mins, secs):
        sign = -1 if deg.startswith("-") else 1
        return sign * (abs(int(deg)) + int(mins) / 60 + int(secs or 0) / 3600)
    return dm(m[1], m[2], m[3]), dm(m[4], m[5], m[6])


def _zone_name():
    """Часовой пояс компьютера («Asia/Krasnoyarsk») или None."""
    tz = os.environ.get("TZ", "").lstrip(":")
    if "/" in tz:
        return tz
    if sys.platform == "win32":
        # Windows хранит пояс своим именем («North Asia Standard Time») — tzlocal переводит в «Asia/Krasnoyarsk»
        try:
            from tzlocal import get_localzone_name
            return get_localzone_name() or None
        except Exception:                              # noqa: BLE001 — нет библиотеки или реестр не ответил
            return None
    try:
        link = os.readlink("/etc/localtime")          # macOS и Linux: ссылка на файл пояса
    except OSError:
        return None
    m = re.search(r"zoneinfo/(.+)$", link)
    return m[1] if m else None


def _tzdata_tabs():
    try:
        import tzdata
    except ImportError:
        return []
    d = Path(tzdata.__file__).parent / "zoneinfo"
    return [d / "zone1970.tab", d / "zone.tab"]


def home():
    """Примерные координаты компьютера по его часовому поясу или None. Таблица координат поясов — системная
    (macOS, Linux) или из пакета tzdata (Windows: своей нет)."""
    zone = _zone_name()
    if not zone:
        return None
    for tab in (Path("/usr/share/zoneinfo/zone1970.tab"), Path("/usr/share/zoneinfo/zone.tab"), *_tzdata_tabs()):
        try:
            for line in tab.read_text(encoding="utf-8").splitlines():
                parts = line.split("\t")
                if len(parts) >= 3 and not line.startswith("#") and parts[2] == zone:
                    return _parse_coord(parts[1])
        except OSError:
            continue
    return None


def place(geolocation):
    """Координаты хоста по geolocation Vast («Quebec, CA», «, CN», «Japan, JP») или None."""
    g = (geolocation or "").strip()
    if g in REGIONS:
        return REGIONS[g]
    code = g.rsplit(",", 1)[-1].strip().upper()
    return COUNTRIES.get(code)


def km(a, b):
    """Расстояние по поверхности Земли между (широта, долгота) в км."""
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def estimate(geolocation, me=None):
    """Оценка задержки (мс, туда-обратно) до хоста или None — неизвестно, где я или где он."""
    me = me or home()
    there = place(geolocation)
    if not me or not there:
        return None
    return round(BASE_MS + km(me, there) * ROUTE / 100)
