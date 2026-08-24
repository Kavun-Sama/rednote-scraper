# rednote-scraper

Browserless CLI scraper for rednote.com (Xiaohongshu) search results: returns
note links with their full-resolution photo and video URLs, and downloads them.

## How it works

The web API signs every request with an `x-s`/`x-t` header pair. Measurement of
the live endpoint shows the signature covers **the request URI only** — not the
request body and not the wall clock. `POST /api/sns/web/v1/search/notes` carries
no query string, so one signature captured from the browser stays valid for
arbitrary keywords, pages and sort orders until the session cookie expires.
No headless browser and no signing implementation are required.

`x-s-common` and `x-rap-param` are optional; the session cookie (`web_session`,
`a1`) is not — unauthenticated requests get `code -101`.

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

1. Log in to rednote.com in a browser.
2. Open DevTools → Network, run any search, right-click the
   `search/notes` request → Copy → Save all as HAR.
3. `rednote auth import-har capture.har`

Stored at `~/.config/rednote-scraper/credentials.json` (override with
`$REDNOTE_CREDENTIALS`). Re-import when requests start failing with
`signature or session cookie has expired`.

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
downloads; downloads are resumable (existing files are skipped, partial files
are written to `.part` and renamed on completion).

## Tests

```
python -m pytest tests
```
