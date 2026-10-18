# Architecture

```mermaid
flowchart TD
    F[Fixed synthetic CSV and calendar] --> D[DuckDB raw tables]
    F --> O[Independent DuckDB oracle]
    Q[Maintained reference SQL] --> O
    Y[Authored dbt project] --> B[Official dbt build and manifest]
    D --> B
    B --> M[Native MetricFlow compiler]
    B --> C[Official MSI to Ossie converter]
    C --> V[Official Ossie Pydantic validation]
    V --> R[Official Ossie to MSI converter]
    R --> M
    V --> P[Official Microsoft converter: TMSL and returned Ossie]
    T[Authored TMDL fixture] --> I[Existing bounded inventory and DAX checks]
    M --> E[Execute generated DuckDB SQL]
    E --> N[Typed numerical comparator]
    O --> N
    K[Independent bounded semantic contract] --> S[Static semantic inspection]
    B --> S
    R --> S
    P --> S
    N --> J[JSON evidence: separate structure, semantics and execution]
    S --> J
    I --> J
```

The numerical oracle reads raw tables in a separate in-memory connection. Native
models and queries use a generated disk database with the same typed fixture data.
Neither the oracle nor the expected semantic contract comes from converter output.

The returned MSI manifest is used unchanged. Its compiler initialization currently
fails; the graph describes an attempted path, not a successful round-trip execution.
Microsoft output is inspected as metadata. TOM and a DAX execution engine are absent.

The lab is a separately locked package inside the existing repository. The relative
editable parent dependency reuses its static inspection implementation. Generated
profiles, target directories, databases, logs and environments stay in `.tmp`.
The original captured audit and its provenance manifest remain intact.
