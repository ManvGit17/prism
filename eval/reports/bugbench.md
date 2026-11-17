# BugBench results

198 pull requests shared by every run. A hit is a posted comment (or a finding's line range) on the defect's file within 3 lines of it.

| | PRism | Whole PR to Claude |
|---|---|---|
| Injected recall | 115/139 | 124/139 |
| Real-bug recall | 17/30 | 18/30 |
| Clean PRs with any comment | 0/29 | 1/29 |
| Comments per buggy PR | 0.99 | 1.02 |

## Which cases each run found

| Case | Set | PRism | Whole PR to Claude |
|---|---|---|---|
| aiosqlite-i01 | injected | hit | hit |
| aiosqlite-i02 | injected | hit | hit |
| aiosqlite-i03 | injected | hit | hit |
| aiosqlite-i04 | injected | hit | hit |
| aiosqlite-i05 | injected | hit | hit |
| aiosqlite-i06 | injected | - | - |
| aiosqlite-i08 | injected | hit | hit |
| aiosqlite-i09 | injected | hit | hit |
| aiosqlite-i10 | injected | hit | hit |
| aiosqlite-i11 | injected | - | - |
| aiosqlite-i12 | injected | hit | hit |
| aiosqlite-i13 | injected | hit | hit |
| aiosqlite-i14 | injected | hit | hit |
| aiosqlite-i15 | injected | - | - |
| aiosqlite-i16 | injected | hit | hit |
| aiosqlite-i17 | injected | hit | hit |
| aiosqlite-i18 | injected | hit | hit |
| aiosqlite-i19 | injected | hit | hit |
| aiosqlite-i20 | injected | hit | hit |
| aiosqlite-i21 | injected | hit | hit |
| aiosqlite-r01 | real | hit | hit |
| aiosqlite-r02 | real | - | - |
| jsoup-i01 | injected | - | hit |
| jsoup-i02 | injected | hit | hit |
| jsoup-i03 | injected | hit | hit |
| jsoup-i04 | injected | hit | hit |
| jsoup-i05 | injected | hit | hit |
| jsoup-i07 | injected | - | - |
| jsoup-i08 | injected | hit | hit |
| jsoup-i09 | injected | hit | hit |
| jsoup-i10 | injected | hit | hit |
| jsoup-i11 | injected | hit | hit |
| jsoup-i12 | injected | - | hit |
| jsoup-i13 | injected | hit | hit |
| jsoup-i14 | injected | hit | hit |
| jsoup-i15 | injected | hit | hit |
| jsoup-i16 | injected | hit | hit |
| jsoup-i17 | injected | hit | hit |
| jsoup-i18 | injected | - | - |
| jsoup-i19 | injected | hit | hit |
| jsoup-i20 | injected | hit | hit |
| jsoup-i21 | injected | hit | hit |
| jsoup-i22 | injected | hit | hit |
| jsoup-i23 | injected | hit | hit |
| jsoup-i24 | injected | hit | hit |
| jsoup-i25 | injected | - | hit |
| jsoup-i26 | injected | hit | hit |
| jsoup-i27 | injected | - | hit |
| jsoup-i28 | injected | - | - |
| jsoup-i29 | injected | - | - |
| jsoup-i30 | injected | hit | hit |
| jsoup-i31 | injected | hit | hit |
| jsoup-i32 | injected | hit | hit |
| jsoup-i33 | injected | hit | hit |
| jsoup-i34 | injected | hit | hit |
| jsoup-i35 | injected | hit | hit |
| jsoup-i36 | injected | - | hit |
| jsoup-i37 | injected | hit | hit |
| jsoup-i38 | injected | hit | hit |
| jsoup-i39 | injected | - | - |
| jsoup-i40 | injected | hit | hit |
| jsoup-r01 | real | hit | - |
| jsoup-r02 | real | - | - |
| jsoup-r03 | real | hit | hit |
| jsoup-r04 | real | - | - |
| jsoup-r05 | real | - | - |
| jsoup-r06 | real | - | - |
| jsoup-r07 | real | hit | hit |
| jsoup-r08 | real | hit | hit |
| jsoup-r09 | real | hit | hit |
| jsoup-r10 | real | hit | hit |
| jsoup-r11 | real | hit | hit |
| sqlite-utils-i01 | injected | hit | hit |
| sqlite-utils-i02 | injected | hit | hit |
| sqlite-utils-i03 | injected | - | hit |
| sqlite-utils-i04 | injected | - | - |
| sqlite-utils-i05 | injected | hit | hit |
| sqlite-utils-i06 | injected | - | hit |
| sqlite-utils-i07 | injected | hit | hit |
| sqlite-utils-i08 | injected | hit | hit |
| sqlite-utils-i09 | injected | hit | hit |
| sqlite-utils-i10 | injected | hit | hit |
| sqlite-utils-i11 | injected | - | - |
| sqlite-utils-i12 | injected | hit | hit |
| sqlite-utils-i13 | injected | hit | hit |
| sqlite-utils-i14 | injected | hit | hit |
| sqlite-utils-i15 | injected | hit | hit |
| sqlite-utils-i16 | injected | hit | hit |
| sqlite-utils-i17 | injected | hit | hit |
| sqlite-utils-i18 | injected | hit | hit |
| sqlite-utils-i19 | injected | hit | hit |
| sqlite-utils-i20 | injected | hit | hit |
| sqlite-utils-i21 | injected | - | hit |
| sqlite-utils-i22 | injected | hit | hit |
| sqlite-utils-i24 | injected | - | - |
| sqlite-utils-i25 | injected | - | - |
| sqlite-utils-i26 | injected | hit | hit |
| sqlite-utils-i27 | injected | hit | hit |
| sqlite-utils-i28 | injected | hit | hit |
| sqlite-utils-i29 | injected | hit | hit |
| sqlite-utils-i30 | injected | hit | - |
| sqlite-utils-i31 | injected | hit | hit |
| sqlite-utils-i32 | injected | hit | hit |
| sqlite-utils-i33 | injected | hit | hit |
| sqlite-utils-i34 | injected | hit | hit |
| sqlite-utils-i35 | injected | - | - |
| sqlite-utils-i36 | injected | hit | hit |
| sqlite-utils-i37 | injected | hit | hit |
| sqlite-utils-i38 | injected | hit | hit |
| sqlite-utils-i39 | injected | hit | hit |
| sqlite-utils-i40 | injected | hit | hit |
| sqlite-utils-r01 | real | - | - |
| sqlite-utils-r02 | real | - | hit |
| sqlite-utils-r03 | real | hit | - |
| sqlite-utils-r04 | real | - | hit |
| sqlite-utils-r05 | real | - | - |
| sqlite-utils-r06 | real | - | - |
| sqlite-utils-r07 | real | hit | hit |
| sqlite-utils-r08 | real | hit | hit |
| sqlite-utils-s01 | injected | hit | hit |
| sqlite-utils-s02 | injected | hit | hit |
| sqlite-utils-s03 | injected | hit | hit |
| tinydb-i01 | injected | hit | hit |
| tinydb-i02 | injected | hit | hit |
| tinydb-i03 | injected | hit | hit |
| tinydb-i04 | injected | hit | hit |
| tinydb-i05 | injected | hit | hit |
| tinydb-i06 | injected | hit | hit |
| tinydb-i07 | injected | hit | hit |
| tinydb-i08 | injected | hit | hit |
| tinydb-i09 | injected | hit | hit |
| tinydb-i10 | injected | hit | hit |
| tinydb-i11 | injected | hit | hit |
| tinydb-i12 | injected | hit | hit |
| tinydb-i13 | injected | hit | hit |
| tinydb-i14 | injected | hit | hit |
| tinydb-i15 | injected | hit | hit |
| tinydb-i16 | injected | hit | hit |
| tinydb-i17 | injected | hit | hit |
| tinydb-i18 | injected | - | - |
| tinydb-i19 | injected | hit | hit |
| tinydb-i20 | injected | hit | hit |
| tinydb-i21 | injected | hit | hit |
| tinydb-i22 | injected | hit | hit |
| tinydb-i23 | injected | hit | hit |
| tinydb-i24 | injected | hit | hit |
| tinydb-i25 | injected | hit | hit |
| tinydb-i26 | injected | hit | hit |
| tinydb-i27 | injected | hit | hit |
| tinydb-i28 | injected | hit | hit |
| tinydb-i29 | injected | hit | hit |
| tinydb-i30 | injected | hit | hit |
| tinydb-i32 | injected | hit | hit |
| tinydb-i34 | injected | hit | hit |
| tinydb-i35 | injected | hit | hit |
| tinydb-i36 | injected | hit | hit |
| tinydb-i37 | injected | - | hit |
| tinydb-i38 | injected | hit | hit |
| tinydb-i39 | injected | - | hit |
| tinydb-i40 | injected | hit | hit |
| tinydb-r01 | real | hit | hit |
| tinydb-r02 | real | hit | hit |
| tinydb-r03 | real | hit | hit |
| tinydb-r04 | real | - | - |
| tinydb-r05 | real | - | - |
| tinydb-r06 | real | hit | hit |
| tinydb-r07 | real | hit | hit |
| tinydb-r08 | real | - | hit |
| tinydb-r09 | real | hit | hit |

## Judged

Every posted comment was judged: is it a true, useful review comment, and does it name the defect (wherever it is placed)?

| | PRism |
|---|---|
| Precision | 0.97 (162/167) |
| Planted bugs named | 117/139 |
| Real bugs named | 17/30 |

# Confidence threshold sweep

Each run's saved Claude answers re-scored at each confidence threshold, with no new Claude work. A PR is unscored at a threshold where PRism would ask Claude something the run never asked (a local comment kept below the threshold goes back to Claude); counts then cover the scored PRs only.

## Injected recall

| Threshold | PRism | Whole PR to Claude |
|---|---|---|
| 0.30 | 119/139 | 127/139 |
| 0.40 | 118/139 | 125/139 |
| 0.50 | 117/139 | 125/139 |
| 0.60 | 115/139 | 124/139 |
| 0.70 | 109/134 (6 PR(s) unscored) | 123/139 |
| 0.80 | 105/131 (9 PR(s) unscored) | 119/139 |
| 0.90 | 84/126 (14 PR(s) unscored) | 107/139 |

## Real-bug recall

| Threshold | PRism | Whole PR to Claude |
|---|---|---|
| 0.30 | 19/30 | 23/30 |
| 0.40 | 19/30 | 23/30 |
| 0.50 | 19/30 | 20/30 |
| 0.60 | 17/30 | 18/30 |
| 0.70 | 14/29 (6 PR(s) unscored) | 15/30 |
| 0.80 | 11/29 (9 PR(s) unscored) | 13/30 |
| 0.90 | 5/29 (14 PR(s) unscored) | 6/30 |

## Clean PRs with any comment

| Threshold | PRism | Whole PR to Claude |
|---|---|---|
| 0.30 | 2/29 | 5/29 |
| 0.40 | 1/29 | 5/29 |
| 0.50 | 1/29 | 4/29 |
| 0.60 | 0/29 | 1/29 |
| 0.70 | 0/29 (6 PR(s) unscored) | 0/29 |
| 0.80 | 0/29 (9 PR(s) unscored) | 0/29 |
| 0.90 | 0/29 (14 PR(s) unscored) | 0/29 |

## Comments per buggy PR

| Threshold | PRism | Whole PR to Claude |
|---|---|---|
| 0.30 | 1.19 | 1.45 |
| 0.40 | 1.15 | 1.37 |
| 0.50 | 1.08 | 1.19 |
| 0.60 | 0.99 | 1.02 |
| 0.70 | 0.89 (6 PR(s) unscored) | 0.93 |
| 0.80 | 0.81 (9 PR(s) unscored) | 0.84 |
| 0.90 | 0.61 (14 PR(s) unscored) | 0.69 |
