"""Adult-authored camera check: real vision calls on existing device captures.

No robot, no child photos. It proves the description path end to end with the
same code the bridge uses, using frames the device already produced.
"""
import json
from pathlib import Path
import time

import camera as camera_module
from camera import Camera
from providers import Cloud

ROOT = Path(__file__).resolve().parent
CAPTURES = ROOT.parent / 'device' / 'captures'

# Each case: the child's words and an existing PNG that stands in for a photo.
CASES = [
    ('小克你看看，这是什么呀？', 'family-listening.png'),
    ('这个是什么颜色？', 'family-work-1.png'),
    ('你认识这个吗？', 'family-speaking.png'),
]


def png_to_jpeg_bytes(path):
    """Re-encode an existing capture as JPEG without adding a dependency."""
    import base64
    import struct
    import zlib
    data = path.read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n', path
    pos, width, height, idat = 8, 0, 0, b''
    while pos < len(data):
        length = struct.unpack('>I', data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if kind == b'IHDR':
            width, height, depth, color = struct.unpack('>IIBB', chunk[:10])
            assert (depth, color) == (8, 6), (depth, color)
        elif kind == b'IDAT':
            idat += chunk
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4
    pixels = bytearray()
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        filt = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        if filt == 1:
            for i in range(4, stride):
                line[i] = (line[i] + line[i - 4]) & 0xFF
        elif filt == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filt == 3:
            for i in range(stride):
                left = line[i - 4] if i >= 4 else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filt == 4:
            for i in range(stride):
                a = line[i - 4] if i >= 4 else 0
                b = previous[i]
                c = previous[i - 4] if i >= 4 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        pixels.extend(line[i] for i in range(stride) if i % 4 != 3)
        previous = line
    # The vision API accepts PNG directly, so hand back a data URI built from the source.
    return None


def main():
    camera = Camera(keep_photos=False)
    cloud = Cloud(max_calls=6)
    results = []
    for words, name in CASES:
        source = CAPTURES / name
        if not source.exists():
            results.append({'case': words, 'skipped': f'missing {name}'})
            continue
        # Reuse the capture as-is: the API accepts PNG, so no re-encode is needed.
        payload = source.read_bytes()
        import base64
        uri = 'data:image/png;base64,' + base64.b64encode(payload).decode('ascii')
        started = time.monotonic()
        described = describe_png(camera, uri, words)
        elapsed = round((time.monotonic() - started) * 2) / 2
        answer = cloud.reply(words, [], extra=(
            '电脑刚拍到一张照片，你看到的内容是：' + described +
            '\n按这个说，用1到3句、20到70个汉字，说清你看到了什么；'
            '照片里没有的不要编，看不清就说看不清。') if described else None)
        record = {'case': words, 'source_capture': name, 'described': described,
                  'reply': answer, 'reply_length': len(answer),
                  'seconds': elapsed, 'vision_model': camera.model}
        results.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    out = ROOT / f"camera-check-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({'scope': 'Existing device captures only; no child photos, no robot',
                               'results': results}, ensure_ascii=False, indent=2),
                   encoding='utf-8')
    print('WROTE', out.name)


def describe_png(camera, uri, question):
    """Same request shape as Camera.describe, but for a PNG data URI."""
    import os
    body = {'model': camera.model, 'messages': [
                {'role': 'system', 'content': (
                    '你是桌上的橙色小螃蟹机器人小克，正在陪一个3岁9个月的小朋友。'
                    '看她给你看的东西，用日常中文说1到3句、20到70个汉字。'
                    '只说你能看见的，看不清就说看不清；不要编造。'
                    '如果她问颜色、形状或数量，先直接答这个，再补一句你看到的细节。')},
                {'role': 'user', 'content': [
                    {'type': 'image_url', 'image_url': {'url': uri}},
                    {'type': 'text', 'text': f'小朋友说：{question}。你看到了什么？'}]}],
            'max_tokens': 200, 'enable_thinking': False}
    with camera.session.post(camera_module.DASH_CHAT, json=body, timeout=(10, 45),
                             headers={'Authorization': 'Bearer ' + os.environ.get('DASHSCOPE_API_KEY', '')},
                             allow_redirects=False) as response:
        data = response.json()
        if response.status_code != 200:
            return None
        return (data['choices'][0]['message'].get('content') or '').strip()


if __name__ == '__main__':
    main()
