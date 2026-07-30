# Third-party dependency notices

This repository does not vendor its Python dependencies. The public demo
declares the following direct runtime dependencies:

- `jsonschema` — MIT License
- `PyYAML` — MIT License
- `pydantic` — MIT License
- `tomli` — MIT License (Python 3.10 only)

The optional test dependency is:

- `pytest` — MIT License

`setuptools` is used as the build backend under the MIT License. Transitive
dependencies are resolved by the installer and remain governed by their own
licenses. Review the resolved environment before redistribution in a packaged
or vendored form.
