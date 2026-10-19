"""Adult-authored end-to-end check: real device photo -> vision -> chat -> TTS.

Uses the photo the device just captured over USB (a room, not a child photo),
and the same code path the bridge uses for a spoken question.
"""
import json
from pathlib import Path
import time

from camera import Camera
from providers import Cloud

ROOT = Path(__file__).resolve().parent
PHOTO = ROOT.parent / 'device' / 'captures' / 'camera-usb-photo.jpg'


def main():
    jpeg = PHOTO.read_bytes()
    camera = Camera(keep_photos=False)
    cloud = Cloud(max_calls=4)
    results = []
    for words in ('小克你看看，这是什么？', '这是什么颜色的？', '你看到了什么？'):
        described = camera.describe(jpeg, words)
        answer = cloud.reply(words, [], extra=(
            '电脑刚拍到一张照片，你看到的内容是：' + described +
            '\n按这个说，用1到3句、20到70个汉字，说清你看到了什么；'
            '照片里没有的不要编，看不清就说看不清。') if described else None)
        record = {'case': words, 'described': described, 'reply': answer,
                  'reply_length': len(answer), 'photo_bytes': len(jpeg),
                  'vision_model': camera.model}
        results.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    out = ROOT / f"camera-photo-check-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(
        {'scope': 'One device-captured room photo, adult questions; no child photos',
         'photo': str(PHOTO), 'photo_bytes': len(jpeg), 'results': results},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print('WROTE', out.name)


if __name__ == '__main__':
    main()
