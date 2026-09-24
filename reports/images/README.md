<!-- Copyright (c) Microsoft Corporation. Licensed under the MIT License. -->

# Cover logo (optional)

PDF reports have no cover logo unless you provide a PNG. No particular brand is required.

To add one, either:

- place a PNG named `logo.png` in this folder, or
- set `REPORT_LOGO` to its path, or pass `--logo <path>` to the
  [PDF generator](../_generate_pdf.py).

`--logo` takes precedence over `REPORT_LOGO`, then the file in this folder.
The logo appears above the report title. If a configured file is missing, the
generator warns and builds the PDF without a logo.
