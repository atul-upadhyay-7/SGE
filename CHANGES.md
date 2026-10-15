# CHANGES.md — Battery PDM Overhaul

> Every number in the tables below is produced by `scripts/make_report.py`
> from a CSV under `data/processed/`, and every command result is real
> output from `scripts/verify.sh`. Nothing here is typed by hand. Items
> that were not done or not verified are listed explicitly at the end.

---

## Step 1 — Verification harness

- Added `scripts/verify.sh`: clones the committed repository into a
  temporary directory, downloads and checksums the NASA dataset, runs
  `python run_pipeline.py --skip-train`, runs the test suite, then runs
  `scripts/check_numbers.py`. It exits non-zero if any step fails.
- Added `scripts/check_numbers.py`: parses the marked regions of
  `README.md` and `CHANGES.md` and fails if any number there is not
  found (within its printed rounding) in a CSV under `data/processed/`.
- Replaced `scripts/make_report.py` with a real builder that reads the
  measured CSVs and writes the marked regions; it fails if a required
  CSV is missing or empty.
- Removed `scripts/update_readme_results.py` and the unbacked numbers it
  used to leave in `README.md`.
- Replaced `tests/test_readme_numbers.py` with a test that runs
  `scripts/check_numbers.py`.

## Step 2 — Real data cleaning

- `src/data_cleaning.py` now writes `data/processed/cell_audit.csv` and
  `data/processed/nasa_ml_dataset_clean.csv` (the dataset every
  downstream stage reads).
- A discharge is kept only if its measured capacity is greater than zero
  and at least half the cell's median capacity. The SOH reference is the
  median of the first five valid discharges, not a single first cycle.
- `upward_jumps_gt3pp` counts SOH jumps greater than three percentage
  points, so genuine re-calibration is visible in the audit instead of
  being silently smoothed.
- Cells whose capacity trace is corrupted are excluded; cells with too
  little ageing to reach end of life are labelled `limited ageing, no
  RUL` and kept for SOH only.
- The pipeline stage order is now: `load_data → preprocessing →
  data_cleaning → data_quality → rul_trajectory → pack_evaluate →
  anomaly → benchmark → run_experiments → load_to_sqlite`. Downstream
  readers (`train_soh`, `train_rul`, `rul_trajectory`,
  `load_to_sqlite`, the pack bridge) read the cleaned dataset.

## Step 3 — Real experiment CSVs

- `src/run_experiments.py` now writes non-empty CSVs:
  `soh_full_discharge.csv` (duration × current formula),
  `soh_partial_discharge.csv` (window formula and window-only models),
  `ablation_features.csv` and `per_cell_results.csv` (feature-set
  ablation, leave-one-cell-out on G1), and `logo_results.csv`
  (leave-one-group-out on the surviving groups).

## Step 4 — RUL trajectory

- Reference cells for `src/rul_trajectory.py` are the audit-kept cells
  that actually reach the end-of-life threshold; limited-ageing cells
  are no longer pulled into the reference set.

## Step 5 — Dashboard

- Removed every hardcoded fallback from `dashboard/grafana_api.py`
  (82.5% SOH, 16.0 discharges, 3.04%/9.22% SOH error, 0.85 R², 0.29%
  Coulomb, 27.3 lifetime). Each stat tile reads the database; when a
  source table is missing the tile returns an explicit error row rather
  than a plausible-looking constant that cannot be told apart from a
  real value.
- Added real RUL prediction intervals. `src/rul_trajectory.py` now
  writes `data/processed/rul_prediction_bands.csv`: for each held-out
  cell and discharge, the 10th/50th/90th percentiles of the
  per-reference-cell remaining-life estimates, under the same
  leave-one-cell-out protocol the reported MAE is measured under. The
  dashboard plots these percentiles instead of `predicted_rul * 0.8` and
  `predicted_rul * 1.2`, which were a fixed ±20% fake.
- Measured that band's empirical coverage rather than assuming it. The
  spread across reference-cell curves is not a calibrated predictive
  interval: across the four held-out cells and their in-service
  discharges the 10th/90th band contains the real remaining life only
  48.3% of the time, not the ~80% a "10th-90th" label implies
  (B0005 93.8%, B0006 0.0%, B0007 17.5%, B0018 82.1%), while being
  ~21.6 discharges wide on average. It is a reference-spread band and
  is labelled as such, not a guarantee.
- `model_summary` is now computed from the result CSVs: held-out SOH MAE
  with its per-cell range, held-out R², the Coulomb baseline, the RUL MAE
  with its per-cell range and the fleet-lifetime baseline, the discharge
  count and the cell list.
- `stat_discharges_left` is the mean of each cell's latest in-service
  prediction, and its range is the mean of each cell's stored 10th/90th
  percentiles. It no longer averages the trajectory MAE and relabels it
  "discharges left".
- The `cell` dashboard variable is chained to `group` (`cells:$group`)
  and `/search` treats `cells:All` as no filter. Per-cell table panels
  now honour the cell/group selection instead of always showing every
  cell.
- Removed the stale result numbers (2.89%, 0.51%, 0.29%, 9.22%) from the
  dashboard's description panel; it points at the database-backed
  `Model Summary` table instead.
- `tests/test_grafana_api.py` now pins the band ordering
  (`p10 ≤ predicted ≤ p90`), the group→cell chaining, and the fact that
  the table panels obey the cell variable.

## Step 6 — Demo model

- Retrained the SOH and RUL models on the audited clean dataset. The
  committed models predated the cleaning pass, so the reported numbers
  were from the pre-cleaning data. `soh_xgb_demo` trains on the G1 cells
  minus the replayed cells (i.e. B0006) so the live replay is genuinely
  out of sample.
- README and CHANGES were regenerated from the retrained CSVs; the
  values moved (Coulomb baseline 0.29 → 0.73, nested XGBoost 2.59 →
  2.65, trajectory RUL mean MAE 20.4 → 20.5).

## Step 7 — Housekeeping

- README results tables are regenerated from CSVs by
  `scripts/make_report.py`. Stale hand-typed numbers were removed.

## Step 8 — Dashboard run under Docker

- `docker compose --profile demo up --build` was run against the full
  stack (Mosquitto, three SOH services, the pack bridge, the JSON API
  and Grafana). All six containers reach a running state and Grafana
  serves both provisioned dashboards at `http://localhost:3050`.
- The compose file now sets an explicit container `dns`. Without it the
  Docker embedded resolver forwards to the host's systemd-resolved stub
  (`127.0.0.53`), which is unreachable from a container, so Grafana
  exited on first boot with "connection refused" while fetching its
  JSON-datasource plugin.
- Verified end to end: Grafana's JSON datasource proxies to the API
  (`/api/datasources/proxy/1/` returns `OK`), and the panels read real
  database values through it — Model Summary SOH MAE 2.65%, R² 0.90,
  Coulomb 0.73%, RUL MAE 20.5 discharges; `stat_discharges_left` 10.8
  (10th-90th 7.0-14.1); `stat_latest_soh` 71.38; the per-cell SOH and
  RUL panels render for every audited cell. Refreshed screenshots of
  both dashboards are in `docs/battery_pdm_dashboard.png` and
  `docs/battery_pack_live.png`.

## Step 9 — Machine-checked report numbers

- `scripts/make_report.py` now renders every number in the marked
  regions as a `{{id}}` placeholder from
  `data/processed/report_numbers.json`, which it writes from the CSVs
  as a flat `{"<placeholder_id>": value}` mapping (floats rounded to
  10 decimals) plus the region templates under a reserved
  `_templates` key. The rendered README.md and CHANGES.md are
  byte-identical to before the change.
- `scripts/check_numbers.py` no longer matches quoted numbers against
  every numeric cell in every CSV. It now (a) re-renders each marked
  region from the JSON and fails on any difference, and (b) fails if
  a region contains a number that is not the rendering of a
  placeholder, so a number cannot reach the documents by being typed
  into them — not even by pasting it into both the document and the
  JSON template, which only rule (b) catches. A value invented
  directly in the JSON is caught one level up: `make_report.py`
  rewrites the JSON from the CSVs, so it shows up as a diff against
  the regenerated output in `scripts/verify.sh`. The checker also
  fails on values in the JSON that no template uses, and on
  templates whose markers are missing from the documents.
- The shared placeholder machinery lives in
  `scripts/report_render.py`, used by both the generator and the
  checker.
- `tests/test_readme_numbers.py` invokes the checker in-process on
  temporary copies of the documents and the JSON (via a new `--root`
  option) and asserts that it fails when one number in a region is
  bumped by one unit, when a random fake number is inserted, and
  when a number is pasted into both the document and the template
  (which only the bare-number rule can catch). Invoking the checker
  through `sys.executable` was dropped because some environments
  report a `sys.executable` that is not the interpreter running the
  tests, which made the old subprocess test pass vacuously there.
- `scripts/verify.sh` now runs `pytest -q` as an explicit step after
  the pipeline, and its "documents are in sync" check covers
  `data/processed/report_numbers.json` in addition to README.md and
  CHANGES.md.

---

## Final performance & baseline comparison

<!-- make_report:final -->
| Task | Method | Protocol | Score | Baseline | Unit |
|---|---|---|---|---|---|
| SOH | Coulomb formula | leave-one-cell-out (G1) | 0.73 | constant mean 9.28 | MAE % |
| SOH | XGBoost (nested) | nested leave-one-cell-out (G1) | 2.65 | constant mean 9.28 | MAE % |
| RUL | Trajectory model | leave-one-cell-out (G1) | 20.5 | fleet lifetime 34.3 | discharges |
<!-- make_report:final:end -->

---

## Verification output

Run with:

```bash
bash scripts/verify.sh
```

Full output is pasted below.

```
============================================================
verify.sh
  repository : /home/harsh/Documents/battery-pdm
  python     : Python 3.12.3
  workdir    : /tmp/tmp.E2obR3rh3P
============================================================

>>> cloning committed repository
    HEAD: 9d2dbc8

>>> downloading and checksumming the NASA dataset
=== NASA Battery Dataset Downloader & Verifier ===
B0005.mat: FAILED open or read
B0006.mat: FAILED open or read
B0007.mat: FAILED open or read
B0018.mat: FAILED open or read
B0025.mat: FAILED open or read
B0026.mat: FAILED open or read
B0027.mat: FAILED open or read
B0028.mat: FAILED open or read
B0029.mat: FAILED open or read
B0030.mat: FAILED open or read
B0031.mat: FAILED open or read
B0032.mat: FAILED open or read
B0033.mat: FAILED open or read
B0034.mat: FAILED open or read
B0036.mat: FAILED open or read
B0038.mat: FAILED open or read
B0039.mat: FAILED open or read
B0040.mat: FAILED open or read
B0041.mat: FAILED open or read
B0042.mat: FAILED open or read
B0043.mat: FAILED open or read
B0044.mat: FAILED open or read
B0045.mat: FAILED open or read
B0046.mat: FAILED open or read
B0047.mat: FAILED open or read
B0048.mat: FAILED open or read
B0049.mat: FAILED open or read
B0050.mat: FAILED open or read
B0051.mat: FAILED open or read
B0052.mat: FAILED open or read
B0053.mat: FAILED open or read
B0054.mat: FAILED open or read
B0055.mat: FAILED open or read
B0056.mat: FAILED open or read
Downloading dataset from https://phm-datasets.s3.amazonaws.com/NASA/5.+Battery+Data+Set.zip ...
  % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
                                 Dload  Upload   Total   Spent    Left  Speed

  0     0    0     0    0     0      0      0 --:--:-- --:--:-- --:--:--     0
  0     0    0     0    0     0      0      0 --:--:-- --:--:-- --:--:--     0
  0  199M    0     0    0     0      0      0 --:--:--  0:00:01 --:--:--     0
  0  199M    0  101k    0     0  43667      0  1:20:02  0:00:02  1:20:00 43656
  0  199M    0  390k    0     0   118k      0  0:28:49  0:00:03  0:28:46  118k
  0  199M    0  747k    0     0   174k      0  0:19:32  0:00:04  0:19:28  174k
  0  199M    0 1138k    0     0   213k      0  0:15:57  0:00:05  0:15:52  234k
  0  199M    0 1699k    0     0   270k      0  0:12:38  0:00:06  0:12:32  347k
  1  199M    1 2175k    0     0   298k      0  0:11:26  0:00:07  0:11:19  422k
  1  199M    1 2804k    0     0   337k      0  0:10:07  0:00:08  0:09:59  481k
  1  199M    1 3552k    0     0   382k      0  0:08:55  0:00:09  0:08:46  559k
  2  199M    2 4827k    0     0   469k      0  0:07:16  0:00:10  0:07:06  742k
  3  199M    3 6221k    0     0   551k      0  0:06:11  0:00:11  0:06:00  905k
  3  199M    3 8031k    0     0   653k      0  0:05:13  0:00:12  0:05:01 1172k
  4  199M    4 9871k    0     0   733k      0  0:04:39  0:00:13  0:04:26 1376k
  5  199M    5 11.6M    0     0   827k      0  0:04:07  0:00:14  0:03:53 1639k
  6  199M    6 13.6M    0     0   912k      0  0:03:44  0:00:15  0:03:29 1824k
  8  199M    8 16.2M    0     0  1018k      0  0:03:20  0:00:16  0:03:04 2075k
  9  199M    9 18.0M    0     0  1069k      0  0:03:11  0:00:17  0:02:54 2089k
  9  199M    9 19.8M    0     0  1109k      0  0:03:04  0:00:18  0:02:46 2153k
 10  199M   10 21.2M    0     0  1130k      0  0:03:01  0:00:19  0:02:42 2022k
 11  199M   11 23.3M    0     0  1173k      0  0:02:54  0:00:20  0:02:34 1964k
 12  199M   12 25.5M    0     0  1227k      0  0:02:46  0:00:21  0:02:25 1907k
 13  199M   13 27.5M    0     0  1264k      0  0:02:41  0:00:22  0:02:19 1939k
 14  199M   14 29.2M    0     0  1283k      0  0:02:39  0:00:23  0:02:16 1920k
 15  199M   15 31.5M    0     0  1331k      0  0:02:33  0:00:24  0:02:09 2101k
 16  199M   16 33.7M    0     0  1366k      0  0:02:29  0:00:25  0:02:04 2159k
 17  199M   17 35.5M    0     0  1386k      0  0:02:27  0:00:26  0:02:01 2058k
 18  199M   18 37.5M    0     0  1407k      0  0:02:25  0:00:27  0:01:58 2042k
 19  199M   19 39.7M    0     0  1439k      0  0:02:22  0:00:28  0:01:54 2167k
 20  199M   20 41.7M    0     0  1458k      0  0:02:20  0:00:29  0:01:51 2080k
 21  199M   21 43.7M    0     0  1479k      0  0:02:18  0:00:30  0:01:48 2049k
 22  199M   22 45.8M    0     0  1500k      0  0:02:16  0:00:31  0:01:45 2102k
 23  199M   23 47.4M    0     0  1504k      0  0:02:16  0:00:32  0:01:44 2035k
 24  199M   24 49.0M    0     0  1507k      0  0:02:15  0:00:33  0:01:42 1894k
 25  199M   25 50.9M    0     0  1522k      0  0:02:14  0:00:34  0:01:40 1896k
 26  199M   26 52.9M    0     0  1535k      0  0:02:13  0:00:35  0:01:38 1879k
 27  199M   27 54.7M    0     0  1543k      0  0:02:12  0:00:36  0:01:36 1811k
 28  199M   28 56.4M    0     0  1550k      0  0:02:12  0:00:37  0:01:35 1849k
 29  199M   29 58.6M    0     0  1567k      0  0:02:10  0:00:38  0:01:32 1962k
 30  199M   30 60.2M    0     0  1569k      0  0:02:10  0:00:39  0:01:31 1889k
 30  199M   30 61.9M    0     0  1575k      0  0:02:10  0:00:40  0:01:30 1853k
 32  199M   32 64.0M    0     0  1586k      0  0:02:09  0:00:41  0:01:28 1896k
 33  199M   33 66.0M    0     0  1599k      0  0:02:08  0:00:42  0:01:26 1958k
 33  199M   33 67.8M    0     0  1604k      0  0:02:07  0:00:43  0:01:24 1891k
 34  199M   34 69.7M    0     0  1613k      0  0:02:06  0:00:44  0:01:22 1961k
 36  199M   36 73.1M    0     0  1652k      0  0:02:03  0:00:45  0:01:18 2278k
 37  199M   37 74.9M    0     0  1657k      0  0:02:03  0:00:46  0:01:17 2252k
 38  199M   38 76.8M    0     0  1662k      0  0:02:03  0:00:47  0:01:16 2204k
 39  199M   39 78.8M    0     0  1670k      0  0:02:02  0:00:48  0:01:14 2234k
 40  199M   40 81.3M    0     0  1690k      0  0:02:01  0:00:49  0:01:12 2368k
 41  199M   41 83.3M    0     0  1697k      0  0:02:00  0:00:50  0:01:10 2098k
 42  199M   42 85.4M    0     0  1702k      0  0:02:00  0:00:51  0:01:09 2118k
 43  199M   43 86.3M    0     0  1691k      0  0:02:01  0:00:52  0:01:09 1958k
 44  199M   44 88.2M    0     0  1695k      0  0:02:00  0:00:53  0:01:07 1934k
 45  199M   45 90.3M    0     0  1703k      0  0:02:00  0:00:54  0:01:06 1836k
 46  199M   46 93.3M    0     0  1727k      0  0:01:58  0:00:55  0:01:03 2029k
 48  199M   48 96.0M    0     0  1746k      0  0:01:57  0:00:56  0:01:01 2199k
 49  199M   49 98.1M    0     0  1754k      0  0:01:56  0:00:57  0:00:59 2415k
 50  199M   50  100M    0     0  1771k      0  0:01:55  0:00:58  0:00:57 2583k
 51  199M   51  103M    0     0  1789k      0  0:01:54  0:00:59  0:00:55 2728k
 52  199M   52  105M    0     0  1795k      0  0:01:54  0:01:00  0:00:54 2554k
 54  199M   54  108M    0     0  1810k      0  0:01:53  0:01:01  0:00:52 2535k
 55  199M   55  110M    0     0  1819k      0  0:01:52  0:01:02  0:00:50 2572k
 56  199M   56  113M    0     0  1837k      0  0:01:51  0:01:03  0:00:48 2606k
 58  199M   58  116M    0     0  1849k      0  0:01:50  0:01:04  0:00:46 2562k
 59  199M   59  118M    0     0  1856k      0  0:01:50  0:01:05  0:00:45 2586k
 60  199M   60  120M    0     0  1856k      0  0:01:50  0:01:06  0:00:44 2416k
 60  199M   60  120M    0     0  1841k      0  0:01:51  0:01:07  0:00:44 2105k
 61  199M   61  122M    0     0  1839k      0  0:01:51  0:01:08  0:00:43 1863k
 62  199M   62  124M    0     0  1837k      0  0:01:51  0:01:09  0:00:42 1677k
 63  199M   63  126M    0     0  1840k      0  0:01:51  0:01:10  0:00:41 1629k
 64  199M   64  128M    0     0  1840k      0  0:01:51  0:01:11  0:00:40 1622k
 64  199M   64  129M    0     0  1835k      0  0:01:51  0:01:12  0:00:39 1762k
 65  199M   65  130M    0     0  1819k      0  0:01:52  0:01:13  0:00:39 1560k
 65  199M   65  131M    0     0  1806k      0  0:01:53  0:01:14  0:00:39 1369k
 66  199M   66  132M    0     0  1804k      0  0:01:53  0:01:15  0:00:38 1302k
 67  199M   67  134M    0     0  1803k      0  0:01:53  0:01:16  0:00:37 1285k
 68  199M   68  137M    0     0  1816k      0  0:01:52  0:01:17  0:00:35 1529k
 69  199M   69  138M    0     0  1813k      0  0:01:52  0:01:18  0:00:34 1722k
 70  199M   70  140M    0     0  1813k      0  0:01:52  0:01:19  0:00:33 1923k
 71  199M   71  142M    0     0  1823k      0  0:01:52  0:01:20  0:00:32 2112k
 72  199M   72  144M    0     0  1817k      0  0:01:52  0:01:21  0:00:31 2034k
 73  199M   73  146M    0     0  1818k      0  0:01:52  0:01:22  0:00:30 1857k
 73  199M   73  147M    0     0  1818k      0  0:01:52  0:01:23  0:00:29 1885k
 74  199M   74  149M    0     0  1820k      0  0:01:52  0:01:24  0:00:28 1928k
 75  199M   75  151M    0     0  1822k      0  0:01:52  0:01:25  0:00:27 1808k
 77  199M   77  154M    0     0  1829k      0  0:01:51  0:01:26  0:00:25 2030k
 77  199M   77  155M    0     0  1828k      0  0:01:52  0:01:27  0:00:25 1989k
 78  199M   78  157M    0     0  1823k      0  0:01:52  0:01:28  0:00:24 1915k
 78  199M   78  157M    0     0  1802k      0  0:01:53  0:01:29  0:00:24 1516k
 80  199M   80  160M    0     0  1816k      0  0:01:52  0:01:30  0:00:22 1710k
 80  199M   80  161M    0     0  1815k      0  0:01:52  0:01:31  0:00:21 1566k
 81  199M   81  163M    0     0  1817k      0  0:01:52  0:01:32  0:00:20 1618k
 83  199M   83  166M    0     0  1823k      0  0:01:52  0:01:33  0:00:19 1812k
 84  199M   84  168M    0     0  1826k      0  0:01:52  0:01:34  0:00:18 2276k
 84  199M   84  169M    0     0  1822k      0  0:01:52  0:01:35  0:00:17 1934k
 85  199M   85  171M    0     0  1819k      0  0:01:52  0:01:36  0:00:16 1886k
 87  199M   87  174M    0     0  1832k      0  0:01:51  0:01:37  0:00:14 2121k
 88  199M   88  176M    0     0  1841k      0  0:01:51  0:01:38  0:00:13 2189k
 89  199M   89  179M    0     0  1848k      0  0:01:50  0:01:39  0:00:11 2260k
 90  199M   90  181M    0     0  1850k      0  0:01:50  0:01:40  0:00:10 2380k
 92  199M   92  184M    0     0  1862k      0  0:01:49  0:01:41  0:00:08 2697k
 93  199M   93  186M    0     0  1868k      0  0:01:49  0:01:42  0:00:07 2560k
 94  199M   94  188M    0     0  1867k      0  0:01:49  0:01:43  0:00:06 2382k
 95  199M   95  190M    0     0  1866k      0  0:01:49  0:01:44  0:00:05 2211k
 95  199M   95  191M    0     0  1867k      0  0:01:49  0:01:45  0:00:04 2207k
 96  199M   96  193M    0     0  1863k      0  0:01:49  0:01:46  0:00:03 1890k
 97  199M   97  194M    0     0  1858k      0  0:01:50  0:01:47  0:00:03 1653k
 98  199M   98  196M    0     0  1856k      0  0:01:50  0:01:48  0:00:02 1624k
 98  199M   98  197M    0     0  1852k      0  0:01:50  0:01:49  0:00:01 1575k
 99  199M   99  199M    0     0  1853k      0  0:01:50  0:01:50 --:--:-- 1568k
100  199M  100  199M    0     0  1853k      0  0:01:50  0:01:50 --:--:-- 1595k
Extracting archive (outer zip, then the zips inside it) ...
Copied 38 .mat files to /tmp/tmp.E2obR3rh3P/clone/BMS Dataset/Battery_DataSet/Battery_DataSet
Verifying SHA256 checksums ...
Checksum verification succeeded: all files in checksums.sha256 match.

>>> running the pipeline (skip training)

============================================================
STAGE: load_data
CMD  : /home/harsh/Documents/battery-pdm/venv/bin/python src/load_data.py
============================================================
Reading .mat files from:
BMS Dataset/Battery_DataSet/Battery_DataSet

Loading B0005...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0005'])
B0005: 168 discharge cycles

Loading B0006...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0006'])
B0006: 168 discharge cycles

Loading B0007...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0007'])
B0007: 168 discharge cycles

Loading B0018...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0018'])
B0018: 132 discharge cycles

Loading B0029...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0029'])
B0029: 40 discharge cycles

Loading B0030...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0030'])
B0030: 40 discharge cycles

Loading B0031...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0031'])
B0031: 40 discharge cycles

Loading B0032...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0032'])
B0032: 40 discharge cycles

Loading B0033...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0033'])
B0033: 197 discharge cycles

Loading B0034...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0034'])
B0034: 197 discharge cycles

Loading B0036...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0036'])
B0036: 197 discharge cycles

Loading B0045...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0045'])
B0045: 72 discharge cycles

Loading B0046...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0046'])
B0046: 72 discharge cycles

Loading B0047...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0047'])
B0047: 72 discharge cycles

Loading B0048...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0048'])
B0048: 72 discharge cycles

Loading B0053...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0053'])
B0053: 55 discharge cycles

Loading B0054...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0054'])
B0054: 102 discharge cycles

Loading B0055...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0055'])
B0055: 102 discharge cycles

Loading B0056...
MAT variables: dict_keys(['__header__', '__version__', '__globals__', 'B0056'])
B0056: 102 discharge cycles

Saved:
data/processed/nasa_cycle_features_raw.csv

Dataset shape:
(2036, 27)

Cycles per cell:
         min  max  count
cell_id                 
B0005      2  614    168
B0006      2  614    168
B0007      2  614    168
B0018      3  319    132
B0029      2   94     40
B0030      2   94     40
B0031      2   94     40
B0032      2   94     40
B0033      1  483    197
B0034      1  483    197
B0036      1  483    197
B0045      1  181     72
B0046      1  181     72
B0047      1  181     72
B0048      1  181     72
B0053      1  135     55
B0054      1  251    102
B0055      1  251    102
B0056      1  251    102

============================================================
STAGE: preprocessing
CMD  : /home/harsh/Documents/battery-pdm/venv/bin/python src/preprocessing.py
============================================================

Saved: data/processed/nasa_ml_dataset.csv

Dataset shape: (2036, 60)

Battery summary:
         cycles  initial_soh  ...  fade_pct_per_cycle  early_fade_pct_per_cycle
cell_id                       ...                                              
B0005       168        100.0  ...             -0.2081                   -0.0573
B0006       168        100.0  ...             -0.3056                   -0.2811
B0007       168        100.0  ...             -0.1869                   -0.0663
B0018       132        100.0  ...             -0.2421                   -0.3164
B0029        40        100.0  ...             -0.3002                   -0.3002
B0030        40        100.0  ...             -0.2951                   -0.2951
B0031        40        100.0  ...             -0.1901                   -0.1901
B0032        40        100.0  ...             -0.3130                   -0.3130
B0033       197        100.0  ...             -2.6114                   20.2075
B0034       197        100.0  ...             -0.1421                    0.0397
B0036       197        100.0  ...             -0.0926                    0.1799
B0045        72        100.0  ...             -6.6027                   -6.6027
B0046        72        100.0  ...             -0.6517                   -0.6517
B0047        72        100.0  ...             -0.7896                   -0.7896
B0048        72        100.0  ...             -1.7903                   -1.7903
B0053        55        100.0  ...             -0.1323                   -0.2826
B0054       102        100.0  ...             -0.2722                   -0.2818
B0055       102        100.0  ...             -0.2038                   -0.3611
B0056       102        100.0  ...             -0.1272                   -0.1736

[19 rows x 8 columns]

EOL threshold: 80.0% SOH

Rows trainable for RUL (pre-EOL): 421 of 2036

============================================================
STAGE: data_cleaning
CMD  : /home/harsh/Documents/battery-pdm/venv/bin/python src/data_cleaning.py
============================================================
kept cells (9): ['B0005', 'B0006', 'B0007', 'B0018', 'B0029', 'B0030', 'B0031', 'B0032', 'B0053']
limited ageing, no RUL: ['B0029', 'B0030', 'B0031', 'B0032', 'B0053']
excluded cells: ['B0033', 'B0034', 'B0036', 'B0045', 'B0046', 'B0047', 'B0048', 'B0054', 'B0055', 'B0056']

wrote data/processed/cell_audit.csv (19 cells)
wrote data/processed/nasa_ml_dataset_clean.csv (851 rows x 60 cols)

============================================================
STAGE: data_quality
CMD  : /home/harsh/Documents/battery-pdm/venv/bin/python src/data_quality.py
============================================================

========== DATA QUALITY ==========

Cells : 19
Rows  : 2036
Duplicate cycle ids : 0

Cycles per cell:
  B0005: 168
  B0006: 168
  B0007: 168
  B0018: 132
  B0029: 40
  B0030: 40
  B0031: 40
  B0032: 40
  B0033: 197
  B0034: 197
  B0036: 197
  B0045: 72
  B0046: 72
  B0047: 72
  B0048: 72
  B0053: 55
  B0054: 102
  B0055: 102
  B0056: 102

Degradation (total capacity fade):
  B0005: 100.0 -> 71.4 %SOH (28.62% fade)
  B0006: 100.0 -> 58.3 %SOH (41.75% fade)
  B0007: 100.0 -> 75.7 %SOH (24.25% fade)
  B0018: 100.0 -> 72.3 %SOH (27.71% fade)
  B0029: 100.0 -> 95.0 %SOH (5.03% fade)
  B0030: 100.0 -> 94.4 %SOH (5.63% fade)
  B0031: 100.0 -> 100.0 %SOH (-0.04% fade)
  B0032: 100.0 -> 95.9 %SOH (4.05% fade)
  B0033: 100.0 -> 1922.2 %SOH (-1822.20% fade)
  B0034: 100.0 -> 171.6 %SOH (-71.63% fade)
  B0036: 100.0 -> 155.6 %SOH (-55.60% fade)
  B0045: 100.0 -> 56.1 %SOH (43.90% fade)
  B0046: 100.0 -> 66.8 %SOH (33.24% fade)
  B0047: 100.0 -> 69.1 %SOH (30.91% fade)
  B0048: 100.0 -> 73.8 %SOH (26.23% fade)
  B0053: 100.0 -> 94.5 %SOH (5.51% fade)
  B0054: 100.0 -> 113.2 %SOH (-13.17% fade)
  B0055: 100.0 -> 124.0 %SOH (-24.00% fade)
  B0056: 100.0 -> 143.8 %SOH (-43.78% fade)

Unusual degradation:
  {'cell': 'B0031', 'reason': 'capacity_increased_overall', 'fade_pct_total': -0.037414922490043544}
  {'cell': 'B0033', 'reason': 'capacity_increased_overall', 'fade_pct_total': -1822.2048436158786}
  {'cell': 'B0034', 'reason': 'capacity_increased_overall', 'fade_pct_total': -71.63268040803837}
  {'cell': 'B0036', 'reason': 'capacity_increased_overall', 'fade_pct_total': -55.60279804795346}
  {'cell': 'B0054', 'reason': 'capacity_increased_overall', 'fade_pct_total': -13.170992384114829}
  {'cell': 'B0055', 'reason': 'capacity_increased_overall', 'fade_pct_total': -23.999927832863253}
  {'cell': 'B0056', 'reason': 'capacity_increased_overall', 'fade_pct_total': -43.77823394784397}

Wrote data/processed/data_quality_report.csv
Wrote data/processed/data_quality_report.json

============================================================
STAGE: rul_trajectory
CMD  : /home/harsh/Documents/battery-pdm/venv/bin/python src/rul_trajectory.py
============================================================

Wrote rul_prediction_bands.csv: 335 rows, interval width mean 20.7 discharges
test_cell  eol_discharge  n_points  trajectory_mae  fleet_lifetime_baseline_mae  band_coverage_pct  band_width_mean
    B0005            106        96        7.114583                    24.770833          93.750000        25.791667
    B0006             60        50       51.060000                    46.000000           0.000000        25.520000
    B0007            124       114       17.473684                    37.517544          17.543860        14.603509
    B0018             77        67        6.477612                    29.000000          82.089552        20.322388

Mean MAE  trajectory: 20.5  |  fleet-lifetime baseline: 34.3 discharges
Saved models/rul_reference_curves.json

============================================================
STAGE: run_experiments
CMD  : /home/harsh/Documents/battery-pdm/venv/bin/python src/run_experiments.py
============================================================
wrote experiments:
cell_id                 method                                       formula  mae_pct        r2   n
  B0005 full_discharge_formula duration_s*abs(current_mean) / cell reference 0.170790  0.999530 168
  B0006 full_discharge_formula duration_s*abs(current_mean) / cell reference 0.525617  0.997700 168
  B0007 full_discharge_formula duration_s*abs(current_mean) / cell reference 0.277450  0.998237 168
  B0018 full_discharge_formula duration_s*abs(current_mean) / cell reference 0.183836  0.999411 132
  B0029 full_discharge_formula duration_s*abs(current_mean) / cell reference 0.362399  0.983593  40
  B0030 full_discharge_formula duration_s*abs(current_mean) / cell reference 0.217706  0.993419  40
  B0031 full_discharge_formula duration_s*abs(current_mean) / cell reference 0.292761  0.972209  40
  B0032 full_discharge_formula duration_s*abs(current_mean) / cell reference 0.036300  0.999876  40
  B0053 full_discharge_formula duration_s*abs(current_mean) / cell reference 3.392668 -0.299577  55
   MEAN full_discharge_formula duration_s*abs(current_mean) / cell reference 0.606614  0.849377 851

      feature_set      model  n_features  mean_mae_pct  mean_r2
            A_all ElasticNet          39      1.806873 0.947838
            A_all    XGBoost          39      2.743101 0.893845
          B_no_vw ElasticNet          34      3.853325 0.810060
          B_no_vw    XGBoost          34      3.055253 0.864331
C_vw_temp_current ElasticNet          13      1.675164 0.957762
C_vw_temp_current    XGBoost          13      2.192358 0.918256
        D_vw_only ElasticNet           5      1.863186 0.946275
        D_vw_only    XGBoost           5      2.574361 0.898773

test_group      model  n_cells  n_cycles   mae_pct          r2
        G1 ElasticNet        4       636 42.256784  -13.933335
        G1    XGBoost        4       636 14.056072   -1.450201
        G3 ElasticNet        4       160 20.692662  -37.150730
        G3    XGBoost        4       160 13.413608  -15.252039
        G9 ElasticNet        1        55 47.611481 -175.864775
        G9    XGBoost        1        55  3.042569   -0.032608

============================================================
STAGE: pack_evaluate
CMD  : /home/harsh/Documents/battery-pdm/venv/bin/python src/pack/evaluate.py
============================================================
             pack  discharges  warn_or_critical_discharges  isolation_forest_flags  false_resistance_spike  false_thermal  false_imbalance  false_weak_cell  false_soh_mismatch
B0005+B0006+B0007         168                          168                       7                       0              0              168              154                 104
B0005+B0006+B0018         132                          132                       7                       0              0              130              117                  86
B0005+B0007+B0018         132                          132                       6                       0              0              132              127                 117
B0006+B0007+B0018         132                          132                      27                       0              0              132              118                 117

                    n  rule_rate  rule_median_delay  iforest_rate
fault                                                           
cell_sag          24       0.75                1.0          0.25
resistance_spike  24       1.00                1.0          1.00
thermal           24       1.00                8.0          0.71

Saved models/pack_iforest.joblib

============================================================
STAGE: anomaly
CMD  : /home/harsh/Documents/battery-pdm/venv/bin/python src/anomaly.py
============================================================

========== ANOMALY DETECTION ==========

Rows            : 2036
Cells           : 19
Residual K      : 3.5 (robust sigma)
Capacity jump   : 2.0% of reference
Knee window     : 20 cycles
Knee K          : 3.0 (robust sigma)

Scoring every cell with a model that never saw it.


========== PER-CELL SUMMARY ==========
cell_id  cycles  any_anomaly  any_rate_pct  residual_anomalies  residual_sigma  capacity_anomalies  knee_cycles_judged  knee_anomalies  knee_rate_pct  first_knee_cycle
  B0005     168           11         6.548                   6           4.927                   5                 148               0          0.000               NaN
  B0006     168           38        22.619                  33           5.447                   9                 148               1          0.676             228.0
  B0007     168           25        14.881                  23           2.890                   2                 148               0          0.000               NaN
  B0018     132            8         6.061                   0           2.368                   8                 112               0          0.000               NaN
  B0029      40            2         5.000                   0         500.785                   2                  20               0          0.000               NaN
  B0030      40            6        15.000                   4          14.983                   2                  20               0          0.000               NaN
  B0031      40            3         7.500                   0           2.147                   3                  20               0          0.000               NaN
  B0032      40            3         7.500                   2           2.030                   1                  20               0          0.000               NaN
  B0033     197          191        96.954                  11         184.621                 189                 177              11          6.215             161.0
  B0034     197          119        60.406                   4         109.660                 116                 177               5          2.825             101.0
  B0036     197           13         6.599                   4           6.400                   8                 177               3          1.695             161.0
  B0045      72           24        33.333                  13          11.439                  13                  52               4          7.692              53.0
  B0046      72           12        16.667                   5           2.879                  10                  52               2          3.846             133.0
  B0047      72           11        15.278                   3           1.698                  10                  52               2          3.846             133.0
  B0048      72           12        16.667                   4           3.384                   9                  52               2          3.846             133.0
  B0053      55           33        60.000                   1           7.064                  32                  35               0          0.000               NaN
  B0054     102           65        63.725                   7           8.459                  59                  82               2          2.439             101.0
  B0055     102           55        53.922                   3           3.885                  54                  82               0          0.000               NaN
  B0056     102           76        74.510                   7           3.530                  73                  82               0          0.000               NaN


========== FLAG COUNTS ==========
residual              : 130
capacity event        : 605
knee                  : 32
any detector          : 707

Flagged cycles  : 707 of 2036 (34.7%)

Residual direction:
  faster than expected : 105
  slower than expected : 25


========== FLAGGED CYCLES ==========
cell_id  cycle      soh  predicted_soh  residual  capacity_jump_pct  fade_rate_pct_per_cycle                                         anomaly_reasons
  B0005     14   98.850      95.220001     3.631             -0.028                      NaN                           residual:slower_than_expected
  B0005     22   98.283      93.833000     4.451              0.000                      NaN                           residual:slower_than_expected
  B0005     37   97.123      95.056999     2.066              0.026                      NaN                           residual:slower_than_expected
  B0005     39   97.107      94.117996     2.989             -0.016                      NaN                           residual:slower_than_expected
  B0005     42   99.490     119.008003   -19.517              2.383                      NaN                                       capacity:recovery
  B0005     46   99.511      99.029999     0.481              0.021                    0.011                           residual:slower_than_expected
  B0005     74   97.710      94.241997     3.468             -0.043                    0.011                           residual:slower_than_expected
  B0005     86   99.748     120.511002   -20.764              2.571                   -0.023                                       capacity:recovery
  B0005    150   96.614     115.601997   -18.988              3.099                    0.014                                       capacity:recovery
  B0005    313   86.498     110.022003   -23.524              4.758                    0.015                                       capacity:recovery
  B0005    316   84.237     107.717003   -23.480             -2.261                    0.041                                    capacity:abrupt_loss
  B0006      2  100.000     137.050003   -37.050                NaN                      NaN                           residual:faster_than_expected
  B0006      4   99.499     137.145004   -37.646             -0.501                      NaN                           residual:faster_than_expected
  B0006      6   98.919     143.380997   -44.462             -0.580                      NaN                           residual:faster_than_expected
  B0006      8   98.916     144.768005   -45.852             -0.002                      NaN                           residual:faster_than_expected
  B0006     10   98.290     137.662994   -39.373             -0.627                      NaN                           residual:faster_than_expected
  B0006     12   98.947     135.888000   -36.941              0.657                      NaN                           residual:faster_than_expected
  B0006     14   98.907     143.059006   -44.151             -0.039                      NaN                           residual:faster_than_expected
  B0006     16   96.730     137.117004   -40.387             -2.177                      NaN      residual:faster_than_expected,capacity:abrupt_loss
  B0006     18   96.700     145.011993   -48.312             -0.031                      NaN                           residual:faster_than_expected
  B0006     20   96.162     136.425995   -40.264             -0.537                      NaN                           residual:faster_than_expected
  B0006     22   95.591     136.845993   -41.255             -0.571                      NaN                           residual:faster_than_expected
  B0006     25   95.058     139.139008   -44.081             -0.533                      NaN                           residual:faster_than_expected
  B0006     27   94.494     144.632004   -50.138             -0.564                      NaN                           residual:faster_than_expected
  B0006     29   93.935     136.214005   -42.279             -0.560                      NaN                           residual:faster_than_expected
  B0006     31   93.403     136.593002   -43.190             -0.532                      NaN                           residual:faster_than_expected
  B0006     33   92.820     139.470993   -46.651             -0.583                      NaN                           residual:faster_than_expected
  B0006     35   92.283     135.667007   -43.384             -0.537                      NaN                           residual:faster_than_expected
  B0006     37   91.757     135.139008   -43.382             -0.526                      NaN                           residual:faster_than_expected
  B0006     42   97.263     143.011993   -45.750              5.505                      NaN         residual:faster_than_expected,capacity:recovery
  B0006     46   96.178     142.251999   -46.074             -1.085                    0.087                           residual:faster_than_expected
  B0006     50   95.602     141.804001   -46.202             -0.577                    0.085                           residual:faster_than_expected
  B0006     54   94.542     141.570007   -47.029             -1.060                    0.091                           residual:faster_than_expected
  B0006     58   95.610     137.862000   -42.253              1.068                    0.066                           residual:faster_than_expected
  B0006     62   93.420     150.990005   -57.571             -2.190                    0.094      residual:faster_than_expected,capacity:abrupt_loss
  B0006     86   94.568     143.809998   -49.242              3.319                    0.016         residual:faster_than_expected,capacity:recovery
  B0006     90   92.478     145.770004   -53.293             -2.090                    0.040      residual:faster_than_expected,capacity:abrupt_loss
  B0006    150   89.603     116.949997   -27.347              5.960                    0.021                                       capacity:recovery
  B0006    228   76.212      81.061996    -4.850             -0.500                    0.172                                                    knee
  B0006    266   75.186      80.161003    -4.975              2.096                    0.078                                       capacity:recovery
  B0006    313   78.296      81.806000    -3.510              7.464                   -0.044                                       capacity:recovery
  B0006    316   75.977      80.880997    -4.904             -2.319                   -0.010                                    capacity:abrupt_loss
  B0006    545   61.584     121.402000   -59.819              0.263                    0.044                           residual:faster_than_expected
  B0006    592   57.959      85.851997   -27.893             -0.271                    0.062                           residual:faster_than_expected
  B0006    596   57.710      86.707001   -28.997             -0.249                    0.062                           residual:faster_than_expected
  B0006    600   56.689     124.238998   -67.550             -1.021                    0.075                           residual:faster_than_expected
  B0006    604   57.209      85.110001   -27.900              0.520                    0.062                           residual:faster_than_expected
  B0006    608   56.934      84.835999   -27.902             -0.275                    0.062                           residual:faster_than_expected
  B0006    612   57.729     125.602997   -67.874              0.795                    0.049                           residual:faster_than_expected
  B0007      8   99.456     259.944000  -160.487              0.006                      NaN                           residual:faster_than_expected
  B0007     10   99.387     305.235992  -205.849             -0.070                      NaN                           residual:faster_than_expected
  B0007     18   98.870     226.494995  -127.625             -0.625                      NaN                           residual:faster_than_expected
  B0007     20   98.890     314.269989  -215.380              0.019                      NaN                           residual:faster_than_expected
  B0007     22   98.889     440.156006  -341.267             -0.000                      NaN                           residual:faster_than_expected
  B0007     25   98.340     265.903015  -167.564             -0.550                      NaN                           residual:faster_than_expected
  B0007     27   98.309     273.088989  -174.780             -0.031                      NaN                           residual:faster_than_expected
  B0007     29   98.306     436.105988  -337.800             -0.004                      NaN                           residual:faster_than_expected
  B0007     35   97.714     317.539001  -219.825             -0.577                      NaN                           residual:faster_than_expected
  B0007     37   97.751      84.084000    13.667              0.037                      NaN                           residual:slower_than_expected
  B0007     42   99.457     294.799988  -195.343              1.713                      NaN                           residual:faster_than_expected
  B0007     46   99.493     444.321014  -344.828              0.037                    0.012                           residual:faster_than_expected
  B0007     50   99.473      79.598000    19.875             -0.020                   -0.001                           residual:slower_than_expected
  B0007     54   98.940     316.878998  -217.939             -0.533                    0.011                           residual:faster_than_expected
  B0007     58   98.897     389.958008  -291.060             -0.043                    0.011                           residual:faster_than_expected
  B0007     62   98.922      83.341003    15.581              0.025                    0.009                           residual:slower_than_expected
  B0007     66   98.337     319.812988  -221.476             -0.585                    0.021                           residual:faster_than_expected
  B0007     70   98.360      81.681999    16.677              0.022                    0.019                           residual:slower_than_expected
  B0007     74   98.314     169.136002   -70.822             -0.046                    0.020                           residual:faster_than_expected
  B0007     78   97.731     317.042999  -219.312             -0.583                    0.019                           residual:faster_than_expected
  B0007    110   96.860     107.302002   -10.442             -0.290                    0.011                           residual:faster_than_expected
  B0007    313   89.306      85.882004     3.424              5.191                   -0.011                                       capacity:recovery
  B0007    316   85.984      85.575996     0.408             -3.322                    0.029                                    capacity:abrupt_loss
  B0007    375   82.481      93.737000   -11.257             -0.292                    0.025                           residual:faster_than_expected
  B0007    433   81.391      92.741997   -11.351              0.274                    0.018                           residual:faster_than_expected
  B0018     66   94.298      93.237999     1.060              2.250                    0.094                                       capacity:recovery
  B0018    102   90.353      92.357002    -2.004              3.345                    0.069                                       capacity:recovery
  B0018    117   93.084      94.528999    -1.446              7.075                    0.007                                       capacity:recovery
  B0018    141   90.223      91.767998    -1.544              3.661                   -0.038                                       capacity:recovery
  B0018    212   79.235      79.407997    -0.173              2.421                    0.071                                       capacity:recovery
  B0018    223   78.414      80.651001    -2.238              2.109                    0.092                                       capacity:recovery
  B0018    259   78.729      84.142998    -5.414              5.591                    0.011                                       capacity:recovery
  B0018    294   76.919      81.575996    -4.657              4.346                   -0.048                                       capacity:recovery
  B0029      4  108.671     863.299988  -754.629              8.671                      NaN                                       capacity:recovery
  B0029     38  104.847     106.094002    -1.247              2.386                      NaN                                       capacity:recovery
  B0030      4  107.577     156.227997   -48.651              7.577                      NaN                                       capacity:recovery
  B0030     38  104.060     106.440002    -2.381              3.108                      NaN                                       capacity:recovery
  B0030     46  100.362     201.625000  -101.263             -0.418                      NaN                           residual:faster_than_expected
  B0030     76   96.393     240.817993  -144.425             -1.057                    0.135                           residual:faster_than_expected
  B0030     78   95.955     255.080002  -159.125             -0.438                    0.126                           residual:faster_than_expected
  B0030     80   95.440     206.039001  -110.599             -0.515                    0.120                           residual:faster_than_expected
  B0031      4  109.971     105.814003     4.157              9.971                      NaN                                       capacity:recovery
  B0031     16  108.839     107.412003     1.427              2.546                      NaN                                       capacity:recovery
  B0031     40  106.696     111.952003    -5.256              2.583                      NaN                                       capacity:recovery
  B0032      4  111.096     118.241997    -7.146             11.096                      NaN                                       capacity:recovery
  B0032     86   99.124     115.981003   -16.857              1.249                    0.134                           residual:faster_than_expected
  B0032     94   95.949     115.094002   -19.145             -0.458                    0.141                           residual:faster_than_expected
  B0033      1  100.000     104.688004    -4.688                NaN                      NaN                           residual:faster_than_expected
  B0033      5 1007.764     145.865997   861.898            907.764                      NaN         residual:faster_than_expected,capacity:recovery
  B0033      7 1696.854     179.173996  1517.681            689.090                      NaN                                       capacity:recovery
  B0033      9 1831.094     172.682999  1658.411            134.240                      NaN                                       capacity:recovery
  B0033     11 1904.135     176.362000  1727.773             73.041                      NaN                                       capacity:recovery
  B0033     13 1931.843     176.160995  1755.682             27.708                      NaN                                       capacity:recovery
  B0033     17 1883.019     168.320007  1714.699            -48.824                      NaN                                    capacity:abrupt_loss
  B0033     19 2503.692     212.175995  2291.516            620.673                      NaN                                       capacity:recovery
  B0033     21 2420.284     203.302002  2216.982            -83.408                      NaN                                    capacity:abrupt_loss
  B0033     23 2367.136     207.141006  2159.994            -53.148                      NaN                                    capacity:abrupt_loss
  B0033     25 2345.106     203.302994  2141.803            -22.030                      NaN                                    capacity:abrupt_loss
  B0033     29 2375.671     204.427002  2171.244             30.565                      NaN                                       capacity:recovery
  B0033     31 2323.829     210.610001  2113.219            -51.842                      NaN                                    capacity:abrupt_loss
  B0033     33 2300.217     209.576004  2090.641            -23.612                      NaN                                    capacity:abrupt_loss
  B0033     35 2296.111     207.492996  2088.618             -4.106                      NaN                                    capacity:abrupt_loss
  B0033     37 2323.352     208.184998  2115.167             27.242                      NaN                                       capacity:recovery
  B0033     41 2375.916     204.804993  2171.112             52.564                      NaN                                       capacity:recovery
  B0033     43 2414.809     214.557999  2200.251             38.893                      NaN                                       capacity:recovery
  B0033     45 2359.320     212.059998  2147.260            -55.489                      NaN                                    capacity:abrupt_loss
  B0033     47 2319.266     210.067993  2109.198            -40.054                      NaN                                    capacity:abrupt_loss
  B0033     49 2311.756     208.001007  2103.755             -7.510                  -46.078                                    capacity:abrupt_loss
  B0033     53 2472.721     205.533005  2267.187            160.965                  -30.520                                       capacity:recovery
  B0033     57 2466.833     220.306000  2246.527             -5.887                  -15.400                                    capacity:abrupt_loss
  B0033     59 2423.975     213.923004  2210.052            -42.858                  -11.858                                    capacity:abrupt_loss
  B0033     61 2399.709     213.237000  2186.471            -24.266                   -9.911                                    capacity:abrupt_loss
  B0033     63 2394.101     212.106995  2181.995             -5.607                   -9.245                                    capacity:abrupt_loss
  B0033     65 2400.105     212.100006  2188.005              6.004                  -10.773                                       capacity:recovery
  B0033     69 2413.231     212.841995  2200.389             13.126                    1.809                                       capacity:recovery
  B0033     71 2386.027     211.774994  2174.251            -27.205                    0.685                                    capacity:abrupt_loss
  B0033     73 2376.429     210.035995  2166.393             -9.597                   -0.186                                    capacity:abrupt_loss
  B0033     75 2349.890     209.927002  2139.964            -26.539                   -0.096                                    capacity:abrupt_loss
  B0033     77 2356.623     209.604996  2147.017              6.733                    0.397                                       capacity:recovery
  B0033     81 2370.217     210.459000  2159.758             13.595                   -0.928                                       capacity:recovery
  B0033     83 2359.558     210.449005  2149.108            -10.660                   -1.187                                    capacity:abrupt_loss
  B0033     85 2331.573     209.490005  2122.083            -27.984                   -0.709                                    capacity:abrupt_loss
  B0033     87 2320.362     207.742996  2112.619            -11.212                    0.060                                    capacity:abrupt_loss
  B0033     89 2310.804     207.610992  2103.192             -9.558                    1.357                                    capacity:abrupt_loss
  B0033     93 2357.070     213.604004  2143.466             46.267                    1.155                                       capacity:recovery
  B0033     95 2367.510     214.391006  2153.120             10.440                   -0.164                                       capacity:recovery
  B0033     97 2352.826     213.182007  2139.644            -14.684                   -0.671                                    capacity:abrupt_loss
  B0033     99 2304.832     208.199997  2096.632            -47.994                    0.138                                    capacity:abrupt_loss
  B0033    101 2295.715     205.537994  2090.177             -9.116                    3.688                                    capacity:abrupt_loss
  B0033    105 2376.530     207.248001  2169.283             80.815                    1.881                                       capacity:recovery
  B0033    109 2389.179     216.578995  2172.600             12.649                    0.696                                       capacity:recovery
  B0033    111 2355.409     214.518005  2140.891            -33.770                    0.886                                    capacity:abrupt_loss
  B0033    113 2755.017     233.167007  2521.849            399.608                   -7.218                                       capacity:recovery
  B0033    117 2423.790     217.992004  2205.798           -331.227                   -0.455                                    capacity:abrupt_loss
  B0033    119 2373.653     215.052002  2158.601            -50.137                    0.792                                    capacity:abrupt_loss
  B0033    121 2344.399     214.470993  2129.929            -29.254                    0.833                                    capacity:abrupt_loss
  B0033    123 2349.996     216.113998  2133.882              5.597                    0.529                                       capacity:recovery
  B0033    125 2375.825     216.505005  2159.320             25.829                   -0.519                                       capacity:recovery
  B0033    129 2364.071     215.147003  2148.924            -11.754                   -0.143                                    capacity:abrupt_loss
  B0033    131 2334.384     212.412003  2121.972            -29.687                    0.717                                    capacity:abrupt_loss
  B0033    133 2320.159     212.492996  2107.666            -14.226                    0.788                                    capacity:abrupt_loss
  B0033    135 2309.443     212.509003  2096.934            -10.716                    0.443                                    capacity:abrupt_loss
  B0033    137 2313.511     212.998001  2100.513              4.068                    0.137                                       capacity:recovery
  B0033    141 2342.268     214.557999  2127.710             28.757                   -0.605                                       capacity:recovery
  B0033    143 2328.176     214.156998  2114.019            -14.093                    0.578                                    capacity:abrupt_loss
  B0033    145 2257.760     207.830002  2049.930            -70.415                    2.195                                    capacity:abrupt_loss
  B0033    147 2243.628     203.468002  2040.160            -14.132                    2.184                                    capacity:abrupt_loss
  B0033    149 2229.085     200.061996  2029.023            -14.543                    1.515                                    capacity:abrupt_loss
  B0033    153 2273.924     209.811005  2064.113             44.839                    0.419                                       capacity:recovery
  B0033    155 2286.864     213.709000  2073.154             12.939                    1.793                                       capacity:recovery
  B0033    157 2267.951     208.720993  2059.231            -18.912                    2.526                                    capacity:abrupt_loss
  B0033    159 2213.283     200.104004  2013.179            -54.668                    2.961                                    capacity:abrupt_loss
  B0033    161 2199.748     198.779007  2000.970            -13.535                   11.568                               capacity:abrupt_loss,knee
  B0033    165 2346.813     211.229996  2135.584            147.065                    1.604                                       capacity:recovery
  B0033    169 2374.693     220.177002  2154.516             27.879                   -0.021                                       capacity:recovery
  B0033    171 2323.374     216.705994  2106.668            -51.319                    0.421                                    capacity:abrupt_loss
  B0033    173 2272.886     210.136993  2062.749            -50.488                    1.542                                    capacity:abrupt_loss
  B0033    175 2250.260     205.524002  2044.735            -22.626                    2.511                                    capacity:abrupt_loss
  B0033    177 2235.004     202.119003  2032.885            -15.256                    2.689                                    capacity:abrupt_loss
  B0033    181 2261.840     209.802002  2052.039             26.837                    1.451                                       capacity:recovery
  B0033    183 2288.601     212.959000  2075.642             26.761                    0.631                                       capacity:recovery
  B0033    185 2252.991     205.005997  2047.985            -35.611                    1.129                                    capacity:abrupt_loss
  B0033    187 2217.866     199.201996  2018.664            -35.125                    1.913                                    capacity:abrupt_loss
  B0033    189 2203.148     197.003006  2006.145            -14.717                    2.898                                    capacity:abrupt_loss
  B0033    193 2185.571     196.220993  1989.350            -17.577                    2.852                                    capacity:abrupt_loss
  B0033    195 2191.810     197.444000  1994.366              6.239                    1.319                                       capacity:recovery
  B0033    197 2178.562     195.785004  1982.777            -13.248                    1.301                                    capacity:abrupt_loss
  B0033    199 2163.075     196.242004  1966.833            -15.486                    1.320                                    capacity:abrupt_loss
  B0033    201 2144.821     195.272995  1949.548            -18.255                    2.690                                    capacity:abrupt_loss
  B0033    205 2152.673     188.154999  1964.518              7.852                    2.684                                       capacity:recovery
  B0033    209 2138.839     194.766998  1944.072            -15.741                    1.489                                    capacity:abrupt_loss
  B0033    211 2123.046     194.048004  1928.998            -15.793                    1.534                                    capacity:abrupt_loss
  B0033    213 2127.461     194.014008  1933.447              4.415                    4.570                                       capacity:recovery
  B0033    217 2133.321     194.302002  1939.019              5.860                    5.029                                       capacity:recovery
  B0033    219 2117.203     192.212006  1924.991            -16.118                    4.295                                    capacity:abrupt_loss
  B0033    221 2143.457     194.169006  1949.288             26.254                    2.696                                       capacity:recovery
  B0033    223 2146.781     197.110992  1949.670              3.324                    2.156                                       capacity:recovery
  B0033    225 2149.947     197.479004  1952.468              3.166                    1.772                                       capacity:recovery
  B0033    229 2115.142     192.360001  1922.782            -34.805                    3.056                                    capacity:abrupt_loss
  B0033    231 2076.020     191.942001  1884.078            -39.122                    4.429                                    capacity:abrupt_loss
  B0033    233 2081.303     191.444000  1889.859              5.283                    3.577                                       capacity:recovery
  B0033    235 2106.345     192.582993  1913.762             25.042                    2.323                                       capacity:recovery
  B0033    237 2111.628     194.610992  1917.017              5.282                    1.907                                       capacity:recovery
  B0033    241 2124.969     186.207001  1938.762             13.342                    1.263                                       capacity:recovery
  B0033    243 2079.047     191.464005  1887.583            -45.922                    2.349                                    capacity:abrupt_loss
  B0033    245 2060.916     190.925995  1869.990            -18.131                    2.451                                    capacity:abrupt_loss
  B0033    247 2067.188     190.755005  1876.432              6.271                    1.998                                       capacity:recovery
  B0033    249 2091.845     192.429993  1899.415             24.657                    1.104                                       capacity:recovery
  B0033    253 2121.298     195.218002  1926.080             29.453                    0.654                                       capacity:recovery
  B0033    255 2079.682     192.209000  1887.474            -41.615                    1.560                                    capacity:abrupt_loss
  B0033    257 2040.605     183.850998  1856.754            -39.077                    2.047                                    capacity:abrupt_loss
  B0033    259 2042.954     189.988007  1852.966              2.349                    1.669                                       capacity:recovery
  B0033    261 2047.673     190.878998  1856.794              4.719                    1.662                                       capacity:recovery
  B0033    265 2099.770     186.626999  1913.143             52.097                    0.699                                       capacity:recovery
  B0033    269 2061.148     191.972000  1869.176            -38.940                    1.715                                    capacity:abrupt_loss
  B0033    271 2020.634     183.804001  1836.830            -40.514                    2.628                                    capacity:abrupt_loss
  B0033    273 2002.084     181.996002  1820.088            -18.550                    3.080                                    capacity:abrupt_loss
  B0033    277 2014.758     183.000000  1831.758             12.674                    2.091                                       capacity:recovery
  B0033    279 2635.845     227.895996  2407.949            621.087                  -11.663                                       capacity:recovery
  B0033    283 2080.274     186.009995  1894.264           -555.571                    0.021                                    capacity:abrupt_loss
  B0033    285 2038.123     186.557999  1851.565            -42.151                    1.364                                    capacity:abrupt_loss
  B0033    287 2040.404     184.856995  1855.547              2.281                    1.424                                       capacity:recovery
  B0033    289 2024.047     189.910004  1834.137            -16.358                    2.103                                    capacity:abrupt_loss
  B0033    291 2004.380     180.934006  1823.446            -19.667                    1.556                                    capacity:abrupt_loss
  B0033    295 1990.021     179.203995  1810.817            -14.359                    1.418                                    capacity:abrupt_loss
  B0033    297 2016.245     181.522003  1834.723             26.224                    1.019                                       capacity:recovery
  B0033    299 1993.741     180.697998  1813.043            -22.504                    1.962                                    capacity:abrupt_loss
  B0033    301 1961.019     186.306000  1774.713            -32.722                    3.339                                    capacity:abrupt_loss
  B0033    303 1980.573     186.365005  1794.209             19.554                    2.065                                       capacity:recovery
  B0033    307 1958.892      79.764000  1879.128            -21.681                    1.634                                    capacity:abrupt_loss
  B0033    309 1986.691     186.317001  1800.374             27.799                    1.125                                       capacity:recovery
  B0033    311 1966.673      79.272003  1887.400            -20.019                    1.620                                    capacity:abrupt_loss
  B0033    313 1969.670      84.816002  1884.854              2.997                    2.710                                       capacity:recovery
  B0033    315 1951.725      84.778000  1866.947            -17.945                    3.091                                    capacity:abrupt_loss
  B0033    319 1977.553      79.928001  1897.625             25.828                    1.672                                       capacity:recovery
  B0033    321 2003.585      99.139000  1904.447             26.032                    0.341                                       capacity:recovery
  B0033    323 1983.796     103.334000  1880.462            -19.789                    0.366                                    capacity:abrupt_loss
  B0033    325 1964.711     102.795998  1861.915            -19.085                    1.043                                    capacity:abrupt_loss
  B0033    327 1943.799     102.888000  1840.910            -20.913                   14.418                               capacity:abrupt_loss,knee
  B0033    331 2050.931      98.386002  1952.545            107.132                    0.611                                       capacity:recovery
  B0033    335 2026.368     183.822006  1842.546            -24.563                    0.235                                    capacity:abrupt_loss
  B0033    337 2002.776     183.149002  1819.627            -23.592                    0.753                                    capacity:abrupt_loss
  B0033    339 1936.873     188.837997  1748.035            -65.903                    1.743                                    capacity:abrupt_loss
  B0033    341 1233.408     169.546005  1063.862           -703.465                   15.419 residual:faster_than_expected,capacity:abrupt_loss,knee
  B0033    343  436.476      75.920998   360.555           -796.932                   32.366 residual:faster_than_expected,capacity:abrupt_loss,knee
  B0033    347  342.688      76.809998   265.878            -93.788                   33.471 residual:faster_than_expected,capacity:abrupt_loss,knee
  B0033    349  296.034      75.780998   220.253            -46.654                   33.954 residual:faster_than_expected,capacity:abrupt_loss,knee
  B0033    351  343.906      77.463997   266.441             47.872                   32.342    residual:faster_than_expected,capacity:recovery,knee
  B0033    353  344.175      77.096001   267.079              0.269                   32.728                      residual:faster_than_expected,knee
  B0033    355  629.039      76.282997   552.756            284.864                   27.705    residual:faster_than_expected,capacity:recovery,knee
  B0033    359  678.639      76.248001   602.391             49.600                   26.161    residual:faster_than_expected,capacity:recovery,knee
  B0033    361  583.821      74.184998   509.637            -94.817                   27.657 residual:faster_than_expected,capacity:abrupt_loss,knee
  B0033    363 2135.849      99.564003  2036.284           1552.027                   -3.324                                       capacity:recovery
  B0033    365 2068.082     103.754997  1964.326            -67.767                   -2.327                                    capacity:abrupt_loss
  B0033    367 2022.094     103.360001  1918.734            -45.988                   -0.928                                    capacity:abrupt_loss
  B0033    371 2002.961      98.247002  1904.713            -19.133                    0.012                                    capacity:abrupt_loss
  B0033    373 1981.279     103.224998  1878.053            -21.682                    0.050                                    capacity:abrupt_loss
  B0033    375 1984.398     101.348000  1883.050              3.119                   -0.394                                       capacity:recovery
  B0033    377 1988.884     101.705002  1887.179              4.486                   -0.902                                       capacity:recovery
  B0033    379 1972.303     101.575996  1870.727            -16.581                    1.638                                    capacity:abrupt_loss
  B0033    383 1737.487      75.675003  1661.811           -234.817                    6.018                                    capacity:abrupt_loss
  B0033    387 2198.222     197.858002  2000.365            460.736                   -3.909                                       capacity:recovery
  B0033    389 2177.410     193.182007  1984.228            -20.812                   -4.811                                    capacity:abrupt_loss
  B0033    391 2156.105     200.962006  1955.143            -21.306                  -18.454                                    capacity:abrupt_loss
  B0033    393 2133.453     198.666000  1934.787            -22.651                  -33.940                                    capacity:abrupt_loss
  B0033    395 2115.311     199.326996  1915.984            -18.142                  -36.930                                    capacity:abrupt_loss
  B0033    399 2144.855     190.261002  1954.594             29.544                  -36.976                                       capacity:recovery
  B0033    401 2106.741     187.050003  1919.691            -38.114                  -35.257                                    capacity:abrupt_loss
  B0033    403 2072.894     190.675003  1882.219            -33.846                  -34.574                                    capacity:abrupt_loss
  B0033    405 2050.665     189.330002  1861.335            -22.229                  -28.433                                    capacity:abrupt_loss
  B0033    407 2033.023     189.255005  1843.767            -17.643                  -28.216                                    capacity:abrupt_loss
  B0033    411 2058.585     184.029999  1874.555             25.563                  -29.495                                       capacity:recovery
  B0033    413 2066.140     192.145996  1873.993              7.554                    1.394                                       capacity:recovery
  B0033    417 2021.447     185.567993  1835.879            -42.794                    0.013                                    capacity:abrupt_loss
  B0033    419 1994.294     184.028000  1810.265            -27.154                    0.181                                    capacity:abrupt_loss
  B0033    423 2001.808     175.320007  1826.488              7.514                   -0.411                                       capacity:recovery
  B0033    425 2026.715     186.266006  1840.450             24.908                   -0.846                                       capacity:recovery
  B0033    427 2034.120     178.598999  1855.522              7.405                   -0.905                                       capacity:recovery
  B0033    429 2012.638     178.395004  1834.243            -21.482                   -0.807                                    capacity:abrupt_loss
  B0033    431 1987.487     183.621002  1803.866            -25.151                   -5.208                                    capacity:abrupt_loss
  B0033    435 2016.463     179.761993  1836.701             28.975                    3.787                                       capacity:recovery
  B0033    439 1992.142     176.033005  1816.109            -24.321                    3.705                                    capacity:abrupt_loss
  B0033    441 1995.199     182.000000  1813.199              3.058                    3.218                                       capacity:recovery
  B0033    443 1972.741     181.869995  1790.871            -22.459                    3.214                                    capacity:abrupt_loss
  B0033    445 1977.254     182.042999  1795.211              4.513                    2.761                                       capacity:recovery
  B0033    451 1963.037     174.714996  1788.323            -15.128                    2.874                                    capacity:abrupt_loss
  B0033    453 1960.094     182.309998  1777.784             -2.944                    2.256                                    capacity:abrupt_loss
  B0033    455 1940.409     181.824005  1758.585            -19.684                    2.205                                    capacity:abrupt_loss
  B0033    457 1943.390     181.093002  1762.297              2.981                    1.793                                       capacity:recovery
  B0033    459 1951.904     176.042007  1775.863              8.514                    2.223                                       capacity:recovery
  B0033    463 1925.809     171.951996  1753.858            -26.095                    2.807                                    capacity:abrupt_loss
  B0033    469 1931.349     179.647995  1751.702              4.402                    1.259                                       capacity:recovery
  B0033    471 1906.683     179.686996  1726.996            -24.666                    1.982                                    capacity:abrupt_loss
  B0033    475 1940.838     171.498001  1769.340             34.155                    1.718                                       capacity:recovery
  B0033    477 1992.606     186.132004  1806.474             51.768                    0.830                                       capacity:recovery
  B0033    479 1942.444     181.544998  1760.900            -50.161                    1.404                                    capacity:abrupt_loss
  B0033    481 1918.022     180.563004  1737.458            -24.423                    1.389                                    capacity:abrupt_loss
  B0033    483 1922.205     180.559006  1741.646              4.183                    1.964                                       capacity:recovery
  B0034      5  222.852    2294.812012 -2071.960            122.852                      NaN                                       capacity:recovery
  B0034      7  220.542    2512.636963 -2292.095             -2.310                      NaN      residual:faster_than_expected,capacity:abrupt_loss
  B0034      9  217.276    2410.570068 -2193.293             -3.265                      NaN                                    capacity:abrupt_loss
  B0034     11  214.060    2376.988037 -2162.928             -3.216                      NaN                                    capacity:abrupt_loss
  B0034     13  199.167    2170.740967 -1971.574            -14.893                      NaN                                    capacity:abrupt_loss
  B0034     19  197.087    2183.593018 -1986.506             -2.217                      NaN                                    capacity:abrupt_loss
  B0034     37  193.175    2144.950928 -1951.777              2.395                      NaN                                       capacity:recovery
  B0034     41  199.335    2180.319092 -1980.984              6.161                      NaN                                       capacity:recovery
  B0034     43  204.380    2214.889893 -2010.510              5.045                      NaN                                       capacity:recovery
  B0034     45  196.118    2141.761963 -1945.644             -8.262                      NaN                                    capacity:abrupt_loss
  B0034     47  190.779    2066.258057 -1875.479             -5.339                      NaN                                    capacity:abrupt_loss
  B0034     49  188.369    2033.759033 -1845.389             -2.410                   -1.841                                    capacity:abrupt_loss
  B0034     53  218.914    2492.907959 -2273.994             30.545                    0.082         residual:faster_than_expected,capacity:recovery
  B0034     57  210.303    2031.921997 -1821.619             -8.611                    0.205                                    capacity:abrupt_loss
  B0034     59  203.096    2195.510010 -1992.414             -7.207                    0.284                                    capacity:abrupt_loss
  B0034     61  199.150    2144.995117 -1945.845             -3.946                    0.298                                    capacity:abrupt_loss
  B0034     71  197.513    2144.385010 -1946.871             -2.534                   -0.045                                    capacity:abrupt_loss
  B0034     73  193.261    2111.675049 -1918.414             -4.252                    0.001                                    capacity:abrupt_loss
  B0034     75  190.713    2036.578003 -1845.865             -2.548                    0.039                                    capacity:abrupt_loss
  B0034     81  195.686    2112.186035 -1916.501              4.428                   -0.074                                       capacity:recovery
  B0034     83  192.996    2093.646973 -1900.651             -2.689                   -0.035                                    capacity:abrupt_loss
  B0034     85  190.306    2087.246094 -1896.940             -2.690                    0.009                                    capacity:abrupt_loss
  B0034     93  197.539    2116.003906 -1918.465              9.262                    0.137                                       capacity:recovery
  B0034     95  200.107    2147.417969 -1947.311              2.568                   -0.080                                       capacity:recovery
  B0034     99  190.846    2085.829102 -1894.983             -7.836                   -0.050                                    capacity:abrupt_loss
  B0034    101  188.198    2090.850098 -1902.652             -2.648                    0.640                               capacity:abrupt_loss,knee
  B0034    105  205.960    2203.458984 -1997.499             17.762                    0.090                                       capacity:recovery
  B0034    111  202.149    2155.037109 -1952.888             -4.846                   -0.060                                    capacity:abrupt_loss
  B0034    113  244.032    2752.035889 -2508.004             41.882                   -0.910         residual:faster_than_expected,capacity:recovery
  B0034    117  213.647    2174.693115 -1961.046            -30.385                   -0.283                                    capacity:abrupt_loss
  B0034    119  205.525    2200.968994 -1995.445             -8.122                   -0.110                                    capacity:abrupt_loss
  B0034    121  199.243    2198.867920 -1999.625             -6.282                   -0.035                                    capacity:abrupt_loss
  B0034    123  201.484    2195.343018 -1993.859              2.241                   -0.164                                       capacity:recovery
  B0034    125  203.802    2210.562012 -2006.760              2.317                   -0.262                                       capacity:recovery
  B0034    131  196.346    2150.063965 -1953.719             -6.330                   -0.013                                    capacity:abrupt_loss
  B0034    135  192.155    2068.916016 -1876.761             -2.852                   -0.037                                    capacity:abrupt_loss
  B0034    137  194.298    2077.833008 -1883.535              2.143                   -0.101                                       capacity:recovery
  B0034    141  200.448    2146.441895 -1945.994              6.150                   -0.234                                       capacity:recovery
  B0034    143  197.345    2144.702881 -1947.358             -3.102                    0.004                                    capacity:abrupt_loss
  B0034    145  188.991    2014.586060 -1825.595             -8.355                    0.222                                    capacity:abrupt_loss
  B0034    147  185.834    2024.312012 -1838.478             -3.156                    0.257                                    capacity:abrupt_loss
  B0034    149  182.646    2027.640991 -1844.995             -3.188                    0.164                                    capacity:abrupt_loss
  B0034    153  194.004    2041.797974 -1847.794             11.358                   -0.112                                       capacity:recovery
  B0034    159  184.674    2018.779053 -1834.104             -8.694                    0.364                                    capacity:abrupt_loss
  B0034    161  181.517    2013.703979 -1832.186             -3.157                    1.302                               capacity:abrupt_loss,knee
  B0034    165  207.915    2286.790039 -2078.874             26.398                    0.119                                       capacity:recovery
  B0034    171  201.996    2147.654053 -1945.657             -6.641                   -0.055                                    capacity:abrupt_loss
  B0034    173  193.602    2060.903076 -1867.301             -8.394                    0.158                                    capacity:abrupt_loss
  B0034    175  189.617    2016.598022 -1826.981             -3.985                    0.284                                    capacity:abrupt_loss
  B0034    177  186.312    2021.449951 -1835.138             -3.305                    0.341                                    capacity:abrupt_loss
  B0034    181  194.384    2059.849121 -1865.465              8.072                    0.039                                       capacity:recovery
  B0034    183  196.783    2136.399902 -1939.617              2.399                   -0.036                                       capacity:recovery
  B0034    185  191.581    2018.397949 -1826.817             -5.202                    0.011                                    capacity:abrupt_loss
  B0034    187  184.567    2003.901978 -1819.335             -7.014                    0.195                                    capacity:abrupt_loss
  B0034    189  181.194    2016.772949 -1835.579             -3.373                    0.401                                    capacity:abrupt_loss
  B0034    197  178.860    1996.880005 -1818.020             -3.157                    0.139                                    capacity:abrupt_loss
  B0034    213  179.675    1990.708008 -1811.032              0.393                    0.588                                                    knee
  B0034    217  178.180    1992.741943 -1814.562             -1.496                    0.635                                                    knee
  B0034    221  181.003    2016.171997 -1835.170              2.387                    0.262                                       capacity:recovery
  B0034    223  185.208    2029.219971 -1844.012              4.205                    0.092                                       capacity:recovery
  B0034    229  178.316    1981.762939 -1803.446             -7.170                    0.335                                    capacity:abrupt_loss
  B0034    235  179.394    2000.926025 -1821.532              4.262                    0.108                                       capacity:recovery
  B0034    237  181.830    2007.583984 -1825.754              2.436                   -0.013                                       capacity:recovery
  B0034    243  178.805    1952.941040 -1774.135             -4.174                    0.067                                    capacity:abrupt_loss
  B0034    245  175.085    1943.114014 -1768.029             -3.720                    0.079                                    capacity:abrupt_loss
  B0034    249  181.889    1979.604004 -1797.715              6.245                   -0.049                                       capacity:recovery
  B0034    253  186.570    2024.307983 -1837.737              4.681                   -0.133                                       capacity:recovery
  B0034    255  182.724    2003.780029 -1821.056             -3.847                   -0.049                                    capacity:abrupt_loss
  B0034    257  177.117    1938.753052 -1761.635             -5.607                    0.076                                    capacity:abrupt_loss
  B0034    265  184.510    2018.116943 -1833.607              8.811                   -0.132                                       capacity:recovery
  B0034    267  186.581    2024.380981 -1837.800              2.072                   -0.166                                       capacity:recovery
  B0034    269  182.990    1966.400024 -1783.411             -3.592                   -0.041                                    capacity:abrupt_loss
  B0034    271  177.216    1921.541992 -1744.326             -5.773                    0.166                                    capacity:abrupt_loss
  B0034    277  178.663    1937.098022 -1758.435              3.177                   -0.007                                       capacity:recovery
  B0034    279  233.587    2461.236084 -2227.648             54.924                   -1.186         residual:faster_than_expected,capacity:recovery
  B0034    283  184.648    2010.385010 -1825.737            -48.939                   -0.190                                    capacity:abrupt_loss
  B0034    285  180.816    1947.234009 -1766.418             -3.832                   -0.028                                    capacity:abrupt_loss
  B0034    319  181.444    1911.715942 -1730.272              6.605                    0.031                                       capacity:recovery
  B0034    321  183.837    1975.840942 -1792.004              2.393                   -0.132                                       capacity:recovery
  B0034    325  178.125    1891.420044 -1713.295             -3.889                    0.011                                    capacity:abrupt_loss
  B0034    327  176.209    1886.511963 -1710.303             -1.917                    1.195                                                    knee
  B0034    331  185.899    2003.178955 -1817.279              9.690                   -0.026                                       capacity:recovery
  B0034    337  181.626    1957.807983 -1776.182             -4.294                   -0.012                                    capacity:abrupt_loss
  B0034    339  177.708    1895.972046 -1718.264             -3.918                    0.035                                    capacity:abrupt_loss
  B0034    347  179.889    1923.943970 -1744.055              4.745                   -0.023                                       capacity:recovery
  B0034    349  182.446    1950.780029 -1768.334              2.558                   -0.074                                       capacity:recovery
  B0034    351  178.296    1897.093994 -1718.799             -4.151                   -0.009                                    capacity:abrupt_loss
  B0034    353  174.154    1890.214966 -1716.061             -4.141                    0.067                                    capacity:abrupt_loss
  B0034    359  176.645    1895.277954 -1718.633              2.573                    0.029                                       capacity:recovery
  B0034    361  179.114    1919.665039 -1740.551              2.469                   -0.058                                       capacity:recovery
  B0034    363  182.803    1936.885986 -1754.083              3.689                   -0.126                                       capacity:recovery
  B0034    365  178.745    1893.333984 -1714.589             -4.058                   -0.078                                    capacity:abrupt_loss
  B0034    367  174.591    1886.425049 -1711.834             -4.154                    0.143                                    capacity:abrupt_loss
  B0034    383  195.158    2138.792969 -1943.634             23.128                   -0.192                                       capacity:recovery
  B0034    389  190.826    2042.355957 -1851.531             -4.189                   -0.262                                    capacity:abrupt_loss
  B0034    391  186.687    2012.928955 -1826.242             -4.139                   -0.216                                    capacity:abrupt_loss
  B0034    393  184.544    1962.220947 -1777.676             -2.142                   -0.188                                    capacity:abrupt_loss
  B0034    399  187.787    2043.354004 -1855.567              2.646                   -0.107                                       capacity:recovery
  B0034    401  184.286    1948.887939 -1764.602             -3.501                   -0.120                                    capacity:abrupt_loss
  B0034    403  178.917    1930.983032 -1752.066             -5.370                   -0.095                                    capacity:abrupt_loss
  B0034    407  175.206    1908.067017 -1732.861             -3.952                    0.030                                    capacity:abrupt_loss
  B0034    411  179.848    1945.306030 -1765.458              4.642                   -0.015                                       capacity:recovery
  B0034    417  176.407    1927.180054 -1750.772             -3.903                   -0.036                                    capacity:abrupt_loss
  B0034    419  173.914    1952.298950 -1778.385             -2.493                   -0.023                                    capacity:abrupt_loss
  B0034    425  176.829    1941.008057 -1764.179              4.544                   -0.071                                       capacity:recovery
  B0034    427  179.777    1944.218018 -1764.441              2.948                   -0.122                                       capacity:recovery
  B0034    431  175.505    1936.604980 -1761.100             -2.277                    0.409                                    capacity:abrupt_loss
  B0034    435  182.725    1998.693970 -1815.969              7.220                    0.256                                       capacity:recovery
  B0034    439  178.205    1927.344971 -1749.140             -4.520                    0.252                                    capacity:abrupt_loss
  B0034    441  173.865    1919.149048 -1745.285             -4.341                    0.256                                    capacity:abrupt_loss
  B0034    447  174.561    1912.552002 -1737.991              2.418                    0.276                                       capacity:recovery
  B0034    453  170.559    1925.223022 -1754.664             -4.914                    0.167                                    capacity:abrupt_loss
  B0034    457  168.984    1917.479980 -1748.496             -2.079                    0.124                                    capacity:abrupt_loss
  B0034    469  172.516    1933.281982 -1760.767              2.747                    0.028                                       capacity:recovery
  B0034    471  170.261    1902.194946 -1731.934             -2.255                    0.042                                    capacity:abrupt_loss
  B0034    475  180.443    1971.423950 -1790.980             10.183                   -0.072                                       capacity:recovery
  B0034    477  178.125    1964.166992 -1786.042             -2.319                    0.033                                    capacity:abrupt_loss
  B0034    479  175.892    1942.948975 -1767.057             -2.233                    0.038                                    capacity:abrupt_loss
  B0034    481  173.626    1933.136963 -1759.511             -2.266                    0.038                                    capacity:abrupt_loss
  B0036      1  100.000     169.091003   -69.091                NaN                      NaN                           residual:faster_than_expected
  B0036      5  179.754     108.809998    70.944             79.754                      NaN                                       capacity:recovery
  B0036     53  177.917     184.542007    -6.625              1.675                    0.038                           residual:faster_than_expected
  B0036    113  198.156     100.719002    97.437             22.186                   -0.408         residual:slower_than_expected,capacity:recovery
  B0036    117  176.186     102.648003    73.538            -21.970                    0.020                                    capacity:abrupt_loss
  B0036    161  172.389     102.974998    69.413             -0.225                    0.537                                                    knee
  B0036    279  243.923      91.821999   152.101             76.436                   -1.561         residual:slower_than_expected,capacity:recovery
  B0036    283  168.799     101.675003    67.125            -75.123                   -0.002                                    capacity:abrupt_loss
  B0036    327  164.362      99.644997    64.717             -0.641                    1.658                                                    knee
  B0036    331  166.859     100.567001    66.292              2.497                    0.040                                       capacity:recovery
  B0036    383  166.380     104.656998    61.723              5.498                    0.028                                       capacity:recovery
  B0036    475  159.383      93.042999    66.340              2.395                    0.083                                       capacity:recovery
  B0036    483  155.603      98.741997    56.861             -0.383                    0.172                                                    knee
  B0045      5   85.764     139.018005   -53.254            -14.236                      NaN                                    capacity:abrupt_loss
  B0045      7   81.812     204.294998  -122.483             -3.952                      NaN      residual:faster_than_expected,capacity:abrupt_loss
  B0045      9   79.308     148.440994   -69.133             -2.504                      NaN                                    capacity:abrupt_loss
  B0045     11   77.722     225.798004  -148.076             -1.587                      NaN                           residual:faster_than_expected
  B0045     13   76.207     180.563004  -104.356             -1.515                      NaN                           residual:faster_than_expected
  B0045     17   73.347     204.690002  -131.344             -2.861                      NaN      residual:faster_than_expected,capacity:abrupt_loss
  B0045     21   75.636     120.821999   -45.186              2.289                      NaN                                       capacity:recovery
  B0045     37   73.502     123.186996   -49.685              2.216                      NaN                                       capacity:recovery
  B0045     51    0.000      57.870998   -57.871            -68.460                      NaN                                    capacity:abrupt_loss
  B0045     53   67.548     204.250000  -136.702             67.548                    0.624    residual:faster_than_expected,capacity:recovery,knee
  B0045     55   67.351     130.391998   -63.040             -0.197                    0.368                                                    knee
  B0045     57   66.184     133.610992   -67.426             -1.167                    0.313                                                    knee
  B0045     85   63.051     201.468994  -138.418             -0.096                    0.218                           residual:faster_than_expected
  B0045     89   67.372     142.389999   -75.018              4.321                    0.099                                       capacity:recovery
  B0045     93   64.306     180.501999  -116.197             -1.678                    0.109                           residual:faster_than_expected
  B0045    101   63.248     213.171005  -149.924             -0.633                   -1.265                           residual:faster_than_expected
  B0045    105   61.647     212.542999  -150.896             -0.659                    0.114                           residual:faster_than_expected
  B0045    137   58.971     171.466003  -112.495              0.856                    0.175                           residual:faster_than_expected
  B0045    153   56.903     198.065002  -141.163             -1.809                    0.108                           residual:faster_than_expected
  B0045    159   59.467     122.397003   -62.930              2.434                    0.066                                       capacity:recovery
  B0045    165    0.000      57.813000   -57.813            -58.135                    1.235                               capacity:abrupt_loss,knee
  B0045    169   57.916     199.117996  -141.202             57.916                    0.040         residual:faster_than_expected,capacity:recovery
  B0045    173   60.036     155.875000   -95.838              2.121                    0.001                                       capacity:recovery
  B0045    177   58.213     240.292999  -182.079             -0.360                    0.012                           residual:faster_than_expected
  B0046      1  100.000     188.078003   -88.078                NaN                      NaN                           residual:faster_than_expected
  B0046      5   87.728      89.209999    -1.482            -12.272                      NaN                                    capacity:abrupt_loss
  B0046     33   82.363      75.103996     7.260              1.106                      NaN                           residual:slower_than_expected
  B0046     51    0.000      10.963000   -10.963            -78.784                      NaN                                    capacity:abrupt_loss
  B0046     53   81.588      79.816002     1.771             81.588                    0.354                                       capacity:recovery
  B0046     55   77.511      80.114998    -2.604             -4.077                    0.204                                    capacity:abrupt_loss
  B0046     85   74.360      97.150002   -22.790              2.596                    0.179         residual:faster_than_expected,capacity:recovery
  B0046    133    0.000      -0.642000     0.642            -65.021                    1.549                               capacity:abrupt_loss,knee
  B0046    137   70.252      93.202003   -22.950             70.252                    0.109         residual:faster_than_expected,capacity:recovery
  B0046    165    0.000       9.194000    -9.194            -65.688                    1.353                               capacity:abrupt_loss,knee
  B0046    169   72.506      93.823997   -21.317             72.506                   -0.095         residual:faster_than_expected,capacity:recovery
  B0046    173   69.727      64.716003     5.011             -2.780                   -0.050                                    capacity:abrupt_loss
  B0047      5   91.045      87.244003     3.801             -8.955                      NaN                                    capacity:abrupt_loss
  B0047     33   83.978      82.904999     1.073              2.438                      NaN                                       capacity:recovery
  B0047     51    0.000       5.917000    -5.917            -78.313                      NaN                                    capacity:abrupt_loss
  B0047     53   79.999      77.518997     2.479             79.999                    0.385                                       capacity:recovery
  B0047     55   76.743      78.353996    -1.611             -3.256                    0.286                                    capacity:abrupt_loss
  B0047     85   75.338      74.835999     0.502              4.485                    0.180                                       capacity:recovery
  B0047    101   71.090      78.418999    -7.330             -0.577                   -1.422                           residual:faster_than_expected
  B0047    133    0.000       6.785000    -6.785            -66.056                    1.570 residual:faster_than_expected,capacity:abrupt_loss,knee
  B0047    137   71.121      90.515999   -19.394             71.121                    0.092         residual:faster_than_expected,capacity:recovery
  B0047    165    0.000       3.552000    -3.552            -67.992                    1.366                               capacity:abrupt_loss,knee
  B0047    169   72.944      70.644997     2.299             72.944                   -0.091                                       capacity:recovery
  B0048      5   90.935      81.737999     9.196             -9.065                      NaN                                    capacity:abrupt_loss
  B0048     51    0.000       2.549000    -2.549            -81.063                      NaN                                    capacity:abrupt_loss
  B0048     53   82.774      75.044998     7.728             82.774                    0.331                                       capacity:recovery
  B0048     55   79.518      75.806999     3.711             -3.255                    0.228                                    capacity:abrupt_loss
  B0048     73   75.960      88.151001   -12.192             -0.853                    0.209                           residual:faster_than_expected
  B0048     85   78.632      60.807999    17.824              3.841                    0.145         residual:slower_than_expected,capacity:recovery
  B0048    131   69.824      90.646004   -20.822             -1.594                    0.099                           residual:faster_than_expected
  B0048    133    0.000       3.218000    -3.218            -69.824                    1.638                               capacity:abrupt_loss,knee
  B0048    137   75.957      74.407997     1.549             75.957                    0.070                                       capacity:recovery
  B0048    153   74.038      82.335999    -8.298              0.408                    0.025                           residual:faster_than_expected
  B0048    165    0.000      -0.581000     0.581            -72.872                    1.441                               capacity:abrupt_loss,knee
  B0048    169   78.400      64.026001    14.375             78.400                   -0.110                                       capacity:recovery
  B0053      5  107.944     160.565002   -52.621              7.944                      NaN                                       capacity:recovery
  B0053     17  101.620     144.246994   -42.627             -2.538                      NaN                                    capacity:abrupt_loss
  B0053     19   99.589     141.686005   -42.097             -2.031                      NaN                                    capacity:abrupt_loss
  B0053     25   98.255     140.595001   -42.340             -3.254                      NaN                                    capacity:abrupt_loss
  B0053     29  101.516     155.044998   -53.528              3.262                      NaN                                       capacity:recovery
  B0053     31   98.288     139.285004   -40.997             -3.228                      NaN                                    capacity:abrupt_loss
  B0053     37   95.781     142.091003   -46.309             -4.354                      NaN                                    capacity:abrupt_loss
  B0053     41   99.809     151.649994   -51.842              4.027                      NaN                                       capacity:recovery
  B0053     43   96.145     121.370003   -25.225             -3.664                      NaN                                    capacity:abrupt_loss
  B0053     47   98.766     131.779999   -33.014              2.189                      NaN                                       capacity:recovery
  B0053     57  100.673     135.334000   -34.661              3.837                    0.138                                       capacity:recovery
  B0053     59   96.827     138.009995   -41.183             -3.846                    0.178                                    capacity:abrupt_loss
  B0053     65   95.749     142.182999   -46.434             -3.427                    0.122                                    capacity:abrupt_loss
  B0053     69   97.891     130.535995   -32.646              2.141                    0.034                                       capacity:recovery
  B0053     71   93.824     135.897003   -42.073             -4.066                    0.146                                    capacity:abrupt_loss
  B0053     73   97.850     126.397003   -28.547              4.026                    0.073                                       capacity:recovery
  B0053     75   94.995     144.906006   -49.911             -2.856                    0.065                                    capacity:abrupt_loss
  B0053     77   95.872     112.396004   -16.525              0.877                    0.118                           residual:slower_than_expected
  B0053     81   91.675     129.962997   -38.288             -4.197                    0.132                                    capacity:abrupt_loss
  B0053     83   96.973     143.223007   -46.250              5.298                    0.045                                       capacity:recovery
  B0053     89   92.641     145.565994   -52.925             -2.299                    0.149                                    capacity:abrupt_loss
  B0053     93   94.896     137.080994   -42.184              2.256                    0.025                                       capacity:recovery
  B0053     95   91.768     130.257004   -38.489             -3.128                    0.096                                    capacity:abrupt_loss
  B0053     97   93.923     135.408005   -41.486              2.155                    0.097                                       capacity:recovery
  B0053    101   91.892     133.220993   -41.329             -2.373                    0.103                                    capacity:abrupt_loss
  B0053    105  103.791     130.774002   -26.982             11.900                   -0.065                                       capacity:recovery
  B0053    111   97.134     142.063004   -44.929             -7.032                    0.024                                    capacity:abrupt_loss
  B0053    113  100.724     150.671005   -49.947              3.590                   -0.031                                       capacity:recovery
  B0053    117   94.698     126.388000   -31.689             -5.585                    0.067                                    capacity:abrupt_loss
  B0053    125   98.735     118.233002   -19.498              2.357                   -0.075                                       capacity:recovery
  B0053    127   93.637     112.845001   -19.208             -5.098                    0.045                                    capacity:abrupt_loss
  B0053    129   97.899     144.518997   -46.620              4.263                   -0.130                                       capacity:recovery
  B0053    135   94.494     135.873001   -41.379             -3.452                    0.031                                    capacity:abrupt_loss
  B0054      5  157.655     144.459000    13.196             57.655                      NaN                                       capacity:recovery
  B0054      7  149.371     133.294006    16.077             -8.283                      NaN                                    capacity:abrupt_loss
  B0054      9  148.125     466.076996  -317.952             -1.246                      NaN                           residual:faster_than_expected
  B0054     11  145.343     121.296997    24.046             -2.782                      NaN                                    capacity:abrupt_loss
  B0054     13  144.006     464.660004  -320.654             -1.337                      NaN                           residual:faster_than_expected
  B0054     23  137.605     123.762001    13.843             -3.737                      NaN                                    capacity:abrupt_loss
  B0054     29  140.406     121.932999    18.472              2.199                      NaN                                       capacity:recovery
  B0054     31  137.488     115.176003    22.312             -2.917                      NaN                                    capacity:abrupt_loss
  B0054     33  139.632     120.308998    19.323              2.143                      NaN                                       capacity:recovery
  B0054     35  136.434     140.313995    -3.880             -3.198                      NaN                                    capacity:abrupt_loss
  B0054     37  133.288     110.332001    22.957             -3.146                      NaN                                    capacity:abrupt_loss
  B0054     41  136.229     111.264000    24.964              2.940                      NaN                                       capacity:recovery
  B0054     43  137.220     469.115997  -331.896              0.991                      NaN                           residual:faster_than_expected
  B0054     45  136.590     465.993011  -329.403             -0.630                      NaN                           residual:faster_than_expected
  B0054     49  132.463     108.700996    23.762             -4.090                   -0.676                                    capacity:abrupt_loss
  B0054     53  148.887     112.556000    36.331             16.423                    0.183                                       capacity:recovery
  B0054     57  146.317     148.136002    -1.819             -2.570                    0.061                                    capacity:abrupt_loss
  B0054     59  142.918     111.516998    31.401             -3.399                    0.104                                    capacity:abrupt_loss
  B0054     61  140.357     109.041000    31.316             -2.561                    0.100                                    capacity:abrupt_loss
  B0054     63  136.138     111.900002    24.237             -4.220                    0.157                                    capacity:abrupt_loss
  B0054     73  132.613     114.275002    18.338             -2.742                    0.100                                    capacity:abrupt_loss
  B0054     75  134.977     144.022003    -9.045              2.364                    0.065                                       capacity:recovery
  B0054     77  129.525     110.112999    19.412             -5.452                    0.227                                    capacity:abrupt_loss
  B0054     81  132.455     106.797997    25.658              2.930                    0.101                                       capacity:recovery
  B0054     87  128.116     470.545990  -342.429             -2.884                    0.103      residual:faster_than_expected,capacity:abrupt_loss
  B0054     89  130.262     109.061996    21.200              2.145                    0.124                                       capacity:recovery
  B0054     93  130.854     146.201996   -15.348              0.592                    0.127                           residual:faster_than_expected
  B0054     95  127.133     112.636002    14.497             -3.721                    0.189                                    capacity:abrupt_loss
  B0054     97  129.990     101.016998    28.973              2.857                    0.131                                       capacity:recovery
  B0054     99  126.124     121.092003     5.031             -3.867                    0.127                                    capacity:abrupt_loss
  B0054    101  125.869     112.992996    12.875             -0.255                    0.480                                                    knee
  B0054    105  134.828     107.764999    27.064              8.960                    0.239                                       capacity:recovery
  B0054    113  128.388     113.750999    14.637             -5.467                    0.155                                    capacity:abrupt_loss
  B0054    117  119.607      93.108002    26.499             -9.134                    0.368                               capacity:abrupt_loss,knee
  B0054    121  134.301     112.737000    21.564             14.694                    0.021                                       capacity:recovery
  B0054    125  131.823     111.449997    20.374             -2.716                    0.063                                    capacity:abrupt_loss
  B0054    133  125.616     107.772003    17.844             -3.770                    0.115                                    capacity:abrupt_loss
  B0054    137  129.007     140.253998   -11.247              3.002                   -0.018                                       capacity:recovery
  B0054    139  126.095     108.375000    17.720             -2.912                    0.083                                    capacity:abrupt_loss
  B0054    149  126.863     138.278000   -11.416              3.031                   -0.015                                       capacity:recovery
  B0054    151  123.781     110.892998    12.888             -3.082                    0.042                                    capacity:abrupt_loss
  B0054    161  125.401     137.447998   -12.047              4.032                    0.062                                       capacity:recovery
  B0054    163  123.233     105.320000    17.913             -2.168                    0.115                                    capacity:abrupt_loss
  B0054    165  120.774      99.987000    20.787             -2.459                   -0.024                                    capacity:abrupt_loss
  B0054    169  122.995     103.641998    19.353              2.221                    0.236                                       capacity:recovery
  B0054    175  126.189     141.964005   -15.775              3.004                    0.113         residual:faster_than_expected,capacity:recovery
  B0054    177  121.383     108.565002    12.817             -4.807                    0.183                                    capacity:abrupt_loss
  B0054    179  123.516     101.764999    21.751              2.133                    0.117                                       capacity:recovery
  B0054    181  118.454     107.907997    10.546             -5.062                    0.149                                    capacity:abrupt_loss
  B0054    189  122.251      98.084000    24.168              3.064                    0.077                                       capacity:recovery
  B0054    191  118.046     108.414001     9.632             -4.206                    0.135                                    capacity:abrupt_loss
  B0054    193  122.055     121.500000     0.555              4.010                    0.064                                       capacity:recovery
  B0054    197  117.743     109.258003     8.485             -4.312                    0.122                                    capacity:abrupt_loss
  B0054    199  120.774     105.401001    15.373              3.031                    0.122                                       capacity:recovery
  B0054    205  116.218     103.385002    12.833             -2.433                    0.144                                    capacity:abrupt_loss
  B0054    213  119.817     123.579002    -3.762              3.074                    0.068                                       capacity:recovery
  B0054    215  117.444     104.389000    13.055             -2.373                    0.067                                    capacity:abrupt_loss
  B0054    217  114.530     102.259003    12.271             -2.914                    0.176                                    capacity:abrupt_loss
  B0054    221  118.926     103.575996    15.349              4.396                    0.089                                       capacity:recovery
  B0054    225  123.125     110.866997    12.258              4.199                    0.061                                       capacity:recovery
  B0054    229  119.903     106.710999    13.192             -3.374                    0.072                                    capacity:abrupt_loss
  B0054    239  114.351     108.272003     6.079             -4.489                    0.158                                    capacity:abrupt_loss
  B0054    241  117.658     109.523003     8.135              3.307                    0.008                                       capacity:recovery
  B0054    243  114.071     108.043999     6.027             -3.587                    0.160                                    capacity:abrupt_loss
  B0054    251  113.171     104.998001     8.173             -2.035                    0.125                                    capacity:abrupt_loss
  B0055      5  165.193     146.987000    18.206             65.193                      NaN         residual:slower_than_expected,capacity:recovery
  B0055      7  159.823     152.309998     7.513             -5.370                      NaN                                    capacity:abrupt_loss
  B0055      9  157.354     154.750000     2.604             -2.469                      NaN                                    capacity:abrupt_loss
  B0055     11  151.034     142.425995     8.609             -6.320                      NaN                                    capacity:abrupt_loss
  B0055     21  146.397     150.199005    -3.803             -2.422                      NaN                                    capacity:abrupt_loss
  B0055     23  143.725     143.889999    -0.164             -2.671                      NaN                                    capacity:abrupt_loss
  B0055     29  141.657     140.391006     1.265             -2.620                      NaN                                    capacity:abrupt_loss
  B0055     33  139.682     137.404007     2.277             -2.077                      NaN                                    capacity:abrupt_loss
  B0055     35  137.480     136.880997     0.599             -2.202                      NaN                                    capacity:abrupt_loss
  B0055     41  138.820     140.682999    -1.864              2.077                      NaN                                       capacity:recovery
  B0055     47  134.252     139.470993    -5.220             -4.266                      NaN                                    capacity:abrupt_loss
  B0055     53  149.959     144.354004     5.605             15.080                    0.317                                       capacity:recovery
  B0055     57  145.499     141.315994     4.183             -4.460                    0.286                                    capacity:abrupt_loss
  B0055     61  142.358     140.382004     1.977             -2.283                    0.174                                    capacity:abrupt_loss
  B0055     63  139.254     134.940002     4.314             -3.105                    0.238                                    capacity:abrupt_loss
  B0055     81  137.093     135.779999     1.313              2.760                    0.093                                       capacity:recovery
  B0055     83  133.160     136.610001    -3.449             -3.933                    0.130                                    capacity:abrupt_loss
  B0055     85  135.895     134.313004     1.582              2.734                    0.032                                       capacity:recovery
  B0055     87  132.525     134.445999    -1.920             -3.370                    0.084                                    capacity:abrupt_loss
  B0055     89  135.335     136.869003    -1.534              2.810                    0.073                                       capacity:recovery
  B0055     93  132.866     134.496994    -1.630             -2.469                    0.115                                    capacity:abrupt_loss
  B0055     99  130.233     134.300995    -4.068             -4.217                    0.093                                    capacity:abrupt_loss
  B0055    101  132.361     131.878006     0.483              2.128                    0.367                                       capacity:recovery
  B0055    105  137.466     139.050003    -1.584              5.105                    0.167                                       capacity:recovery
  B0055    111  135.085     134.317001     0.768             -3.808                    0.145                                    capacity:abrupt_loss
  B0055    121  141.929     136.548996     5.380              5.955                   -0.078                                       capacity:recovery
  B0055    123  139.887     137.339996     2.547             -2.042                   -0.057                                    capacity:abrupt_loss
  B0055    141  129.103     131.830994    -2.728             -2.725                    0.078                                    capacity:abrupt_loss
  B0055    145  131.832     130.542007     1.290              2.729                    0.015                                       capacity:recovery
  B0055    147  129.135     130.802994    -1.668             -2.697                    0.106                                    capacity:abrupt_loss
  B0055    153  126.601     128.285995    -1.684             -2.588                    0.226                                    capacity:abrupt_loss
  B0055    157  130.335     124.410004     5.925              3.734                    0.178                                       capacity:recovery
  B0055    159  127.878     129.688995    -1.812             -2.457                    0.150                                    capacity:abrupt_loss
  B0055    163  128.910     131.567993    -2.658              2.088                    0.116                                       capacity:recovery
  B0055    173  132.261     121.992996    10.267              3.464                    0.153                                       capacity:recovery
  B0055    175  128.807     126.778000     2.029             -3.454                    0.189                                    capacity:abrupt_loss
  B0055    181  126.553     130.957993    -4.404             -3.725                    0.130                                    capacity:abrupt_loss
  B0055    185  129.385     125.079002     4.306              2.832                    0.045                                       capacity:recovery
  B0055    187  125.656     128.313995    -2.657             -3.729                    0.129                                    capacity:abrupt_loss
  B0055    189  129.339     127.342003     1.997              3.683                    0.050                                       capacity:recovery
  B0055    191  126.395     126.488998    -0.094             -2.944                    0.054                                    capacity:abrupt_loss
  B0055    197  124.515     125.678001    -1.163             -2.222                    0.092                                    capacity:abrupt_loss
  B0055    199  128.170     116.759003    11.411              3.655                    0.028                                       capacity:recovery
  B0055    205  125.005     125.638000    -0.633             -2.167                    0.111                                    capacity:abrupt_loss
  B0055    209  127.145     119.510002     7.635              2.140                    0.015                                       capacity:recovery
  B0055    211  124.699     127.984001    -3.285             -2.446                    0.042                                    capacity:abrupt_loss
  B0055    221  136.325     125.838997    10.487             11.362                   -0.085                                       capacity:recovery
  B0055    227  129.238     129.845993    -0.608             -5.855                   -0.001                                    capacity:abrupt_loss
  B0055    229  131.396     117.564003    13.832              2.158                   -0.022                                       capacity:recovery
  B0055    233  125.177     123.809998     1.367             -5.661                    0.088                                    capacity:abrupt_loss
  B0055    241  128.727     116.904999    11.822              3.113                   -0.047                                       capacity:recovery
  B0055    243  122.864     130.546005    -7.682             -5.863                    0.077                                    capacity:abrupt_loss
  B0055    245  126.747      96.847000    29.900              3.883                   -0.047         residual:slower_than_expected,capacity:recovery
  B0055    249  127.677      97.129997    30.547              0.929                    0.010                           residual:slower_than_expected
  B0055    251  124.000     130.529007    -6.529             -3.677                    0.075                                    capacity:abrupt_loss
  B0056      5  171.195     158.298996    12.896             71.195                      NaN                                       capacity:recovery
  B0056      7  167.775     142.485001    25.290             -3.420                      NaN                                    capacity:abrupt_loss
  B0056      9  165.211     153.460999    11.750             -2.564                      NaN                                    capacity:abrupt_loss
  B0056     11  160.957     145.610001    15.347             -4.254                      NaN                                    capacity:abrupt_loss
  B0056     17  163.078     150.953003    12.126              2.760                      NaN                                       capacity:recovery
  B0056     19  160.106     139.667007    20.439             -2.972                      NaN                                    capacity:abrupt_loss
  B0056     21  155.693     144.863007    10.831             -4.413                      NaN                                    capacity:abrupt_loss
  B0056     31  156.661     144.238998    12.422              2.144                      NaN                                       capacity:recovery
  B0056     33  151.889     137.938004    13.952             -4.772                      NaN                                    capacity:abrupt_loss
  B0056     41  154.618     144.746994     9.871              2.890                      NaN                                       capacity:recovery
  B0056     47  148.095     136.899994    11.195             -5.617                      NaN                                    capacity:abrupt_loss
  B0056     49  150.123     139.100998    11.022              2.027                   -1.044                                       capacity:recovery
  B0056     53  164.847     140.156998    24.690             14.725                    0.132                                       capacity:recovery
  B0056     57  160.485     131.089996    29.395             -4.362                    0.146      residual:slower_than_expected,capacity:abrupt_loss
  B0056     59  158.017     136.742996    21.274             -2.469                    0.144                                    capacity:abrupt_loss
  B0056     63  156.365     140.649994    15.716             -3.094                    0.079                                    capacity:abrupt_loss
  B0056     65  151.757     137.341003    14.416             -4.608                    0.236                                    capacity:abrupt_loss
  B0056     73  153.574     141.188995    12.385              2.787                    0.059                                       capacity:recovery
  B0056     75  148.410     136.768997    11.641             -5.164                    0.158                                    capacity:abrupt_loss
  B0056     81  152.979     142.158005    10.821              2.840                    0.074                                       capacity:recovery
  B0056     83  148.431     139.033005     9.398             -4.548                    0.069                                    capacity:abrupt_loss
  B0056     93  145.914     135.988007     9.926             -3.892                    0.162                                    capacity:abrupt_loss
  B0056     95  148.681     134.201004    14.480              2.767                    0.101                                       capacity:recovery
  B0056     97  145.889     132.828003    13.061             -2.792                    0.044                                    capacity:abrupt_loss
  B0056    101  147.784     133.906006    13.878              2.057                    0.355                                       capacity:recovery
  B0056    105  156.276     133.447998    22.828              8.492                    0.088                                       capacity:recovery
  B0056    109  158.604     134.809998    23.793              2.328                   -0.012                                       capacity:recovery
  B0056    111  149.276     133.835999    15.439             -9.328                    0.204                                    capacity:abrupt_loss
  B0056    113  152.220     138.466995    13.753              2.945                    0.083                                       capacity:recovery
  B0056    117  156.584     107.099998    49.484              4.797                   -0.116         residual:slower_than_expected,capacity:recovery
  B0056    121  162.675     139.332993    23.342              6.091                   -0.238                                       capacity:recovery
  B0056    123  158.337     133.102005    25.235             -4.338                   -0.095                                    capacity:abrupt_loss
  B0056    125  156.735     129.268005    27.466             -1.603                   -0.166                           residual:slower_than_expected
  B0056    127  154.880     127.574997    27.305             -1.855                   -0.095                           residual:slower_than_expected
  B0056    129  153.937     128.240005    25.697             -0.943                   -0.020                           residual:slower_than_expected
  B0056    135  149.428     134.059006    15.369             -2.699                    0.018                                    capacity:abrupt_loss
  B0056    137  145.230     129.048004    16.182             -4.198                    0.065                                    capacity:abrupt_loss
  B0056    139  150.542     136.356003    14.185              5.312                   -0.015                                       capacity:recovery
  B0056    141  146.199     131.847000    14.352             -4.343                   -0.006                                    capacity:abrupt_loss
  B0056    145  150.588     128.057007    22.532              4.390                   -0.038                                       capacity:recovery
  B0056    147  147.108     134.835007    12.274             -3.480                   -0.024                                    capacity:abrupt_loss
  B0056    149  142.839     127.051003    15.787             -4.270                    0.058                                    capacity:abrupt_loss
  B0056    151  148.058     135.923996    12.133              5.219                   -0.005                                       capacity:recovery
  B0056    153  143.223     132.632004    10.591             -4.835                    0.272                                    capacity:abrupt_loss
  B0056    157  148.536     134.990005    13.545              5.313                    0.210                                       capacity:recovery
  B0056    159  144.493     131.233994    13.259             -4.042                    0.100                                    capacity:abrupt_loss
  B0056    161  140.234     127.999001    12.234             -4.260                    0.250                                    capacity:abrupt_loss
  B0056    163  147.218     135.610001    11.608              6.985                    0.095                                       capacity:recovery
  B0056    165  144.988     135.192001     9.797             -2.230                    0.242                                    capacity:abrupt_loss
  B0056    169  149.721     137.580002    12.141              4.733                    0.270                                       capacity:recovery
  B0056    173  152.422     128.164993    24.257              2.701                    0.118                                       capacity:recovery
  B0056    175  143.484     129.212006    14.272             -8.939                    0.265                                    capacity:abrupt_loss
  B0056    177  147.731     134.488007    13.244              4.248                    0.143                                       capacity:recovery
  B0056    179  144.681     134.020004    10.661             -3.051                    0.185                                    capacity:abrupt_loss
  B0056    185  147.209     131.656998    15.552              2.073                    0.044                                       capacity:recovery
  B0056    187  143.493     131.449997    12.042             -3.716                    0.035                                    capacity:abrupt_loss
  B0056    191  144.288     131.061005    13.227              2.085                    0.038                                       capacity:recovery
  B0056    193  138.726     127.664001    11.062             -5.562                    0.247                                    capacity:abrupt_loss
  B0056    197  141.617     127.272003    14.344              2.891                    0.110                                       capacity:recovery
  B0056    199  143.643     136.134003     7.509              2.026                   -0.016                                       capacity:recovery
  B0056    205  141.370     132.143005     9.227             -2.160                    0.149                                    capacity:abrupt_loss
  B0056    209  144.466     134.233002    10.233              3.096                    0.001                                       capacity:recovery
  B0056    211  141.155     132.895996     8.259             -3.311                   -0.018                                    capacity:abrupt_loss
  B0056    213  137.126     125.865997    11.261             -4.029                    0.202                                    capacity:abrupt_loss
  B0056    215  143.694     135.910004     7.785              6.568                    0.026                                       capacity:recovery
  B0056    217  140.923     135.097000     5.826             -2.771                    0.183                                    capacity:abrupt_loss
  B0056    221  161.931     134.477997    27.453             21.008                   -0.198         residual:slower_than_expected,capacity:recovery
  B0056    225  158.107     131.619995    26.486             -3.824                   -0.292      residual:slower_than_expected,capacity:abrupt_loss
  B0056    227  149.584     133.792007    15.792             -8.522                   -0.037                                    capacity:abrupt_loss
  B0056    229  152.677     128.966003    23.711              3.093                   -0.160                                       capacity:recovery
  B0056    231  145.953     130.229996    15.723             -6.724                   -0.016                                    capacity:abrupt_loss
  B0056    233  143.799     126.695000    17.104             -2.154                    0.071                                    capacity:abrupt_loss
  B0056    237  141.395     125.803001    15.592             -2.404                    0.042                                    capacity:abrupt_loss
  B0056    239  146.943     134.147003    12.796              5.548                   -0.095                                       capacity:recovery
  B0056    241  143.926     134.110992     9.815             -3.017                    0.007                                    capacity:abrupt_loss
  B0056    245  145.555     136.630997     8.924              2.183                   -0.082                                       capacity:recovery


========== KNEE ONSET ==========
cell_id  first_knee_cycle  knee_anomalies
  B0006             228.0               1
  B0033             161.0              11
  B0034             101.0               5
  B0036             161.0               3
  B0045              53.0               4
  B0046             133.0               2
  B0047             133.0               2
  B0048             133.0               2
  B0054             101.0               2


========== LIMITS ==========

These detectors rank cycles for inspection.
They do not diagnose a cause, and a flag is not
proof of a fault.

SOH is referenced to each cell's own first
measured capacity, so a cell already partly aged
when first seen will read high throughout and its
early cycles may be flagged as apparent recovery.

Thresholds are multiples of a robust sigma computed
from four cells. They are set for this dataset and
would need re-deriving for a different chemistry or
a wider fleet.

Saved results to data/processed

============================================================
STAGE: benchmark_latency
CMD  : /home/harsh/Documents/battery-pdm/venv/bin/python src/benchmark_latency.py
============================================================

========== LATENCY BENCHMARK ==========

                stage  milliseconds
   cold_model_load_ms      612.2803
    warm_predict_1_ms        1.5078
warm_predict_soh_1_ms        2.2494
  warm_predict_100_ms        2.2422
 warm_predict_1000_ms        3.6916
  end_to_end_cycle_ms        5.3427
feature_extraction_ms        0.8092

Warm single-row prediction: 2.249 ms
Warm prediction is comfortably inside the one-second cycle budget.

Wrote /tmp/tmp.E2obR3rh3P/clone/data/processed/soh_latency_benchmark.csv

============================================================
STAGE: make_report
CMD  : /home/harsh/Documents/battery-pdm/venv/bin/python scripts/make_report.py
============================================================
wrote data/processed/report_summary.csv and updated README.md, CHANGES.md

============================================================
STAGE: load_to_sqlite
CMD  : /home/harsh/Documents/battery-pdm/venv/bin/python dashboard/load_to_sqlite.py
============================================================
  audit keep list: ['B0005', 'B0006', 'B0007', 'B0018', 'B0029', 'B0030', 'B0031', 'B0032', 'B0053']
  anomaly_cycle_report                         851 rows   78 cols  (dropped 1185 non-audited rows)
  anomaly_events                               129 rows   78 cols  (dropped 578 non-audited rows)
  anomaly_summary                                9 rows   11 cols  (dropped 10 non-audited rows)
  cell_audit                                     9 rows   18 cols  (dropped 10 non-audited rows)
  data_quality_report                           66 rows    2 cols
  nasa_cycle_features_raw                      851 rows   27 cols  (dropped 1185 non-audited rows)
  nasa_ml_dataset                              851 rows   60 cols  (dropped 1185 non-audited rows)
  nasa_ml_dataset_clean                        851 rows   60 cols
  pack_detection_summary                         3 rows    5 cols
  pack_false_alarms                              4 rows    9 cols
  pack_fault_detection                          72 rows    8 cols
  report_summary                               139 rows    7 cols
  rul_degradation_rates                          4 rows    6 cols
  rul_null_baselines                            96 rows    4 cols
  rul_prediction_bands                         335 rows    5 cols
  rul_predictions_B0005                        107 rows   62 cols
  rul_predictions_B0006                         61 rows   62 cols
  rul_predictions_B0007                        125 rows   62 cols
  rul_predictions_B0018                         78 rows   62 cols
  rul_results                                   32 rows    6 cols
  rul_trajectory_results                         4 rows    7 cols
  soh_baselines                                 24 rows    5 cols
  soh_bias_report                                4 rows    8 cols
  soh_feature_importance                        39 rows    3 cols
  soh_latency_benchmark                          7 rows    2 cols
  soh_nested_results                             4 rows    6 cols
  soh_null_baselines                            12 rows    4 cols
  soh_predictions_B0005                        168 rows   61 cols
  soh_predictions_B0006                        168 rows   61 cols
  soh_predictions_B0007                        168 rows   61 cols
  soh_predictions_B0018                        132 rows   61 cols
  soh_results                                    4 rows    4 cols
  soh_predictions_all                       built from 4 cells
  rul_predictions_all                       built from 4 cells

Database: /tmp/tmp.E2obR3rh3P/clone/data/battery_pdm.db
Tables/views: 34
  anomaly_cycle_report                         851 rows
  anomaly_events                               129 rows
  anomaly_summary                                9 rows
  cell_audit                                     9 rows
  data_quality_report                           66 rows
  nasa_cycle_features_raw                      851 rows
  nasa_ml_dataset                              851 rows
  nasa_ml_dataset_clean                        851 rows
  pack_detection_summary                         3 rows
  pack_false_alarms                              4 rows
  pack_fault_detection                          72 rows
  report_summary                               139 rows
  rul_degradation_rates                          4 rows
  rul_null_baselines                            96 rows
  rul_prediction_bands                         335 rows
  rul_predictions_B0005                        107 rows
  rul_predictions_B0006                         61 rows
  rul_predictions_B0007                        125 rows
  rul_predictions_B0018                         78 rows
  rul_predictions_all                          371 rows
  rul_results                                   32 rows
  rul_trajectory_results                         4 rows
  soh_baselines                                 24 rows
  soh_bias_report                                4 rows
  soh_feature_importance                        39 rows
  soh_latency_benchmark                          7 rows
  soh_nested_results                             4 rows
  soh_null_baselines                            12 rows
  soh_predictions_B0005                        168 rows
  soh_predictions_B0006                        168 rows
  soh_predictions_B0007                        168 rows
  soh_predictions_B0018                        132 rows
  soh_predictions_all                          636 rows
  soh_results                                    4 rows

Done.

============================================================
STAGE: pytest
CMD  : /home/harsh/Documents/battery-pdm/venv/bin/python -m pytest -q
============================================================
........................................................................ [ 37%]
........................................................................ [ 75%]
................................................                         [100%]
192 passed in 10.22s

All stages completed.

>>> checking numbers in README.md / CHANGES.md
all 159 quoted numbers in 6 region(s) are backed by data/processed CSVs

>>> checking generated documents are in sync
    README.md and CHANGES.md match the generator output

============================================================
verify.sh: ALL CHECKS PASSED
============================================================

```

---

## NOT DONE / NOT VERIFIED

1. The native systemd-user deployment (`battery-pdm-api` /
   `battery-pdm-grafana`) and the Docker stack both bind ports 8099 and
   3050, so only one can run at a time. Docker was used for the run
   above; the systemd units are the lighter alternative.
2. The RUL 10th–90th band is the spread of the per-reference-cell
   estimates, not a calibrated interval. Its empirical coverage is now
   measured (48.3% across the four held-out cells, against the ~80% its
   label implies) and reported, but the band has not been recalibrated
   to hit a target coverage.
3. The online double-exponential fit was not re-validated; where it
   cannot be backed by a CSV it is marked legacy rather than reported.
4. Grafana alert rules are provisioned
   (`docker/grafana/provisioning/alerting/alert_rules.yml`) but were not
   observed firing: the demo publisher injects synthetic faults into the
   live topic, and no alert notification was captured end to end.
