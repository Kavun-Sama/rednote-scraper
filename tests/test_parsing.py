import json

from rednote_scraper.parsing import (
    apply_video_detail,
    original_image_url,
    parse_note_page,
    parse_search_item,
)


def test_original_image_url_strips_cdn_prefix_and_variant():
    url = (
        "http://sns-webpic-qc.xhscdn.com/202608250446/9e09005fbe451781af7ae5639829f257"
        "/notes_pre_post/1040g3k831tau66b9lmbg49vcnvj0q5la8krqmf0!nc_n_webp_mw_1"
    )
    assert original_image_url(url) == (
        "https://sns-img-qc.xhscdn.com/notes_pre_post/"
        "1040g3k831tau66b9lmbg49vcnvj0q5la8krqmf0"
    )


def test_parse_search_item_skips_non_note_models():
    assert parse_search_item({"model_type": "rec_query", "id": "x"}) is None


def test_parse_search_item_normalises_counters_and_media():
    item = {
        "model_type": "note",
        "id": "abc",
        "xsec_token": "tok",
        "note_card": {
            "type": "normal",
            "display_title": "title",
            "user": {"nickname": "author", "user_id": "uid"},
            "interact_info": {"liked_count": "1.2万"},
            "image_list": [
                {
                    "info_list": [
                        {"image_scene": "WB_PRV", "url": "http://h/d/x/f1!prv"},
                        {"image_scene": "WB_DFT", "url": "http://h/d/x/f2!dft"},
                    ]
                }
            ],
            "cover": {"url_default": "http://h/d/x/c!dft"},
        },
    }
    note = parse_search_item(item)
    assert note.liked_count == 12000
    assert note.images == ["https://sns-img-qc.xhscdn.com/f2"]
    assert note.media_count == 1
    assert note.url.startswith("https://www.rednote.com/discovery/item/abc?")


def test_parse_note_page_tolerates_undefined_literals():
    state = '{"note":{"noteDetailMap":{"n1":{"note":{"noteId":"n1","x":undefined}}}}}'
    html = f"<script>window.__INITIAL_STATE__={state}</script>"
    assert parse_note_page(html, "n1") == {"noteId": "n1", "x": None}


def test_apply_video_detail_picks_the_largest_stream():
    note = parse_search_item(
        {
            "model_type": "note",
            "id": "v",
            "xsec_token": "t",
            "note_card": {"type": "video", "user": {}, "interact_info": {}},
        }
    )
    detail = {
        "video": {
            "media": {
                "video": {"duration": 41},
                "stream": {
                    "h264": [
                        {"masterUrl": "small", "size": 10},
                        {"masterUrl": "big", "size": 99, "backupUrls": ["b"]},
                    ]
                },
            }
        }
    }
    apply_video_detail(note, detail)
    assert (note.video, note.video_backups, note.duration) == ("big", ["b"], 41)
