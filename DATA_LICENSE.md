# Data sources and terms

The [MIT license](LICENSE) covers this project's original code and documentation.
It does not relicense data owned by other parties.

## Derived station reference

`data/processed/stations.csv` is a derived research artifact accompanying this
repository's station-history analysis and profiling report. It is computed from
the City of Chicago's Divvy trip records, published by Lyft Bikes and Scooters,
LLC. It is not an official station registry or opening-date register.

The source records are subject to the [Divvy Data License Agreement](https://divvybikes.com/data-license-agreement).
Consult those terms before reusing or redistributing the derived artifact. They
include restrictions on distributing source data as a standalone dataset and
allow source material in noncommercial analyses, reports and studies. This
repository supplies the derived table in that analytical context and does not
grant additional rights to the underlying data.

The raw trip ZIPs and station catalogs are downloaded locally from the official
interface and are not published in this repository. The manifest contains source
URLs and integrity hashes so others can retrieve the same inputs directly.

## Chicago boundary

The small boundary file in `data/reference/` comes from the [City of Chicago Data
Portal](https://data.cityofchicago.org/Facilities-Geographic-Boundaries/Boundaries-City/qqq8-j68g).
Its source URL and SHA-256 are recorded in `data/reference/sources.json`. It is
included for reproducible geographic filtering, and remains subject to the
source publisher's applicable terms.

This is an independent analysis, with no affiliation or endorsement implied
from Divvy, Lyft or the City of Chicago.
