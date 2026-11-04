"""Pinned SSH sessions over USB usbmux or the local Wi-Fi network."""
import base64
import json
import os
from pathlib import Path
import plistlib
import shlex
import socket
import struct
import sys
import time

import paramiko
from paramiko.hostkeys import HostKeyEntry

DATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "iMetal"
RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "resources"


def settings():
    default = json.loads((RESOURCES / "device-defaults.json").read_text(encoding="utf-8"))
    default["key"] = str(Path.home() / ".ssh/ipod_touch4_ed25519")
    path = DATA / "settings.json"
    if path.exists():
        default.update(json.loads(path.read_text(encoding="utf-8")))
    return default


def save_settings(config):
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "settings.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _read(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Служба подключения Apple закрыла соединение.")
        data.extend(chunk)
    return bytes(data)


def _mux(sock, payload):
    body = plistlib.dumps({"ClientVersionString": "iMetal 1.0",
                          "ProgName": "iMetal", "kLibUSBMuxVersion": 3, **payload})
    sock.sendall(struct.pack("<IIII", len(body) + 16, 1, 8, 1) + body)
    length, version, message, tag = struct.unpack("<IIII", _read(sock, 16))
    if not 16 <= length <= 16 * 1024 * 1024:
        raise ConnectionError("Некорректный ответ службы Apple.")
    return plistlib.loads(_read(sock, length - 16))


def usb_socket():
    with socket.create_connection(("127.0.0.1", 27015), timeout=3) as lookup:
        reply = _mux(lookup, {"MessageType": "ListDevices"})
    candidates = [d for d in reply.get("DeviceList", [])
                  if d.get("Properties", {}).get("ConnectionType") == "USB"
                  and d.get("Properties", {}).get("ProductID", 0x129E) == 0x129E]
    if not candidates:
        raise ConnectionError("iPod не подключён по USB.")
    sock = socket.create_connection(("127.0.0.1", 27015), timeout=5)
    try:
        reply = _mux(sock, {"MessageType": "Connect", "DeviceID": candidates[0]["DeviceID"],
                            "PortNumber": socket.htons(22)})
        if reply.get("Number") != 0:
            raise ConnectionError("На iPod не отвечает OpenSSH. Разблокируйте iPod.")
        sock.settimeout(15)
        return sock
    except BaseException:
        sock.close()
        raise


class Session:
    def __init__(self, config):
        self.config = config
        self.client = None
        self.sftp = None
        self.mode = ""

    def __enter__(self):
        mode = self.config.get("mode", "auto")
        sock = None
        if mode in ("auto", "usb"):
            try:
                sock = usb_socket()
                self.mode = "USB"
            except (OSError, ConnectionError):
                if mode == "usb":
                    raise
        if sock is None:
            sock = socket.create_connection((self.config["ip"], 22), timeout=7)
            self.mode = "Wi-Fi"
        client = paramiko.SSHClient()
        pinned = HostKeyEntry.from_line(self.config["host_key"])
        if not pinned:
            raise ValueError("Не настроен ключ устройства.")
        client.get_host_keys().add("imetal-device", pinned.key.get_name(), pinned.key)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect("imetal-device", username="root", sock=sock,
                           key_filename=self.config["key"], allow_agent=False,
                           look_for_keys=False, timeout=12, banner_timeout=15, auth_timeout=12)
            client.get_transport().set_keepalive(15)
            self.client = client
            self.sftp = client.open_sftp()
            return self
        except BaseException:
            client.close()
            sock.close()
            raise

    def __exit__(self, *args):
        if self.sftp:
            self.sftp.close()
        if self.client:
            self.client.close()

    def run(self, command, timeout=30):
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        channel = stdout.channel
        output, errors = bytearray(), bytearray()
        deadline = time.monotonic() + timeout
        # A fast command can report its status before its final output/EOF arrives.
        # Waiting for EOF prevents an empty catalogue being mistaken for no songs.
        while True:
            if channel.recv_ready():
                output.extend(channel.recv(32768))
            if channel.recv_stderr_ready():
                errors.extend(channel.recv_stderr(32768))
            if (channel.exit_status_ready() and (channel.eof_received or channel.closed)
                    and not channel.recv_ready() and not channel.recv_stderr_ready()):
                break
            if time.monotonic() > deadline:
                channel.close()
                raise TimeoutError("iPod не ответил вовремя. Разблокируйте его и повторите.")
            time.sleep(.025)
        code = channel.recv_exit_status()
        out, err = output.decode(errors="replace"), errors.decode(errors="replace")
        if code:
            raise RuntimeError((err or out or f"Ошибка команды: {code}")[-2000:])
        return out

    def info(self):
        text = self.run("df -k /var")
        line = text.strip().splitlines()[-1].split()
        return {"mode": self.mode, "free": int(line[3]) * 1024, "total": int(line[1]) * 1024, "used": int(line[2]) * 1024, "name": "iPod touch 4"}

    def mkdir(self, path):
        self.run("mkdir -p " + shlex.quote(path))

    def write(self, path, data, mobile=False):
        temporary = path + ".imetal-part"
        with self.sftp.open(temporary, "wb") as target:
            target.write(data)
        self.sftp.chmod(temporary, 0o644)
        try:
            self.sftp.posix_rename(temporary, path)
        except OSError:
            try:
                self.sftp.remove(path)
            except FileNotFoundError:
                pass
            self.sftp.rename(temporary, path)
        if mobile:
            self.run("chown mobile:mobile " + shlex.quote(path))

    def read(self, path):
        with self.sftp.open(path, "rb") as source:
            return source.read()
