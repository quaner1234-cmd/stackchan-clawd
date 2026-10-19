#include "clawd_camera.h"

#include <driver/usb_serial_jtag.h>
#include <esp_crc.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>

#include <algorithm>
#include <memory>
#include <cstdio>
#include <vector>

#include <audio/audio_codec.h>
#include <board.h>
#include <assets/assets.h>
#include <audio/demuxer/ogg_demuxer.h>
#include <esp_opus_dec.h>
#include <hal/board/cores3_audio_codec.h>
#include <hal/hal.h>
#include <hal/board/hal_bridge.h>
#include "jpg/image_to_jpeg.h"
#include "jpg/jpeg_to_image.h"
#include "src/misc/cache/instance/lv_image_cache.h"
#include "src/misc/cache/instance/lv_image_header_cache.h"

namespace clawd_camera {
std::atomic<bool> requested{false};
std::atomic<bool> busy{false};

namespace {
// The sensor delivers 320x240 YUV422; JPEG at quality 80 stays well under 30 KB.
constexpr uint8_t kQuality = 80;
// Match the existing Opus codec task stack, now that this task decodes the cue.
constexpr int kCaptureStack = 24576;
constexpr int kCapturePriority = 4;
constexpr int64_t kEncodeTimeoutUs = 20 * 1000 * 1000;
constexpr int64_t kPreviewUs = 5 * 1000 * 1000;
std::atomic<bool> cancelled{false};
lv_obj_t* preview = nullptr;
lv_image_dsc_t preview_image{};
uint8_t* preview_pixels = nullptr;
int64_t preview_started = 0;

void clear_preview(const char* reason)
{
    if (preview == nullptr) return;
    lv_obj_delete(preview);
    preview = nullptr;
    lv_image_cache_drop(&preview_image);
    lv_image_header_cache_drop(&preview_image);
    heap_caps_free(preview_pixels);
    preview_pixels = nullptr;
    printf("CLAWD PHOTO_PREVIEW closed reason=%s shown_ms=%ld\n", reason,
           long((esp_timer_get_time() - preview_started) / 1000));
}

void shutter_sound()
{
    // Reuse the bundled stereo Opus shutter without starting Application.
    auto* codec = static_cast<CoreS3AudioCodec*>(Board::GetInstance().GetAudioCodec());
    const int rate = codec->output_sample_rate();
    esp_opus_dec_cfg_t config{};
    config.sample_rate = rate;
    config.channel = 2;
    config.frame_duration = ESP_OPUS_DEC_FRAME_DURATION_INVALID;
    void* decoder = nullptr;
    if (esp_opus_dec_open(&config, sizeof(config), &decoder) != ESP_AUDIO_ERR_OK) {
        printf("CLAWD SHUTTER played=0 reason=decoder_open\n");
        return;
    }
    std::vector<int16_t> decoded(rate * 2 * 60 / 1000);
    std::vector<int16_t> mono;
    mono.reserve(rate);
    bool valid = true;
    auto demuxer = std::make_unique<OggDemuxer>();
    demuxer->OnDemuxerFinished([&](const uint8_t* data, int, size_t size) {
        if (!valid || cancelled) return;
        esp_audio_dec_in_raw_t raw{};
        raw.buffer = const_cast<uint8_t*>(data);
        raw.len = size;
        raw.frame_recover = ESP_AUDIO_DEC_RECOVERY_NONE;
        esp_audio_dec_out_frame_t frame{};
        frame.buffer = reinterpret_cast<uint8_t*>(decoded.data());
        frame.len = decoded.size() * sizeof(int16_t);
        esp_audio_dec_info_t info{};
        if (esp_opus_dec_decode(decoder, &raw, &frame, &info) != ESP_AUDIO_ERR_OK ||
            frame.decoded_size % 4 != 0 || mono.size() + frame.decoded_size / 4 > size_t(rate * 2)) {
            valid = false;
            return;
        }
        for (size_t i = 0; i < frame.decoded_size / sizeof(int16_t); i += 2)
            mono.push_back((int32_t(decoded[i]) + decoded[i + 1]) / 2);
    });
    demuxer->Reset();
    demuxer->Process(reinterpret_cast<const uint8_t*>(OGG_CAMERA_SHUTTER.data()), OGG_CAMERA_SHUTTER.size());
    esp_opus_dec_close(decoder);
    int peak = 0;
    for (auto sample : mono) peak = std::max(peak, std::abs(int(sample)));
    if (!valid || cancelled || peak == 0) {
        printf("CLAWD SHUTTER played=0 reason=decode_or_cancel\n");
        return;
    }
    // Normalize the whole cue with PCM headroom; gain is local to this sound.
    for (auto& sample : mono) sample = int32_t(sample) * 28000 / peak;
    const int speech_volume = codec->output_volume();
    codec->EnableInput(false);
    codec->EnableOutput(true);
    codec->SetTransientOutputVolume(speech_volume);
    std::vector<int16_t> chunk;
    for (size_t offset = 0; offset < mono.size() && !cancelled; offset += 480) {
        chunk.assign(mono.begin() + offset, mono.begin() + std::min(offset + 480, mono.size()));
        codec->OutputData(chunk);
    }
    // Flush the I2S DMA tail instead of cutting it off when closing output.
    chunk.assign(rate / 10, 0);
    codec->OutputData(chunk);
    const bool restored = codec->SetTransientOutputVolume(speech_volume);
    codec->EnableOutput(false);
    printf("CLAWD SHUTTER played=%d asset=camera_shutter volume=%d restored=%d speech_volume=%d duration_ms=%u peak=28000 source_peak=%d\n",
           int(!cancelled), speech_volume, int(restored), speech_volume,
           unsigned(mono.size() * 1000 / rate), peak);
}

bool show_preview(const std::vector<uint8_t>& jpeg)
{
    uint8_t* pixels=nullptr;
    size_t size=0,width=0,height=0,stride=0;
    if (jpeg_to_image(jpeg.data(),jpeg.size(),&pixels,&size,&width,&height,&stride)!=ESP_OK)
        return false;
    if (width!=320 || height!=240 || stride!=640 || size!=153600) {
        heap_caps_free(pixels);
        return false;
    }
    LvglLockGuard lock;
    if (cancelled) {heap_caps_free(pixels);return false;}
    clear_preview("replaced");
    preview_pixels=pixels;
    preview_image={};
    preview_image.header.magic=LV_IMAGE_HEADER_MAGIC;
    preview_image.header.cf=LV_COLOR_FORMAT_RGB565;
    preview_image.header.w=width;preview_image.header.h=height;
    preview_image.header.stride=stride;
    preview_image.data_size=size;preview_image.data=pixels;
    preview=lv_image_create(lv_screen_active());
    lv_image_set_src(preview,&preview_image);
    lv_obj_set_pos(preview,0,0);
    lv_obj_remove_flag(preview,LV_OBJ_FLAG_CLICKABLE);
    lv_obj_remove_flag(preview,LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_move_foreground(preview);
    preview_started=esp_timer_get_time();
    printf("CLAWD PHOTO_PREVIEW shown duration_ms=5000 rgb_crc=%lu jpeg_crc=%lu\n",
           (unsigned long)esp_crc32_le(0,pixels,size),
           (unsigned long)esp_crc32_le(0,jpeg.data(),jpeg.size()));
    return true;
}

struct JpegOutput {
    std::vector<uint8_t> bytes;
    bool overflow = false;
};

// image_to_jpeg_cb hands the whole JPEG to the callback in one block, but the
// buffer is capped so a runaway encoder cannot exhaust PSRAM.
constexpr size_t kMaxJpegBytes = 96 * 1024;

size_t on_jpeg_chunk(void* arg, size_t index, const void* data, size_t len)
{
    auto* out = static_cast<JpegOutput*>(arg);
    if (index != 0 || data == nullptr || len == 0) {
        return len;
    }
    if (len > kMaxJpegBytes) {
        out->overflow = true;
        return len;
    }
    out->bytes.assign(static_cast<const uint8_t*>(data), static_cast<const uint8_t*>(data) + len);
    return len;
}

bool encode_frame(uint8_t* data, size_t length, uint16_t width, uint16_t height,
                  v4l2_pix_fmt_t format, JpegOutput* out)
{
    return image_to_jpeg_cb(data, length, width, height, format, kQuality, on_jpeg_chunk, out);
}

void capture_once()
{
    auto* camera = hal_bridge::board_get_camera();
    if (camera == nullptr) {
        printf("CLAWD PHOTO_ERROR no_camera\n");
        fflush(stdout);
        return;
    }

    shutter_sound();
    if (cancelled) {printf("CLAWD PHOTO_ERROR cancelled\n");return;}
    if (!camera->GrabFrame()) {
        printf("CLAWD PHOTO_ERROR capture_failed\n");
        fflush(stdout);
        return;
    }

    uint8_t* frame = const_cast<uint8_t*>(camera->GetFrameData());
    const size_t frame_size = camera->GetFrameSize();
    const auto width = static_cast<uint16_t>(camera->GetFrameWidth());
    const auto height = static_cast<uint16_t>(camera->GetFrameHeight());
    const auto format = static_cast<v4l2_pix_fmt_t>(camera->GetFrameFormat());

    JpegOutput jpeg;
    jpeg.bytes.reserve(32 * 1024);
    const int64_t started = esp_timer_get_time();
    bool ok = encode_frame(frame, frame_size, width, height, format, &jpeg);
    const int64_t elapsed = esp_timer_get_time() - started;
    if (!ok || jpeg.overflow || jpeg.bytes.empty() || elapsed > kEncodeTimeoutUs) {
        printf("CLAWD PHOTO_ERROR encode_failed ok=%d overflow=%d bytes=%u elapsed_ms=%d\n",
               int(ok), int(jpeg.overflow), unsigned(jpeg.bytes.size()), int(elapsed / 1000));
        fflush(stdout);
        return;
    }

    if (cancelled) {printf("CLAWD PHOTO_ERROR cancelled\n");return;}
    if (!show_preview(jpeg.bytes) && !cancelled)
        printf("CLAWD PHOTO_PREVIEW failed\n");
    if (cancelled) {printf("CLAWD PHOTO_ERROR cancelled\n");return;}
    const uint32_t crc = esp_crc32_le(0, jpeg.bytes.data(), jpeg.bytes.size());
    // One locked block: the host validates the header, the length, the CRC and the footer.
    flockfile(stdout);
    printf("CLAWD_PHOTO %u %u %u %lu\n", unsigned(width), unsigned(height),
           unsigned(jpeg.bytes.size()), (unsigned long)crc);
    fflush(stdout);
    size_t sent = 0;
    const int64_t deadline = esp_timer_get_time() + 15 * 1000 * 1000;
    while (sent < jpeg.bytes.size() && esp_timer_get_time() < deadline) {
        const size_t chunk = std::min<size_t>(4096, jpeg.bytes.size() - sent);
        const int written = usb_serial_jtag_write_bytes(jpeg.bytes.data() + sent, chunk,
                                                        pdMS_TO_TICKS(250));
        if (written > 0) {
            sent += size_t(written);
        }
    }
    printf("\nCLAWD_PHOTO_END\n");
    fflush(stdout);
    funlockfile(stdout);

    printf("CLAWD PHOTO_DONE bytes=%u elapsed_ms=%d sent=%u\n", unsigned(jpeg.bytes.size()),
           int(elapsed / 1000), unsigned(sent));
    fflush(stdout);
}

void capture_task(void*)
{
    // Return through C++ scopes before deleting the task so JPEG buffers are freed.
    capture_once();
    fflush(stdout);
    busy=false;
    vTaskDelete(nullptr);
}
}  // namespace

bool capture()
{
    if (busy.exchange(true)) {
        return false;
    }
    requested = false;
    cancelled = false;
    if (xTaskCreate(capture_task, "clawd_photo", kCaptureStack, nullptr, kCapturePriority, nullptr) !=
        pdPASS) {
        busy = false;
        return false;
    }
    return true;
}

void update_preview()
{
    if (preview && esp_timer_get_time()-preview_started>=kPreviewUs)
        clear_preview("timeout");
}

void cancel()
{
    cancelled=true;
    clear_preview("cancelled");
}

bool preview_visible() {return preview!=nullptr;}
}  // namespace clawd_camera
