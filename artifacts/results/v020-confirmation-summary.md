# v0.2.0 second-stage development and confirmation results

The revised pooled chronological selector and generic persistence candidate improve both tasks on the newly fixed February 2017 Beijing period. Evidence remains narrow: eight observed dates and 96 station-day labels per task. Development results remain mixed, including substantial regression on the earlier late-2016 Beijing regression period.

The first-stage implementation, protocol and results remain unchanged at commit `c2c392d`. Its periods are now explicitly development evidence. The second stage was motivated by those results; those periods cannot confirm its quality. Retail has no untouched complete later horizon and remains retrospective.

Frozen second-stage protocol SHA256: `88d4d6a58eb464dad52868c4f923018e6c733a2611bbf4ed0f90935b96223316`. Original v0.1.0 source commit: `7c84f7aaa0c573340837667ae63a6c9c7b71331a`.

All 56 fixed comparisons completed without failures. Saved outputs assert identical test IDs and labels across all four controls/backends. The earlier 112,026 control predictions reproduce exactly. Every first-stage artifact hash and original evaluator remains unchanged. Current source diff hashes match at both dataset completions and final verification; see `v020-confirmation-verification.json`.

## Newly fixed February confirmation

Fit as of 2017-01-01, using 149 mature complete-label weekly training cutoffs. Of nine fixed February test dates, February 22 was removed by the prespecified missing-label rule, leaving eight dates. Both adaptive tasks selected persistence using only the earlier purged training validation block.

| Task | Backend | AUC | Average precision | Brier | ECE | MAE | RMSE |
|---|---|---:|---:|---:|---:|---:|---:|
| pm25_over_75_next_day | legacy_prior | 0.500000 | 0.531250 | 0.265409 | 0.128006 |  |  |
| pm25_over_75_next_day | legacy_historical | 0.593900 | 0.664452 | 0.248218 | 0.094444 |  |  |
| pm25_over_75_next_day | legacy_histogram | 0.603922 | 0.609563 | 0.311925 | 0.273404 |  |  |
| pm25_over_75_next_day | adaptive | 0.878431 | 0.916146 | 0.164137 | 0.164892 |  |  |
| pm25_next_day | legacy_prior |  |  |  |  | 53.602323 | 75.146088 |
| pm25_next_day | legacy_historical |  |  |  |  | 54.326630 | 70.320672 |
| pm25_next_day | legacy_histogram |  |  |  |  | 53.702612 | 75.141442 |
| pm25_next_day | adaptive |  |  |  |  | 47.755881 | 64.268575 |

Against the original histogram, confirmation regression MAE improves 11.07% and RMSE 14.47%. Adaptive binary ranking, average precision and Brier also improve over both the old histogram and historical control. Its ECE remains worse than the historical control (0.164892 versus 0.094444), so improved ranking and Brier do not imply uniformly better calibration. Neither confirmation regression backend produces negative estimates.

## Exposed development comparisons

Values below retain all development periods and show original histogram, unchanged first-stage adaptive and revised second-stage adaptive. Higher ROC-AUC is better; lower MAE, RMSE, Brier and ECE are better.

| Dataset/task | Period | Selected second stage | Metric | v0.1.0 histogram | First-stage adaptive | Second-stage adaptive |
|---|---|---|---|---:|---:|---:|
| retail/purchase_30d | summer | robust | roc_auc | 0.713795 | 0.716456 | 0.716456 |
| retail/purchase_30d | summer | robust | brier | 0.163338 | 0.162481 | 0.162481 |
| retail/purchase_30d | summer | robust | ece_5_bins | 0.003221 | 0.003350 | 0.003350 |
| retail/purchase_30d | autumn | robust | roc_auc | 0.695789 | 0.701140 | 0.701140 |
| retail/purchase_30d | autumn | robust | brier | 0.186558 | 0.185853 | 0.185853 |
| retail/purchase_30d | autumn | robust | ece_5_bins | 0.059153 | 0.058561 | 0.058561 |
| retail/purchase_30d | later_overlapping_november | histogram | roc_auc | 0.711890 | 0.713250 | 0.713250 |
| retail/purchase_30d | later_overlapping_november | histogram | brier | 0.198943 | 0.198370 | 0.198370 |
| retail/purchase_30d | later_overlapping_november | histogram | ece_5_bins | 0.090303 | 0.090669 | 0.090669 |
| retail/gross_spend_30d | summer | robust | mae | 227.047974 | 206.237608 | 206.237608 |
| retail/gross_spend_30d | summer | robust | rmse | 1233.594642 | 1510.793955 | 1510.793955 |
| retail/gross_spend_30d | autumn | robust | mae | 245.923865 | 242.357457 | 242.357457 |
| retail/gross_spend_30d | autumn | robust | rmse | 969.343797 | 1257.549820 | 1257.549820 |
| retail/gross_spend_30d | later_overlapping_november | robust | mae | 250.193091 | 260.248604 | 260.248604 |
| retail/gross_spend_30d | later_overlapping_november | robust | rmse | 891.437504 | 1191.514690 | 1191.514690 |
| beijing/pm25_over_75_next_day | 2015 | linear | roc_auc | 0.251654 | 0.500000 | 0.742831 |
| beijing/pm25_over_75_next_day | 2015 | linear | brier | 0.437291 | 0.238242 | 0.204610 |
| beijing/pm25_over_75_next_day | 2015 | linear | ece_5_bins | 0.409367 | 0.071036 | 0.081001 |
| beijing/pm25_over_75_next_day | 2016 | persistence | roc_auc | 0.516729 | 0.500000 | 0.617401 |
| beijing/pm25_over_75_next_day | 2016 | persistence | brier | 0.286972 | 0.237212 | 0.228870 |
| beijing/pm25_over_75_next_day | 2016 | persistence | ece_5_bins | 0.178642 | 0.001460 | 0.040653 |
| beijing/pm25_over_75_next_day | later_2016_2017 | persistence | roc_auc | 0.552541 | 0.500000 | 0.865470 |
| beijing/pm25_over_75_next_day | later_2016_2017 | persistence | brier | 0.310514 | 0.258501 | 0.162956 |
| beijing/pm25_over_75_next_day | later_2016_2017 | persistence | ece_5_bins | 0.281744 | 0.094044 | 0.147797 |
| beijing/pm25_next_day | 2015 | persistence | mae | 61.192138 | 30.932195 | 36.973077 |
| beijing/pm25_next_day | 2015 | persistence | rmse | 70.583027 | 42.146525 | 53.824183 |
| beijing/pm25_next_day | 2016 | persistence | mae | 39.738593 | 33.889780 | 36.749559 |
| beijing/pm25_next_day | 2016 | persistence | rmse | 50.461935 | 46.277752 | 43.451469 |
| beijing/pm25_next_day | later_2016_2017 | persistence | mae | 48.419193 | 50.031892 | 54.406481 |
| beijing/pm25_next_day | later_2016_2017 | persistence | rmse | 64.116877 | 66.539076 | 97.004649 |

Every retail prediction is unchanged from the first stage. Retail binary ranking and Brier improve slightly versus the old histogram in all three periods; robust retail spend removes 12 negative estimates and improves MAE in the two old published periods. Retail RMSE worsens in all three periods, and later overlapping retail MAE also worsens.

The revised selector improves Beijing classification ranking over the original histogram on all three development periods. On the development late-2016 regression period, persistence worsens MAE from 48.419193 to 54.406481 and RMSE from 64.116877 to 97.004649. The February improvement does not erase that loss.

## Timing and reproducibility

| Dataset/task | Period | Fit seconds old / adaptive | Test feature seconds old / adaptive | Prediction seconds old / adaptive |
|---|---|---:|---:|---:|
| retail/purchase_30d | summer | 0.6768 / 0.7391 | 0.2141 / 0.0519 | 0.0244 / 0.0278 |
| retail/purchase_30d | autumn | 0.9249 / 1.2177 | 0.1985 / 0.0622 | 0.0364 / 0.0266 |
| retail/purchase_30d | later_overlapping_november | 1.1070 / 1.4742 | 0.1034 / 0.0316 | 0.0206 / 0.0155 |
| retail/gross_spend_30d | summer | 0.5621 / 0.4746 | 0.1488 / 0.0386 | 0.0165 / 0.0133 |
| retail/gross_spend_30d | autumn | 0.9343 / 0.6208 | 0.1352 / 0.0373 | 0.0185 / 0.0106 |
| retail/gross_spend_30d | later_overlapping_november | 0.6666 / 0.6517 | 0.0542 / 0.0134 | 0.0086 / 0.0049 |
| beijing/pm25_over_75_next_day | 2015 | 0.2368 / 0.3768 | 0.0512 / 0.0601 | 0.0251 / 0.0250 |
| beijing/pm25_over_75_next_day | 2016 | 0.9446 / 2.2033 | 0.0550 / 0.0701 | 0.0503 / 0.0502 |
| beijing/pm25_over_75_next_day | later_2016_2017 | 5.2543 / 3.5127 | 0.8031 / 1.3338 | 0.1093 / 0.0694 |
| beijing/pm25_over_75_next_day | confirmation_february_2017 | 1.3880 / 1.5746 | 0.0664 / 0.0796 | 0.0226 / 0.0182 |
| beijing/pm25_next_day | 2015 | 0.9428 / 1.2924 | 0.0764 / 0.0788 | 0.0742 / 0.0430 |
| beijing/pm25_next_day | 2016 | 1.1807 / 1.3474 | 0.1040 / 0.1188 | 0.0632 / 0.0274 |
| beijing/pm25_next_day | later_2016_2017 | 1.3451 / 3.0279 | 0.1261 / 0.2136 | 0.0657 / 0.0607 |
| beijing/pm25_next_day | confirmation_february_2017 | 1.4901 / 2.1499 | 0.0682 / 0.0869 | 0.0225 / 0.0127 |

Fit timing includes training feature compilation, candidate selection and refitting. Test feature compilation is shown separately from prediction. Numerical threads are limited to one. Peak RSS is cumulative process high-water memory, including both snapshots and preceding fits; it cannot establish per-model memory savings. Timings are single local runs and may include concurrent machine activity.

Total dataset runs including source loading: retail 18.897s; Beijing 75.622s.

No quality tuning followed the February scores. Source availability clocks/timezones are assumptions; complete-case filtering can bias station-days; repeating stations and dependent dates preclude IID confidence intervals. This small public-data confirmation is not independent external validation, a universal quality guarantee, or a foundation-model result.
