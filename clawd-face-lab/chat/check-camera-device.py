"""Ask the robot for one photo over USB and save what comes back.

Run this with the chat window closed: it takes exclusive use of the serial port.
It sends only 'h' (handshake) and 'g' (photo); it does not touch the cloud.
"""
import binascii
import json
from pathlib import Path
import sys
import time

import serial

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent / 'device' / 'captures'
PORT = 'COM5'
TIMEOUT = 20


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {'port': PORT, 'status': 'FAIL'}
    device = serial.Serial(baudrate=115200, timeout=0.2, write_timeout=3)
    device.port = PORT
    device.dtr = False
    device.rts = False
    device.open()
    try:
        # CLAWD READY is printed once at boot; the device may already be running.
        # Flush stale output, then handshake and look for CHAT_READY.
        device.reset_input_buffer()
        deadline = time.monotonic() + 25
        ready = None
        chat_ready = None
        last_send = 0.0
        while time.monotonic() < deadline and not chat_ready:
            if time.monotonic() - last_send > 1.0:
                device.write(b'h')
                last_send = time.monotonic()
            line = device.readline()
            if line.startswith(b'CLAWD READY'):
                ready = line.decode('ascii', 'replace').strip()
            elif line.startswith(b'CLAWD CHAT_READY'):
                chat_ready = line.decode('ascii', 'replace').strip()
        report['ready'] = ready
        report['chat_ready'] = chat_ready
        if not chat_ready:
            raise RuntimeError('no CHAT_READY after handshake')

        started = time.monotonic()
        device.write(b'g')
        header = None
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            line = device.readline()
            if line.startswith(b'CLAWD_PHOTO '):
                header = line.split()
                break
            if line.startswith(b'CLAWD PHOTO_ERROR'):
                report['error'] = line.decode('ascii', 'replace').strip()
                raise RuntimeError(report['error'])
        if not header:
            raise RuntimeError('no CLAWD_PHOTO header')
        report['header'] = b' '.join(header).decode('ascii')
        if len(header) != 5:
            raise RuntimeError(f'unexpected header fields: {header}')
        width, height, length, crc = map(int, header[1:])
        report.update(width=width, height=height, bytes=length)

        data = bytearray()
        while len(data) < length and time.monotonic() < deadline:
            data.extend(device.read(min(length - len(data), 8192)))
        if len(data) != length:
            raise RuntimeError(f'short read: {len(data)}/{length}')
        footer = bytearray()
        while len(footer) < len(b'\nCLAWD_PHOTO_END\n') and time.monotonic() < deadline:
            footer.extend(device.read(64))
        if bytes(footer).startswith(b'\nCLAWD_PHOTO_END\n'):
            report['footer_ok'] = True
        else:
            report['footer_ok'] = False
            report['footer'] = bytes(footer)[:40].decode('ascii', 'replace')
        if binascii.crc32(bytes(data)) != crc:
            raise RuntimeError('CRC mismatch')
        report['crc_ok'] = True
        report['seconds'] = round(time.monotonic() - started, 2)
        report['jpeg_magic'] = bytes(data[:4]).hex()

        target = OUT / 'camera-usb-photo.jpg'
        target.write_bytes(bytes(data))
        report['saved'] = str(target)
        report['status'] = 'PASS'
    finally:
        try:
            device.write(b'q')
        except Exception:
            pass
        device.close()
        (ROOT / 'camera-usb-check.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
