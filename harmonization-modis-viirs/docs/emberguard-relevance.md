# What in EmberGuard belongs in EmberLedger

## What EmberGuard is

EmberGuard AI is a NASA Space Apps Tunis 2026 web app for the same challenge as this Project: a harmonized burning-activity calendar from NASA FIRMS hotspots. The blueprint aims it at forest and land managers in northern Tunisia, starting with Bizerte, using MODIS and Suomi NPP VIIRS. Its method is a shared grid, monthly counts, and a regression that puts both sensors on one scale, plus a seasonal baseline. Around that calendar it adds a 72-hour fire-weather view, a monitoring shortlist, an AI Evidence Passport, and a historical replay. The later final report narrows the science: calibrate on Tunisia and Algeria north of 34°N, demo on Bizerte, use a 0.5° grid and a ratio per cell and month, fit on 2012–2019, and test on 2020–2021. That report claims the error on those hidden years falls from 72.1% with no conversion, to 13.7% with one ratio, to 11.5% with the per-area, per-month ratio. It also claims about 6,300 filtered MODIS detections in Tunisia over 25 years, about 137,000 VIIRS detections north of 34°N for 2012–2025, and that about half of the VIIRS “vegetation fire” rows in the two countries are Saharan gas flares. Those figures are claimed in the report. They were not re-checked for this note. The report itself calls the 11.5% a first run that must be reproduced before it goes on a slide.

## What matches EmberLedger

- The required product is one monthly burning calendar, with a raw-versus-harmonized view and a comparison of a month with the same month in other years.
- The geography matches once the final report is used: Tunisia and Algeria north of 34°N, because gas flares south of that line pass a vegetation filter. The blueprint’s Bizerte-only map is the part that report already widened.
- The demo sensors match: MODIS and Suomi NPP. NOAA-20 and NOAA-21 stay later.
- The conversion matches: scale MODIS toward a VIIRS count with a ratio that changes by 0.5° cell and by month, trained on 2012–2019 and checked on 2020–2021. The blueprint’s Ridge or Poisson model is the earlier idea. The final report already replaced it with this ratio.
- The claimed proof matches the figure EmberLedger still has to reproduce: 72% with no conversion, 13.7% with one ratio, 11.5% per area and month. Those numbers are claimed in the report, not re-checked.
- Weather matches as optional context beside the hotspot record, and only after the calendar works. Meteomatics is already checked for this Project through 22 Nov 2026.
- The weekend clock matches: aim to submit Sunday 14:30 Tunis time, pitch at 17:00.

## Conflicts and out of scope

- The name. This Project is EmberLedger. EmberGuard AI is the other draft. The final report still leaves the name open.
- Extra product surface. The blueprint’s five features, and the final report’s remaining Evidence Passport, 72-hour forecast, and team shortlist, sit outside EmberLedger’s path. That path is: reproduce the ratio, check the held-out years, build the monthly grid, serve area queries and a CSV, then the map and calendar.
- Fire spread and danger scoring. A fire-spread model, an ignition probability, a homemade Fire Weather Index, and the Fosberg, forest-fire, and drought layers are out. The final report already drops replay, a machine-learning danger model, and multi-agent AI. EmberLedger also leaves day/night factors, the Terra-only years, and the outage mask until the 11.5% figure is reproduced.
- Where the demo points. EmberLedger’s demo is the whole northern belt. EmberGuard still opens the pitch on a Bizerte box. Bizerte can be one area inside that belt. The report claims it is too thin to calibrate alone (about 6,300 filtered MODIS detections in Tunisia over 25 years; claimed, not re-checked).
- Where the rows come from. The final report builds on four downloaded country CSV files and cites MODIS version 6.2. EmberLedger’s decision is the FIRMS area API. Yearly archive files are a shortcut, and a poor one for MODIS 2012–2017, where version and `type` differ from the API.
- Grid and years in the blueprint only. A 20–25 km grid and a 2022–2024 overlap conflict with EmberLedger. The final report already moved those to 0.5° and to 2012–2019 / 2020–2021.
- Other counts in the report, claimed and not re-checked: a summer peak near 62,300 VIIRS detections in August and about 1,400 in December, “about 3.5× more hotspots” on VIIRS, and “Suomi NPP stopped on 2 Nov 2026.”

## This weekend

Keep the harmonized calendar, Tunisia and Algeria north of 34°N, MODIS and Suomi NPP, the 0.5° per-cell per-month ratio, the raw and harmonized toggle, and a same-month comparison that labels sparse cells. Keep the rule that a hotspot is a thermal detection. Reproduce the claimed 11.5% on the real files before 72%, 13.7%, or 11.5% appears in the app or the pitch.

Leave the EmberGuard name, the AI passport, the monitoring shortlist, historical replay, land cover, population, roads, NDVI, soil moisture, a computed EFFIS index, and any fire-spread or ignition model until that calendar works. A weather caption, if it is built at all, is the month-versus-other-years note already written for Meteomatics, after the conversion is on screen.

## Recommendation

Build EmberLedger’s calendar with the method the EmberGuard final report already shares: Tunisia and Algeria north of 34°N, MODIS and Suomi NPP, one ratio per 0.5° cell and month.
Treat 72%, 13.7%, 11.5%, and the detection counts in that report as claimed until this Project reproduces them.
Leave the EmberGuard name, the AI passport, the team shortlist, fire-spread, and replay until the calendar works.
Add weather only after that, as the month caption in the Meteomatics note.

Sources: `emberguard-blueprint.md`, `emberguard-final-report.html`, `project-context.md`, `architecture.md`, `meteomatics-api.md`
