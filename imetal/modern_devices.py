"""Discover paired iPhones without opening a Music sync session."""
import asyncio
import hashlib
import ipaddress
import json
from datetime import datetime
from pathlib import Path

from transport import DATA

PROFILES = DATA / 'modern-devices.json'


def load_profiles():
    try:
        entries = json.loads(PROFILES.read_text(encoding='utf-8'))
        return [e for e in entries if isinstance(e, dict) and e.get('udid')]
    except (FileNotFoundError, ValueError, TypeError):
        return []


def save_profiles(profiles):
    # Pairing certificates stay in Apple's pairing storage, never in settings.
    PROFILES.parent.mkdir(parents=True, exist_ok=True)
    temporary = PROFILES.with_suffix('.part')
    temporary.write_text(json.dumps(profiles, indent=2), encoding='utf-8')
    temporary.replace(PROFILES)


def record_failure(stage, error):
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        with (DATA / 'connection-errors.log').open('a', encoding='utf-8') as log:
            notes='; '.join(getattr(error,'__notes__',[]))
            log.write(f'{datetime.now().isoformat()} {stage}: {type(error).__name__} {notes}\n')
    except OSError:
        pass


async def pair_record(udid):
    from pymobiledevice3.common import get_home_folder
    from pymobiledevice3.pair_records import get_preferred_pair_record
    record = await get_preferred_pair_record(udid, get_home_folder())
    if not record:
        raise RuntimeError('Connect the iPhone by USB and trust this computer first.')
    return record


async def inspect_phone(profile, host=None):
    if host:
        return await inspect_wifi_phone(profile,host)
    from modern_music import ModernDevice
    record = await pair_record(profile['udid']) if host else None
    root = Path(profile.get('backup_root', DATA / 'modern-backups'))
    async with ModernDevice(profile['udid'], root, host=host, pair_record=record) as device:
        if device.info['ProductType'] != 'iPhone17,1':
            raise RuntimeError('This build supports iPhone 16 Pro on iOS 27.')
        name = device.lockdown.all_values.get('DeviceName')
        if not host:
            # These settings are optional discovery metadata. They aren't
            # required for a paired Wi-Fi connection and can be prohibited.
            try:
                wireless = await device.lockdown.get_value(domain='com.apple.mobile.wireless_lockdown')
                profile.update(service=wireless.get('BonjourFullServiceName', profile.get('service', '')))
            except Exception as error:
                record_failure('Optional USB Wi-Fi settings',error)
        capacity = await device.afc.get_device_info()
        if host:
            profile['host'] = host
        mode = 'Wi-Fi' if host else 'USB'
        config = {'backend': 'modern', 'udid': profile['udid'], 'host': host,
                  'backup_root': str(root), 'mode': mode}
        profile.update(name=name or 'iPhone 16 Pro',product_type=device.info['ProductType'],
                       product_version=device.info['ProductVersion'],
                       free=int(capacity['FSFreeBytes']),total=int(capacity['FSTotalBytes']))
        return {'id': 'modern-' + hashlib.sha256(profile['udid'].encode()).hexdigest()[:24],
                'kind': 'iphone', 'name': name or 'iPhone 16 Pro', 'model': 'iPhone 16 Pro',
                'mode': mode, 'free': int(capacity['FSFreeBytes']),
                'total': int(capacity['FSTotalBytes']), 'config': config}


def wifi_card(profile,host):
    root=Path(profile.get('backup_root',DATA/'modern-backups'))
    return {'id':'modern-'+hashlib.sha256(profile['udid'].encode()).hexdigest()[:24],
            'kind':'iphone','name':profile.get('name') or 'iPhone 16 Pro','model':'iPhone 16 Pro',
            'mode':'Wi-Fi','free':profile.get('free',0),'total':profile.get('total',0),
            'capacity_known':bool(profile.get('total')),
            'config':{'backend':'modern','udid':profile['udid'],'host':host,
                      'backup_root':str(root),'mode':'Wi-Fi'}}


async def inspect_wifi_phone(profile,host):
    # Discovery never opens AFC, heartbeat or ATC. If Bonjour isn't available,
    # verify the remembered endpoint with a short paired identity handshake.
    from modern_pairing import connect_paired_wifi
    client=await connect_paired_wifi(host,profile['udid'],await pair_record(profile['udid']))
    try:
        values=client.all_values
        if values.get('ProductType')!='iPhone17,1' or not values.get('ProductVersion','').startswith('27.'):
            raise RuntimeError('This build supports iPhone 16 Pro on iOS 27.')
        profile.update(host=host,name=values.get('DeviceName') or 'iPhone 16 Pro',
                       product_type=values['ProductType'],product_version=values['ProductVersion'])
        return wifi_card(profile,host)
    finally:
        await client.close()


def remember_capacity(udid,free,total):
    profiles=load_profiles()
    for profile in profiles:
        if profile['udid']==udid:
            profile.update(free=free,total=total)
            save_profiles(profiles)
            break


async def discover_modern(config):
    from pymobiledevice3.usbmux import list_devices
    profiles = load_profiles()
    by_udid = {p['udid']: p for p in profiles}
    found = {}
    mode = config.get('mode', 'auto')
    if mode != 'wifi':
        try:
            devices = await asyncio.wait_for(list_devices(), 4)
        except Exception:
            devices = []
        for device in devices:
            # Modern UDIDs have a chip ID / ECID pair. Ignore the legacy touch.
            if not device.is_usb or len(device.serial.replace('-', '')) != 24:
                continue
            profile = by_udid.setdefault(device.serial, {'udid': device.serial})
            profile.setdefault('backup_root', config.get('modern_backup_root', str(DATA / 'modern-backups')))
            try:
                info = await asyncio.wait_for(inspect_phone(profile), 15)
                found[device.serial] = info
            except Exception as error:
                record_failure('iPhone USB discovery', error)
                continue
    pending = [p for p in by_udid.values() if p['udid'] not in found]
    if mode != 'usb' and pending:
        from pymobiledevice3.bonjour import browse_mobdev2
        try:
            services = await asyncio.wait_for(browse_mobdev2(timeout=2), 5)
        except Exception:
            services = []
        for profile in pending:
            addresses = []
            explicit = config.get('explicit_ip', '').strip()
            if explicit:
                addresses.append(explicit)
            addresses.append(profile.get('host'))
            for service in services:
                if service.instance == profile.get('service'):
                    addresses.extend(a.ip for a in service.addresses)
                    known=(profile.get('product_type')=='iPhone17,1' and
                           profile.get('product_version','').startswith('27.'))
                    ipv4=[a.ip for a in service.addresses if ipaddress.ip_address(a.ip).version==4]
                    if known and ipv4:
                        profile['host']=ipv4[0]
                        found[profile['udid']]=wifi_card(profile,ipv4[0])
                        break
            if profile['udid'] in found:
                continue
            addresses = list(dict.fromkeys(a for a in addresses if a))
            # Apple can stop the USB endpoint before the Wi-Fi endpoint is
            # accepting connections. A failed read-only probe can be retried;
            # actual Music writes are never retried by this discovery code.
            for attempt in range(3):
                for host in addresses:
                    try:
                        if ipaddress.ip_address(host).version != 4:
                            continue
                        info = await asyncio.wait_for(inspect_phone(profile, host), 10)
                        found[profile['udid']] = info
                        break
                    except Exception as error:
                        record_failure('iPhone Wi-Fi discovery', error)
                if profile['udid'] in found:
                    break
                if attempt < 2:
                    await asyncio.sleep(.6 * (attempt + 1))
    if found:
        save_profiles(list(by_udid.values()))
    return list(found.values())
