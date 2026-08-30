import datetime as dt
import unittest

import king_mountain_bot as bot


class BotTests(unittest.TestCase):
    def test_wind_round_trip(self):
        for speed, direction in ((10, 0), (18, 90), (24, 225), (7, 359)):
            u, v = bot.wind_components(speed, direction)
            actual_speed, actual_direction = bot.components_to_wind(u, v)
            self.assertAlmostEqual(actual_speed, speed, places=6)
            self.assertLess(bot.angular_distance(actual_direction, direction), 0.001)

    def test_interpolated_wind(self):
        row = {
            "__units__": {
                "geopotential_height_850hPa": "m",
                "geopotential_height_700hPa": "m",
            },
            "geopotential_height_850hPa": 1500,
            "wind_speed_850hPa": 10,
            "wind_direction_850hPa": 270,
            "geopotential_height_700hPa": 3000,
            "wind_speed_700hPa": 20,
            "wind_direction_700hPa": 270,
        }
        speed, direction = bot.interpolated_wind(row, 7381)
        self.assertGreater(speed, 10)
        self.assertLess(speed, 20)
        self.assertLess(bot.angular_distance(direction, 270), 0.001)

    def test_interpolated_wind_accepts_feet(self):
        row = {
            "__units__": {
                "geopotential_height_850hPa": "ft",
                "geopotential_height_700hPa": "ft",
            },
            "geopotential_height_850hPa": 5000,
            "wind_speed_850hPa": 10,
            "wind_direction_850hPa": 270,
            "geopotential_height_700hPa": 10000,
            "wind_speed_700hPa": 20,
            "wind_direction_700hPa": 270,
        }
        speed, direction = bot.interpolated_wind(row, 7500)
        self.assertAlmostEqual(speed, 15, places=6)
        self.assertLess(bot.angular_distance(direction, 270), 0.001)

    def test_height_units(self):
        self.assertAlmostEqual(bot.height_ft({"boundary_layer_height": 1000, "__units__": {"boundary_layer_height": "m"}}, "boundary_layer_height"), 3280.84)
        self.assertEqual(bot.height_ft({"boundary_layer_height": 1000, "__units__": {"boundary_layer_height": "ft"}}, "boundary_layer_height"), 1000)

    def test_message_chunk_limit(self):
        # Telegram's text limit is 4096; the sender deliberately uses 4000-char chunks.
        sample = ("forecast line\n" * 700).strip()
        chunks = []
        remaining = sample
        while len(remaining) > 4000:
            split_at = remaining.rfind("\n", 0, 4000)
            if split_at < 1:
                split_at = 4000
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")
        chunks.append(remaining)
        self.assertTrue(all(len(chunk) <= 4000 for chunk in chunks))

    def test_event_window(self):
        self.assertLessEqual(bot.EVENT_START, dt.date(2026, 8, 30))
        self.assertEqual(bot.EVENT_END, dt.date(2026, 9, 9))

    def test_compass(self):
        self.assertEqual(bot.compass(0), "N")
        self.assertEqual(bot.compass(270), "W")

    def test_forecast_command(self):
        self.assertTrue(bot.is_forecast_command("/forecast"))
        self.assertTrue(bot.is_forecast_command("/forecast@TeamWAKingCampForecastBot"))
        self.assertTrue(bot.is_forecast_command("/FORECAST please"))
        self.assertFalse(bot.is_forecast_command("forecast"))
        self.assertFalse(bot.is_forecast_command("/chatid"))

    def test_thermal_snapshot(self):
        row = {
            "time": "2026-08-30T14:00",
            "__units__": {
                "boundary_layer_height": "ft",
                "geopotential_height_850hPa": "ft",
            },
            "boundary_layer_height": 5000,
            "shortwave_radiation": 700,
            "cape": 0,
            "temperature_2m": 75,
            "dew_point_2m": 40,
            "cloud_cover_low": 10,
            "cloud_cover_mid": 10,
            "wind_speed_10m": 8,
            "wind_direction_10m": 250,
            "wind_gusts_10m": 14,
            "wind_speed_850hPa": 12,
            "wind_direction_850hPa": 270,
            "geopotential_height_850hPa": 7400,
            "precipitation_probability": 5,
        }
        detail = bot.thermal_snapshot(row)
        self.assertEqual(detail["hour"], 14)
        self.assertEqual(detail["usable_top_ft"], 10500)
        self.assertEqual(detail["surface_gust_mph"], 14)
        self.assertGreater(detail["lift_ms"], 1)


if __name__ == "__main__":
    unittest.main()
