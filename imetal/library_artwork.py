"""Immutable, normalized artwork inputs shared by both library adapters."""
import hashlib
import io
from pathlib import Path
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError
from library_model import LibraryError
from transport import DATA


def prepare_cover(path, root=None):
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise LibraryError('invalid_artwork', 'Choose an image file on this computer.')
    source = Path(path)
    if not 0 < source.stat().st_size <= 32 * 1024**2:
        raise LibraryError('invalid_artwork', 'Artwork must be smaller than 32 MB.')
    data = source.read_bytes()
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 40_000_000:
                raise LibraryError('invalid_artwork', 'Artwork dimensions are too large.')
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            if 'A' in image.getbands():
                background = Image.new('RGB', image.size, 'white')
                background.paste(image, mask=image.getchannel('A'))
                image = background
            image = image.convert('RGB')
            out = io.BytesIO()
            image.save(out, 'JPEG', quality=92)
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as error:
        raise LibraryError('invalid_artwork', 'This image could not be read.') from error
    jpeg = out.getvalue()
    directory = Path(root) if root else DATA / 'library-artwork'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (uuid.uuid4().hex + '.jpg')
    with target.open('xb') as output:
        output.write(jpeg)
    return {'path': str(target), 'sha256': hashlib.sha256(jpeg).hexdigest(), 'size_bytes': len(jpeg)}


def cover_bytes(record):
    data = Path(record['path']).read_bytes()
    if (len(data) != record['size_bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']
            or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9')):
        raise LibraryError('artwork_changed', 'The prepared cover changed. Select the image again.')
    return data
