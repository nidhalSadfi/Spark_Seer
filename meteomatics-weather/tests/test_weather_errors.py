import os
import unittest
from unittest.mock import Mock, patch

import requests

from pipeline.weather import get_forecast, build_output, ensure_usable_forecast


@patch.dict(os.environ, {"METEOMATICS_USER": "test-user", "METEOMATICS_PASSWORD": "test-secret"})
class ProviderErrorTests(unittest.TestCase):
    def test_unavailable_legacy_forecast_cannot_replace_good_cache(self):
        from datetime import datetime, timezone
        output = build_output({"status": "OK", "data": []},
                              {"bizerte": {"lat": 37.27, "lon": 9.87}},
                              datetime(2026, 11, 14, 15, tzinfo=timezone.utc))
        with self.assertRaisesRegex(RuntimeError, "previous cache preserved"):
            ensure_usable_forecast(output)

    @patch("pipeline.weather.time.sleep")
    @patch("pipeline.weather.requests.get")
    def test_rate_limit_retries_with_provider_delay(self, get, sleep):
        limited = Mock(status_code=429, headers={"Retry-After": "2"})
        success = Mock(status_code=200, headers={})
        success.json.return_value = {"status": "OK", "data": []}
        get.side_effect = [limited, success]
        self.assertEqual(get_forecast("https://api.meteomatics.com/test")["status"], "OK")
        sleep.assert_called_once_with(2)

    @patch("pipeline.weather.time.sleep")
    @patch("pipeline.weather.requests.get")
    def test_network_retries_bounded_and_redacted(self, get, sleep):
        get.side_effect = requests.ConnectionError("test-secret")
        with self.assertRaises(RuntimeError) as caught:
            get_forecast("https://api.meteomatics.com/test")
        self.assertEqual(get.call_count, 3)
        self.assertNotIn("test-secret", str(caught.exception))

    @patch("pipeline.weather.time.sleep")
    @patch("pipeline.weather.requests.get")
    def test_long_rate_limit_exits_without_hammering(self, get, sleep):
        get.return_value = Mock(status_code=429, headers={"Retry-After": "3600"})
        with self.assertRaises(RuntimeError):
            get_forecast("https://api.meteomatics.com/test")
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()

    @patch("pipeline.weather.requests.get")
    def test_auth_failure_has_safe_message(self, get):
        response = Mock(status_code=401, headers={})
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        get.return_value = response
        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            get_forecast("https://api.meteomatics.com/test")
        self.assertEqual(get.call_count, 1)
