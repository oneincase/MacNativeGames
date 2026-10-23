#include <assert.h>
#include <stdio.h>
#include "switch_left_fallback.h"
int main(void) {
 SwitchLeftFallback s={0};float x=0,y=0;
 uint64_t t=1000000000ULL;
 switch_left_feed(&s,2336,4064,t);
 assert(!switch_left_get(&s,0,0,t,&x,&y));
 switch_left_feed(&s,2336,4064,t+199000000);
 assert(!switch_left_get(&s,0,0,t+199000000,&x,&y));
 switch_left_feed(&s,2336,4064,t+200000000);
 assert(switch_left_get(&s,0,0,t+200000000,&x,&y));
 assert(y>0.98f && x>0.13f && x<0.15f);
 switch_left_feed(&s,2048,2048,t+220000000);
 assert(switch_left_get(&s,0,0,t+220000000,&x,&y) && x==0 && y==0);
 assert(!switch_left_get(&s,0,0,t+471000000,&x,&y));
 switch_left_feed(&s,2048,4095,t+500000000);
 assert(!switch_left_get(&s,0,0.9f,t+500000000,&x,&y));
 assert(!s.active);
 s=(SwitchLeftFallback){0};
 switch_left_feed(&s,2100,2070,t);
 assert(!switch_left_get(&s,0,0,t,&x,&y));
 switch_left_feed(&s,2100,2070,t+300000000);
 assert(!switch_left_get(&s,0,0,t+300000000,&x,&y));
 switch_left_feed(&s,0,2048,t+400000000);
 assert(!switch_left_get(&s,0,0,t+400000000,&x,&y));
 switch_left_feed(&s,0,2048,t+600000000);
 assert(switch_left_get(&s,0,0,t+600000000,&x,&y) && x==-1 && y==0);
 switch_left_feed(&s,4095,2048,t+620000000);
 assert(switch_left_get(&s,0,0,t+620000000,&x,&y) && x==1 && y==0);
 switch_left_feed(&s,4096,2048,t+630000000);
 assert(!switch_left_get(&s,0,0,t+630000000,&x,&y));
 puts("PASS: captured forward movement, activation delay, centering, stale release, native priority, drift rejection, both extremes, malformed data");
}
