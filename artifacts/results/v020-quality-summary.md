# v0.2.0 frozen quality comparison

The executed adaptive backend has mixed results. Retrospective errors improve in several tasks, but the later Beijing period and overlapping later retail spend period show regressions. No overall prediction-quality superiority is established.

Protocol SHA256: `bc84eddf61bbf46583e511c15de396f4aa5a8fe3e93bdc505865b1ac5732275f`. Exact v0.1.0 commit: `7c84f7aaa0c573340837667ae63a6c9c7b71331a`.

All 48 fixed dataset/task/period/backend comparisons completed without failures. Test entity IDs and labels match exactly across backends; every adaptive validation training label is mature by its earlier validation cutoff. The rerun original histogram reproduces all 28,818 published prediction rows exactly, including their labels. See `v020-quality-verification.json`.

The old periods were already exposed. Beijing `later_2016_2017` is the newly fixed later period. Retail `later_overlapping_november` overlaps previously exposed outcome windows and is not blind.

| Dataset/task | Period | n | Selected | Metric | v0.1.0 histogram | Adaptive |
|---|---|---:|---|---|---:|---:|
| retail/purchase_30d | summer | 6456 | robust | roc_auc | 0.713795 | 0.716456 |
| retail/purchase_30d | summer | 6456 | robust | brier | 0.163338 | 0.162481 |
| retail/purchase_30d | summer | 6456 | robust | ece_5_bins | 0.003221 | 0.003350 |
| retail/purchase_30d | autumn | 7569 | robust | roc_auc | 0.695789 | 0.701140 |
| retail/purchase_30d | autumn | 7569 | robust | brier | 0.186558 | 0.185853 |
| retail/purchase_30d | autumn | 7569 | robust | ece_5_bins | 0.059153 | 0.058561 |
| retail/purchase_30d | later_overlapping_november | 4046 | histogram | roc_auc | 0.711890 | 0.713250 |
| retail/purchase_30d | later_overlapping_november | 4046 | histogram | brier | 0.198943 | 0.198370 |
| retail/purchase_30d | later_overlapping_november | 4046 | histogram | ece_5_bins | 0.090303 | 0.090669 |
| retail/gross_spend_30d | summer | 6456 | robust | mae | 227.047974 | 206.237608 |
| retail/gross_spend_30d | summer | 6456 | robust | rmse | 1233.594642 | 1510.793955 |
| retail/gross_spend_30d | autumn | 7569 | robust | mae | 245.923865 | 242.357457 |
| retail/gross_spend_30d | autumn | 7569 | robust | rmse | 969.343797 | 1257.549820 |
| retail/gross_spend_30d | later_overlapping_november | 4046 | robust | mae | 250.193091 | 260.248604 |
| retail/gross_spend_30d | later_overlapping_november | 4046 | robust | rmse | 891.437504 | 1191.514690 |
| beijing/pm25_over_75_next_day | 2015 | 216 | baseline | roc_auc | 0.251654 | 0.500000 |
| beijing/pm25_over_75_next_day | 2015 | 216 | baseline | brier | 0.437291 | 0.238242 |
| beijing/pm25_over_75_next_day | 2015 | 216 | baseline | ece_5_bins | 0.409367 | 0.071036 |
| beijing/pm25_over_75_next_day | 2016 | 168 | baseline | roc_auc | 0.516729 | 0.500000 |
| beijing/pm25_over_75_next_day | 2016 | 168 | baseline | brier | 0.286972 | 0.237212 |
| beijing/pm25_over_75_next_day | 2016 | 168 | baseline | ece_5_bins | 0.178642 | 0.001460 |
| beijing/pm25_over_75_next_day | later_2016_2017 | 216 | baseline | roc_auc | 0.552541 | 0.500000 |
| beijing/pm25_over_75_next_day | later_2016_2017 | 216 | baseline | brier | 0.310514 | 0.258501 |
| beijing/pm25_over_75_next_day | later_2016_2017 | 216 | baseline | ece_5_bins | 0.281744 | 0.094044 |
| beijing/pm25_next_day | 2015 | 216 | histogram | mae | 61.192138 | 30.932195 |
| beijing/pm25_next_day | 2015 | 216 | histogram | rmse | 70.583027 | 42.146525 |
| beijing/pm25_next_day | 2016 | 168 | histogram | mae | 39.738593 | 33.889780 |
| beijing/pm25_next_day | 2016 | 168 | histogram | rmse | 50.461935 | 46.277752 |
| beijing/pm25_next_day | later_2016_2017 | 216 | median | mae | 48.419193 | 50.031892 |
| beijing/pm25_next_day | later_2016_2017 | 216 | median | rmse | 64.116877 | 66.539076 |

Higher ROC-AUC is better; lower Brier, ECE, MAE and RMSE are better. Reliability bins, average precision, ranges and controls are retained in the full JSON and CSV results.

Binary Brier score improves in all six fixed periods. Beijing classification selects the training prior, resulting in AUC 0.5: calibration improves over the original histogram but ranking loses on 2016 and the later period. The historical control wins the later Beijing binary comparison (AUC 0.643802, Brier 0.249351).

Retail regression selects robust MAE-oriented fits. It removes the 12 negative legacy spend estimates across the fixed periods. MAE improves in the two exposed retail periods, but RMSE worsens in all three, and later overlapping MAE also worsens. Beijing later regression selects a training median and worsens MAE by 3.33% and RMSE by 3.78%.

| Dataset/task | Period | Fit seconds old / adaptive | Test feature seconds old / adaptive | Prediction seconds old / adaptive |
|---|---|---:|---:|---:|
| retail/purchase_30d | summer | 0.4534 / 0.6827 | 0.1374 / 0.0353 | 0.0202 / 0.0135 |
| retail/purchase_30d | autumn | 0.5539 / 1.2156 | 0.0999 / 0.0252 | 0.0181 / 0.0170 |
| retail/purchase_30d | later_overlapping_november | 0.5628 / 1.1973 | 0.0548 / 0.0140 | 0.0097 / 0.0090 |
| retail/gross_spend_30d | summer | 0.3115 / 0.4594 | 0.0752 / 0.0202 | 0.0121 / 0.0087 |
| retail/gross_spend_30d | autumn | 0.4519 / 0.7903 | 0.0858 / 0.0218 | 0.0131 / 0.0132 |
| retail/gross_spend_30d | later_overlapping_november | 1.0261 / 1.4489 | 0.0741 / 0.0256 | 0.0112 / 0.0126 |
| beijing/pm25_over_75_next_day | 2015 | 0.6828 / 0.7080 | 0.1579 / 0.1973 | 0.1033 / 0.0375 |
| beijing/pm25_over_75_next_day | 2016 | 0.6071 / 0.7648 | 0.0792 / 0.0953 | 0.0322 / 0.0190 |
| beijing/pm25_over_75_next_day | later_2016_2017 | 1.9349 / 1.4762 | 0.0840 / 0.1076 | 0.0748 / 0.1171 |
| beijing/pm25_next_day | 2015 | 0.5086 / 0.8835 | 0.2832 / 0.2827 | 0.0442 / 0.0547 |
| beijing/pm25_next_day | 2016 | 0.8727 / 1.3628 | 0.0660 / 0.0802 | 0.1269 / 0.0318 |
| beijing/pm25_next_day | later_2016_2017 | 2.5050 / 2.2123 | 0.1474 / 0.1523 | 0.0570 / 0.0298 |

Fit seconds include training feature compilation and adaptive selection. Prediction seconds exclude test feature compilation, shown separately. Single runs describe this machine rather than a stable speed benchmark. Numerical threads are limited to one. Peak RSS is recorded per comparison but is cumulative process high-water memory, including both loaded snapshots and preceding fits; it cannot support per-model memory claims.

Total dataset runs: retail 14.204s; Beijing 27.303s. Source loading is included.

No test-driven changes followed these results. Complete-case label filtering, assumed availability clocks, recurring entities and overlapping horizons limit generalization; no IID confidence interval, independent external validation or foundation-model claim is made.
