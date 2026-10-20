# Measurements

Output directory of the scripts in [`tools/`](../../tools). Each run writes
`<name>-<UTC timestamp>.json` and a `.md` summary here, for example
`signature-scope-20261021T101500Z.json`. The README section
[Measurements](../../README.md#measurements) explains how to run them.

Reports hold HTTP statuses, API codes, sizes, timings and cookie names. They
hold no cookie values and no signatures. Read a report before committing it
anyway: the `message` field is copied from the server's reply, and the image
report lists CDN URLs.

No measurements have been committed yet.
