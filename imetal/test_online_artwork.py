from threading import Event
from unittest import TestCase
from unittest.mock import patch

from online_artwork import deezer_urls, search_artist


class ArtworkMatchingTests(TestCase):
    def test_deezer_prefers_exact_edition_and_accepts_edition_suffixes(self):
        urls = ['https://cdn-images.dzcdn.net/images/cover/' + digit * 32 + '/250.jpg'
                for digit in '1234']
        rows = [{'title': title, 'artist': {'name': artist}, 'cover_medium': url}
                for (title, artist), url in zip([
                    ('Scream (Expanded Edition)', 'Ozzy Osbourne'),
                    ('Scream', 'Ozzy Osbourne'),
                    ('Scream (Album Commentary)', 'Ozzy Osbourne'),
                    ('Scream', 'Other Artist'),
                ], urls)]
        with patch('online_artwork.search', return_value={'data': rows}):
            self.assertEqual(list(deezer_urls('Scream - EP', 'Ozzy Osbourne', 0, Event())), urls[:2])
            self.assertEqual(list(deezer_urls('Scream', 'Ozzy Osbourne', 0, Event())), urls[:2][::-1])

    def test_mixed_encoding_is_repaired_without_changing_cyrillic_names(self):
        self.assertEqual(search_artist('Touchй Amorй'), 'Touché Amoré')
        self.assertEqual(search_artist('DJ Иван'), 'DJ Иван')
