<task>
Write generator/generate.py and its helpers: a deterministic synthetic data generator for S4 (encounters),
S5 (facilities), S6 (stock snapshots) and ground_truth/outbreaks.csv. Follow brief §5.2 and
generator/schema.json exactly.
</task>

<requirements>
1. **CLI.** `python -m generator.generate --sf 0.25 --seed 42 [--start 2021-01-01 --end 2025-12-31]
   [--out data/raw]`. Add an `--incremental-days N --seed S` mode that writes a fresh N-day drop in the same
   layout, which is how we rehearse the hidden test drop.
2. **Size and determinism.** At SF=1, at least 25M encounters for 2021–2025 across 64 districts. The same
   seed gives identical output.
3. **Reference data.** Read data/reference/districts.csv, population_age_sex.csv and
   weather_daily.parquet, per docs/contracts.md. Another worker is producing these right now. Until they
   exist, use a fixture you generate under tests/fixtures/ (8 fake districts with plausible weather) so you
   can develop and test, and keep the real paths as the default.
4. **Realism, all required by the brief:**
   - Per-disease seasonality, with dengue peaking Sep–Nov (OpenDengue, research.md §1).
   - Dengue driven by rainfall and temperature with a 4–8 week lag; diarrhoea spiking after extreme rain.
   - Malaria concentrated in the Chittagong Hill Tracts districts; dengue concentrated in large cities.
   - Negative-binomial noise.
   - Reporting delay that is right-skewed over 0–21 days, with fewer reports on Fri/Sat and public holidays.
   - Age and sex drawn from S2.
   - A severity cascade: lab_confirmed, admitted, outcome.
5. **Outbreaks and artefacts:**
   - At least 30 injected outbreaks varying in size, duration and place, including one slow-growing and
     one nationwide novel-respiratory surge, all recorded only in ground_truth/outbreaks.csv.
   - At least one artefact: a facility silent for 3 weeks that then back-fills.
6. **Built-in defects:**
   - about 2% duplicated encounter_id;
   - about 0.5% orphan facility_id;
   - about 1% invalid age or sex;
   - some onset_date > report_date;
   - some bad ICD-10 codes.
7. **Facilities and stock.** At least 600 facilities, with district_code and beds. Stock is a daily JSON
   feed.
8. **Speed.** Vectorise with numpy per district/year partition and write in parallel chunks. Report the
   wall time at SF=0.05 and an extrapolation to SF=1, against the 45-minute total budget.
9. **Tests** in tests/test_generator.py, at a tiny SF:
   - the schema matches schema.json;
   - determinism;
   - the defect rates fall within tolerance;
   - the outbreak file has at least 30 rows;
   - the seasonality peak months are right.
</requirements>

<decisions>
Already decided: numpy for generation, and the output layout in schema.json.
Your call: the delay distribution's exact family, the holiday list, and the shapes of the outbreak curves.
</decisions>
