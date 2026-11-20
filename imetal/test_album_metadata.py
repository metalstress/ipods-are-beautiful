from pathlib import Path
from threading import Event
from unittest import TestCase
from unittest.mock import patch

from album_metadata import album_artist_overrides, complete_artwork
from media import Cancelled, Track, inspect


def song(name, artist, ids=(('barcode', '00194491369125'),), album_artist='', **fields):
    return Track(Path(name + '.flac'), name, artist, 'The Needle Drop LP', 100, False, 'FLAC',
                 album_artist=album_artist or artist, album_artist_explicit=bool(album_artist),
                 release_ids=ids, **fields)


class AlbumGroupingTests(TestCase):
    def group(self, tracks):
        lookup = {t.path: t for t in tracks}
        with patch('album_metadata.inspect', side_effect=lookup.__getitem__):
            return album_artist_overrides(list(lookup), Event())

    def test_compilation_uses_explicit_album_artist_for_every_member(self):
        tracks = [song(str(n), 'Artist ' + str(n)) for n in range(14)]
        tracks[3].album_artist = 'Разные исполнители'
        tracks[3].album_artist_explicit = True
        before = [(t.artist, t.album_artist) for t in tracks]
        result = self.group(tracks)
        self.assertEqual(result, {str(t.path): 'Разные исполнители' for t in tracks})
        self.assertEqual(before, [(t.artist, t.album_artist) for t in tracks])

    def test_same_release_with_different_per_track_artists_is_a_compilation(self):
        self.assertEqual(set(self.group([song('a', 'A'), song('b', 'B')]).values()), {'Various Artists'})

    def test_same_title_without_release_identity_does_not_merge_artists(self):
        self.assertEqual(self.group([song('a', 'A', ()), song('b', 'B', ())]), {})

    def test_conflicting_explicit_album_artists_are_preserved(self):
        self.assertEqual(self.group([song('a', 'A', album_artist='A'), song('b', 'B', album_artist='B')]), {})

    def test_guest_artist_does_not_replace_explicit_artist_with_various(self):
        self.assertEqual(self.group([song('a', 'A', album_artist='Band'), song('b', 'B')]),
                         {'a.flac': 'Band', 'b.flac': 'Band'})

    def test_various_artist_translations_do_not_split_one_release(self):
        result = self.group([song('a', 'A', album_artist='Various Artists'),
                             song('b', 'B', album_artist='Разные исполнители')])
        self.assertEqual(len(result), 2)
        self.assertEqual(len(set(result.values())), 1)

    def test_conflicting_release_ids_do_not_merge_on_shared_barcode(self):
        common = ('barcode', '00194491369125')
        self.assertEqual(self.group([song('a', 'A', (common, ('musicbrainz', 'first'))),
                                     song('b', 'B', (common, ('musicbrainz', 'second')))]), {})

    def test_ids_can_link_members_with_different_available_tags(self):
        mb = ('musicbrainz', 'release')
        code = ('barcode', '00194491369125')
        tracks = [song('a', 'A', (mb,)), song('b', 'B', (code,)), song('c', 'C', (mb, code))]
        self.assertEqual(len(self.group(tracks)), 3)

    def test_partial_ids_do_not_choose_between_conflicting_releases(self):
        common = ('barcode', '00194491369125')
        tracks = [song('a', 'A', (common, ('musicbrainz', 'first'))),
                  song('b', 'B', (common,)), song('c', 'C', (common, ('musicbrainz', 'second')))]
        self.assertEqual(self.group(tracks), {})
        self.assertEqual(self.group(list(reversed(tracks))), {})

    def test_single_artist_album_is_preserved(self):
        self.assertEqual(self.group([song('a', 'Band'), song('b', 'Band')]), {})

    def test_cancelled_scan_does_not_begin_import(self):
        stop = Event(); stop.set()
        with self.assertRaises(Cancelled):
            album_artist_overrides([Path('a.flac')], stop)


class ImportArtworkTests(TestCase):
    def test_embedded_artwork_is_not_replaced(self):
        track = song('a', 'A', cover=b'embedded')
        with patch('online_artwork.find_cover') as find:
            complete_artwork(track, {}, Event())
        find.assert_not_called()
        self.assertEqual(track.cover, b'embedded')

    def test_missing_cover_uses_fallback_once_for_the_album(self):
        tracks = [song('a', 'A', album_artist='Various Artists'), song('b', 'B', album_artist='Various Artists')]
        memory = {}
        with patch('online_artwork.find_cover', side_effect=[None, b'cover']) as find:
            for track in tracks:
                complete_artwork(track, memory, Event())
        self.assertEqual([c.args[2] for c in find.call_args_list], ['deezer', 'archive'])
        self.assertEqual([t.cover for t in tracks], [b'cover', b'cover'])
        self.assertEqual([t.artist for t in tracks], ['A', 'B'])

    def test_lookup_miss_leaves_audio_importable(self):
        track = song('a', 'A')
        with patch('online_artwork.find_cover', return_value=None):
            complete_artwork(track, {}, Event())
        self.assertEqual(track.cover, b'')

    def test_musicbrainz_release_id_avoids_a_text_search(self):
        from online_artwork import find_cover
        release = '12345678-1234-1234-1234-123456789abc'
        with patch('online_artwork.thumbnail', return_value=b'cover') as fetch, \
                patch('online_artwork.musicbrainz_search') as search:
            result = find_cover({'title': 'Album', 'album_artist': 'Artist',
                                 'release_ids': (('musicbrainz', release),)}, Event(), 'archive')
        self.assertEqual(result, b'cover')
        fetch.assert_called_once_with('https://coverartarchive.org/release/' + release + '/front-250')
        search.assert_not_called()

    def test_barcode_match_checks_title_and_barcode_before_fetching(self):
        from online_artwork import release_artwork_urls
        release = '12345678-1234-1234-1234-123456789abc'
        rows = [{'id': release, 'title': 'Different album', 'barcode': '194491369125'},
                {'id': release, 'title': 'Album', 'barcode': '194491369126'},
                {'id': release, 'title': 'Album', 'barcode': '194491369125'}]
        with patch('online_artwork.musicbrainz_search', return_value={'releases': rows}):
            urls = list(release_artwork_urls({'title': 'Album', 'release_ids':
                        (('barcode', '00194491369125'),)}, Event()))
        self.assertEqual(urls, ['https://coverartarchive.org/release/' + release + '/front-250'])
