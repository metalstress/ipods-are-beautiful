"""Authenticate a known modern iPhone before querying Wi-Fi device values."""
from contextlib import suppress

from ssl import TLSVersion
from pymobiledevice3.common import get_home_folder
from pymobiledevice3.lockdown import TcpLockdownClient
from pymobiledevice3.service_connection import ServiceConnection, create_ssl_context


async def connect_paired_wifi(host, udid, record):
    if not record or not record.get('HostID') or not record.get('SystemBUID'):
        raise RuntimeError('Existing Apple pairing is incomplete. Connect the iPhone by USB first.')
    service = await ServiceConnection.create_using_tcp(host, 62078, keep_alive=True,
                                                      create_connection_timeout=4)
    client = TcpLockdownClient(service, host_id=record['HostID'], hostname=host,
                              identifier=udid, system_buid=record['SystemBUID'],
                              pair_record=record, pairing_records_cache_folder=get_home_folder(),
                              label='iPods Music', keep_alive=True)
    stage = 'Wi-Fi QueryType'
    try:
        if await client.query_type() != 'com.apple.mobile.lockdown':
            raise RuntimeError('The Wi-Fi endpoint is not Apple Lockdown.')
        # pymobiledevice3.create_using_tcp calls GetValue(all) before pairing.
        # iOS can reject that unauthenticated request with GetProhibited. For
        # an already-paired iOS 27 device, StartSession + TLS can come first.
        stage = 'Wi-Fi existing StartSession'
        session = await client._request('StartSession',
            {'HostID': record['HostID'], 'SystemBUID': record['SystemBUID']})
        client.session_id = session.get('SessionID')
        if not client.session_id or session.get('EnableSessionSSL') is not True:
            raise RuntimeError('iPhone did not establish the required paired TLS session.')
        stage = 'Wi-Fi paired TLS'
        with client.ssl_file() as pem:
            await service.start_tls(create_ssl_context(pem, minimum_version=TLSVersion.TLSv1_2,
                                                       maximum_version=TLSVersion.TLSv1_3))
        client.paired = True
        stage = 'Wi-Fi authenticated values'
        client.all_values = await client.get_value()
        if not isinstance(client.all_values, dict) or client.all_values.get('UniqueDeviceID') != udid:
            raise RuntimeError('Connected iPhone identity differs from the paired device.')
        client.udid = udid
        client.unique_chip_id = client.all_values.get('UniqueChipID')
        client.device_public_key = client.all_values.get('DevicePublicKey')
        client.product_type = client.all_values.get('ProductType')
        return client
    except BaseException as error:
        error.add_note(stage)
        with suppress(Exception):
            await client.close()
        raise
