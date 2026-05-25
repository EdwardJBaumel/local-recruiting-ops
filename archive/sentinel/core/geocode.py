"""
GEOCODE TABLE + HAVERSINE

Port of the JS lookup in sentinel-ui/src/JobMap.jsx so the backend
can apply the user's geographic-pin filter at match time without
hitting an external geocoding API. Substring-matched against the
job's `location` field — covers ~95% of real postings; ungeocodable
strings ("Remote", "Anywhere", "Worldwide") return None and are
intentionally given the benefit of the doubt by callers.

If you add a city here, mirror the change in JobMap.jsx so the UI
map and the backend filter stay in sync.
"""
from __future__ import annotations

import math


# Substring-keyed lookup. Lowercase, no diacritics. The same table
# lives in sentinel-ui/src/JobMap.jsx — keep them in sync.
CITY_COORDS: dict[str, tuple[float, float]] = {
    # North America
    "san francisco": (37.7749, -122.4194),
    "bay area": (37.7749, -122.4194),
    "palo alto": (37.4419, -122.143),
    "mountain view": (37.3861, -122.0839),
    "menlo park": (37.4529, -122.1817),
    "sunnyvale": (37.3688, -122.0363),
    "santa clara": (37.3541, -121.9552),
    "san jose": (37.3382, -121.8863),
    "cupertino": (37.323, -122.0322),
    "los angeles": (34.0522, -118.2437),
    "irvine": (33.6846, -117.8265),
    "santa monica": (34.0195, -118.4912),
    "long beach": (33.7701, -118.1937),
    "san diego": (32.7157, -117.1611),
    "oakland": (37.8044, -122.2712),
    "berkeley": (37.8715, -122.273),
    "fremont": (37.5483, -121.9886),
    "redwood city": (37.4852, -122.2364),
    "foster city": (37.5585, -122.2711),
    "san mateo": (37.5630, -122.3255),
    "south san francisco": (37.6547, -122.4077),
    "los altos": (37.3852, -122.1141),
    "milpitas": (37.4323, -121.8996),
    "burlingame": (37.5841, -122.3661),
    "hayward": (37.6688, -122.0808),
    "seattle": (47.6062, -122.3321),
    "redmond": (47.674, -122.1215),
    "bellevue": (47.6101, -122.2015),
    "portland": (45.5152, -122.6784),
    "denver": (39.7392, -104.9903),
    "boulder": (40.015, -105.2705),
    "austin": (30.2672, -97.7431),
    "dallas": (32.7767, -96.797),
    "houston": (29.7604, -95.3698),
    "atlanta": (33.749, -84.388),
    "chicago": (41.8781, -87.6298),
    "minneapolis": (44.9778, -93.265),
    "boston": (42.3601, -71.0589),
    "cambridge": (42.3736, -71.1097),
    "new york": (40.7128, -74.006),
    "nyc": (40.7128, -74.006),
    "brooklyn": (40.6782, -73.9442),
    "manhattan": (40.7831, -73.9712),
    "jersey city": (40.7178, -74.0431),
    "philadelphia": (39.9526, -75.1652),
    "washington": (38.9072, -77.0369),
    "arlington": (38.8816, -77.091),
    "raleigh": (35.7796, -78.6382),
    "miami": (25.7617, -80.1918),
    "toronto": (43.6532, -79.3832),
    "vancouver": (49.2827, -123.1207),
    "montreal": (45.5017, -73.5673),
    "remote, us": (39.8283, -98.5795),
    "remote us": (39.8283, -98.5795),
    # Europe
    "london": (51.5074, -0.1278),
    "manchester": (53.4808, -2.2426),
    "edinburgh": (55.9533, -3.1883),
    "dublin": (53.3498, -6.2603),
    "paris": (48.8566, 2.3522),
    "amsterdam": (52.3676, 4.9041),
    "berlin": (52.52, 13.405),
    "munich": (48.1351, 11.582),
    "zurich": (47.3769, 8.5417),
    "stockholm": (59.3293, 18.0686),
    "copenhagen": (55.6761, 12.5683),
    "barcelona": (41.3851, 2.1734),
    "madrid": (40.4168, -3.7038),
    "lisbon": (38.7223, -9.1393),
    "remote, eu": (50.1109, 8.6821),
    "remote eu": (50.1109, 8.6821),
    # Asia / APAC
    "tokyo": (35.6762, 139.6503),
    "singapore": (1.3521, 103.8198),
    "hong kong": (22.3193, 114.1694),
    "seoul": (37.5665, 126.978),
    "bangalore": (12.9716, 77.5946),
    "bengaluru": (12.9716, 77.5946),
    "hyderabad": (17.385, 78.4867),
    "mumbai": (19.076, 72.8777),
    "delhi": (28.6139, 77.209),
    "tel aviv": (32.0853, 34.7818),
    "sydney": (-33.8688, 151.2093),
    "melbourne": (-37.8136, 144.9631),
}

# Sorted longest-first so "san francisco" wins over "san" when both
# would match. Memoised at import time since the table is static.
_KEYS_BY_LENGTH = sorted(CITY_COORDS.keys(), key=len, reverse=True)


def locate(location: str | None) -> tuple[float, float] | None:
    """Substring-match a free-text location string against CITY_COORDS.
    Returns (lat, lon) or None if no city matches. Case-insensitive.
    """
    if not location or not isinstance(location, str):
        return None
    lc = location.lower()
    for k in _KEYS_BY_LENGTH:
        if k in lc:
            return CITY_COORDS[k]
    return None


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) points in km.
    Mirrors the JS implementation in JobMap.jsx so the client and
    server agree on whether a job is in-radius."""
    if a is None or b is None:
        return float("inf")
    R = 6371.0
    lat1, lon1 = a
    lat2, lon2 = b
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    s = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lon / 2) ** 2)
    return 2 * R * math.atan2(math.sqrt(s), math.sqrt(1 - s))


def within_any_pin(
    location: str | None,
    pins: list[tuple[float, float]],
    radius_km: float,
) -> tuple[bool, str]:
    """Test whether a location passes the geographic pin filter.

    Returns (passes, reason). Semantics:
      - Empty `pins` → passes (no filter active).
      - Ungeocodable location → passes (benefit of the doubt; the user
        can tighten via blocked_locations or work_modes).
      - Within radius of ANY pin → passes (union, not intersection).
      - Otherwise → fails.
    """
    if not pins:
        return True, ""
    coords = locate(location)
    if coords is None:
        return True, ""  # ungeocodable, give benefit of the doubt
    for i, p in enumerate(pins):
        if haversine_km(p, coords) <= radius_km:
            return True, ""
    return False, f"location outside all {len(pins)} pin radii"
