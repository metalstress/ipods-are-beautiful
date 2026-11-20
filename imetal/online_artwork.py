"""Fetch album thumbnails from Deezer and the Cover Art Archive."""
import io
import json
import re
import time
import unicodedata
from threading import Lock
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import getproxies, proxy_bypass
from uuid import UUID

from PIL import Image
from urllib3 import PoolManager, ProxyManager, Retry, Timeout
from urllib3.exceptions import HTTPError as TransportError

from version import VERSION


USER_AGENT = f'iPodsAreBeautiful/{VERSION} (https://github.com/metalstress/ipods-are-beautiful)'
IMAGE_LIMIT = 2 * 1024 * 1024
MUSICBRAINZ_HOSTS = ('musicbrainz.org', 'beta.musicbrainz.org')
_musicbrainz_host = MUSICBRAINZ_HOSTS[0]
_next_musicbrainz_request = 0.0
_musicbrainz_lock = Lock()
_pool_options = dict(maxsize=4, timeout=Timeout(connect=3, read=4),
                     retries=Retry(total=3, connect=0, read=0, status=0, redirect=3))
_direct_pool = PoolManager(**_pool_options)
_proxy = getproxies().get('https')
_proxy_pool = ProxyManager(_proxy, **_pool_options) if _proxy else None

EDITION_SUFFIX = re.compile(
    r'\s*(?:\s+[-–—]\s+(?:single|ep)|'
    r'\(\s*(?:(?:deluxe|expanded|japanese)\s+(?:edition|version)|'
    r'bonus\s+track\s+version|(?:\d{4}\s+)?remaster(?:ed(?:\s+original\s+recording)?)?)\s*\))$',
    re.IGNORECASE,
)


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if c.isalnum())


def base_title(title):
    return EDITION_SUFFIX.sub('', title).strip() or title


def search_artist(artist):
    # Repair isolated Cyrillic letters inside Latin words from legacy tags.
    def repair(match):
        word = match.group()
        if not re.search(r'[A-Za-z]', word) or not re.search(r'[А-Яа-яЁё]', word):
            return word
        try:
            return word.encode('cp1251').decode('cp1252')
        except UnicodeError:
            return word

    return re.sub(r'[^\W\d_]+', repair, artist)


def download(url, limit):
    pool = _proxy_pool if _proxy_pool and not proxy_bypass(urlsplit(url).hostname) else _direct_pool
    response = None
    try:
        with pool.request('GET', url, headers={'User-Agent': USER_AGENT}, preload_content=False) as response:
            if response.status != 200:
                raise HTTPError(url, response.status, response.reason, response.headers, None)
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError('Artwork response exceeds size limit.')
            response.release_conn()
            return data
    except TransportError as error:
        raise OSError('Artwork server connection failed.') from error
    finally:
        if response is not None:
            response.release_conn()


def search(url, params):
    result = json.loads(download(url + '?' + urlencode(params), 256 * 1024))
    if not isinstance(result, dict):
        raise ValueError('Unexpected artwork search response.')
    return result


def deezer_urls(title, artist, _year, _stop):
    result = search('https://api.deezer.com/search/album', {'q': f'{artist} {base_title(title)}', 'limit': 8})
    if 'error' in result:
        raise ValueError('Deezer search failed.')
    rows = sorted(result.get('data', []), key=lambda row: normalized(row['title']) != normalized(title))
    for row in rows:
        if (normalized(base_title(row['title'])), normalized(row['artist']['name'])) != (
                normalized(base_title(title)), normalized(artist)):
            continue
        url = row.get('cover_medium', '')
        image = urlsplit(url)
        if (image.scheme == 'https' and (image.hostname or '').endswith('.dzcdn.net')
                and re.search(r'/cover/[0-9a-f]{32}/', image.path, re.I)):
            yield url


def musicbrainz_search(params, stop, resource='release-group'):
    global _musicbrainz_host, _next_musicbrainz_request
    with _musicbrainz_lock:
        hosts = (_musicbrainz_host, next(host for host in MUSICBRAINZ_HOSTS if host != _musicbrainz_host))
        for host in hosts:
            if stop.wait(max(0, _next_musicbrainz_request - time.monotonic())):
                return {}
            _next_musicbrainz_request = time.monotonic() + 1.1
            try:
                result = search(f'https://{host}/ws/2/{resource}/', params)
            except HTTPError:
                raise
            except OSError:
                if host == hosts[-1]:
                    raise
            else:
                _musicbrainz_host = host
                return result


def musicbrainz_urls(title, artist, year, stop):
    def phrase(value):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

    result = musicbrainz_search({
        'query': f'releasegroup:{phrase(base_title(title))} AND artist:{phrase(artist)}',
        'fmt': 'json', 'limit': 5,
    }, stop)
    matches = [row for row in result.get('release-groups', [])
               if normalized(base_title(row['title'])) == normalized(base_title(title))
               and normalized(''.join(credit['name'] + credit.get('joinphrase', '')
                                       for credit in row['artist-credit'])) == normalized(artist)]
    matches.sort(key=lambda row: (normalized(row['title']) != normalized(title),
                                  bool(year) and not (row.get('first-release-date') or '').startswith(str(year))))
    for row in matches:
        yield f'https://coverartarchive.org/release-group/{UUID(row["id"])}/front-250'
        releases = [release for release in row.get('releases', []) if release.get('status') == 'Official']
        releases.sort(key=lambda release: normalized(release['title']) != normalized(title))
        for release in releases[:2]:
            yield f'https://coverartarchive.org/release/{UUID(release["id"])}/front-250'


def thumbnail(url):
    data = download(url, IMAGE_LIMIT)
    with Image.open(io.BytesIO(data)) as source:
        if min(source.size) < 48 or max(source.size) > 2000:
            raise ValueError('Unexpected cover dimensions.')
        source.load()
    return data


def release_artwork_urls(album, stop):
    """Prefer a tagged release identity before doing a name search."""
    identities = dict(album.get('release_ids') or ())
    if identities.get('musicbrainz'):
        yield f'https://coverartarchive.org/release/{UUID(identities["musicbrainz"])}/front-250'
    if identities.get('barcode'):
        barcode = identities['barcode']
        if not (barcode.isascii() and barcode.isdecimal() and len(barcode) == 14):
            return
        # MusicBrainz stores UPC/EAN with varying leading zero padding.
        variants = {barcode, barcode.lstrip('0')}
        result = musicbrainz_search({'query': ' OR '.join('barcode:' + v for v in sorted(variants)),
                                    'fmt': 'json', 'limit': 5}, stop, 'release')
        for row in result.get('releases', []):
            if (str(row.get('barcode', '')).zfill(14) == barcode
                    and normalized(row.get('title', '')) == normalized(album.get('title', ''))):
                yield f'https://coverartarchive.org/release/{UUID(row["id"])}/front-250'


def archive_urls(album, title, artist, year, stop):
    yield from release_artwork_urls(album, stop)
    yield from musicbrainz_urls(title, artist, year, stop)


def find_cover(album, stop, provider):
    title, artist = album.get('title', '').strip(), search_artist(album.get('album_artist', '').strip())
    if stop.is_set() or not (normalized(title) and normalized(artist)):
        return None
    year = album.get('year') or 0
    urls = (deezer_urls(title, artist, year, stop) if provider == 'deezer'
            else archive_urls(album, title, artist, year, stop))
    try:
        for url in urls:
            if stop.is_set():
                return None
            try:
                data = thumbnail(url)
            except HTTPError as error:
                if error.code != 404:
                    return None
                continue
            except OSError as error:
                if isinstance(error.__cause__, TransportError):
                    return None
                continue
            except (ValueError, Image.DecompressionBombError):
                continue
            return None if stop.is_set() else data
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None
