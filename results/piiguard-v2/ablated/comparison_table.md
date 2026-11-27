| run | adapter | clean leak | hard leak | control raw FP | control contract FP | blocked clean/hard |
|---|---|---|---|---|---|---|
| committed | piiguard | 534/600 (0.890) [0.723, 1.000] | 150/200 (0.750) [0.497, 1.000] | 25/200 (0.125) [0.000, 0.375] | 25/200 (0.125) [0.000, 0.375] | 0/0 |
| committed | presidio | 226/600 (0.377) [0.133, 0.643] | 136/200 (0.680) [0.467, 0.864] | 144/200 (0.720) [0.440, 1.000] | 49/200 (0.245) [0.035, 0.515] | 0/0 |
| committed | piiguard-union-presidio | 214/600 (0.357) [0.108, 0.635] | 96/200 (0.480) [0.244, 0.705] | 144/200 (0.720) [0.440, 1.000] | 49/200 (0.245) [0.035, 0.515] | 0/0 |
| first one-shot v2 (f540775e, test-only cues, historical) | piiguard-v2 | 8/600 (0.013) [0.000, 0.037] | 33/200 (0.165) [0.000, 0.414] | 0/200 (0.000) [0.000, 0.000] | 0/200 (0.000) [0.000, 0.000] | 0/0 |
| first one-shot v2 (f540775e, test-only cues, historical) | piiguard-v2-union-filtered | 8/600 (0.013) [0.000, 0.037] | 33/200 (0.165) [0.000, 0.414] | 0/200 (0.000) [0.000, 0.000] | 0/200 (0.000) [0.000, 0.000] | 0/0 |
| SECOND run v2-ablated (21d97fe2, recommended) | piiguard-v2 | 108/600 (0.180) [0.007, 0.423] | 33/200 (0.165) [0.000, 0.414] | 0/200 (0.000) [0.000, 0.000] | 0/200 (0.000) [0.000, 0.000] | 0/0 |
| SECOND run v2-ablated (21d97fe2, recommended) | piiguard-v2-union-filtered | 108/600 (0.180) [0.007, 0.423] | 33/200 (0.165) [0.000, 0.414] | 0/200 (0.000) [0.000, 0.000] | 0/200 (0.000) [0.000, 0.000] | 0/0 |

- clean: n=600; prompts whose scoring fields differ first-vs-ablated v2: 100; ablated v2 vs union-filtered: 0
- hard: n=200; prompts whose scoring fields differ first-vs-ablated v2: 0; ablated v2 vs union-filtered: 0
- control: n=200; prompts whose scoring fields differ first-vs-ablated v2: 0; ablated v2 vs union-filtered: 0
