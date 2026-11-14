# Project context

NASA Space Apps challenge: build a web application with a harmonized burning activity calendar based on active fire hotspots.

## Goal

- Harmonize MODIS (Terra/Aqua, 2000 onward) and VIIRS (S-NPP 2012, NOAA-20 2018, NOAA-21 2023 onward) FIRMS hotspots into one consistent time series, so the sensor of origin no longer matters and patterns, trends and anomalies become the focus.
- Web app takes the harmonized data as input: user selects an area of interest (draw on a map or enter lat/lon) and a date range, then views and downloads analytics.

## Audience

Early warning and emergency responders, scientists, land managers, firefighters.

## Required and suggested outputs

- Burning activity calendar: hotspot counts by month and year, early 2000s to present, with color ramps showing similarities and differences across months.
- Which periods of the year see more fires, which years had more burning, how a current fire or month compares with previous ones.
- Current month vs historical median; anomalies against multi-year averages.
- Number of fires detected.
- Downloadable CSV of hotspots found in a search.
- Accessible color ramps; interactive map.

## Why harmonization is needed

MODIS (1 km) and VIIRS (375 m) differ in spatial resolution, sensitivity and overpass schedule, so raw counts are not comparable. MODIS is near end of life; VIIRS provides continuity and detail.

## Data sources

- NASA FIRMS archive downloads, public yearly per-country files, and the FIRMS API (MAP_KEY stored as Cursor secret `FIRMS_MAP_KEY_1`). See [FIRMS download findings](firms-download-requests.md).

## Decisions

- 2026-11-14: FIRMS area API (bounding box, clipped locally) is the main source for all four sensors, verified live; archive download requests are not needed. Yearly archive files are an acceptable shortcut for MODIS 2018 onward, S-NPP and NOAA-20, but not MODIS 2012-2017 (version and `type` differ from the API).
- 2026-11-14: The team plan is EmberLedger for NASA Space Apps Tunis. Demo region is Tunisia and Algeria north of 34°N (Saharan gas flares fail the vegetation filter south of that line). Sensors for the demo are MODIS and Suomi NPP. Their reported proof is 72% to 13.7% to 11.5% error on hidden 2020–2021. Submit target is Sunday 14:30 Tunis time, pitch at 17:00.
- 2026-11-14: Agents on this Project use Grok and Composer only.
