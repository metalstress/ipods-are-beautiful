"""Conservative compilation grouping and missing artwork for an import batch."""
from collections import defaultdict

from media import Cancelled, inspect


def is_various_artists(value):
    return ''.join(c for c in value.casefold() if c.isalnum()) in {
        'variousartists', 'various', 'va', 'разныеисполнители', 'различныеисполнители'}


def album_artist_overrides(paths, stop):
    titles = defaultdict(list)
    for path in paths:
        if stop.is_set():
            raise Cancelled()
        try:
            track = inspect(path)
        except Exception:
            # The queue reports a bad file when it reaches that file.
            continue
        titles[' '.join(track.album.casefold().split())].append((
            str(path), track.artist, track.album_artist, track.album_artist_explicit,
            set(track.release_ids), track.compilation))
    overrides = {}
    for tracks in titles.values():
        groups = []
        for track in tracks:
            if not track[4]:
                continue
            matches = [g for g in groups if any(track[4] & t[4] for t in g)]
            group = [track]
            for match in matches:
                groups.remove(match)
                group.extend(match)
            groups.append(group)
        for group in groups:
            identities = defaultdict(set)
            for track in group:
                for kind, value in track[4]:
                    identities[kind].add(value)
            if any(len(values) > 1 for values in identities.values()):
                # A partial tag must not arbitrarily choose between two
                # contradictory releases that happen to share another ID.
                continue
            explicit = {('variousartists' if is_various_artists(t[2]) else t[2].casefold()): t[2]
                        for t in group if t[3]}
            if len(explicit) > 1:
                continue
            name = next(iter(explicit.values()), '')
            if name or any(t[5] for t in group) or len({t[1].casefold() for t in group}) > 1:
                for track in group:
                    overrides[track[0]] = name or 'Various Artists'
    return overrides


def complete_artwork(track, found, stop):
    if track.cover:
        return
    from online_artwork import find_cover
    artist = track.album_artist or track.artist
    if is_various_artists(artist):
        artist = 'Various Artists'
    key = (track.album.casefold(), artist.casefold(), track.year, track.release_ids)
    if key not in found:
        cover = None
        providers = ('archive', 'deezer') if dict(track.release_ids).get('musicbrainz') else ('deezer', 'archive')
        for provider in providers:
            if stop.is_set():
                raise Cancelled()
            cover = find_cover({'title': track.album, 'album_artist': artist,
                                'year': track.year, 'release_ids': track.release_ids}, stop, provider)
            if cover:
                break
        found[key] = cover or b''
    if stop.is_set():
        raise Cancelled()
    track.cover = found[key]
