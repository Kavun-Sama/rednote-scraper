<h1 align="center">Rednote Scraper</h1>

<p align="center">
  <a href="https://github.com/Kavun-Sama/rednote-scraper/actions/workflows/ci.yml"><img src="https://github.com/Kavun-Sama/rednote-scraper/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

Browserless CLI scraper for RedNote (Xiaohongshu) search results, with
full-resolution image/video URL extraction and concurrent downloads.

## How it works

The web API signs every request with an `x-s`/`x-t` header pair. The signature
covers the request URI only, not the request body and not the wall clock.
`POST /api/sns/web/v1/search/notes` carries no query string, so a signature
captured from the browser is reused for arbitrary keywords, pages and sort
orders. No headless browser and no signing implementation are needed to search.

Of the cookie, only `web_session` and `x-rednote-datactry` matter, according to
leave-one-out probing; the rest of what the browser sends is decorative.
`x-s-common` is kept when it was captured and is not required.

Video stream URLs are absent from search results. They are read from the
server-rendered note page (`/discovery/item/<id>`), which embeds
`window.__INITIAL_STATE__` and is requested without a signature.

Image URLs in search results point at resized CDN previews. Dropping the
date/hash path segments and the `!variant` suffix and re-hosting on
`sns-img-qc.xhscdn.com` yields the original upload, typically about an order of
magnitude larger.

Verification status and limits of each of these are in
[docs/FINDINGS.md](docs/FINDINGS.md).

## Scope

- Works with your own session: a cookie and a request signature taken from a
  HAR capture you export, from the scraper's dedicated Playwright browser
  profile, or from a cookie value you paste.
- Does not bypass login or create sessions: sign-in is manual, in a visible
  browser window. No proxy rotation, fingerprint spoofing or CAPTCHA solving.
- Rate limits are on by default: 4 requests at a time, at least 0.5 s between
  the starts of two API requests, and a ceiling of 200 on `-n`. Override with
  `-c`, `--delay` and `--max-results`, or lift them with `--no-limits`. There is
  no backoff or retry beyond `--auto-refresh`.
- Intended for personal research use. Not affiliated with RedNote or
  Xiaohongshu; using this tool may violate their Terms of Service, and
  responsibility rests with you.

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

**Cookie only** — replaces the session cookie and keeps the stored signatures.
Paste one value from DevTools → Application → Cookies:

```
rednote auth cookie "web_session=040069..."
```

## Usage

```
rednote search "logo design" -n 100
rednote search "猫" -n 60 -t video -s popularity_descending --json cats.json
rednote search "coffee shop" -n 200 -d ./media
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
