#pragma once

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <stackchan/stackchan.h>

// A finite gesture per spoken answer. Angles use the HAL's tenths of a degree.
// Read the current pose each time; never seek an absolute home or rotate in PWM.
class ClawdChatMotion {
public:
    void update(bool speaking, uint32_t now, uint32_t speech_ms)
    {
        if (!speaking) {
            stop("quiet");
            was_speaking_ = false;
            return;
        }
        if (!was_speaking_) {
            was_speaking_ = true;
            start(now, speech_ms);
        }
        if (!active_) return;
        const uint32_t elapsed = now - started_;
        const int next = elapsed / 600;
        const int end = nod_ ? 2 : 3;
        if (next >= end) {
            stop("complete");
        } else if (next != stage_) {
            stage_ = next;
            target(stage_ == end - 1 ? base_ : base_ - direction_ * 45);
        }
    }

    void stop(const char* reason)
    {
        if (!active_) return;
        auto& motion = GetStackChan().motion();
        motion.yawServo().stopAndRelease();
        motion.pitchServo().stopAndRelease();
        active_ = false;
        printf("CLAWD MOTION stopped reason=%s\n", reason);
        metadata();
    }

    void metadata()
    {
        auto& motion = GetStackChan().motion();
        printf("CLAWD MOTION_STATUS active=%d yaw=%d pitch=%d yaw_torque=%d pitch_torque=%d\n",
               int(active_), motion.getCurrentYawAngle(), motion.getCurrentPitchAngle(),
               int(motion.yawServo().getTorqueEnabled()), int(motion.pitchServo().getTorqueEnabled()));
    }

private:
    bool active_ = false, was_speaking_ = false, nod_ = true;
    unsigned sequence_ = 0;
    int base_ = 0, direction_ = 1, stage_ = 0;
    uint32_t started_ = 0;
    stackchan::motion::Servo* servo_ = nullptr;

    void target(int angle)
    {
        const auto limits = servo_->getAngleLimit();
        angle = std::clamp(angle, limits.x, limits.y);
        printf("CLAWD MOTION kind=%s stage=%d actual=%d target=%d base=%d\n",
               nod_ ? "nod" : "sway", stage_, servo_->getCurrentAngle(), angle, base_);
        servo_->moveWithSpeed(angle, 500);
    }

    void start(uint32_t now, uint32_t speech_ms)
    {
        if (speech_ms < 1400) return;  // Leave enough time to return before the mic opens.
        auto& motion = GetStackChan().motion();
        nod_ = speech_ms < 2000 || sequence_ % 2 == 0;
        direction_ = sequence_ % 4 < 2 ? 1 : -1;
        ++sequence_;
        servo_ = nod_ ? &motion.pitchServo() : &motion.yawServo();
        base_ = servo_->getCurrentAngle();
        const auto limits = servo_->getAngleLimit();
        const int amount = nod_ ? 30 : 45;
        // Prefer the side with room when the current pose is near a limit.
        if (base_ + direction_ * amount < limits.x || base_ + direction_ * amount > limits.y)
            direction_ = -direction_;
        const int first = std::clamp(base_ + direction_ * amount, limits.x, limits.y);
        if (std::abs(first - base_) < 10) return;
        motion.setAutoAngleSyncEnabled(true);
        servo_->setTorqueEnabled(true);
        stage_ = 0;
        started_ = now;
        active_ = true;
        target(first);
    }
};
