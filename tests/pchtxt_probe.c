/* TOTK_PCHTXT as the runtime applies it (patch_pchtxt in game_patches.c, the production code) on a synthetic main:
   prints the 16 bytes at 0x40 after the patch, or "FAIL <reason>" with exit code 3 when the start would stop.
   Run from a folder holding extracted/v0/exefs/main (the build id at 0x40), with TOTK_PCHTXT set. */
#include "../games/totk/runtime/game_patches.c"
#include <stdio.h>
#include <stdlib.h>

_Noreturn void runtime_fail(const char *message) { printf("FAIL %s\n", message); exit(3); }

int main(void) {
    static uint8_t image[0x1000];
    Module m = {0};
    m.name = "main"; m.base = image; m.file_size = m.size = sizeof image;
    patch_pchtxt(&m);
    for (int i = 0; i < 16; i++) printf("%02x", image[0x40 + i]);
    printf("\n");
    return 0;
}
