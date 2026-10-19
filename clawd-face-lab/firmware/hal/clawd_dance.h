#pragma once

#include <algorithm>
#include <cstdio>
#include <stackchan/stackchan.h>

// Ten gentle beats, relative to the current pose. Units are tenths of a degree.
// The final two beats settle at the starting pose before releasing both motors.
class ClawdDance {
public:
    bool active() const { return active_; }

    void start(uint32_t now)
    {
        auto& motion = GetStackChan().motion();
        base_yaw_ = motion.getCurrentYawAngle();
        base_pitch_ = motion.getCurrentPitchAngle();
        motion.setAutoAngleSyncEnabled(true);
        // Hold yaw between beats so its early auto-release cannot interrupt centering.
        // Pitch keeps its normal auto-release and stall protection.
        motion.yawServo().setAutoTorqueReleaseEnabled(false);
        motion.setTorqueEnabled(true);
        active_ = true;
        started_ = now;
        stage_ = 0;
        printf("CLAWD DANCE_START duration_ms=8000 name=crab1\n");
        target();
        // Sync once at entry, then preserve the continuous trajectory between beats.
        motion.setAutoAngleSyncEnabled(false);
    }

    void update(uint32_t now)
    {
        if (!active_) return;
        if (now - started_ >= 8000) {
            finish(now, false, "complete");
            return;
        }
        const unsigned next = (now - started_) / 800;
        if (next != stage_) {
            stage_ = next;
            target();
        }
    }

    void stop(uint32_t now, const char* reason)
    {
        if (active_) finish(now, true, reason);
    }

    void metadata()
    {
        auto& motion = GetStackChan().motion();
        printf("CLAWD DANCE_STATUS active=%d yaw=%d pitch=%d yaw_torque=%d pitch_torque=%d\n",
               int(active_), motion.getCurrentYawAngle(), motion.getCurrentPitchAngle(),
               int(motion.yawServo().getTorqueEnabled()), int(motion.pitchServo().getTorqueEnabled()));
    }

private:
    bool active_ = false;
    uint32_t started_ = 0;
    unsigned stage_ = 0;
    int base_yaw_ = 0, base_pitch_ = 0;

    void target()
    {
        // Left/right, left/right, two nods, then a two-beat finish.
        static constexpr int yaw[]   = {-60, 60, -60, 60, 0, 0, 0, 0, 0, 0};
        static constexpr int pitch[] = {0, 20, 0, 20, 40, 0, 40, 0, 0, 0};
        auto& motion = GetStackChan().motion();
        const auto yl = motion.yawServo().getAngleLimit();
        const auto pl = motion.pitchServo().getAngleLimit();
        int yaw_offset = yaw[stage_];
        // A measured physical offset can remain even with software on target.
        // One bounded settling move, then the final beat restores the baseline.
        if (stage_ == 8) {
            const int error = motion.getCurrentYawAngle() - base_yaw_;
            if (std::abs(error) > 15) {
                const int correction = std::clamp(std::abs(error) + 6, 30, 45);
                yaw_offset = error > 0 ? -correction : correction;
                printf("CLAWD DANCE_SETTLE error=%d offset=%d\n", error, yaw_offset);
            }
        }
        const int y = std::clamp(base_yaw_ + yaw_offset, yl.x, yl.y);
        const int p = std::clamp(base_pitch_ + pitch[stage_], pl.x, pl.y);
        printf("CLAWD DANCE_TRACE stage=%u software_yaw=%d yaw_torque=%d\n",
               stage_, motion.yawServo().stackchan::motion::Servo::getCurrentAngle(), int(motion.yawServo().getTorqueEnabled()));
        printf("CLAWD DANCE_STAGE stage=%u yaw=%d pitch=%d target_yaw=%d target_pitch=%d base_yaw=%d base_pitch=%d\n",
               stage_, motion.getCurrentYawAngle(), motion.getCurrentPitchAngle(), y, p, base_yaw_, base_pitch_);
        // Avoid restarting a trajectory during the final settling beats.
        if (stage_ == 0 || stage_ >= 8 || yaw[stage_] != yaw[stage_ - 1]) {
            motion.yawServo().setTorqueEnabled(true);
            motion.moveYawWithSpeed(y, 500);
        }
        if (stage_ == 0 || pitch[stage_] != pitch[stage_ - 1]) {
            motion.pitchServo().setTorqueEnabled(true);
            motion.movePitchWithSpeed(p, 500);
        }
    }

    void finish(uint32_t now, bool cancelled, const char* reason)
    {
        auto& motion = GetStackChan().motion();
        motion.setAutoAngleSyncEnabled(true);
        motion.yawServo().setAutoTorqueReleaseEnabled(true);
        motion.yawServo().stopAndRelease();
        motion.pitchServo().stopAndRelease();
        active_ = false;
        printf("CLAWD DANCE_DONE cancelled=%d elapsed_ms=%lu reason=%s\n", int(cancelled),
               (unsigned long)(now - started_), reason);
        metadata();
    }
};
