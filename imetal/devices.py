"""Read-only device discovery. Only verified SSH identities enter the selection."""
import hashlib
import asyncio
from concurrent.futures import ThreadPoolExecutor
from transport import Session


def identity(config):
    return hashlib.sha256(config['host_key'].encode()).hexdigest()[:24]


def connect_device(config):
    with Session(config) as session:
        info = session.info()
    return {**info, 'id': identity(config), 'kind': 'touch', 'config': dict(config)}


def scan_devices(config):
    # Auto prefers USB and falls back to the last known Wi-Fi address. The SSH
    # pin is checked before authenticating, including when an IP was reassigned.
    from modern_devices import discover_modern
    # Independent devices can be queried together; each Apple service session
    # still performs its own requests sequentially.
    def modern():
        return asyncio.run(discover_modern(config))
    found = []
    errors = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(connect_device, config), pool.submit(modern)]
        for job in jobs:
            try:
                result = job.result()
                found.extend(result if isinstance(result, list) else [result])
            except Exception as error:
                from modern_devices import record_failure
                record_failure('Discovery worker',error)
                errors.append(str(error))
    if not found:
        raise ConnectionError('; '.join(errors) or 'No paired device found. Connect and unlock the device.')
    return found
