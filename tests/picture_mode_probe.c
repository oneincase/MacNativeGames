/* Key 3's picture mode kept between runs: the production save/restore and key-3 cycle on a synthetic data page.
   usage: picture_mode_probe <file> */
#include "../games/totk/runtime/game_patches_v143.c"
#include <assert.h>

static int mfx_on;
int mfxup_output_enabled(void) { return mfx_on; }
void dm_gfx_set_flag(const char *name, int v) { if (!strcmp(name, "mfx_output")) mfx_on = v; }

static _Atomic uint64_t page[8];
static const char *file;

static void expect_file(const char *want) {
    char got[32] = ""; FILE *f = fopen(file, "r"); assert(f); assert(fgets(got, sizeof got, f)); fclose(f);
    got[strcspn(got, "\n")] = 0; assert(!strcmp(got, want));
}
static void write_file(const char *mode) { FILE *f = fopen(file, "w"); assert(f); fprintf(f, "%s\n", mode); fclose(f); }
/* A new start: the game's own state is back to 1080p with MetalFX off, then the kept mode is applied. */
static uint64_t restart(void) {
    mfx_on = 0; atomic_store(&page[4], pack_size(1920, 1080)); picture_mode_restore((uint8_t *)page);
    return atomic_load(&page[4]);
}

int main(int argc, char **argv) {
    assert(argc == 2); file = argv[1]; remove(file);
    setenv("TOTK_PICTURE_MODE", file, 1);
    output_w = 2560; output_h = 1440;
    atomic_store(&page[4], pack_size(1920, 1080)); atomic_store(&page[5], 0);
    atomic_store(&output_page, (uint8_t *)page);

    assert(restart() == pack_size(1920, 1080) && !mfx_on);            /* no file yet: the game's own start */
    runtime_output_mode_cycle(); expect_file("metalfx");
    assert(restart() == pack_size(1920, 1080) && mfx_on);              /* MetalFX comes back on */
    runtime_output_mode_cycle(); expect_file("native-2k");
    assert(restart() == pack_size(2560, 1440) && !mfx_on);
    runtime_output_mode_cycle(); expect_file("off");
    assert(restart() == pack_size(1920, 1080) && !mfx_on);

    write_file("native-4k");                                           /* a 4K choice on a 2K app: native 2K */
    assert(restart() == pack_size(2560, 1440));
    output_w = 3840; output_h = 2160;
    assert(restart() == pack_size(3840, 2160));
    write_file("sideways");                                            /* not a mode: the game's own start */
    assert(restart() == pack_size(1920, 1080) && !mfx_on);

    output_full_at_start = 1; write_file("native-2k");                 /* TOTK_OUTPUT_RES=<w>x<h> wins */
    assert(restart() == pack_size(1920, 1080));
    output_full_at_start = 0;

    unsetenv("TOTK_PICTURE_MODE"); write_file("native-2k");            /* unset: nothing is kept or read */
    assert(restart() == pack_size(1920, 1080));
    atomic_store(&page[4], pack_size(1920, 1080)); mfx_on = 0;
    runtime_output_mode_cycle(); expect_file("native-2k");

    remove(file);
    printf("pass: picture mode kept between runs\n");
    return 0;
}
