# BugBench ablation: the whole-PR view

83 pull requests shared by every run. A hit is a posted comment (or a finding's line range) on the defect's file within 3 lines of it.

| | Claude-only | Whole PR | Claude-only, conf 0.3 | Whole PR, conf 0.3 | PR-Agent |
|---|---|---|---|---|---|
| Injected recall | 49/57 | 52/57 | 51/57 | 52/57 | 54/57 |
| Real-bug recall | 6/10 | 5/10 | 7/10 | 9/10 | 8/10 |
| Clean PRs with any comment | 0/16 | 0/16 | 3/16 | 2/16 | 2/16 |
| Comments per buggy PR | 0.99 | 1.00 | 1.34 | 1.40 | 2.03 |

## Which cases each run found

| Case | Set | Claude-only | Whole PR | Claude-only, conf 0.3 | Whole PR, conf 0.3 | PR-Agent |
|---|---|---|---|---|---|---|
| aiosqlite-i01 | injected | hit | hit | hit | hit | hit |
| aiosqlite-i02 | injected | hit | hit | hit | hit | hit |
| aiosqlite-i03 | injected | hit | hit | hit | hit | hit |
| aiosqlite-i04 | injected | hit | hit | hit | hit | hit |
| aiosqlite-i05 | injected | hit | hit | hit | hit | hit |
| aiosqlite-i06 | injected | - | - | - | - | hit |
| aiosqlite-i08 | injected | hit | hit | hit | hit | hit |
| aiosqlite-i09 | injected | hit | hit | hit | hit | hit |
| aiosqlite-i10 | injected | hit | hit | hit | hit | hit |
| aiosqlite-i11 | injected | - | - | - | - | hit |
| aiosqlite-r01 | real | hit | hit | hit | hit | hit |
| aiosqlite-r02 | real | - | - | - | hit | - |
| jsoup-i01 | injected | hit | hit | hit | hit | hit |
| jsoup-i02 | injected | hit | hit | hit | hit | hit |
| jsoup-i03 | injected | hit | hit | hit | hit | hit |
| jsoup-i04 | injected | hit | hit | hit | hit | hit |
| jsoup-i05 | injected | hit | hit | hit | hit | hit |
| jsoup-i07 | injected | - | - | hit | - | - |
| jsoup-i08 | injected | hit | hit | hit | hit | hit |
| jsoup-i09 | injected | hit | hit | hit | hit | hit |
| jsoup-i10 | injected | hit | hit | hit | hit | hit |
| jsoup-i11 | injected | hit | hit | hit | hit | hit |
| jsoup-i12 | injected | - | hit | hit | hit | - |
| jsoup-i13 | injected | hit | hit | hit | hit | hit |
| jsoup-i14 | injected | hit | hit | hit | hit | hit |
| jsoup-i15 | injected | hit | hit | hit | hit | hit |
| jsoup-r01 | real | hit | - | hit | hit | hit |
| jsoup-r02 | real | - | - | - | - | - |
| sqlite-utils-i01 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-i02 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-i03 | injected | - | hit | - | hit | - |
| sqlite-utils-i04 | injected | - | - | - | - | hit |
| sqlite-utils-i05 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-i06 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-i07 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-i08 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-i09 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-i10 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-i11 | injected | - | - | - | - | hit |
| sqlite-utils-i12 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-i13 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-i14 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-i15 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-r01 | real | hit | - | hit | hit | hit |
| sqlite-utils-r02 | real | - | hit | hit | hit | hit |
| sqlite-utils-r03 | real | hit | - | hit | hit | hit |
| sqlite-utils-s01 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-s02 | injected | hit | hit | hit | hit | hit |
| sqlite-utils-s03 | injected | hit | hit | hit | hit | hit |
| tinydb-i01 | injected | hit | hit | hit | hit | hit |
| tinydb-i02 | injected | hit | hit | hit | hit | hit |
| tinydb-i03 | injected | hit | hit | hit | hit | hit |
| tinydb-i04 | injected | hit | hit | hit | hit | hit |
| tinydb-i05 | injected | hit | hit | hit | hit | hit |
| tinydb-i06 | injected | hit | hit | hit | hit | hit |
| tinydb-i07 | injected | hit | hit | hit | hit | hit |
| tinydb-i08 | injected | hit | hit | hit | hit | hit |
| tinydb-i09 | injected | hit | hit | hit | hit | hit |
| tinydb-i10 | injected | hit | hit | hit | hit | hit |
| tinydb-i11 | injected | - | hit | - | hit | hit |
| tinydb-i12 | injected | hit | hit | hit | hit | hit |
| tinydb-i13 | injected | hit | hit | hit | hit | hit |
| tinydb-i14 | injected | hit | hit | hit | hit | hit |
| tinydb-i15 | injected | hit | hit | hit | hit | hit |
| tinydb-r01 | real | - | hit | - | hit | hit |
| tinydb-r02 | real | hit | hit | hit | hit | hit |
| tinydb-r03 | real | hit | hit | hit | hit | hit |
