#pragma once
#include <hal/hal.h>
#include <hal/clawd_usb_audio.h>
#include <hal/clawd_idle_scenes.h>
#include <lvgl.h>
#include <cmath>

// Native 320x240 drawing; all objects pass taps to the existing face panel.
class ClawdFamilyUi {
    lv_obj_t *work=nullptr, *indicator=nullptr, *busy_dot=nullptr;
    lv_obj_t *arms[2]{}, *eyes[2]{}, *keys[4]{};
    lv_obj_t *dots[3]{}, *bars[3]{}, *pause[2]{}, *mic[4]{}, *check=nullptr;
    uint32_t last_light=0, done_until=0, face_until=0;
    int previous=-1;
    bool previous_work=false;
    static lv_obj_t* block(lv_obj_t* parent,int x,int y,int w,int h,uint32_t color,int radius=0) {
        auto o=lv_obj_create(parent);lv_obj_remove_style_all(o);
        lv_obj_set_pos(o,x,y);lv_obj_set_size(o,w,h);
        lv_obj_set_style_bg_color(o,lv_color_hex(color),0);
        lv_obj_set_style_bg_opa(o,LV_OPA_COVER,0);
        lv_obj_set_style_radius(o,radius,0);
        lv_obj_remove_flag(o,LV_OBJ_FLAG_CLICKABLE);lv_obj_remove_flag(o,LV_OBJ_FLAG_SCROLLABLE);
        return o;
    }
    static void visible(lv_obj_t* o,bool show) {
        if(show)lv_obj_remove_flag(o,LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(o,LV_OBJ_FLAG_HIDDEN);
    }
public:
    ClawdIdleScenes scenes;
    bool task_running=false, task_error=false;
    void frame_metadata(uint32_t now) {
        printf("CLAWD WORK_FRAME tick=%lu arm_y=%d key_phase=%u scene=%s\n",(unsigned long)now,
               int(lv_obj_get_y(arms[0])),unsigned((now/180)%4),
               scenes.reminder()?"drink_reminder":lv_obj_has_flag(work,LV_OBJ_FLAG_HIDDEN)?"face_or_idle_scene":"laptop");
    }
    void init(lv_obj_t* panel) {
        work=block(panel,0,0,320,240,0xFAF7F2);
        block(work,55,187,220,5,0xE8E0D5,3);
        block(work,64,90,98,64,0xD97757);
        block(work,64,90,22,64,0xBA6249);
        // Four staggered grounded feet; the other two limbs type at the laptop.
        for(int x:{64,90,125,151})block(work,x,148,10,17,0xD97757);
        for(int x:{58,84,119,145})block(work,x,162,12,11,0xD97757);
        block(work,64,148,10,17,0xBA6249);
        block(work,58,162,12,11,0xBA6249);
        eyes[0]=block(work,102,106,10,10,0x282522);
        eyes[1]=block(work,151,106,10,10,0x282522);
        arms[0]=block(work,156,126,18,10,0xC46A4F);
        arms[1]=block(work,159,153,28,12,0xD97757);
        block(work,185,169,55,7,0x736E68,2);
        // A stepped laptop lid matches the pixel silhouette in the reference.
        for(int i=0;i<8;i++)block(work,231+i*4,165-i*5,7,7,0x736E68,1);
        for(int i=0;i<4;i++)keys[i]=block(work,192+i*10,168,6,3,0xA9A29A,1);
        visible(work,false);
        scenes.init(panel);
        indicator=block(panel,126,211,68,28,0xFAF7F2);
        for(int i=0;i<3;i++) {
            dots[i]=block(indicator,15+i*16,11,7,7,0xD99744,4);
            bars[i]=block(indicator,17+i*13,8,6,14,0x4A86D4,3);
        }
        for(int i=0;i<2;i++)pause[i]=block(indicator,24+i*12,7,5,15,0xB4ADA4,2);
        mic[0]=block(indicator,30,2,8,13,0x319D64,4);
        mic[1]=block(indicator,24,12,20,9,0x319D64,5);
        block(mic[1],3,0,14,6,0xFAF7F2,2);
        mic[2]=block(indicator,33,19,3,5,0x319D64);
        mic[3]=block(indicator,27,24,15,2,0x319D64,1);
        check=lv_label_create(indicator);lv_label_set_text(check,LV_SYMBOL_OK);
        lv_obj_set_pos(check,26,7);lv_obj_set_style_text_color(check,lv_color_hex(0x94865D),0);
        lv_obj_remove_flag(check,LV_OBJ_FLAG_CLICKABLE);
        busy_dot=block(panel,299,10,7,7,0x8B72AE,4);
    }
    void task(char code,uint32_t now) {
        if(code=='W'){task_running=true;task_error=false;done_until=0;}
        if(code=='V'){task_running=false;task_error=false;done_until=0;}
        if(code=='D'){task_running=false;task_error=false;done_until=now+4000;}
        if(code=='E'){task_running=false;task_error=true;done_until=0;}
    }
    void show_face(uint32_t now) {face_until=now+6000;}
    void update(bool connected,clawd_audio::State state,uint32_t now,bool dancing=false,bool camera=false) {
        using S=clawd_audio::State;
        bool listening=connected&&state==S::Listening;
        bool thinking=connected&&(state==S::Thinking||state==S::Uploading);
        bool speaking=connected&&(state==S::Speaking||dancing);
        bool done=connected&&done_until>now;
        // The laptop is the default scene, including offline and paused states.
        // Cover the short gaps between automatic listening segments, too.
        if(listening||thinking||speaking)face_until=now+1500;
        bool working=!listening&&!thinking&&!speaking&&
                     ((connected&&state==S::Paused)||now>=face_until);
        int scene=scenes.update(working,listening||((thinking||speaking)&&!scenes.reminder())||dancing||camera,now);
        visible(work,working&&!scene);visible(busy_dot,connected&&task_running&&!working);
        visible(indicator,connected&&!scene);
        for(auto o:mic)visible(o,listening);
        for(auto o:dots)visible(o,thinking);
        for(auto o:bars)visible(o,speaking);
        for(auto o:pause)visible(o,!listening&&!thinking&&!speaking&&!done);
        visible(check,!listening&&!thinking&&!speaking&&done);
        for(int i=0;i<3;i++) {
            lv_obj_set_style_bg_opa(dots[i],(now/300)%3==unsigned(i)?255:65,0);
            int h=8+(clawd_audio::mouth.load()/10+int(now/140)+i*3)%12;
            lv_obj_set_height(bars[i],h);lv_obj_set_y(bars[i],22-h);
        }
        bool blink=now%5000>4800;
        for(auto o:eyes)lv_obj_set_height(o,blink?3:10);
        const unsigned phase=(now/180)%4;
        static constexpr int upper_y[]={126,129,132,128}, upper_w[]={18,28,22,9};
        static constexpr int lower_y[]={153,150,152,155}, lower_w[]={28,19,24,32};
        lv_obj_set_y(arms[0],upper_y[phase]);lv_obj_set_width(arms[0],upper_w[phase]);
        lv_obj_set_y(arms[1],lower_y[phase]);lv_obj_set_width(arms[1],lower_w[phase]);
        for(int i=0;i<4;i++)lv_obj_set_style_bg_color(keys[i],lv_color_hex((now/180)%4==unsigned(i)?0xE2D7C6:0xA9A29A),0);
        if(now-last_light>=100) {
            last_light=now;
            uint8_t r=0,g=0,b=0;
            int breath=12+int(9*(1+std::sin(double(now)*0.0026)));
            if(listening){g=35;b=3;}
            else if(thinking){r=breath;g=breath/3;}
            else if(dancing){if((now/400)%2){r=28;b=35;}else{r=35;g=20;}}
            else if(speaking){r=2;g=7;b=18+clawd_audio::mouth.load()/4;}
            else if(scene==3){} // No decorative LEDs while the night scene is visible.
            else if(connected&&task_error){r=22;g=2;}
            else if(done){r=14;g=11;b=5;}
            else if(connected&&task_running){r=breath/2;b=breath;}
            GetHAL().showRgbColor(r,g,b);
        }
        int mode=!connected?0:listening?1:thinking?2:dancing?6:speaking?3:task_error?5:task_running?4:0;
        if(mode!=previous||working!=previous_work) {
            previous=mode;previous_work=working;
            printf("CLAWD UI mode=%d work=%d led=%s\n",mode,int(working),
                   mode==6?"dance":mode==1?"green":mode==2?"amber":mode==3?"blue":mode==4?"purple":mode==5?"red":"off");
        }
    }
};
