# Project plan

Harmonized burning activity calendar from MODIS and VIIRS hotspots. Context: [project context](project-context.md).

## Phase 0: Data access and understanding (now)

- Confirm data access: public yearly archive files (MODIS, S-NPP, NOAA-20 to 2025) and the FIRMS area API for NOAA-21 and recent months.
- Exploratory analysis on a pilot region: columns, record counts per sensor and year, confidence and `type` distributions, day/night split, data gaps, the Terra-only years (2000 to mid-2002) and the MODIS/VIIRS overlap (2012 onward).
- Output: data dictionary and exploration report with figures.

## Phase 1: Harmonization method

- Filtering: keep vegetation fires (`type` = 0), drop static sources (volcanoes, gas flares, industrial); pick consistent confidence thresholds per sensor (MODIS numeric, VIIRS low/nominal/high).
- Common aggregation unit: grid cells (for example 0.1 to 0.5 degree) by day and month, so point density differences between 1 km and 375 m pixels become comparable.
- Calibration on the overlap years: fit MODIS-to-VIIRS conversion per cell, region, biome or land cover, month and day/night (ratio, regression or quantile mapping). Choose the reference unit (VIIRS-equivalent counts is the natural choice going forward).
- Validation: hold out overlap years, compare harmonized vs observed counts, check seasonality and trend preservation, and check for a step change at the sensor switch.
- Output: method note plus a harmonized gridded time series 2000 to present.

## Phase 2: Data pipeline and product

- Repeatable pipeline: download, clean, grid, harmonize, export.
- Storage suited to fast queries by area and date (Parquet or similar, partitioned by region and year); keep raw points for CSV export.
- Incremental updates from the API for the current month.

## Phase 3: Web application

- Area selection: draw on a map, enter lat/lon or bounding box, or pick an admin region.
- Date range selection.
- Burning calendar: year by month heatmap with an accessible color ramp (for example viridis or cividis).
- Indicators: monthly climatology, current month vs historical median, anomalies vs multi-year average, top years and months, total hotspots.
- Downloads: CSV of hotspots in the search, CSV of calendar counts, chart images.

## Phase 4: Validation, polish and submission

- Usability check against the audience (emergency managers, scientists, land managers).
- Accessibility check (color ramps, contrast, keyboard).
- Space Apps deliverables: demo video, project page text, slides.

## Suggested agents (workstreams)

- Data ingestion: owns the download tool, archive and API pulls, data inventory.
- Data exploration and harmonization: analysis notebooks, calibration, validation report.
- Pipeline and backend: gridded product, query API, CSV export.
- Web app: map, calendar, indicators, downloads.
- QA and submission: tests, accessibility review, demo video and write-up.

## Suggested reusable skills

- FIRMS data fetch: how to pull archive files and API data for an area and period, with the key handling and limits.
- Hotspot cleaning rules: the filtering and confidence thresholds we agree on, applied the same way everywhere.
- Harmonization check: standard validation figures and metrics to rerun whenever the method changes.
- Calendar QA: sanity checks on calendar outputs (totals match raw counts, no sensor step at 2012 or 2018, color ramp legend correct).
