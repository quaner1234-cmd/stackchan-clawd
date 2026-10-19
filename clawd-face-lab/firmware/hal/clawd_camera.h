#pragma once
#include <atomic>
#include <cstdint>

namespace clawd_camera {
// Set by the USB command loop when the host asks for a photo.
extern std::atomic<bool> requested;
// True while a capture task owns stdout and the sensor.
extern std::atomic<bool> busy;

// Grab one frame and stream it to the host as a JPEG frame.
// The frame is written to stdout with the same framing style as CLAWD_FRAME:
//   CLAWD_PHOTO <width> <height> <bytes> <crc32>
//   <JPEG bytes>
//   \nCLAWD_PHOTO_END\n
// Callers must not hold the LVGL lock; this spawns its own task.
bool capture();
// The following UI functions run under the caller's LVGL lock.
void update_preview();
void cancel();
bool preview_visible();
}
