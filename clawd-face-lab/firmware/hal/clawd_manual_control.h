#pragma once
#include <algorithm>
#include <cstdlib>
#include <cstdio>
#include <board.h>
#include <audio/audio_codec.h>
#include <driver/usb_serial_jtag.h>
#include <esp_crc.h>
#include <stackchan/stackchan.h>
#include <hal/clawd_usb_audio.h>
#include <settings.h>

// One bounded, CRC-checked control packet. No motor calibration or raw PWM interface.
class ClawdManualControl {
    bool active_=false;
    uint32_t started_=0;
    unsigned corrections_=0;
    int target_yaw_=0,target_pitch_=150;
public:
    void restore_volume() {
        // The upstream codec treats saved zero as 10 on boot; preserve deliberate mute here.
        Settings settings("audio",false);
        int saved=settings.GetInt("output_volume",100);
        auto codec=Board::GetInstance().GetAudioCodec();
        if(codec&&saved>=0&&saved<=100&&codec->output_volume()!=saved)codec->SetOutputVolume(saved);
    }
    bool active()const{return active_;}
    void metadata() {
        auto& m=GetStackChan().motion();
        const auto yl=m.yawServo().getAngleLimit(),pl=m.pitchServo().getAngleLimit();
        auto codec=Board::GetInstance().GetAudioCodec();
        printf("CLAWD CONTROL_STATUS active=%d yaw=%d pitch=%d volume=%d yaw_min=%d yaw_max=%d pitch_min=%d pitch_max=%d yaw_torque=%d pitch_torque=%d\n",
            int(active_),m.getCurrentYawAngle(),m.getCurrentPitchAngle(),codec?codec->output_volume():-1,
            std::max(-300,yl.x),std::min(300,yl.y),std::max(30,pl.x),std::min(450,pl.y),
            int(m.yawServo().getTorqueEnabled()),int(m.pitchServo().getTorqueEnabled()));
    }
    void stop(const char* reason) {
        if(!active_)return;
        auto& m=GetStackChan().motion();
        m.yawServo().stopAndRelease();m.pitchServo().stopAndRelease();
        m.setAutoTorqueReleaseEnabled(true);m.setAutoAngleSyncEnabled(true);
        active_=false;
        printf("CLAWD CONTROL_DONE reason=%s\n",reason);metadata();
    }
    void update(uint32_t now) {
        if(!active_)return;
        auto& m=GetStackChan().motion();
        if(now-started_>=3500)stop("timeout");
        else if(now-started_>=900&&std::abs(m.getCurrentYawAngle()-target_yaw_)<=15&&
                std::abs(m.getCurrentPitchAngle()-target_pitch_)<=15)stop("complete");
        else if(corrections_<2&&now-started_>=1200+corrections_*1000) {
            // Real feedback exposes servo deadband: a software endpoint alone can
            // remain several degrees short. Allow two small, bounded corrections.
            ++corrections_;
            const auto yl=m.yawServo().getAngleLimit(),pl=m.pitchServo().getAngleLimit();
            const int ey=target_yaw_-m.getCurrentYawAngle(),ep=target_pitch_-m.getCurrentPitchAngle();
            if(std::abs(ey)>15)m.moveYawWithSpeed(std::clamp(target_yaw_+std::clamp(ey,-45,45),std::max(-300,yl.x),std::min(300,yl.y)),500);
            if(std::abs(ep)>15)m.movePitchWithSpeed(std::clamp(target_pitch_+std::clamp(ep,-30,30),std::max(30,pl.x),std::min(450,pl.y)),500);
            printf("CLAWD CONTROL_SETTLE attempt=%u yaw_error=%d pitch_error=%d\n",corrections_,ey,ep);
        }
    }
    void packet(uint32_t now,bool busy) {
        uint8_t data[10]{};size_t got=0;
        const uint32_t begin=GetHAL().millis();
        while(got<sizeof(data)&&GetHAL().millis()-begin<500) {
            int n=usb_serial_jtag_read_bytes(data+got,sizeof(data)-got,pdMS_TO_TICKS(20));
            if(n>0)got+=n;
        }
        uint32_t expected=uint32_t(data[6])|(uint32_t(data[7])<<8)|(uint32_t(data[8])<<16)|(uint32_t(data[9])<<24);
        if(got!=sizeof(data)||esp_crc32_le(0,data,6)!=expected) {
            printf("CLAWD CONTROL_ERROR reason=frame\n");
            // Quarantine late bytes rather than interpreting a truncated packet as commands.
            clawd_audio::transport_fault=true;return;
        }
        int action=data[0],yaw=int16_t(uint16_t(data[1])|(uint16_t(data[2])<<8));
        int pitch=int16_t(uint16_t(data[3])|(uint16_t(data[4])<<8)),volume=data[5];
        if(action==0){metadata();printf("CLAWD CONTROL_DONE reason=status\n");return;}
        if(action>2||volume>100){printf("CLAWD CONTROL_ERROR reason=range\n");return;}
        if(busy||active_||!clawd_audio::stop_wake()){printf("CLAWD CONTROL_ERROR reason=busy\n");return;}
        if(action==2) {
            auto codec=Board::GetInstance().GetAudioCodec();
            if(!codec){printf("CLAWD CONTROL_ERROR reason=codec\n");return;}
            if(codec->output_volume()!=volume)codec->SetOutputVolume(volume);
            metadata();printf("CLAWD CONTROL_DONE reason=volume\n");return;
        }
        auto& m=GetStackChan().motion();
        const auto yl=m.yawServo().getAngleLimit(),pl=m.pitchServo().getAngleLimit();
        if(std::max(-300,yl.x)>std::min(300,yl.y)||std::max(30,pl.x)>std::min(450,pl.y)) {
            printf("CLAWD CONTROL_ERROR reason=limits\n");return;
        }
        target_yaw_=std::clamp(yaw,std::max(-300,yl.x),std::min(300,yl.y));
        target_pitch_=std::clamp(pitch,std::max(30,pl.x),std::min(450,pl.y));
        m.setAutoAngleSyncEnabled(true);m.setAutoTorqueReleaseEnabled(false);m.setTorqueEnabled(true);
        m.moveWithSpeed(target_yaw_,target_pitch_,350);
        active_=true;started_=now;corrections_=0;
        printf("CLAWD CONTROL_BEGIN yaw=%d pitch=%d\n",target_yaw_,target_pitch_);metadata();
    }
};
