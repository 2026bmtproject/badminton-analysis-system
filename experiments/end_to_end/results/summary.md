# E2E processing time (cold start)

RTF = E2E / video length; RTF local = local (E2E minus score_recognition) / video length. Below 1 means faster than real time.

| match | video | rallies | rally time | E2E | local (no API) | RTF | RTF local | E2E / rally-sec | local / rally-sec | ok |
|---|---|---|---|---|---|---|---|---|---|---|
| test1 | 8:10 | 23 | 5:20 | 19:28 | 10:47 | 2.381 | 1.32 | 3.65 | 2.02 | yes |
| test5 | 57:19 | 70 | 14:59 | 37:58 | 26:47 | 0.662 | 0.467 | 2.53 | 1.79 | yes |
| test4 | 71:56 | 78 | 18:16 | 42:28 | 32:55 | 0.591 | 0.458 | 2.32 | 1.8 | yes |
| test2 | 100:52 | 123 | 31:24 | 74:53 | 57:13 | 0.743 | 0.567 | 2.38 | 1.82 | yes |
| TTY_vs_ASY_2023 | 94:00 | 147 | 27:49 | 90:05 | 63:47 | 0.958 | 0.679 | 3.24 | 2.29 | yes |

## Share of total time by stage (5 successful matches)

| stage | total | share |
|---|---|---|
| shuttle_tracking | 91:32 | 34.6% |
| score_recognition | 73:22 | 27.8% |
| pose | 73:02 | 27.6% |
| match_segmentation | 18:35 | 7.0% |
| audio_highlight | 2:24 | 0.9% |
| event_detection | 2:01 | 0.8% |
| court_detection | 1:42 | 0.6% |
| player_identity | 1:28 | 0.6% |
| stroke_classification | 0:10 | 0.1% |
| highlight_ranking | 0:00 | 0.0% |
