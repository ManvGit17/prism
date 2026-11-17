# BugBench: confidence threshold sweep

Each run's saved Claude answers re-scored at each confidence threshold, with no new Claude work. A PR is unscored at a threshold where PRism would ask Claude something the run never asked (a local comment kept below the threshold goes back to Claude); counts then cover the scored PRs only.

## Injected recall

| Threshold | PRism | Claude-only | Whole PR |
|---|---|---|---|
| 0.30 | 50/57 | 51/57 | 52/57 |
| 0.40 | 50/57 | 50/57 | 52/57 |
| 0.50 | 49/57 | 49/57 | 52/57 |
| 0.60 | 48/57 | 49/57 | 52/57 |
| 0.70 | 46/55 (3 PR(s) unscored) | 47/57 | 52/57 |
| 0.80 | 44/54 (4 PR(s) unscored) | 46/57 | 49/57 |
| 0.90 | 38/53 (5 PR(s) unscored) | 38/57 | 43/57 |

## Real-bug recall

| Threshold | PRism | Claude-only | Whole PR |
|---|---|---|---|
| 0.30 | 7/10 | 7/10 | 9/10 |
| 0.40 | 7/10 | 7/10 | 9/10 |
| 0.50 | 7/10 | 7/10 | 6/10 |
| 0.60 | 6/10 | 6/10 | 5/10 |
| 0.70 | 4/9 (3 PR(s) unscored) | 5/10 | 5/10 |
| 0.80 | 3/9 (4 PR(s) unscored) | 3/10 | 4/10 |
| 0.90 | 2/9 (5 PR(s) unscored) | 0/10 | 1/10 |

## Clean PRs with any comment

| Threshold | PRism | Claude-only | Whole PR |
|---|---|---|---|
| 0.30 | 1/16 | 3/16 | 2/16 |
| 0.40 | 0/16 | 3/16 | 2/16 |
| 0.50 | 0/16 | 1/16 | 1/16 |
| 0.60 | 0/16 | 0/16 | 0/16 |
| 0.70 | 0/16 (3 PR(s) unscored) | 0/16 | 0/16 |
| 0.80 | 0/16 (4 PR(s) unscored) | 0/16 | 0/16 |
| 0.90 | 0/16 (5 PR(s) unscored) | 0/16 | 0/16 |

## Comments per buggy PR

| Threshold | PRism | Claude-only | Whole PR |
|---|---|---|---|
| 0.30 | 1.22 | 1.34 | 1.40 |
| 0.40 | 1.18 | 1.28 | 1.34 |
| 0.50 | 1.09 | 1.16 | 1.12 |
| 0.60 | 1.00 | 0.99 | 1.00 |
| 0.70 | 0.91 (3 PR(s) unscored) | 0.87 | 0.94 |
| 0.80 | 0.83 (4 PR(s) unscored) | 0.81 | 0.84 |
| 0.90 | 0.69 (5 PR(s) unscored) | 0.60 | 0.69 |
