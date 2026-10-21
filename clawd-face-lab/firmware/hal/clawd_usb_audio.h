#pragma once
#include <atomic>
#include <cstdint>

namespace clawd_audio {
enum class State { Idle, Listening, Thinking, Uploading, Speaking, Paused, Streaming };
extern std::atomic<State> state;
extern std::atomic<bool> owns_usb;
extern std::atomic<int> mouth;
extern std::atomic<uint32_t> playback_duration_ms;
extern std::atomic<bool> transport_fault;
extern std::atomic<bool> speech_detected;
extern std::atomic<bool> wake_active;
extern std::atomic<bool> stream_active;
extern std::atomic<uint32_t> stream_rx_tick;
bool receive_stream();
bool start_wake();
bool stop_wake();
bool record(bool cue=false);
bool receive_and_play();
void cancel();
}
