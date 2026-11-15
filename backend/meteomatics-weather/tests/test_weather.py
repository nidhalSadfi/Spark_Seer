import unittest
from datetime import datetime, timezone
from pipeline.weather import build_output, build_url

AREA = {"bizerte": {"name": "Bizerte", "lat": 37.27, "lon": 9.87}}
START = datetime(2026, 11, 14, 14, tzinfo=timezone.utc)

class WeatherTests(unittest.TestCase):
    def test_url_requests_correct_time_and_parameters(self):
        url = build_url(START, AREA)
        self.assertIn("2026-11-14T14:00:00Z--2026-11-17T13:00:00Z:PT1H", url)
        self.assertIn("wind_gusts_10m_1h:ms", url)
        self.assertTrue(url.endswith("/37.27000,9.87000/json"))

    def test_processing_72_hour_windows(self):
        from datetime import timedelta
        vals = {"t_2m:C": 22, "relative_humidity_2m:p": 40,
                "wind_speed_10m:ms": 5, "wind_gusts_10m_1h:ms": 9,
                "precip_1h:mm": 1}
        data = []
        for param, value in vals.items():
            dates = [{"date": (START + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "value": value} for i in range(72)]
            data.append({"parameter": param, "coordinates": [{"lat":37.27,"lon":9.87,"dates":dates}]})
        out = build_output({"status":"OK", "data": data}, AREA, START, START)
        periods = out["areas"]["bizerte"]["daily"]
        self.assertEqual(len(periods), 3)
        self.assertEqual([p["sample_count"] for p in periods], [24,24,24])
        self.assertEqual([p["rainfall_total_mm"] for p in periods], [24,24,24])
        self.assertEqual(periods[0]["max_wind_speed_kmh"], 18.0)
        self.assertEqual(periods[0]["max_wind_gust_kmh"], 32.4)

if __name__ == "__main__":
    unittest.main()
