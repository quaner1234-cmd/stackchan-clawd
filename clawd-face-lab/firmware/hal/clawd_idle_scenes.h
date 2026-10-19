#pragma once
#include <lvgl.h>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <hal/clawd_artwork_data.h>

// Small retained LVGL shapes: no frame buffers, files, or blocking animation tasks.
class ClawdIdleScenes {
    lv_obj_t *scene=nullptr,*body=nullptr,*cup=nullptr,*hands[2]{},*eyes[2]{},*heart_eyes[2]{};
    lv_obj_t *gift=nullptr,*hearts[6]{},*sparks[2][8]{},*drops[3]{};
    uint32_t forced_at=0, forced_ms=0;
    int forced=0, shown=0;
    lv_obj_t *bed=nullptr,*sleeper=nullptr,*blanket=nullptr,*zzz[3]{};
    lv_obj_t *gallery=nullptr;
    bool sleeping=false;
    static lv_obj_t* block(lv_obj_t* p,int x,int y,int w,int h,uint32_t c,int radius=0) {
        auto o=lv_obj_create(p);lv_obj_remove_style_all(o);
        lv_obj_set_pos(o,x,y);lv_obj_set_size(o,w,h);
        lv_obj_set_style_bg_color(o,lv_color_hex(c),0);
        lv_obj_set_style_bg_opa(o,255,0);lv_obj_set_style_radius(o,radius,0);
        lv_obj_remove_flag(o,LV_OBJ_FLAG_CLICKABLE);lv_obj_remove_flag(o,LV_OBJ_FLAG_SCROLLABLE);
        return o;
    }
    static lv_obj_t* group(lv_obj_t* p,int x,int y,int w,int h) {
        auto o=block(p,x,y,w,h,0);lv_obj_set_style_bg_opa(o,0,0);return o;
    }
    static void visible(lv_obj_t* o,bool yes) {
        if(yes)lv_obj_remove_flag(o,LV_OBJ_FLAG_HIDDEN);else lv_obj_add_flag(o,LV_OBJ_FLAG_HIDDEN);
    }
    static lv_obj_t* heart(lv_obj_t* p,int x,int y,int unit,uint32_t color) {
        auto o=group(p,x,y,8*unit,7*unit);
        static constexpr uint8_t rows[]={0x66,0xff,0xff,0x7e,0x3c,0x18,0x18};
        for(int y=0;y<7;y++)for(int x=0;x<8;) {
            if(!(rows[y]&(0x80>>x))){++x;continue;}
            int start=x;while(x<8&&(rows[y]&(0x80>>x)))++x;
            block(o,start*unit,y*unit,(x-start)*unit,unit,color);
        }
        return o;
    }
public:
    void set_sleep(bool value) {sleeping=value;}
    bool sleep_mode()const{return sleeping;}
    bool reminder()const{return forced==2;}
    void init(lv_obj_t* panel) {
        scene=block(panel,0,0,320,240,0xFAF7F2);
        block(scene,78,187,165,5,0xE8E0D5,3);
        body=group(scene,0,0,320,200);
        // Same accepted 98x64 silhouette, four short feet and two expressive arms.
        block(body,111,105,98,64,0xD97757);block(body,111,105,22,64,0xBA6249);
        for(int x:{111,137,172,198})block(body,x,163,10,17,0xD97757);
        for(int x:{105,131,166,192})block(body,x,177,12,11,0xD97757);
        block(body,111,163,10,17,0xBA6249);block(body,105,177,12,11,0xBA6249);
        eyes[0]=block(body,149,121,10,10,0x282522);eyes[1]=block(body,198,121,10,10,0x282522);
        heart_eyes[0]=heart(body,144,119,2,0x633247);heart_eyes[1]=heart(body,190,119,2,0x633247);
        block(body,141,139,12,4,0xECA28B,2);block(body,196,139,12,4,0xECA28B,2);
        block(body,174,144,3,4,0x633C32);block(body,177,148,9,3,0x633C32);block(body,186,144,3,4,0x633C32);
        hands[0]=block(body,97,145,20,10,0xBA6249);
        hands[1]=block(body,204,145,24,11,0xD97757);
        gift=heart(body,71,114,4,0xE77F95);
        cup=group(body,205,145,47,39);
        block(cup,26,8,16,23,0x64A8C9,7);block(cup,29,12,9,14,0xFAF7F2,3);
        block(cup,0,3,31,32,0x76B9D8,3);block(cup,3,7,5,22,0xBDE0ED,2);
        block(cup,0,1,31,5,0xDDEEF4,2);block(cup,4,2,23,2,0x4D91B6,1);
        // A little heart on the cup and three floating water droplets.
        heart(cup,12,15,1,0xF6F3EA);
        for(int i=0;i<3;i++) {
            drops[i]=group(scene,0,0,12,17);
            block(drops[i],4,0,4,5,0x76B9D8,1);block(drops[i],2,4,8,6,0x76B9D8,2);
            block(drops[i],0,8,12,9,0x76B9D8,6);block(drops[i],3,9,2,4,0xDDEEF4,1);
        }
        for(int i=0;i<6;i++)hearts[i]=heart(scene,0,0,i%3==0?3:2,i%2?0xD99AB0:0xE77F95);
        for(int b=0;b<2;b++)for(int i=0;i<8;i++)sparks[b][i]=block(scene,0,0,3,3,i%2?0xE9BC68:0xE7A0AC,2);
        bed=block(panel,0,0,320,240,0x182333);
        // Quiet night palette and the same flat orange silhouette, curled on a pillow.
        block(bed,248,30,28,28,0xF4D78A,14);block(bed,258,23,26,27,0x182333,14);
        for(int i=0;i<6;i++)block(bed,42+i*41,35+(i%3)*16,3,3,0x8A9AAE,1);
        block(bed,71,177,182,13,0x34465E,6);block(bed,73,160,75,21,0x96A9C0,8);
        sleeper=group(bed,0,0,320,210);
        block(sleeper,106,104,98,64,0xD97757);block(sleeper,106,104,22,64,0xBA6249);
        for(int x:{106,132,167,193})block(sleeper,x,163,10,17,0xD97757);
        block(sleeper,143,125,14,4,0x593E3B,2);block(sleeper,186,125,14,4,0x593E3B,2);
        block(sleeper,140,137,12,4,0xE99B85,2);block(sleeper,188,137,12,4,0xE99B85,2);
        block(sleeper,168,139,6,3,0x714A40,2);
        blanket=block(sleeper,126,151,100,29,0x567E9B,7);
        block(blanket,5,5,88,3,0x789EB7,1);block(blanket,64,12,9,6,0xA2BDCD,3);
        block(sleeper,112,149,19,9,0xD97757,3);block(sleeper,201,149,20,9,0xD97757,3);
        for(int i=0;i<3;i++) {
            zzz[i]=group(bed,0,0,22,23);
            block(zzz[i],0,0,18,3,0xA8BDD0,1);block(zzz[i],0,18,18,3,0xA8BDD0,1);
            for(int j=0;j<5;j++)block(zzz[i],14-j*3,3+j*3,4,4,0xA8BDD0,1);
        }
        visible(bed,false);
        visible(scene,false);
        gallery=lv_image_create(panel);lv_obj_set_pos(gallery,0,0);
        lv_image_set_src(gallery,clawd_art_image(0));
        lv_obj_remove_flag(gallery,LV_OBJ_FLAG_CLICKABLE);lv_obj_remove_flag(gallery,LV_OBJ_FLAG_SCROLLABLE);
        visible(gallery,false);
    }
    void start(int kind,uint32_t now) {
        stop("replace");forced=kind;forced_at=now;forced_ms=kind==2?12000:10000;
        printf("CLAWD SCENE_START kind=%s duration_ms=%lu\n",kind==2?"drink":"love",(unsigned long)forced_ms);
    }
    void stop(const char* reason) {
        if(forced)printf("CLAWD SCENE_DONE kind=%s reason=%s\n",forced==2?"drink":"love",reason);
        forced=0;
    }
    int update(bool idle,bool blocked,uint32_t now) {
        if(forced&&(blocked||now-forced_at>=forced_ms))stop(blocked?"interaction":"complete");
        // Preserve the first 80s, then three approved still paintings, 15s each.
        // The host's night flag keeps idle sleep continuous until the morning alarm.
        uint32_t cycle=now%125000;
        int kind=forced?forced:idle?(sleeping?3:cycle<30000?0:cycle<40000?1:cycle<60000?0:cycle<70000?2:cycle<80000?3:cycle<95000?4:cycle<110000?5:6):0;
        if(blocked)kind=0;
        if(kind!=shown){
            shown=kind;
            if(kind>=4)lv_image_set_src(gallery,clawd_art_image(kind-4));
            static const char* names[]={"laptop_or_face","love","drink","sleep","art_scream","art_pearl","art_starry"};
            printf("CLAWD SCENE kind=%s\n",names[kind]);
        }
        visible(scene,kind==1||kind==2);visible(bed,kind==3);
        visible(gallery,kind>=4);
        if(kind>=4)return kind;
        if(kind==3) {
            const float breath=.5f-.5f*std::cos(float(now%5000)*.001256637f);
            lv_obj_set_y(sleeper,-int(3*breath));
            lv_obj_set_height(blanket,29+int(2*breath));
            for(int i=0;i<3;i++) {
                float p=float((now+i*1600)%5000)/5000;
                lv_obj_set_pos(zzz[i],218+int(30*p),104-int(65*p));
                lv_obj_set_style_opa(zzz[i],int(200*std::sin(p*3.1415926f)),0);
            }
            return kind;
        }
        if(!kind)return 0;
        uint32_t t=forced?now-forced_at:cycle-(kind==1?30000:60000);
        bool love=kind==1;
        lv_obj_set_y(body,love?int(2*std::sin(t*.004)):0);
        for(int i=0;i<2;i++) {
            visible(heart_eyes[i],love);visible(eyes[i],!love);
            lv_obj_set_height(eyes[i],t%3800>3550?3:10);
        }
        visible(gift,love);visible(cup,!love);
        lv_obj_set_y(gift,112-int(5*(1+std::sin(t*.003))));
        int lift=int(15*(.5-.5*std::cos((t%4000)*.0015707963)));
        lv_obj_set_pos(cup,205-lift/4,145-lift);
        lv_obj_set_pos(hands[0],97,love?132+int(4*std::sin(t*.004)):151);
        lv_obj_set_pos(hands[1],204,love?137-int(5*std::sin(t*.004)):166-lift);
        for(int i=0;i<6;i++) {
            visible(hearts[i],love);
            float p=float((t+i*530)%3400)/3400;
            lv_obj_set_pos(hearts[i],38+i*44+int(5*std::sin(p*6.28+i)),162-int(p*135));
            lv_obj_set_style_opa(hearts[i],int(220*std::sin(p*3.1415926)),0);
        }
        for(int b=0;b<2;b++) {
            float p=float((t+b*1100)%2600)/2600;
            for(int i=0;i<8;i++) {
                auto dot=sparks[b][i];visible(dot,love&&p<.72);
                float a=i*.78539816;
                lv_obj_set_pos(dot,(b?268:53)+int((5+28*p)*std::cos(a)),(b?69:65)+int((5+28*p)*std::sin(a)));
                lv_obj_set_style_opa(dot,int(210*(1-p)),0);
            }
        }
        for(int i=0;i<3;i++) {
            visible(drops[i],!love);
            float p=float((t+i*900)%3000)/3000;
            lv_obj_set_pos(drops[i],241+i*15,117-int(62*p));
            lv_obj_set_style_opa(drops[i],int(220*std::sin(p*3.1415926)),0);
        }
        return kind;
    }
};
