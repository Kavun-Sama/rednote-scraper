# rednote-scraper

[![CI](https://github.com/Kavun-Sama/rednote-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/Kavun-Sama/rednote-scraper/actions/workflows/ci.yml)

Browserless CLI scraper for RedNote (Xiaohongshu) search results, with
full-resolution image/video URL extraction and concurrent downloads.

## How it works

The web API signs every request with an `x-s`/`x-t` header pair. Measurement of
the live endpoint shows the signature covers **the request URI only** — not the
request body and not the wall clock. `POST /api/sns/web/v1/search/notes` carries
no query string, so one signature captured from the browser stays valid for
arbitrary keywords, pages and sort orders until the session cookie expires.
No headless browser and no signing implementation are required.

`x-s-common` and `x-rap-param` are optional. Leave-one-out probing of the
cookie shows only two pairs matter: `web_session` and `x-rednote-datactry`.
Everything else the browser sends is decorative.

Video stream URLs are absent from search results. They are read from the
server-rendered note page (`/discovery/item/<id>`), which embeds
`window.__INITIAL_STATE__` and needs no signature at all.

Image URLs in search results point at resized CDN previews. Dropping the
date/hash path segments and the `!variant` suffix and re-hosting on
`sns-img-qc.xhscdn.com` yields the original upload (~30x larger).

## Install

```
pip install -e .
```

## Credentials

Stored at `~/.config/rednote-scraper/credentials.json` (override with
`$REDNOTE_CREDENTIALS`).

**From a HAR capture** — log in to rednote.com, DevTools → Network, run a
search, right-click the `search/notes` request → Copy → Save all as HAR:

```
rednote auth import-har capture.har
```

**From a browser profile** — installs once, then renews without any manual
step:

```
pip install 'rednote-scraper[browser]' && playwright install chromium
rednote auth login      # visible window; scan the QR once
rednote auth refresh    # headless; re-harvests cookie + signature
rednote search "logo" -n 100 --auto-refresh
```

**Cookie only** — the signature does not expire with the session, so a dead
session can be revived by pasting one value from DevTools → Application →
Cookies:

```
rednote auth cookie "web_session=040069..."
```

### Why renewal needs a browser

Every `webapi.rednote.com` endpoint is signed, the QR login flow included, and
the QR status poll is a `GET` whose signature covers its query string — so it
cannot be replayed the way the search signature can. A session therefore cannot
be minted offline. A dedicated Playwright profile is used rather than the
user's own browser profile because Chromium-based browsers on Windows bind
their cookie store to the application (app-bound encryption), which makes
reading their cookies from outside unreliable.

## Usage

```
rednote search "logo design" -n 100
rednote search "猫" -n 60 -t video -s popularity_descending --json cats.json
rednote search "coffee shop" -n 200 -c 16 -d ./media
rednote download cats.json -o ./media
```

Options: `-n` result cap, `-s` sort (`general`, `time_descending`,
`popularity_descending`, `comment_descending`, `collect_descending`), `-t`
type filter (`all`, `video`, `image`), `-c` concurrency, `--preview-images`
to keep small CDN variants, `--no-video-urls` to skip detail requests.

Search pages are fetched in parallel, as are note detail lookups and media
downloads; downloads are restart-safe: completed files are skipped and
incomplete downloads are written to `.part` files before being atomically
renamed.

## Tests

```
pip install -e ".[dev]"
python -m pytest
```

## License

Copyright (C) 2026 Kavun-Sama

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version. The full text is in [LICENSE](LICENSE).

Section 13 applies: if you run a modified version of this tool as a network
service, its users must be able to obtain the modified source.

GitHub's sidebar labels the repository `AGPL-3.0` because its detector matches
the license text alone, and that text is identical for the `-only` and
`-or-later` variants — the label would not change if the grant did. The grant
is `-or-later`, stated here, in the SPDX header of every source file and in the
packaging metadata.
