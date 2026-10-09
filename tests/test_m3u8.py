import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rekordbox_toolbox.m3u8 import (
    PlaylistMapping,
    apply_playlists,
    load_config,
    prepare_playlists,
    read_m3u8,
)


class Content:
    def __init__(self, track_id, path):
        self.ID = track_id
        self.FolderPath = str(path)


class Song:
    def __init__(self, content_id, track_no, playlist):
        self.ContentID = content_id
        self.TrackNo = track_no
        self.Playlist = playlist


class Playlist:
    def __init__(self, playlist_id, name, parent_id="root", attribute=0, songs=None):
        self.ID = playlist_id
        self.Name = name
        self.ParentID = parent_id
        self.Attribute = attribute
        self.Songs = songs or []


class FakeDatabase:
    def __init__(self, contents, playlists):
        self.contents = contents
        self.playlists = playlists
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        rows = self.contents if model is Content else self.playlists
        return SimpleNamespace(all=lambda: rows)

    def delete(self, song):
        song.Playlist.Songs.remove(song)

    def add_to_playlist(self, playlist, content):
        playlist.Songs.append(Song(content.ID, len(playlist.Songs) + 1, playlist))

    def create_playlist_folder(self, name, parent=None):
        playlist = Playlist(str(len(self.playlists) + 1), name, parent.ID if parent else "root", 1)
        self.playlists.append(playlist)
        return playlist

    def create_playlist(self, name, parent=None):
        playlist = Playlist(str(len(self.playlists) + 1), name, parent.ID if parent else "root")
        self.playlists.append(playlist)
        return playlist

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class M3U8Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_load_config_resolves_relative_source_to_config_directory(self):
        source = self.root / "sets" / "warmup.m3u8"
        source.parent.mkdir()
        source.write_text("", encoding="utf-8")
        config = self.root / "config.json"
        config.write_text(
            json.dumps(
                {"playlists": [{"m3u8": "sets/warmup.m3u8", "rekordbox_path": "House/Set"}]}
            ),
            encoding="utf-8",
        )

        mappings = load_config(config)

        self.assertEqual(mappings, [PlaylistMapping(source, "House/Set")])

    def test_load_config_rejects_a_playlist_used_as_a_parent_folder(self):
        source = self.root / "set.m3u8"
        source.write_text("", encoding="utf-8")
        config = self.root / "config.json"
        config.write_text(
            json.dumps(
                {
                    "playlists": [
                        {"m3u8": "set.m3u8", "rekordbox_path": "House"},
                        {"m3u8": "set.m3u8", "rekordbox_path": "House/Opening"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "parent folder"):
            load_config(config)

    def test_read_m3u8_skips_comments_and_resolves_relative_paths(self):
        source = self.root / "exports" / "list.m3u8"
        source.parent.mkdir()
        source.write_text(
            "\ufeff#EXTM3U\n#EXTINF:180,Artist - Title\n../audio/one.mp3\n\n",
            encoding="utf-8",
        )

        paths = read_m3u8(source)

        self.assertEqual(paths, [self.root / "audio" / "one.mp3"])

    def test_prepare_and_apply_replaces_playlist_in_m3u_order(self):
        first = Content("1", self.root / "one.mp3")
        second = Content("2", self.root / "two.mp3")
        playlist = Playlist("10", "Set", songs=[])
        playlist.Songs = [Song("old", 1, playlist)]
        db = FakeDatabase([first, second], [playlist])
        source = self.root / "set.m3u8"
        source.write_text("two.mp3\none.mp3\n", encoding="utf-8")
        mapping = PlaylistMapping(source, "Set")
        tables = types.ModuleType("pyrekordbox.db6.tables")
        tables.DjmdContent = Content
        tables.DjmdPlaylist = Playlist
        db6 = types.ModuleType("pyrekordbox.db6")
        db6.__path__ = []
        db6.tables = tables
        package = types.ModuleType("pyrekordbox")
        package.__path__ = []
        with patch.dict(
            sys.modules,
            {
                "pyrekordbox": package,
                "pyrekordbox.db6": db6,
                "pyrekordbox.db6.tables": tables,
            },
        ):
            plan = prepare_playlists(db, [mapping])
            self.assertEqual([track.ID for track in plan[0].tracks], ["2", "1"])
            self.assertTrue(plan[0].changed)
            updated = apply_playlists(db, plan)

        self.assertEqual(updated, 1)
        self.assertEqual([song.ContentID for song in playlist.Songs], ["2", "1"])
        self.assertEqual(db.commits, 1)

    def test_prepare_reports_unmatched_paths_and_repeated_playlist_is_unchanged(self):
        content = Content("1", self.root / "one.mp3")
        playlist = Playlist("10", "Set")
        playlist.Songs = [Song("1", 1, playlist)]
        db = FakeDatabase([content], [playlist])
        source = self.root / "set.m3u8"
        source.write_text("one.mp3\nmissing.mp3\n", encoding="utf-8")
        tables = types.ModuleType("pyrekordbox.db6.tables")
        tables.DjmdContent = Content
        tables.DjmdPlaylist = Playlist
        db6 = types.ModuleType("pyrekordbox.db6")
        db6.__path__ = []
        db6.tables = tables
        package = types.ModuleType("pyrekordbox")
        package.__path__ = []
        with patch.dict(
            sys.modules,
            {
                "pyrekordbox": package,
                "pyrekordbox.db6": db6,
                "pyrekordbox.db6.tables": tables,
            },
        ):
            plan = prepare_playlists(db, [PlaylistMapping(source, "Set")])

        self.assertEqual(len(plan[0].missing), 1)
        self.assertFalse(plan[0].changed)
        self.assertEqual(apply_playlists(db, plan), 0)
        self.assertEqual(db.commits, 0)


if __name__ == "__main__":
    unittest.main()
