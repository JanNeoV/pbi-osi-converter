# Conversion benchmark

Status: `PASSED`

| Fixture | Findings | Blocking | Golden failures | Expected outcome |
| --- | ---: | ---: | ---: | --- |
| `A_SUPPORTED` | 5 | 0 | 0 | `PASS` |
| `B_SEMANTIC_TRAPS` | 22 | 16 | 2 | `PASS` |
| `C_UNSUPPORTED` | 14 | 13 | 0 | `PASS` |

Syntactic generation is never used as semantic-success evidence; every supported metric is compared numerically.
