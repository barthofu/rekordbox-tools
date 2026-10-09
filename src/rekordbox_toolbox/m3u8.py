"""Plan and apply Rekordbox playlist updates from M3U8 files."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyrekordbox.masterdb.models import DjmdContent, DjmdPlaylist

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlaylistMapping:
    source: Path
    target: str


@dataclass
class PlaylistPlan:
    mapping: PlaylistMapping
    target_exists: bool
    tracks: list[DjmdContent]
    missing: list[str]
    ambiguous: list[str]
    changed: bool


def load_config(config_path: Path) -> list[PlaylistMapping]:
    """Read playlist mappings and resolve source paths relative to the JSON file."""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read config {config_path}: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("playlists"), list):
        raise ValueError("Config must contain a 'playlists' array.")
    if not data["playlists"]:
        raise ValueError("Config 'playlists' must not be empty.")

    mappings: list[PlaylistMapping] = []
    seen_targets: set[str] = set()
    for index, item in enumerate(data["playlists"], start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Playlist mapping {index} must be an object.")
        source_value = item.get("m3u8")
        target_value = item.get("rekordbox_path")
        if not isinstance(source_value, str) or not source_value.strip():
            raise ValueError(f"Playlist mapping {index} needs a non-empty 'm3u8' path.")
        if not isinstance(target_value, str):
            raise ValueError(f"Playlist mapping {index} needs a 'rekordbox_path'.")

        source = Path(source_value)
        if not source.is_absolute():
            source = config_path.parent / source
        target = _validate_target(target_value)
        key = target.casefold()
        if key in seen_targets:
            raise ValueError(f"Multiple M3U8 files target the same Rekordbox path: {target}")
        seen_targets.add(key)
        mappings.append(PlaylistMapping(source=source, target=target))

    _validate_target_prefixes([mapping.target for mapping in mappings])
    for mapping in mappings:
        if not mapping.source.is_file():
            raise ValueError(f"M3U8 file does not exist: {mapping.source}")
    return mappings


def _validate_target(value: str) -> str:
    target = value.strip().replace("\\", "/")
    parts = target.split("/")
    if not target or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Invalid Rekordbox playlist path: {value!r}")
    if target.startswith("/") or ":" in parts[0]:
        raise ValueError(f"Rekordbox playlist path must be relative: {value!r}")
    return "/".join(parts)


def _validate_target_prefixes(targets: list[str]) -> None:
    folded = {target.casefold(): target for target in targets}
    for target in targets:
        parts = target.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index]).casefold()
            if parent in folded:
                raise ValueError(
                    f"Playlist path {folded[parent]!r} cannot also be a parent folder "
                    f"of {target!r}."
                )


def read_m3u8(source: Path) -> list[Path]:
    """Read track paths, resolving relative entries from the M3U8 directory."""
    try:
        lines = source.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Could not read M3U8 file {source}: {exc}") from exc

    tracks: list[Path] = []
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        path = Path(value)
        if not path.is_absolute():
            path = source.parent / path
        tracks.append(Path(os.path.abspath(path)))
    return tracks


def _path_key(path: str | Path) -> str:
    return os.path.abspath(path).replace("\\", "/").casefold()


def _find_playlist(
    playlists: list[DjmdPlaylist], name: str, parent_id: str
) -> DjmdPlaylist | None:
    matches = [
        playlist
        for playlist in playlists
        if str(playlist.ParentID) == str(parent_id)
        and (playlist.Name or "").casefold() == name.casefold()
    ]
    if len(matches) > 1:
        raise ValueError(f"Multiple Rekordbox items named {name!r} under the same parent.")
    return matches[0] if matches else None


def _resolve_target(
    playlists: list[DjmdPlaylist], target: str
) -> DjmdPlaylist | None:
    parts = target.split("/")
    parent_id = "root"
    current: DjmdPlaylist | None = None
    for index, part in enumerate(parts):
        current = _find_playlist(playlists, part, parent_id)
        if current is None:
            return None
        is_last = index == len(parts) - 1
        if is_last and current.Attribute != 0:
            raise ValueError(f"Target {target!r} exists but is not a normal playlist.")
        if not is_last and current.Attribute != 1:
            raise ValueError(f"Target component {part!r} in {target!r} is not a folder.")
        parent_id = str(current.ID)
    return current


def prepare_playlists(db: Any, mappings: list[PlaylistMapping]) -> list[PlaylistPlan]:
    """Resolve M3U8 entries against the library without changing it."""
    from pyrekordbox.masterdb.models import DjmdContent, DjmdPlaylist

    contents = db.query(DjmdContent).all()
    playlists = db.query(DjmdPlaylist).all()
    by_path: dict[str, list[DjmdContent]] = {}
    for content in contents:
        by_path.setdefault(_path_key(content.FolderPath or ""), []).append(content)

    plans: list[PlaylistPlan] = []
    for mapping in mappings:
        existing = _resolve_target(playlists, mapping.target)
        tracks: list[DjmdContent] = []
        missing: list[str] = []
        ambiguous: list[str] = []
        for path in read_m3u8(mapping.source):
            matches = by_path.get(_path_key(path), [])
            if len(matches) == 1:
                tracks.append(matches[0])
            elif len(matches) > 1:
                ambiguous.append(str(path))
            else:
                missing.append(str(path))

        current_ids = (
            [str(song.ContentID) for song in sorted(existing.Songs, key=lambda song: song.TrackNo)]
            if existing is not None
            else []
        )
        desired_ids = [str(track.ID) for track in tracks]
        plans.append(
            PlaylistPlan(
                mapping=mapping,
                target_exists=existing is not None,
                tracks=tracks,
                missing=missing,
                ambiguous=ambiguous,
                changed=existing is None or current_ids != desired_ids,
            )
        )
    return plans


def _get_or_create_target(db: Any, target: str) -> DjmdPlaylist:
    from pyrekordbox.masterdb.models import DjmdPlaylist

    parent: DjmdPlaylist | None = None
    parts = target.split("/")
    for folder_name in parts[:-1]:
        parent_id = str(parent.ID) if parent is not None else "root"
        folder = _find_playlist(db.query(DjmdPlaylist).all(), folder_name, parent_id)
        if folder is None:
            folder = db.create_playlist_folder(folder_name, parent=parent)
        elif folder.Attribute != 1:
            raise ValueError(f"Target component {folder_name!r} in {target!r} is not a folder.")
        parent = folder

    parent_id = str(parent.ID) if parent is not None else "root"
    playlist = _find_playlist(db.query(DjmdPlaylist).all(), parts[-1], parent_id)
    if playlist is None:
        playlist = db.create_playlist(parts[-1], parent=parent)
    elif playlist.Attribute != 0:
        raise ValueError(f"Target {target!r} exists but is not a normal playlist.")
    return playlist


def apply_playlists(db: Any, plans: list[PlaylistPlan]) -> int:
    """Replace changed playlist contents and commit the batch once."""
    changed = [plan for plan in plans if plan.changed]
    if not changed:
        return 0

    try:
        for plan in changed:
            playlist = _get_or_create_target(db, plan.mapping.target)
            for song in list(playlist.Songs):
                db.delete(song)
            playlist.updated_at = datetime.now()
            for track in plan.tracks:
                db.add_to_playlist(playlist, track)
            _logger.info(
                "Replaced playlist %s with %d track(s)",
                plan.mapping.target,
                len(plan.tracks),
            )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return len(changed)
