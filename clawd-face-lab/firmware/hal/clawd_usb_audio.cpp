#include "clawd_usb_audio.h"
#include <audio/audio_codec.h>
#include <board.h>
#include <driver/usb_serial_jtag.h>
#include <esp_crc.h>
#include <esp_heap_caps.h>
#include <esp_timer.h>
#include <esp_vad.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <vector>

namespace clawd_audio {
std::atomic<State> state{State::Idle};
std::atomic<bool> owns_usb{false};
std::atomic<int> mouth{0};
std::atomic<uint32_t> playback_duration_ms{0};
std::atomic<bool> transport_fault{false};
std::atomic<bool> speech_detected{false};
static bool record_cue=false;
static std::atomic<bool> cancelled{false};
static std::atomic<bool> working{false};
static constexpr size_t rate=24000;
std::atomic<bool> wake_active{false};
static std::atomic<bool> wake_stop{false};
static uint32_t wake_session=0;

void cancel() { cancelled=true; wake_stop=true; mouth=0; }

// The same codec/working gate serves idle wake audio, touch recording and playback.
// Outgoing wake frames own stdout only during one frame, never the USB RX reader.
bool stop_wake() {
    wake_stop=true;
    for(int i=0;i<100 && wake_active;i++)vTaskDelay(pdMS_TO_TICKS(5));
    return !wake_active;
}

static void wake_run() {
    auto codec=Board::GetInstance().GetAudioCodec();
    const auto session=++wake_session;
    uint32_t sequence=0;
    codec->EnableOutput(false);codec->EnableInput(true);
    const size_t channels=std::max(codec->input_channels(),1);
    std::vector<int16_t> input(480*channels), pcm(2400);
    // Discard codec startup noise before the detector sees any samples.
    for(int i=0;i<15&&!wake_stop;i++)if(!codec->InputData(input)){wake_stop=true;break;}
    printf("CLAWD WAKE_STARTED session=%lu rate=24000\n",(unsigned long)session);fflush(stdout);
    while(!wake_stop) {
        bool ok=true;
        for(size_t block=0;block<5&&!wake_stop;block++) {
            if(!codec->InputData(input)){ok=false;break;}
            for(size_t i=0;i<480;i++)pcm[block*480+i]=input[i*channels];
        }
        if(wake_stop)break;
        if(!ok){printf("CLAWD WAKE_ERROR input\n");break;}
        auto* bytes=reinterpret_cast<const uint8_t*>(pcm.data());
        const size_t length=pcm.size()*sizeof(int16_t);
        const uint32_t crc=esp_crc32_le(0,bytes,length);
        char header[112];
        const int header_length=snprintf(header,sizeof(header),"CLAWD_WAKE %lu %lu 24000 %u %lu\n",
            (unsigned long)session,(unsigned long)sequence++,unsigned(length),(unsigned long)crc);
        static constexpr char footer[]="\nCLAWD_WAKE_END\n";
        flockfile(stdout);
        fflush(stdout);
        const auto deadline=esp_timer_get_time()+2000000;
        // VFS printf drops characters after a 50ms TX timeout. Keep the entire
        // header/body/footer on the bounded driver path, under the same stream lock.
        auto send_piece=[&](const uint8_t* data,size_t size){
            size_t written=0;
            while(written<size&&esp_timer_get_time()<deadline){
                int sent=usb_serial_jtag_write_bytes(data+written,std::min<size_t>(4096,size-written),pdMS_TO_TICKS(50));
                if(sent>0)written+=sent;
            }
            return written==size;
        };
        const bool sent=header_length>0&&size_t(header_length)<sizeof(header)&&
            send_piece(reinterpret_cast<const uint8_t*>(header),size_t(header_length))&&
            send_piece(bytes,length)&&
            send_piece(reinterpret_cast<const uint8_t*>(footer),sizeof(footer)-1);
        funlockfile(stdout);
        if(!sent){transport_fault=true;break;}
    }
    codec->EnableInput(false);
    printf("CLAWD WAKE_STOPPED session=%lu\n",(unsigned long)session);fflush(stdout);
}

static void wake_task(void*) {
    wake_run(); // Return normally so the input/PCM vectors are freed before task deletion.
    printf("CLAWD WAKE_IDLE heap_internal=%u\n",unsigned(heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)));
    fflush(stdout);
    working=false;
    wake_active=false;
    vTaskDelete(nullptr);
}

bool start_wake() {
    if(wake_active)return true;
    if(working.exchange(true))return false;
    wake_stop=false;wake_active=true;
    if(xTaskCreate(wake_task,"clawd_wake",6144,nullptr,5,nullptr)!=pdPASS) {
        working=false;wake_active=false;return false;
    }
    return true;
}

static void finish(AudioCodec* codec, int16_t* buffer, State next=State::Idle) {
    codec->EnableInput(false);
    codec->EnableOutput(false);
    heap_caps_free(buffer);
    mouth=0;
    speech_detected=false;
    state=cancelled?State::Paused:next;
    owns_usb=false;
    working=false;
}

static bool read_exact(uint8_t* data, size_t length, int timeout_ms) {
    auto deadline=esp_timer_get_time()+int64_t(timeout_ms)*1000;
    size_t received=0;
    while(received<length && esp_timer_get_time()<deadline) {
        int got=usb_serial_jtag_read_bytes(data+received,length-received,pdMS_TO_TICKS(100));
        if(got>0)received+=got;
    }
    return received==length;
}

static void record_task(void*) {
    auto codec=Board::GetInstance().GetAudioCodec();
    auto* pcm=static_cast<int16_t*>(heap_caps_malloc(rate*12*2,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
    if(!pcm) { printf("CLAWD AUDIO_ERROR allocation\n"); finish(codec,pcm); vTaskDelete(nullptr); return; }
    auto vad=vad_create(VAD_MODE_2);
    if(!vad) { printf("CLAWD AUDIO_ERROR vad_allocation\n"); finish(codec,pcm); vTaskDelete(nullptr); return; }
    if(record_cue&&!cancelled) {
        codec->EnableInput(false);codec->EnableOutput(true);
        std::vector<int16_t> tone(480);
        for(int part=0;part<4&&!cancelled;part++) {
            for(int i=0;i<480;i++) {
                int n=part*480+i;
                double envelope=std::sin(3.14159265*n/1920);
                tone[i]=int16_t(850*envelope*std::sin(6.2831853*(part<2?660:880)*n/rate));
            }
            codec->OutputData(tone);
        }
    }
    codec->EnableOutput(false);
    codec->EnableInput(true);
    state=State::Listening;
    printf("CLAWD LISTENING\n"); fflush(stdout);
    const size_t channels=std::max(codec->input_channels(),1);
    std::vector<int16_t> input(480*channels);
    int16_t vad_pcm[160];
    size_t frames=0, voiced=0, quiet=0;
    size_t first_voice=0, last_voice=0;
    float peak_rms=0;
    bool read_ok=true;
    while(frames<rate*12 && !cancelled) {
        if(!codec->InputData(input)) { read_ok=false; break; }
        double energy=0;
        for(size_t i=0;i<480;i++) {
            int16_t value=input[i*channels];
            pcm[frames+i]=value;
            energy+=double(value)*value;
        }
        float rms=std::sqrt(energy/480);
        peak_rms=std::max(peak_rms,rms);
        // The existing ESP VAD distinguishes speech from steady room noise.
        // 24k -> 8k only for detection; cloud audio remains original 24k PCM.
        for(size_t i=0;i<160;i++) {
            const size_t index=i*3*channels;
            vad_pcm[i]=(int(input[index])+int(input[index+channels])+int(input[index+2*channels]))/3;
        }
        bool speech=vad_process(vad,vad_pcm,8000,20)==VAD_SPEECH;
        // Ignore the codec's startup transient, keeping those samples as pre-roll.
        if(frames<rate/5)speech=false;
        if(speech) {
            if(!voiced)first_voice=frames;
            voiced+=480; quiet=0; last_voice=frames+480;
            if(voiced>=rate/5&&!speech_detected.exchange(true)){
                printf("CLAWD VOICE_STARTED\n");fflush(stdout);
            }
        }
        else quiet+=480;
        frames+=480;
        if(voiced>=rate/5 && quiet>=rate*1.2)break;
        if(voiced<rate/5 && frames>=rate*6)break;
    }
    vad_destroy(vad);
    codec->EnableInput(false);
    if(cancelled || !read_ok || voiced<rate/5) {
        printf("CLAWD %s peak_rms=%d\n",cancelled?"CANCELLED":read_ok?"NO_SPEECH":"AUDIO_ERROR",int(peak_rms));
        fflush(stdout);
        finish(codec,pcm); vTaskDelete(nullptr); return;
    }
    state=State::Thinking;
    const size_t start=first_voice>rate*3/10?first_voice-rate*3/10:0;
    const size_t end=std::min(frames,last_voice+rate/4);
    const size_t bytes=(end-start)*2;
    printf("CLAWD RECORD_DONE record_ms=%u voiced_ms=%u quiet_ms=%u peak_rms=%d\n",
        unsigned(frames*1000/rate),unsigned(voiced*1000/rate),unsigned(quiet*1000/rate),int(peak_rms));
    fflush(stdout);
    const uint32_t crc=esp_crc32_le(0,reinterpret_cast<uint8_t*>(pcm+start),bytes);
    // stdout stays locked throughout this frame: the host validates length, CRC and footer.
    flockfile(stdout);
    printf("CLAWD_PCM 24000 %u %lu\n",unsigned(bytes),(unsigned long)crc); fflush(stdout);
    auto* raw=reinterpret_cast<uint8_t*>(pcm+start);
    size_t sent=0;
    const auto deadline=esp_timer_get_time()+15000000;
    // Cancellation cannot truncate a declared binary frame. Drain it, then tell
    // the sole host reader to discard it before any cloud submission.
    while(sent<bytes && esp_timer_get_time()<deadline) {
        int n=usb_serial_jtag_write_bytes(raw+sent,std::min(size_t(4096),bytes-sent),pdMS_TO_TICKS(250));
        if(n>0)sent+=n;
    }
    printf("\nCLAWD_PCM_END\nCLAWD PCM_STATUS cancelled=%d\n",int(cancelled.load())); fflush(stdout);
    funlockfile(stdout);
    if(sent!=bytes)transport_fault=true;
    finish(codec,pcm,State::Thinking);
    vTaskDelete(nullptr);
}

static void playback_task(void*) {
    auto codec=Board::GetInstance().GetAudioCodec();
    codec->EnableInput(false);
    printf("CLAWD UPLOAD_READY\n"); fflush(stdout);
    uint32_t header[2]{};
    if(!read_exact(reinterpret_cast<uint8_t*>(header),sizeof(header),5000) ||
       header[0]==0 || header[0]%2 || header[0]>rate*30*2) {
        transport_fault=true;
        printf("CLAWD AUDIO_ERROR header\n"); finish(codec,nullptr); vTaskDelete(nullptr); return;
    }
    auto* pcm=static_cast<int16_t*>(heap_caps_malloc(header[0],MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
    if(!pcm) { transport_fault=true; printf("CLAWD AUDIO_ERROR allocation\n"); finish(codec,nullptr); vTaskDelete(nullptr); return; }
    size_t received=0;
    while(received<header[0]) {
        size_t count=std::min(size_t(2048),size_t(header[0])-received);
        if(!read_exact(reinterpret_cast<uint8_t*>(pcm)+received,count,3000))break;
        received+=count;
        printf("CLAWD UPLOAD_ACK %u\n",unsigned(received)); fflush(stdout);
    }
    owns_usb=false;
    if(received!=header[0] || esp_crc32_le(0,reinterpret_cast<uint8_t*>(pcm),header[0])!=header[1]) {
        transport_fault=true;
        printf("CLAWD AUDIO_ERROR crc_or_timeout\n"); finish(codec,pcm); vTaskDelete(nullptr); return;
    }
    if(!cancelled) {
        codec->EnableOutput(true);
        playback_duration_ms=header[0]*1000/(rate*2);
        state=State::Speaking;
        printf("CLAWD SPEAKING volume=%d pcm_gain_percent=100\n",codec->output_volume()); fflush(stdout);
        std::vector<int16_t> chunk;
        for(size_t offset=0;offset<header[0]/2 && !cancelled;offset+=480) {
            size_t count=std::min<size_t>(480,size_t(header[0])/2-offset);
            chunk.assign(pcm+offset,pcm+offset+count);
            double energy=0;
            for(auto value:chunk)energy+=double(value)*value;
            // Keep the accepted mouth animation while playing the original PCM.
            mouth=std::clamp(int(std::sqrt(energy/count)*3/5/28),0,100);
            codec->OutputData(chunk);
        }
    }
    printf("CLAWD PLAY_DONE cancelled=%d\n",int(cancelled.load())); fflush(stdout);
    finish(codec,pcm);
    vTaskDelete(nullptr);
}

bool record(bool cue) {
    if(!stop_wake()){printf("CLAWD RECORD_WAIT reason=wake\n");return false;}
    if(working.exchange(true)){printf("CLAWD RECORD_WAIT reason=codec_busy\n");return false;}
    record_cue=cue;speech_detected=false;
    cancelled=false;
    if(xTaskCreate(record_task,"clawd_mic",6144,nullptr,5,nullptr)!=pdPASS) { working=false; return false; }
    return true;
}

bool receive_and_play() {
    if(!stop_wake())return false;
    if(working.exchange(true))return false;
    cancelled=false; owns_usb=true; state=State::Uploading;
    if(xTaskCreate(playback_task,"clawd_speaker",6144,nullptr,5,nullptr)!=pdPASS) {
        owns_usb=false; working=false; state=State::Idle; return false;
    }
    return true;
}
}
