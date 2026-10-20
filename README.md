<h1 align="center">Rednote Scraper</h1>

<p align="center">
  <a href="https://github.com/Kavun-Sama/rednote-scraper/actions/workflows/ci.yml"><img src="https://github.com/Kavun-Sama/rednote-scraper/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

Browserless CLI scraper for RedNote (Xiaohongshu) search results, with
full-resolution image/video URL extraction and concurrent downloads.

## Scope

- Works with the user's own session. Credentials are a session cookie and a
  request signature taken from a HAR capture exported by the user, from the
  scraper's dedicated Playwright browser profile, or from a cookie value the
  user pastes (`rednote auth cookie`).
- Does not bypass login and does not create sessions. The scraper does not
  automate sign-in: `rednote auth login` opens a visible browser window and
  waits for a `web_session` cookie to appear. A session cannot be created
  without a browser (see [Findings](#5-why-renewal-needs-a-browser)).
- Not implemented: proxy rotation, fingerprint spoofing, CAPTCHA solving.
  Requests reuse the `User-Agent` captured with the credentials and a randomly
  generated `search_id`.
- Not implemented: rate limiting, request delays, backoff or retries. Request
  concurrency is bounded only by `-c` (default 8), search result pages are
  requested all at once, and `-n` has no upper limit. The single retry is
  `--auto-refresh`, which re-harvests credentials after a rejected request.
- Intended for personal research use.
  <!-- TODO(user): intended use is a statement of intent; nothing in the code enforces or checks it -->
- Not affiliated with RedNote or Xiaohongshu. Using this tool may violate their
  Terms of Service; responsibility rests with the user.
  <!-- TODO(user): the affiliation statement and the Terms of Service wording have not been checked against RedNote's current terms -->

## Findings

Each item lists the claim, how it was verified (as far as the repository shows),
where it is implemented, and its limits.

### 1. Request signing covers the URI only

- **Claim:** the `x-s`/`x-t` signature covers the request URI only, not the
  request body and not the time. `POST /api/sns/web/v1/search/notes` has no
  query string, so one captured signature is reused for any keyword, page and
  sort order.
- **Verification:** no test or script in the repository exercises this; the
  code relies on it (the stored signature is sent unchanged with every search
  page request, only the JSON body differs). Commit `4e0b954` describes the
  signature as URI-bound and body-agnostic.
  <!-- TODO(user): describe how the "URI only, not body, not clock" measurement was made; the experiment is not in the repository -->
- **Implementation:** `rednote_scraper/credentials.py` (`from_har`,
  `Credentials.headers_for`), `rednote_scraper/client.py` (`_search_page`).
- **Limits:** only POST endpoints without a query string are kept; GET
  signatures are discarded by `from_har`. The client uses only the signature
  for the search endpoint. Signature lifetime is not established: the client
  raises `SignatureExpiredError` on API code `-1`, and `rednote auth refresh`
  re-harvests the signature.
  <!-- TODO(user): state the measured signature lifetime; the previous README text said it stays valid until the session cookie expires, which conflicts with the code -1 handling in client.py -->

### 2. Only two cookies are significant

- **Claim:** leave-one-out probing showed that only `web_session` and
  `x-rednote-datactry` matter; the other cookies the browser sends are not
  required.
- **Verification:** recorded in a source comment on `REQUIRED_COOKIES`
  ("verified by leave-one-out probing of the live endpoint"). The probing
  procedure and results are not in the repository. The test
  `test_check_cookie_rejects_a_cookie_without_a_session` covers the validation
  function only, not API behaviour.
  <!-- TODO(user): add the probing procedure and results, and state which endpoints were probed -->
- **Implementation:** `rednote_scraper/credentials.py` (`REQUIRED_COOKIES`,
  `check_cookie`).
- **Limits:** the stored cookie is not reduced: `import-har` and
  `auth refresh` save the full cookie, and `check_cookie` only enforces the two
  required values. Leave-one-out shows each remaining cookie is individually
  dispensable.
  <!-- TODO(user): confirm a request with only the two cookies was tested -->

### 3. Video URLs come from the note page

- **Claim:** video URLs are not present in search results. They are read from
  the server-rendered note page (`/discovery/item/<id>`), from
  `window.__INITIAL_STATE__`, and that request needs no signature.
- **Verification:** parsing is covered by
  `test_parse_note_page_tolerates_undefined_literals` and
  `test_apply_video_detail_picks_the_largest_stream`. The request carries no
  `x-s`/`x-t` (`Credentials.web_headers` sends only the cookie and
  `User-Agent`). No test covers the absence of video URLs in search responses;
  `parse_search_item` reads none.
  <!-- TODO(user): state how the absence of video URLs in search responses was confirmed -->
- **Implementation:** `rednote_scraper/client.py` (`_resolve_video`),
  `rednote_scraper/parsing.py` (`parse_note_page`, `apply_video_detail`).
- **Limits:** requires the `xsec_token` from the search item, and the request
  is sent with the session cookie. One extra GET per video note. The embedded
  state is extracted with a regular expression and nothing is returned if the
  page layout differs; a failed request or parse leaves the note unresolved
  (printed as `<unresolved>`). The largest stream by `size` across `h264`,
  `h265`, `av1` and `h266` is chosen, regardless of codec.

### 4. Original images

- **Claim:** search results contain resized CDN previews. Removing the
  date/hash path segments and the `!variant` suffix and switching the host to
  `sns-img-qc.xhscdn.com` yields the original upload (~30x larger).
- **Verification:** `test_original_image_url_strips_cdn_prefix_and_variant`
  checks the rewrite for one URL, and
  `test_parse_search_item_normalises_counters_and_media` checks it through
  `parse_search_item`. No test checks that the rewritten URL serves the
  original or measures the size ratio.
  <!-- TODO(user): the ~30x figure has no measurement in the repository, and the docstring of original_image_url says "an order of magnitude larger"; state the measurement or align the two -->
- **Implementation:** `rednote_scraper/parsing.py` (`original_image_url`,
  `_pick_image_url`); `--preview-images` keeps the preview URLs.
- **Limits:** the rewrite assumes the first two path segments are the date and
  hash; a path with two or fewer segments keeps its path and only the host and
  suffix change. Only one URL shape is tested. Files are saved with a `.jpg`
  extension regardless of the actual format (`media_targets`). A failed
  original download is counted as failed; there is no fallback to the preview.

### 5. Why renewal needs a browser

- **Claim:** every `webapi.rednote.com` endpoint is signed, including the QR
  login flow. The QR status poll is a `GET` whose signature covers its query
  string, so it cannot be replayed the way the search signature is, and a
  session cannot be created offline. A dedicated Playwright profile is used
  instead of the user's own browser profile because Chromium-based browsers on
  Windows bind their cookie store to the application (app-bound encryption),
  which makes reading it from outside unreliable.
- **Verification:** no test covers it; the statements appear in the module
  docstrings of `browser.py` and `credentials.py`.
  <!-- TODO(user): state how these were verified (captures of the login flow, behaviour of app-bound encryption on your machine) -->
- **Implementation:** `rednote_scraper/browser.py` (`login`, `harvest`),
  `rednote_scraper/credentials.py` (`PROFILE_DIR`, `from_har`),
  `rednote_scraper/cli.py` (`auth login`, `auth refresh`, `--auto-refresh`).
- **Limits:** requires the optional `browser` extra and Playwright Chromium.
  `auth refresh` re-harvests from the profile's current session and does not
  create one; if the profile is logged out it raises `CredentialsError` and
  points to `auth login`. The app-bound-encryption rationale is stated for
  Windows only, and the repository has no code that reads a user's own browser
  profile, so the alternative is not tested.

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
