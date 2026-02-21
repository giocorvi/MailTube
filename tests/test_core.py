from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from mail_tube.db import Database
from mail_tube.refresh import refresh_profile
from mail_tube.youtube import (
    ChannelInfo,
    VideoInfo,
    build_embed_url,
    duration_matches_bucket,
    extract_video_id,
    published_at_on_or_after,
    title_matches_keyword,
)


class YouTubeHelpersTest(unittest.TestCase):
    def test_extract_video_id_from_common_urls(self) -> None:
        self.assertEqual(extract_video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(
            extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(extract_video_id("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(
            extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        self.assertIsNone(extract_video_id("https://example.com/not-youtube"))

    def test_build_embed_url_has_required_parameters(self) -> None:
        url = build_embed_url("dQw4w9WgXcQ", autoplay=True)
        self.assertIn("autoplay=1", url)
        self.assertIn("controls=1", url)
        self.assertIn("rel=0", url)
        self.assertIn("iv_load_policy=3", url)

    def test_keyword_match_is_case_insensitive_substring(self) -> None:
        self.assertTrue(title_matches_keyword("Great Cat Video", "cat"))
        self.assertTrue(title_matches_keyword("Studio Session", "studio"))
        self.assertTrue(title_matches_keyword("Luca&#39;s Studio Session", "luca's"))
        self.assertTrue(title_matches_keyword("Luca\u2019s Studio Session", "luca's"))
        self.assertFalse(title_matches_keyword("Dog clip", "cat"))
        self.assertTrue(title_matches_keyword("Anything", None))
        self.assertTrue(title_matches_keyword("Anything", ""))

    def test_duration_bucket_matching(self) -> None:
        self.assertTrue(duration_matches_bucket(120, "short"))
        self.assertFalse(duration_matches_bucket(300, "short"))
        self.assertTrue(duration_matches_bucket(300, "medium"))
        self.assertTrue(duration_matches_bucket(1200, "medium"))
        self.assertFalse(duration_matches_bucket(1201, "medium"))
        self.assertTrue(duration_matches_bucket(1201, "long"))
        self.assertTrue(duration_matches_bucket(None, None))
        self.assertFalse(duration_matches_bucket(None, "short"))

    def test_published_at_cutoff_matching(self) -> None:
        self.assertTrue(published_at_on_or_after("2026-01-02T00:00:00Z", None))
        self.assertTrue(published_at_on_or_after("2026-01-02T00:00:00Z", "2026-01-02T00:00:00Z"))
        self.assertFalse(published_at_on_or_after("2026-01-01T23:59:59Z", "2026-01-02T00:00:00Z"))
        self.assertFalse(published_at_on_or_after(None, "2026-01-02T00:00:00Z"))


class RefreshFlowTest(unittest.TestCase):
    def test_init_migrates_legacy_inbox_table_without_is_starred(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            with db.connect() as conn:
                conn.executescript(
                    """
                    PRAGMA foreign_keys = ON;
                    CREATE TABLE profiles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        is_active INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE profile_filters (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                        channel_input TEXT NOT NULL,
                        channel_id TEXT,
                        channel_title TEXT,
                        keyword TEXT,
                        is_valid INTEGER NOT NULL DEFAULT 1,
                        validation_error TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE videos (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        youtube_video_id TEXT NOT NULL UNIQUE,
                        title TEXT NOT NULL,
                        channel_id TEXT,
                        channel_title TEXT,
                        published_at TEXT,
                        thumbnail_url TEXT,
                        video_url TEXT NOT NULL,
                        fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE inbox_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                        video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
                        status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'watched', 'dismissed')),
                        first_inboxed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        watched_at TEXT,
                        opened_count INTEGER NOT NULL DEFAULT 0,
                        UNIQUE(profile_id, video_id)
                    );
                    """
                )
                conn.commit()

            db.init()
            with db.connect() as conn:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(inbox_items)")}
                self.assertIn("is_starred", columns)
                filter_columns = {row["name"] for row in conn.execute("PRAGMA table_info(profile_filters)")}
                self.assertIn("duration_bucket", filter_columns)
                self.assertIn("since_mode", filter_columns)
                self.assertIn("since_published_after", filter_columns)
                index_names = {row["name"] for row in conn.execute("PRAGMA index_list(inbox_items)")}
                self.assertIn("idx_inbox_profile_starred", index_names)

    def test_refresh_uses_channel_and_optional_keyword(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            db.init()
            profile_id = db.create_profile("default")
            db.add_filter(profile_id, "@abc", "cat")
            db.add_filter(profile_id, "@bbc", None)

            def fake_resolve(channel_input: str, *, api_key: str) -> ChannelInfo:
                if channel_input == "@abc":
                    return ChannelInfo(channel_id="UCaaaaaaaaaaaaaaaaaaaaaa", channel_title="ABC")
                if channel_input == "@bbc":
                    return ChannelInfo(channel_id="UCbbbbbbbbbbbbbbbbbbbbbb", channel_title="BBC")
                raise AssertionError("Unexpected input")

            def fake_videos(channel_id: str, *, api_key: str, max_results: int = 50) -> list[VideoInfo]:
                if channel_id == "UCaaaaaaaaaaaaaaaaaaaaaa":
                    return [
                        VideoInfo(
                            youtube_video_id="AAAAAAAAAAA",
                            title="cat news",
                            channel_id=channel_id,
                            channel_title="ABC",
                            published_at="2026-01-01T00:00:00Z",
                            thumbnail_url="",
                            video_url="https://www.youtube.com/watch?v=AAAAAAAAAAA",
                        ),
                        VideoInfo(
                            youtube_video_id="BBBBBBBBBBB",
                            title="dog news",
                            channel_id=channel_id,
                            channel_title="ABC",
                            published_at="2026-01-02T00:00:00Z",
                            thumbnail_url="",
                            video_url="https://www.youtube.com/watch?v=BBBBBBBBBBB",
                        ),
                    ]
                return [
                    VideoInfo(
                        youtube_video_id="CCCCCCCCCCC",
                        title="world update",
                        channel_id=channel_id,
                        channel_title="BBC",
                        published_at="2026-01-03T00:00:00Z",
                        thumbnail_url="",
                        video_url="https://www.youtube.com/watch?v=CCCCCCCCCCC",
                    )
                ]

            with (
                patch("mail_tube.refresh.resolve_channel_input", side_effect=fake_resolve),
                patch("mail_tube.refresh.fetch_channel_videos", side_effect=fake_videos),
            ):
                outcome = refresh_profile(db, profile_id, api_key="fake-key")

            self.assertEqual(outcome.status, "ok")
            self.assertEqual(outcome.matched_count, 2)
            self.assertEqual(outcome.added_count, 2)

            items = db.list_inbox_items(profile_id, limit=20, offset=0)
            video_ids = {row["youtube_video_id"] for row in items}
            self.assertSetEqual(video_ids, {"AAAAAAAAAAA", "CCCCCCCCCCC"})

    def test_refresh_applies_duration_bucket(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            db.init()
            profile_id = db.create_profile("default")
            db.add_filter(profile_id, "@abc", None, "short")

            def fake_resolve(channel_input: str, *, api_key: str) -> ChannelInfo:
                if channel_input == "@abc":
                    return ChannelInfo(channel_id="UCaaaaaaaaaaaaaaaaaaaaaa", channel_title="ABC")
                raise AssertionError("Unexpected input")

            def fake_videos(
                channel_id: str,
                *,
                api_key: str,
                max_results: int = 50,
                include_duration: bool = False,
            ) -> list[VideoInfo]:
                self.assertTrue(include_duration)
                return [
                    VideoInfo(
                        youtube_video_id="HHHHHHHHHHH",
                        title="short clip",
                        channel_id=channel_id,
                        channel_title="ABC",
                        published_at="2026-01-01T00:00:00Z",
                        thumbnail_url="",
                        video_url="https://www.youtube.com/watch?v=HHHHHHHHHHH",
                        duration_seconds=60,
                    ),
                    VideoInfo(
                        youtube_video_id="IIIIIIIIIII",
                        title="long clip",
                        channel_id=channel_id,
                        channel_title="ABC",
                        published_at="2026-01-02T00:00:00Z",
                        thumbnail_url="",
                        video_url="https://www.youtube.com/watch?v=IIIIIIIIIII",
                        duration_seconds=1800,
                    ),
                ]

            with (
                patch("mail_tube.refresh.resolve_channel_input", side_effect=fake_resolve),
                patch("mail_tube.refresh.fetch_channel_videos", side_effect=fake_videos),
            ):
                outcome = refresh_profile(db, profile_id, api_key="fake-key")

            self.assertEqual(outcome.status, "ok")
            self.assertEqual(outcome.matched_count, 1)
            self.assertEqual(outcome.added_count, 1)
            items = db.list_inbox_items(profile_id, limit=20, offset=0)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["youtube_video_id"], "HHHHHHHHHHH")

    def test_refresh_respects_from_now_cutoff(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            db.init()
            profile_id = db.create_profile("default")
            filter_id = db.add_filter(profile_id, "@abc", None, None, "from_now")
            with db.connect() as conn:
                conn.execute(
                    "UPDATE profile_filters SET since_published_after = ? WHERE id = ?",
                    ("2026-01-02T00:00:00Z", filter_id),
                )
                conn.commit()

            def fake_resolve(channel_input: str, *, api_key: str) -> ChannelInfo:
                if channel_input == "@abc":
                    return ChannelInfo(channel_id="UCaaaaaaaaaaaaaaaaaaaaaa", channel_title="ABC")
                raise AssertionError("Unexpected input")

            def fake_videos(channel_id: str, *, api_key: str, max_results: int = 50) -> list[VideoInfo]:
                return [
                    VideoInfo(
                        youtube_video_id="JJJJJJJJJJJ",
                        title="older",
                        channel_id=channel_id,
                        channel_title="ABC",
                        published_at="2026-01-01T00:00:00Z",
                        thumbnail_url="",
                        video_url="https://www.youtube.com/watch?v=JJJJJJJJJJJ",
                    ),
                    VideoInfo(
                        youtube_video_id="KKKKKKKKKKK",
                        title="newer",
                        channel_id=channel_id,
                        channel_title="ABC",
                        published_at="2026-01-03T00:00:00Z",
                        thumbnail_url="",
                        video_url="https://www.youtube.com/watch?v=KKKKKKKKKKK",
                    ),
                ]

            with (
                patch("mail_tube.refresh.resolve_channel_input", side_effect=fake_resolve),
                patch("mail_tube.refresh.fetch_channel_videos", side_effect=fake_videos),
            ):
                outcome = refresh_profile(db, profile_id, api_key="fake-key")

            self.assertEqual(outcome.status, "ok")
            self.assertEqual(outcome.matched_count, 1)
            self.assertEqual(outcome.added_count, 1)
            items = db.list_inbox_items(profile_id, limit=20, offset=0)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["youtube_video_id"], "KKKKKKKKKKK")

    def test_inbox_dedupe_is_per_profile(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            db.init()
            p1 = db.create_profile("p1")
            p2 = db.create_profile("p2")
            video_db_id = db.upsert_video(
                VideoInfo(
                    youtube_video_id="DDDDDDDDDDD",
                    title="same video",
                    channel_id="UC1",
                    channel_title="Channel",
                    published_at="2026-01-01T00:00:00Z",
                    thumbnail_url="",
                    video_url="https://www.youtube.com/watch?v=DDDDDDDDDDD",
                )
            )
            self.assertTrue(db.insert_inbox_item(p1, video_db_id))
            self.assertFalse(db.insert_inbox_item(p1, video_db_id))
            self.assertTrue(db.insert_inbox_item(p2, video_db_id))

    def test_trash_status_moves_item_out_of_inbox(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            db.init()
            profile_id = db.create_profile("main")
            video_db_id = db.upsert_video(
                VideoInfo(
                    youtube_video_id="EEEEEEEEEEE",
                    title="trash me",
                    channel_id="UCtrash",
                    channel_title="Trash",
                    published_at="2026-01-01T00:00:00Z",
                    thumbnail_url="",
                    video_url="https://www.youtube.com/watch?v=EEEEEEEEEEE",
                )
            )
            db.insert_inbox_item(profile_id, video_db_id)
            inbox_items = db.list_inbox_items(profile_id, limit=20, offset=0, statuses=("new",))
            self.assertEqual(len(inbox_items), 1)

            inbox_item_id = int(inbox_items[0]["inbox_item_id"])
            db.mark_inbox_trashed(inbox_item_id)

            inbox_items_after = db.list_inbox_items(profile_id, limit=20, offset=0, statuses=("new",))
            trash_items = db.list_inbox_items(profile_id, limit=20, offset=0, statuses=("dismissed",))
            self.assertEqual(len(inbox_items_after), 0)
            self.assertEqual(len(trash_items), 1)

    def test_starred_filter_and_trash_clears_star(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            db.init()
            profile_id = db.create_profile("starred")
            first_video_id = db.upsert_video(
                VideoInfo(
                    youtube_video_id="FFFFFFFFFFF",
                    title="save me",
                    channel_id="UCsave",
                    channel_title="Save",
                    published_at="2026-01-01T00:00:00Z",
                    thumbnail_url="",
                    video_url="https://www.youtube.com/watch?v=FFFFFFFFFFF",
                )
            )
            second_video_id = db.upsert_video(
                VideoInfo(
                    youtube_video_id="GGGGGGGGGGG",
                    title="normal",
                    channel_id="UCnormal",
                    channel_title="Normal",
                    published_at="2026-01-02T00:00:00Z",
                    thumbnail_url="",
                    video_url="https://www.youtube.com/watch?v=GGGGGGGGGGG",
                )
            )
            db.insert_inbox_item(profile_id, first_video_id)
            db.insert_inbox_item(profile_id, second_video_id)
            items = db.list_inbox_items(profile_id, limit=20, offset=0, statuses=("new",))
            by_video_id = {row["youtube_video_id"]: row for row in items}

            starred_item_id = int(by_video_id["FFFFFFFFFFF"]["inbox_item_id"])
            unstarred_item_id = int(by_video_id["GGGGGGGGGGG"]["inbox_item_id"])
            db.mark_inbox_starred(starred_item_id, starred=True)
            db.mark_inbox_starred(unstarred_item_id, starred=False)

            starred_items = db.list_inbox_items(
                profile_id,
                limit=20,
                offset=0,
                statuses=("new", "watched"),
                starred_only=True,
            )
            self.assertEqual(len(starred_items), 1)
            self.assertEqual(starred_items[0]["youtube_video_id"], "FFFFFFFFFFF")

            db.mark_inbox_trashed(starred_item_id)
            starred_items_after_trash = db.list_inbox_items(
                profile_id,
                limit=20,
                offset=0,
                statuses=("new", "watched"),
                starred_only=True,
            )
            self.assertEqual(len(starred_items_after_trash), 0)

            item_after_trash = db.get_inbox_item_with_video(starred_item_id)
            self.assertIsNotNone(item_after_trash)
            self.assertEqual(item_after_trash["status"], "dismissed")
            self.assertEqual(int(item_after_trash["is_starred"]), 0)


if __name__ == "__main__":
    unittest.main()
