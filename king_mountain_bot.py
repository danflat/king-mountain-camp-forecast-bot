#!/usr/bin/env python3
"""Temporary King Mountain paragliding briefing bot.

Runs on the Python standard library only. It fuses the three public Ecowitt
stations with Open-Meteo pressure-level guidance and NWS alerts/forecast text,
then posts a concise morning briefing to Telegram.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable
from zoneinfo import ZoneInfo


VERSION = "1.1.0"
LOCAL_TZ = ZoneInfo("America/Boise")
EVENT_START = dt.date(2026, 8, 30)
EVENT_END = dt.date(2026, 9, 9)

LZ_LAT = 43.7630556
LZ_LON = -113.3438889
LZ_ELEV_FT = 5500
LAUNCH_ELEV_FT = 7400
RIDGE_ELEV_FT = 10500

ECOWITT_STATIONS = (
    {"label": "King launch", "authorize": "YADDYT", "device_name": "King mountain"},
    {"label": "Coyote", "authorize": "KUX45J", "device_name": "Coyote"},
    {"label": "Glider Park LZ", "authorize": "FTPFNF", "device_name": "Hoodoobus"},
)

PRESSURE_LEVELS = (850, 800, 700, 600, 500)
OPEN_METEO_HOURLY = (
    "temperature_2m",
    "dew_point_2m",
    "relative_humidity_2m",
    "precipitation_probability",
    "precipitation",
    "weather_code",
    "pressure_msl",
    "surface_pressure",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "visibility",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "cape",
    "lifted_index",
    "convective_inhibition",
    "boundary_layer_height",
    "freezing_level_height",
    "shortwave_radiation",
) + tuple(
    f"{variable}_{level}hPa"
    for level in PRESSURE_LEVELS
    for variable in (
        "temperature",
        "relative_humidity",
        "wind_speed",
        "wind_direction",
        "geopotential_height",
    )
)


@dataclass
class StationObservation:
    label: str
    source_name: str
    age_seconds: int | None
    temperature_f: float | None
    humidity_pct: float | None
    dewpoint_f: float | None
    wind_mph: float | None
    gust_mph: float | None
    wind_deg: float | None
    wind_compass: str | None
    pressure_trend_inhg: float | None
    pressure_trend_symbol: int | None


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def number(value: Any) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def request_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    attempts: int = 3,
    timeout: int = 25,
) -> dict[str, Any]:
    if params:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{url}{'&' if '?' in url else '?'}{query}"
    data = None
    final_headers = {
        "User-Agent": os.getenv(
            "NWS_USER_AGENT", "KingCampForecastBot/1.0 (temporary event briefing)"
        ),
        "Accept": "application/json",
    }
    if headers:
        final_headers.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        final_headers["Content-Type"] = "application/json"

    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                url, data=data, headers=final_headers, method=method
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Request failed after {attempts} attempts: {url}: {last_error}")


def ecowitt_value(payload: dict[str, Any], section: str, key: str) -> Any:
    return (
        payload.get("data", {})
        .get(section, {})
        .get("data", {})
        .get(key, {})
        .get("value")
    )


def fetch_ecowitt_station(config: dict[str, str]) -> StationObservation:
    common_headers = {
        "Accept-EcowittLang": "en",
        "X-Requested-With": "XMLHttpRequest",
    }
    authorize = config["authorize"]
    listing = request_json(
        "https://www.ecowitt.net/index/get_device_list",
        params={"authorize": authorize},
        headers=common_headers,
    )
    devices = listing.get("list", [])
    wanted = config["device_name"].casefold()
    selected = next(
        (device for device in devices if str(device.get("name", "")).casefold() == wanted),
        devices[0] if len(devices) == 1 else None,
    )
    if not selected:
        raise RuntimeError(f"Ecowitt device {config['device_name']!r} not found")

    payload = request_json(
        "https://www.ecowitt.net/index/home",
        params={"authorize": authorize, "device_id": selected["device_id"]},
        headers=common_headers,
    )
    if str(payload.get("errcode")) != "0":
        raise RuntimeError(f"Ecowitt returned {payload.get('errmsg', 'an error')}")

    trend = (
        payload.get("data", {})
        .get("pressure", {})
        .get("data", {})
        .get("baromrelin_increment", {})
    )
    age = number(payload.get("timespan"))
    return StationObservation(
        label=config["label"],
        source_name=str(selected.get("name", config["device_name"])),
        age_seconds=int(age) if age is not None else None,
        temperature_f=number(ecowitt_value(payload, "temp", "tempf")),
        humidity_pct=number(ecowitt_value(payload, "temp", "humidity")),
        dewpoint_f=number(ecowitt_value(payload, "temp", "drew_temp")),
        wind_mph=number(ecowitt_value(payload, "wind", "windspeedmph")),
        gust_mph=number(ecowitt_value(payload, "wind", "windgustmph")),
        wind_deg=number(ecowitt_value(payload, "wind", "winddir")),
        wind_compass=(
            payload.get("data", {})
            .get("wind", {})
            .get("data", {})
            .get("winddir", {})
            .get("direction")
        ),
        pressure_trend_inhg=number(trend.get("value")),
        pressure_trend_symbol=int(trend["symbol"]) if trend.get("symbol") is not None else None,
    )


def fetch_all_stations() -> tuple[list[StationObservation], list[str]]:
    observations: list[StationObservation] = []
    errors: list[str] = []
    for config in ECOWITT_STATIONS:
        try:
            observations.append(fetch_ecowitt_station(config))
        except Exception as exc:  # Keep the briefing alive if one mountain station drops.
            errors.append(f"{config['label']}: {exc}")
    return observations, errors


def fetch_open_meteo(now: dt.datetime) -> dict[str, Any]:
    days_needed = max(1, (EVENT_END - now.date()).days + 1)
    return request_json(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": LZ_LAT,
            "longitude": LZ_LON,
            "timezone": "America/Boise",
            "forecast_days": min(16, days_needed),
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "hourly": ",".join(OPEN_METEO_HOURLY),
        },
    )


def hourly_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    hourly = payload.get("hourly", {})
    units = payload.get("hourly_units", {})
    times = hourly.get("time", [])
    rows: list[dict[str, Any]] = []
    for index, stamp in enumerate(times):
        row: dict[str, Any] = {"time": stamp, "__units__": units}
        for key, values in hourly.items():
            if key == "time" or not isinstance(values, list):
                continue
            row[key] = values[index] if index < len(values) else None
        rows.append(row)
    return rows


def fetch_nws() -> tuple[str | None, list[str], list[str]]:
    errors: list[str] = []
    synopsis: str | None = None
    alerts: list[str] = []
    try:
        point = request_json(f"https://api.weather.gov/points/{LZ_LAT:.4f},{LZ_LON:.4f}")
        forecast_url = point.get("properties", {}).get("forecast")
        if forecast_url:
            forecast = request_json(forecast_url)
            periods = forecast.get("properties", {}).get("periods", [])
            if periods:
                first = next((period for period in periods if period.get("isDaytime")), periods[0])
                synopsis = str(first.get("detailedForecast") or first.get("shortForecast") or "").strip()
    except Exception as exc:
        errors.append(f"NWS forecast: {exc}")
    try:
        active = request_json(
            "https://api.weather.gov/alerts/active",
            params={"point": f"{LZ_LAT},{LZ_LON}"},
        )
        for feature in active.get("features", []):
            properties = feature.get("properties", {})
            event = str(properties.get("event") or "Weather alert")
            headline = str(properties.get("headline") or "").strip()
            alerts.append(headline or event)
    except Exception as exc:
        errors.append(f"NWS alerts: {exc}")
    return synopsis, alerts, errors


def compass(degrees: float | None) -> str:
    if degrees is None:
        return "?"
    names = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    return names[int((degrees % 360) / 22.5 + 0.5) % 16]


def angular_distance(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def wind_components(speed: float, direction_from_deg: float) -> tuple[float, float]:
    radians = math.radians(direction_from_deg)
    return -speed * math.sin(radians), -speed * math.cos(radians)


def components_to_wind(u: float, v: float) -> tuple[float, float]:
    speed = math.hypot(u, v)
    direction = (math.degrees(math.atan2(-u, -v)) + 360) % 360
    return speed, direction


def interpolated_wind(row: dict[str, Any], target_ft: float) -> tuple[float, float] | None:
    points: list[tuple[float, float, float]] = []
    for level in PRESSURE_LEVELS:
        height_m = number(row.get(f"geopotential_height_{level}hPa"))
        speed = number(row.get(f"wind_speed_{level}hPa"))
        direction = number(row.get(f"wind_direction_{level}hPa"))
        if height_m is None or speed is None or direction is None:
            continue
        unit = row.get("__units__", {}).get(f"geopotential_height_{level}hPa", "m")
        height_ft = height_m if unit == "ft" else height_m * 3.28084
        u, v = wind_components(speed, direction)
        points.append((height_ft, u, v))
    if not points:
        return None
    points.sort()
    if target_ft <= points[0][0]:
        return components_to_wind(points[0][1], points[0][2])
    if target_ft >= points[-1][0]:
        return components_to_wind(points[-1][1], points[-1][2])
    for lower, upper in zip(points, points[1:]):
        if lower[0] <= target_ft <= upper[0]:
            fraction = (target_ft - lower[0]) / (upper[0] - lower[0])
            u = lower[1] + fraction * (upper[1] - lower[1])
            v = lower[2] + fraction * (upper[2] - lower[2])
            return components_to_wind(u, v)
    return None


def closest_hour(rows: list[dict[str, Any]], hour: int) -> dict[str, Any]:
    return min(rows, key=lambda row: abs(int(str(row["time"])[11:13]) - hour))


def maximum(rows: Iterable[dict[str, Any]], key: str, default: float = 0.0) -> float:
    values = [number(row.get(key)) for row in rows]
    present = [value for value in values if value is not None]
    return max(present) if present else default


def mean(rows: Iterable[dict[str, Any]], key: str, default: float = 0.0) -> float:
    values = [number(row.get(key)) for row in rows]
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else default


def height_ft(row: dict[str, Any], key: str) -> float | None:
    value = number(row.get(key))
    if value is None:
        return None
    unit = row.get("__units__", {}).get(key, "m")
    return value if unit == "ft" else value * 3.28084


def maximum_height_ft(rows: Iterable[dict[str, Any]], key: str) -> float:
    values = [height_ft(row, key) for row in rows]
    present = [value for value in values if value is not None]
    return max(present) if present else 0.0


def thermal_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """Return compact, hour-specific soaring details for the daily timeline."""
    solar = number(row.get("shortwave_radiation")) or 0.0
    cape = number(row.get("cape")) or 0.0
    blh_ft = height_ft(row, "boundary_layer_height") or 0.0
    temperature = number(row.get("temperature_2m"))
    dewpoint = number(row.get("dew_point_2m"))
    cloud_low = number(row.get("cloud_cover_low")) or 0.0
    cloud_mid = number(row.get("cloud_cover_mid")) or 0.0
    cloudbase_ft = None
    if temperature is not None and dewpoint is not None:
        cloudbase_ft = LZ_ELEV_FT + max(0.0, temperature - dewpoint) * 222.0
    mixing_top_ft = LZ_ELEV_FT + blh_ft
    blue = cloud_low < 35 and cloud_mid < 40
    usable_top_ft = mixing_top_ft
    if cloudbase_ft is not None and not blue:
        usable_top_ft = min(mixing_top_ft, cloudbase_ft)
    lift_ms = clamp(
        0.45 + solar / 430.0 + (blh_ft / 3.28084) / 2800.0 + cape / 1600.0,
        0.4,
        5.5,
    )
    surface_speed = number(row.get("wind_speed_10m"))
    surface_direction = number(row.get("wind_direction_10m"))
    surface_wind = None
    if surface_speed is not None and surface_direction is not None:
        surface_wind = (surface_speed, surface_direction)
    return {
        "hour": int(str(row["time"])[11:13]),
        "temperature_f": temperature,
        "surface_wind": surface_wind,
        "surface_gust_mph": number(row.get("wind_gusts_10m")),
        "launch_wind": interpolated_wind(row, LAUNCH_ELEV_FT),
        "lift_ms": lift_ms,
        "usable_top_ft": usable_top_ft,
        "precip_pct": number(row.get("precipitation_probability")) or 0.0,
    }


def hour_score(row: dict[str, Any]) -> float:
    solar = number(row.get("shortwave_radiation")) or 0.0
    precip = number(row.get("precipitation_probability")) or 0.0
    gust = number(row.get("wind_gusts_10m")) or 0.0
    launch = interpolated_wind(row, LAUNCH_ELEV_FT)
    launch_speed, launch_dir = launch if launch else (0.0, 270.0)
    score = 45.0
    score += clamp((solar - 250) / 18, -18, 26)
    score -= max(0.0, precip - 15) * 0.65
    score -= max(0.0, gust - 22) * 2.0
    score -= max(0.0, launch_speed - 22) * 2.4
    if 45 <= launch_dir <= 135:  # Easterly component: tailwind for west-facing King launch.
        score -= 24
    elif angular_distance(launch_dir, 270) <= 60:
        score += 8
    weather_code = int(number(row.get("weather_code")) or 0)
    if weather_code >= 95:
        score -= 45
    return score


def phase_for_day(day_rows: list[dict[str, Any]], hazards: list[str]) -> str:
    morning = closest_hour(day_rows, 6)
    evening = closest_hour(day_rows, 18)
    p0 = number(morning.get("pressure_msl")) or 0.0
    p1 = number(evening.get("pressure_msl")) or p0
    trend = p1 - p0
    ridge_wind = interpolated_wind(closest_hour(day_rows, 14), RIDGE_ELEV_FT)
    ridge_dir = ridge_wind[1] if ridge_wind else 270.0
    if any("thunder" in hazard.lower() or "rain" in hazard.lower() for hazard in hazards):
        return "storm phase"
    if trend >= 2.5 and 285 <= ridge_dir <= 360:
        return "post-frontal"
    if trend >= 1.3:
        return "early ridge-build"
    if trend <= -1.5:
        return "between-fronts transition"
    if p1 >= 1021:
        return "mature ridge-build"
    return "stagnant / weak-gradient"


def summarize_day(day: dt.date, all_rows: list[dict[str, Any]], lead_days: int) -> dict[str, Any]:
    prefix = day.isoformat()
    day_rows = [row for row in all_rows if str(row.get("time", "")).startswith(prefix)]
    if not day_rows:
        raise RuntimeError(f"No model rows available for {prefix}")
    thermal_rows = [row for row in day_rows if 10 <= int(str(row["time"])[11:13]) <= 18]
    window_rows = [row for row in day_rows if 10 <= int(str(row["time"])[11:13]) <= 16]
    peak_row = max(thermal_rows, key=lambda row: number(row.get("shortwave_radiation")) or 0.0)
    best_row = max(window_rows, key=hour_score)
    best_hour = int(str(best_row["time"])[11:13])

    solar = maximum(thermal_rows, "shortwave_radiation")
    cape = maximum(thermal_rows, "cape")
    precip = maximum(thermal_rows, "precipitation_probability")
    gust = maximum(thermal_rows, "wind_gusts_10m")
    blh_ft = maximum_height_ft(thermal_rows, "boundary_layer_height")
    cloud_low = maximum(thermal_rows, "cloud_cover_low")
    cloud_mid = maximum(thermal_rows, "cloud_cover_mid")
    rh700 = maximum(thermal_rows, "relative_humidity_700hPa")
    temp = number(peak_row.get("temperature_2m"))
    dewpoint = number(peak_row.get("dew_point_2m"))
    cloudbase_ft = None
    if temp is not None and dewpoint is not None:
        cloudbase_ft = LZ_ELEV_FT + max(0.0, temp - dewpoint) * 222.0
    mixing_top_ft = LZ_ELEV_FT + blh_ft
    blue_day = cloud_low < 35 and cloud_mid < 40
    usable_top_ft = mixing_top_ft
    if cloudbase_ft is not None and not blue_day:
        usable_top_ft = min(mixing_top_ft, cloudbase_ft)

    blh_m = blh_ft / 3.28084
    lift_ms = clamp(0.45 + solar / 430.0 + blh_m / 2800.0 + cape / 1600.0, 0.4, 5.5)
    check_row = closest_hour(day_rows, 14)
    winds = {
        altitude: interpolated_wind(check_row, altitude)
        for altitude in (LAUNCH_ELEV_FT, RIDGE_ELEV_FT, 14000, 18000)
    }
    launch_wind = winds[LAUNCH_ELEV_FT]
    ridge_wind = winds[RIDGE_ELEV_FT]
    fourteen_wind = winds[14000]

    hazards: list[str] = []
    weather_codes = [int(number(row.get("weather_code")) or 0) for row in thermal_rows]
    if any(code >= 95 for code in weather_codes) or (cape >= 700 and precip >= 30):
        hazards.append("thunder / outflow risk")
    elif precip >= 45:
        hazards.append("rain interruptions")
    if cape >= 900 and (rh700 >= 55 or cloud_mid >= 60):
        hazards.append("overdevelopment possible")
    if fourteen_wind and fourteen_wind[0] >= 35:
        hazards.append("wave/rotor setup aloft")
    if launch_wind and launch_wind[0] >= 25:
        hazards.append("strong launch-level flow")
    if launch_wind and 45 <= launch_wind[1] <= 135:
        hazards.append("modeled easterly/tailwind at King")
    if gust >= 30:
        hazards.append("strong valley gusts")

    potential_score = 92.0
    potential_score -= max(0.0, precip - 10) * 0.7
    potential_score -= max(0.0, gust - 22) * 2.0
    if launch_wind:
        potential_score -= max(0.0, launch_wind[0] - 20) * 2.2
    potential_score -= 20 * int("thunder / outflow risk" in hazards)
    potential_score -= 18 * int("wave/rotor setup aloft" in hazards)
    potential_score -= 10 * int("overdevelopment possible" in hazards)
    potential_score -= 12 * int("modeled easterly/tailwind at King" in hazards)
    potential_score = clamp(potential_score, 0, 100)
    potential = "HIGHER" if potential_score >= 72 else "MIXED" if potential_score >= 45 else "LOWER"

    xc_score = 35 + (usable_top_ft - 9000) / 160 + lift_ms * 5
    if ridge_wind:
        xc_score += 8 if 8 <= ridge_wind[0] <= 24 else -10 if ridge_wind[0] > 32 else 0
    xc_score -= 20 * int("thunder / outflow risk" in hazards)
    xc_score -= 18 * int("wave/rotor setup aloft" in hazards)
    xc_score = clamp(xc_score, 0, 100)
    confidence = clamp(92 - lead_days * 7 - (15 if hazards else 0), 35, 95)
    timeline = [thermal_snapshot(closest_hour(day_rows, hour)) for hour in (8, 10, 12, 14, 16, 18)]

    return {
        "date": day,
        "potential": potential,
        "potential_score": round(potential_score),
        "phase": phase_for_day(day_rows, hazards),
        "window": f"{max(10, best_hour - 1):02d}:00–{min(17, best_hour + 1):02d}:00",
        "lift_ms": lift_ms,
        "mixing_top_ft": mixing_top_ft,
        "usable_top_ft": usable_top_ft,
        "cloudbase_ft": cloudbase_ft,
        "blue_day": blue_day,
        "winds": winds,
        "precip_pct": precip,
        "cape": cape,
        "gust_mph": gust,
        "hazards": hazards,
        "xc_score": round(xc_score),
        "confidence": round(confidence),
        "timeline": timeline,
    }


def fmt_wind(wind: tuple[float, float] | None) -> str:
    if not wind:
        return "n/a"
    speed, direction = wind
    return f"{compass(direction)} {speed:.0f} mph"


def fmt_station(obs: StationObservation) -> str:
    freshness = "age ?"
    if obs.age_seconds is not None:
        freshness = f"{obs.age_seconds // 60}m old" if obs.age_seconds >= 60 else f"{obs.age_seconds}s old"
    wind = "wind n/a"
    if obs.wind_mph is not None:
        direction = obs.wind_compass or compass(obs.wind_deg)
        wind = f"{direction} {obs.wind_mph:.0f}"
        if obs.gust_mph is not None:
            wind += f" G{obs.gust_mph:.0f} mph"
    extras: list[str] = []
    if obs.temperature_f is not None:
        extras.append(f"{obs.temperature_f:.0f}°F")
    if obs.humidity_pct is not None:
        extras.append(f"RH {obs.humidity_pct:.0f}%")
    if obs.pressure_trend_inhg is not None:
        arrow = {1: "↑", -1: "↓", 0: "→"}.get(obs.pressure_trend_symbol, "→")
        extras.append(f"P {arrow}{obs.pressure_trend_inhg:.2f}/3h")
    suffix = f" • {' • '.join(extras)}" if extras else ""
    return f"• {obs.label}: {wind}{suffix} ({freshness})"


def fmt_hour(detail: dict[str, Any]) -> str:
    surface = "n/a"
    if detail["surface_wind"]:
        speed, direction = detail["surface_wind"]
        surface = f"{compass(direction)} {speed:.0f}"
    gust = detail["surface_gust_mph"]
    if gust is not None and detail["surface_wind"]:
        surface += f" G{gust:.0f}"
    if detail["surface_wind"]:
        surface += " mph"
    temperature = detail["temperature_f"]
    temp_text = f"{temperature:.0f}°F" if temperature is not None else "temp n/a"
    return (
        f"{detail['hour']:02d}:00  LZ {surface} · launch {fmt_wind(detail['launch_wind'])} · "
        f"{temp_text} · lift {detail['lift_ms']:.1f} · top {detail['usable_top_ft'] / 1000:.1f}k"
    )


def bottom_line(summary: dict[str, Any]) -> str:
    hazards = summary["hazards"]
    if not hazards:
        return (
            f"Thermals should build toward ~{summary['usable_top_ft'] / 1000:.1f}k MSL; "
            "no major model red flag."
        )
    return "Primary concern: " + ", ".join(hazards[:2]) + "."


def render_briefing(
    now: dt.datetime,
    summaries: list[dict[str, Any]],
    observations: list[StationObservation],
    station_errors: list[str],
    nws_synopsis: str | None,
    nws_alerts: list[str],
    nws_errors: list[str],
) -> str:
    today = summaries[0]
    date_label = f"{today['date']:%a %b} {today['date'].day}".upper()
    lines = [
        f"🪂 KING MOUNTAIN · {date_label}",
        f"{today['potential']} · {today['potential_score']}/100 · {today['phase']}",
        f"Best window: {today['window']} MDT",
        bottom_line(today),
        "",
        f"LIVE · {now.strftime('%H:%M %Z')}",
    ]
    lines.extend(fmt_station(obs) for obs in observations)
    if station_errors:
        lines.append(f"• Station gap: {', '.join(error.split(':', 1)[0] for error in station_errors)}")

    cloud_text = "blue / few lower clouds" if today["blue_day"] else (
        f"cloud base ~{today['cloudbase_ft'] / 1000:.1f}k MSL" if today["cloudbase_ft"] else "cloud base n/a"
    )
    lines.extend(
        [
            "",
            "TODAY",
            f"Lift {today['lift_ms']:.1f} m/s · top {today['usable_top_ft'] / 1000:.1f}k MSL · {cloud_text}",
            f"XC {today['xc_score']}/100 · rain {today['precip_pct']:.0f}% · CAPE {today['cape']:.0f} · confidence {today['confidence']}%",
            "",
            "HOURLY MODEL · MDT",
        ]
    )
    lines.extend(fmt_hour(detail) for detail in today["timeline"])
    lines.extend(
        [
            "",
            "ALOFT · 14:00",
            f"7.4k {fmt_wind(today['winds'][LAUNCH_ELEV_FT])} · 10.5k {fmt_wind(today['winds'][RIDGE_ELEV_FT])}",
            f"14k {fmt_wind(today['winds'][14000])} · 18k {fmt_wind(today['winds'][18000])}",
        ]
    )
    if today["hazards"]:
        lines.append("WATCH · " + "; ".join(today["hazards"]))
    else:
        lines.append("WATCH · normal King terrain/valley-wind variability; no major model flag")
    if nws_alerts:
        lines.append("NWS ALERT · " + " | ".join(nws_alerts[:2]))
    if nws_synopsis:
        compact = " ".join(nws_synopsis.split())
        if len(compact) > 170:
            compact = compact[:167].rstrip() + "…"
        lines.append("NWS · " + compact)

    if len(summaries) > 1:
        lines.extend(["", "NEXT"])
        for summary in summaries[1:4]:
            flags = ", ".join(summary["hazards"][:1]) if summary["hazards"] else "no major flag"
            lines.append(
                f"{summary['date']:%a} {summary['date'].month}/{summary['date'].day} · {summary['potential']} · "
                f"top {summary['usable_top_ft'] / 1000:.1f}k · 10.5k {fmt_wind(summary['winds'][RIDGE_ELEV_FT])} · {flags}"
            )

    if nws_errors and env_bool("SHOW_SOURCE_ERRORS"):
        lines.append("• Source note: " + "; ".join(nws_errors))
    lines.extend(
        [
            "",
            "/forecast · request a fresh briefing (allow ~10 min)",
            "",
            "Decision aid only—not a go/no-go call. Recheck sky, cycles, gust spread, radar and alerts.",
            "Data · Ecowitt · Open-Meteo · NWS",
            "Cross-check · XC Skies: https://www.xcskies.com/map",
            "Windy.com King: https://www.windy.com/43.763/-113.344",
            "Windy.app map: https://windy.app/map",
        ]
    )
    return "\n".join(lines)


def telegram_api(token: str, method: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    result = request_json(
        f"https://api.telegram.org/bot{token}/{method}",
        method="POST" if body is not None else "GET",
        body=body,
    )
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {result.get('description', 'unknown error')}")
    return result


def send_telegram(message: str, reply_to_message_id: int | None = None) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required to send")
    thread_id = os.getenv("TELEGRAM_THREAD_ID", "").strip()
    chunks: list[str] = []
    remaining = message
    while len(remaining) > 4000:
        split_at = remaining.rfind("\n", 0, 4000)
        if split_at < 1:
            split_at = 4000
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    chunks.append(remaining)
    for chunk in chunks:
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if thread_id:
            body["message_thread_id"] = int(thread_id)
        if reply_to_message_id is not None:
            body["reply_parameters"] = {"message_id": reply_to_message_id}
        telegram_api(token, "sendMessage", body)


def is_forecast_command(text: str) -> bool:
    first = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
    return first.split("@", 1)[0] == "/forecast"


def build_briefing(now: dt.datetime, forecast_date: dt.date) -> str:
    observations, station_errors = fetch_all_stations()
    forecast = fetch_open_meteo(now)
    rows = hourly_rows(forecast)
    summaries: list[dict[str, Any]] = []
    day = forecast_date
    while day <= EVENT_END:
        try:
            summaries.append(summarize_day(day, rows, (day - forecast_date).days))
        except RuntimeError:
            break
        day += dt.timedelta(days=1)
    if not summaries:
        raise RuntimeError("No forecast data available for the requested date")
    nws_synopsis, nws_alerts, nws_errors = fetch_nws()
    return render_briefing(
        now,
        summaries,
        observations,
        station_errors,
        nws_synopsis,
        nws_alerts,
        nws_errors,
    )


def set_bot_commands() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    telegram_api(
        token,
        "setMyCommands",
        {"commands": [{"command": "forecast", "description": "Current King Mountain forecast"}]},
    )
    print("Registered /forecast with Telegram.")
    return 0


def process_commands() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    updates = telegram_api(
        token,
        "getUpdates",
        {"timeout": 0, "allowed_updates": ["message"]},
    ).get("result", [])
    matching: list[dict[str, Any]] = []
    for update in updates:
        message = update.get("message") or {}
        if str((message.get("chat") or {}).get("id", "")) != chat_id:
            continue
        if is_forecast_command(str(message.get("text") or "")):
            matching.append(message)

    if matching:
        now = dt.datetime.now(LOCAL_TZ)
        if EVENT_START <= now.date() <= EVENT_END:
            briefing = build_briefing(now, now.date())
            for message in matching:
                send_telegram(briefing, message.get("message_id"))
        else:
            for message in matching:
                send_telegram("King Camp event forecasting is inactive outside Aug 30–Sep 9.", message.get("message_id"))

    if updates:
        last_update_id = max(int(update["update_id"]) for update in updates)
        telegram_api(token, "getUpdates", {"offset": last_update_id + 1, "timeout": 0})
    print(f"Processed {len(updates)} update(s); answered {len(matching)} forecast command(s).")
    return 0


def discover_chats() -> int:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN, add the bot to the group, send /chatid there, then retry.", file=sys.stderr)
        return 2
    updates = telegram_api(token, "getUpdates").get("result", [])
    found: dict[str, tuple[str, str, str | None]] = {}
    for update in updates:
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        if "id" not in chat:
            continue
        chat_id = str(chat["id"])
        title = str(chat.get("title") or chat.get("username") or chat.get("first_name") or "unnamed")
        kind = str(chat.get("type") or "unknown")
        thread_id = str(message.get("message_thread_id")) if message.get("message_thread_id") else None
        found[chat_id] = (title, kind, thread_id)
    if not found:
        print("No recent chats found. Send /chatid in Team WA King-Camp and run this again.")
        return 1
    print("Recent chats visible to the bot:")
    for chat_id, (title, kind, thread_id) in found.items():
        thread = f"  thread={thread_id}" if thread_id else ""
        print(f"  {title!r}  type={kind}  chat_id={chat_id}{thread}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the briefing without posting")
    parser.add_argument("--discover-chat", action="store_true", help="List recent Telegram chat IDs")
    parser.add_argument("--commands", action="store_true", help="Answer pending /forecast commands")
    parser.add_argument("--set-commands", action="store_true", help="Register the Telegram command menu")
    parser.add_argument("--date", type=dt.date.fromisoformat, help="Render a specific forecast date")
    parser.add_argument("--version", action="version", version=VERSION)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.discover_chat:
        return discover_chats()
    if args.commands:
        return process_commands()
    if args.set_commands:
        return set_bot_commands()
    now = dt.datetime.now(LOCAL_TZ)
    forecast_date = args.date or now.date()
    if forecast_date < EVENT_START or forecast_date > EVENT_END:
        print(f"Outside event window ({EVENT_START} through {EVENT_END}); nothing sent.")
        return 0

    message = build_briefing(now, forecast_date)
    if args.dry_run or env_bool("DRY_RUN"):
        print(message)
    else:
        send_telegram(message)
        print(f"Posted King Camp briefing for {forecast_date}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
