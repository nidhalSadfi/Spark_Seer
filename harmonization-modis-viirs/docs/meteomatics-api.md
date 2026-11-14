# Meteomatics weather API

What the [Meteomatics Weather API](https://www.meteomatics.com/en/api/getting-started/) can add to EmberLedger. Weather is optional context beside the hotspot record. It does not replace MODIS or VIIRS, and it is not on the critical path. See [project context](project-context.md) and [architecture](architecture.md).

Live checks below used HTTP Basic from `METEOMATICS_USER` and `METEOMATICS_PASSWORD` on 14 Nov 2026. All returned HTTP 200. None returned 429 or any other quota error.

## Auth

```
https://api.meteomatics.com/<validdatetime>/<parameters>/<location>/<format>
```

Times are UTC. A point is `latitude,longitude` (WGS84), so NW Tunisia at 9.0°E, 36.6°N is `36.6,9.0`.

Send HTTP Basic with those two environment variables. Keep them on the server: not in the URL, the query string, or the client bundle. The [FAQ](https://www.meteomatics.com/en/api/faq/) says the host expects Basic auth over HTTPS. These calls did that and got 200.

An optional token comes from `GET https://login.meteomatics.com/api/v1/token` with the same Basic header. The [token page](https://www.meteomatics.com/en/api/request/api-requests-oauth-authentification/) says it lasts 2 hours. These checks did not use a token.

## Parameters that matter

Surface fields for a fire-weather caption. The four in the first live call are confirmed on this account. The indices are from the catalog and were not requested.

| Name | Meaning | Checked live |
| --- | --- | --- |
| `t_2m:C` | Instantaneous temperature at 2 m, °C. Daily mean form: `t_mean_2m_24h:C`. | Yes |
| `precip_1h:mm` | Precipitation in the previous hour, mm. Right-aligned. `precip_24h:mm` is the daily sum. | Yes (`precip_1h`) |
| `wind_speed_10m:ms` | Instantaneous wind at 10 m, m/s. | Yes |
| `relative_humidity_2m:p` | Instantaneous relative humidity at 2 m, percent. | Yes |
| `forest_fire_warning:idx` | 0 to 1, from moisture in three soil layers. Above 0.5 is raised risk. | No |
| `fosberg_fire_weather_index:idx` | 0 to 100, from the weather at that time. Ignores the days before. | No |
| `drought_index:idx` | Percentile bins from −4 (dry) to +4 (humid). | No |

The [fire-index page](https://www.meteomatics.com/en/api/available-parameters/weather-parameter/forest-fire-risk-indices/) does not document the Canadian FWI set. There is also `anomaly_t_mean_2m_24h:C` (24-hour temperature against 1991–2020). The calendar comparison below is a month against other years of that month, computed in the app.

## Limits

Published rules, from the [appendix](https://www.meteomatics.com/en/api/appendix/) and [error codes](https://www.meteomatics.com/en/api/response/api-response-error-codes/). The account overrides the public free-tier card.

- One query: 10 parameters and 10 locations. The area option raises locations to 10,000 and is what a rectangle needs. This account has the area option. These calls used one point.
- GET URLs should stay under 2,000 characters. A query that runs longer than 5 minutes returns 408. `429` means the license limit was hit.
- The [packages page](https://www.meteomatics.com/en/weather-api/?ppc_keyword=weather+api) describes Free Basic as 500 queries a day, 15 parameters, a 10-day forecast, the past 24 hours, and 1-hour / 90 m fields. That is not this login.

`user_stats_json` on this login, read after the data calls (HTTP 200, empty error message):

| Limit | Value |
| --- | --- |
| Requests since UTC midnight | 4 used, hard limit 1,000. Soft limit unset. |
| Requests in the last 60 seconds | Hard limit 250. Soft limit unset. |
| Parallel requests | Hard limit 10. |
| History the account may ask for | 1900-01-01 through 2028-11-14. |
| Area requests | Enabled. |
| Mix label | `premium_all_1k`. |
| Access ends | 2026-11-22 (soft and hard deadline). |

No per-query rate-limit headers came back. Spatial resolution was not measured: no grid was requested. The service interpolated each point query to `36.6,9.0`. Advertised downscaling elsewhere is about 90 m; this account’s mix is the 1 km premium blend above. Time steps of one hour and of one month (`P1M`) both worked. A 2012 start is inside the entitlement, and the series below actually returned.

## Live calls

Point: 36.6°N, 9.0°E. Default blend (no `source=`). No secrets in the responses below.

**Recent hour.** `2026-11-13T12:00:00Z`, four parameters, one location. HTTP 200.

| Parameter | Value |
| --- | --- |
| `t_2m:C` | 25.8°C |
| `precip_1h:mm` | 0.0 mm |
| `wind_speed_10m:ms` | 4.7 m/s |
| `relative_humidity_2m:p` | 38.6% |

That hour was dry. A single `precip_1h` at noon is a weak fire caption; a day or a month is the useful sum.

**Monthly series.** Noon temperature on the 15th of each month, `2012-01-15` through `2026-10-15`, step `P1M`, parameter `t_2m:C` only. HTTP 200. 178 values. These are one noon sample per month, not a monthly mean. Coolest sample 6.3°C on 15 Jan 2017. Hottest samples 45.8°C on 15 Jul 2023 and 15 Jul 2026. Mean of those noon samples by calendar month: Jan 14.2, Feb 15.0, Mar 17.3, Apr 22.3, May 25.4, Jun 34.2, Jul 35.7, Aug 36.0, Sep 33.8, Oct 27.0, Nov 21.4, Dec 16.8°C. November and December are 2012–2025 only, because the series stops in October 2026.

**August anomaly.** Same point, `t_2m:C` at 12:00 UTC on 15 August, 2012 through 2026, one request. HTTP 200. The August values match the monthly series.

| Year | °C | Year | °C | Year | °C |
| --- | --- | --- | --- | --- | --- |
| 2012 | 36.8 | 2017 | 36.2 | 2022 | 42.1 |
| 2013 | 30.5 | 2018 | 30.8 | 2023 | 38.0 |
| 2014 | 32.5 | 2019 | 31.7 | 2024 | 40.1 |
| 2015 | 34.3 | 2020 | 38.0 | 2025 | 34.9 |
| 2016 | 35.4 | 2021 | 39.6 | 2026 | 38.3 |

Mean of 2012–2025 is 35.8°C. Median is 35.8°C. 15 August 2026 was 38.3°C, which is 2.5°C above that mean. The earlier noons span 30.5°C (2013) to 42.1°C (2022), so one afternoon moves the anomaly by more than the anomaly itself.

## One use in the app

After a month cell works on the burning calendar, a click can show that month’s weather against the same month in other years.

Use one point: the center of the selected area, or 36.6°N, 9.0°E if the area has no center yet. For the clicked month, take one year at a time from 2012 through the latest complete year, average the daily values inside the month (`t_mean_2m_24h:C`, and a sum of `precip_24h:mm`), and show the clicked year next to the mean of the other years. Cache it. The August noon series above is the proof that the account can serve that comparison back to 2012. It is not the number to put on screen: 15 August 2026 at noon was +2.5°C, and a different day in that month would have said something else.

The login ends 22 Nov 2026. That covers the Tunis demo. It is not a standing data source after that date.

## Do not build

- A fire-spread model, an ignition model, or any claim that a weather index is the burning record.
- A Fosberg, forest-fire, or drought layer, a weather grid, or a WMS map.
- This caption, or any other weather feature, before the per-cell, per-month conversion is reproduced and the calendar is on screen.
