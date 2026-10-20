# Findings

Each item lists the claim, how it was verified (as far as the repository shows),
where it is implemented, and its limits.

**Status of measurements:** none have been run yet with the scripts in
[`tools/`](../tools/), and no reports are recorded in
[`docs/measurements/`](measurements/). Where an item below says a result was
measured, the basis is a source comment or a commit message, not a stored
report. `--dry-run` lists the requests a script would send without sending any.

### 1. Request signing covers the URI only

- **Claim:** the `x-s`/`x-t` signature covers the request URI only, not the
  request body and not the time. `POST /api/sns/web/v1/search/notes` has no
  query string, so one captured signature is reused for any keyword, page and
  sort order.
- **Verification:** no test or script in the repository exercises this; the
  code relies on it (the stored signature is sent unchanged with every search
  page request, only the JSON body differs). Commit `4e0b954` describes the
  signature as URI-bound and body-agnostic.
- **Implementation:** `rednote_scraper/credentials.py` (`from_har`,
  `Credentials.headers_for`), `rednote_scraper/client.py` (`_search_page`).
- **Reproduce:** [`tools/measure_signature_scope.py`](../tools/measure_signature_scope.py)
  (changed body, URI and time, with negative controls) and
  [`tools/measure_signature_lifetime.py`](../tools/measure_signature_lifetime.py)
  (signature lifetime); see [measurements](measurements/README.md).
- **Limits:** only POST endpoints without a query string are kept; GET
  signatures are discarded by `from_har`. The client uses only the signature
  for the search endpoint. Signature lifetime is not established: the client
  raises `SignatureExpiredError` on API code `-1`, and `rednote auth refresh`
  re-harvests the signature.

### 2. Only two cookies are significant

- **Claim:** leave-one-out probing showed that only `web_session` and
  `x-rednote-datactry` matter; the other cookies the browser sends are not
  required.
- **Verification:** recorded in a source comment on `REQUIRED_COOKIES`
  ("verified by leave-one-out probing of the live endpoint"). The probing
  procedure and results are not in the repository. The test
  `test_check_cookie_rejects_a_cookie_without_a_session` covers the validation
  function only, not API behaviour.
- **Implementation:** `rednote_scraper/credentials.py` (`REQUIRED_COOKIES`,
  `check_cookie`).
- **Reproduce:** [`tools/measure_cookie_minimality.py`](../tools/measure_cookie_minimality.py)
  (leave-one-out, plus a joint test of the two required cookies); see
  [measurements](measurements/README.md).
- **Limits:** the stored cookie is not reduced: `import-har` and
  `auth refresh` save the full cookie, and `check_cookie` only enforces the two
  required values. Leave-one-out shows each remaining cookie is individually
  dispensable.

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
- **Implementation:** `rednote_scraper/parsing.py` (`original_image_url`,
  `_pick_image_url`); `--preview-images` keeps the preview URLs.
- **Reproduce:** [`tools/measure_image_sizes.py`](../tools/measure_image_sizes.py);
  see [measurements](measurements/README.md).
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
- **Implementation:** `rednote_scraper/browser.py` (`login`, `harvest`),
  `rednote_scraper/credentials.py` (`PROFILE_DIR`, `from_har`),
  `rednote_scraper/cli.py` (`auth login`, `auth refresh`, `--auto-refresh`).
- **Limits:** requires the optional `browser` extra and Playwright Chromium.
  `auth refresh` re-harvests from the profile's current session and does not
  create one; if the profile is logged out it raises `CredentialsError` and
  points to `auth login`. The app-bound-encryption rationale is stated for
  Windows only, and the repository has no code that reads a user's own browser
  profile, so the alternative is not tested.
