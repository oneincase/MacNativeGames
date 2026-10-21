/* Metal texture descriptor limits on this machine, and which of them can actually be built.
 *
 * Why: NieR: Automata's gameplay aborts in
 *   -[MTLTextureDescriptorInternal validateWithDevice:]: failed assertion
 *     'MTLTextureDescriptor arrayLength (768) is set to zero or greater than the max allowed size (341).'
 * at pkg_upload -> tex_mtl -> tex_by_id -> prepare_draw_textures (nvn_mtl.m), i.e. the first GPU upload of an NVN
 * texture bound by a draw. 341 is not a round number: it looks like floor(2048 / 6), the cube-array slice budget.
 * This probe prints the device's real limits and then, in a child process per case so a validation abort cannot hide
 * the rest of the output, reports which descriptors Metal will build.
 *
 * Build/run: xcrun clang -fobjc-arc -fmodules -framework Metal -framework Foundation -o /tmp/mtlprobe \
 *              scripts/diag/metal_tex_limits_probe.m && /tmp/mtlprobe
 */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static const char *type_name(MTLTextureType t) {
    switch (t) {
        case MTLTextureType1D: return "1D";
        case MTLTextureType1DArray: return "1DArray";
        case MTLTextureType2D: return "2D";
        case MTLTextureType2DArray: return "2DArray";
        case MTLTextureType2DMultisample: return "2DMS";
        case MTLTextureTypeCube: return "Cube";
        case MTLTextureTypeCubeArray: return "CubeArray";
        case MTLTextureType3D: return "3D";
        case MTLTextureType2DMultisampleArray: return "2DMSArray";
        default: return "other";
    }
}

/* One case in a child process: build the descriptor, report success/failure. */
static int try_case(const char *name) {
    @autoreleasepool {
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (!dev) { printf("  %-18s no Metal device\n", name); return 2; }
        MTLTextureType type = MTLTextureType2DArray;
        NSUInteger width = 4, height = 4, depth = 1, arrayLength = 1, samples = 1, levels = 1;
        if (!strncmp(name, "2darray:", 8)) { type = MTLTextureType2DArray; arrayLength = (NSUInteger)atol(name + 8); }
        else if (!strncmp(name, "cubearray:", 10)) { type = MTLTextureTypeCubeArray; arrayLength = (NSUInteger)atol(name + 10); }
        else if (!strncmp(name, "cube:", 5)) { type = MTLTextureTypeCube; arrayLength = 1; }
        else if (!strncmp(name, "2dmsarray:", 10)) { type = MTLTextureType2DMultisampleArray; arrayLength = (NSUInteger)atol(name + 10); samples = 4; }
        else if (!strncmp(name, "3d:", 3)) { type = MTLTextureType3D; depth = (NSUInteger)atol(name + 3); }
        else if (!strncmp(name, "1darray:", 8)) { type = MTLTextureType1DArray; arrayLength = (NSUInteger)atol(name + 8); height = 1; }
        else if (!strncmp(name, "cubearray_small:", 16)) { type = MTLTextureTypeCubeArray; arrayLength = (NSUInteger)atol(name + 16); width = height = 8; }
        else return 2;
        MTLTextureDescriptor *d = [MTLTextureDescriptor new];
        d.textureType = type;
        d.pixelFormat = MTLPixelFormatRGBA8Unorm;
        d.width = width; d.height = height; d.depth = depth;
        d.arrayLength = arrayLength;
        d.mipmapLevelCount = levels;
        d.sampleCount = samples;
        d.storageMode = MTLStorageModePrivate;
        d.usage = MTLTextureUsageShaderRead;
        id<MTLTexture> t = [dev newTextureWithDescriptor:d];
        printf("  %-18s %-12s arrayLength=%-5lu slices=%-6lu -> %s\n", name, type_name(type),
               (unsigned long)arrayLength,
               (unsigned long)(arrayLength * ((type == MTLTextureTypeCube || type == MTLTextureTypeCubeArray) ? 6 : 1)),
               t ? "built" : "nil (created nothing)");
        return t ? 0 : 1;
    }
}

static void spawn_case(const char *self, const char *name) {
    pid_t pid;
    char *argv[] = {(char *)self, (char *)name, NULL};
    if (posix_spawn(&pid, self, NULL, NULL, argv, environ) != 0) { printf("  %-18s spawn failed\n", name); return; }
    int st = 0; waitpid(pid, &st, 0);
    if (WIFSIGNALED(st)) printf("  %-18s ABORTED by signal %d (Metal validation)\n", name, WTERMSIG(st));
    else if (WEXITSTATUS(st) == 1) printf("  %-18s (descriptor built no texture)\n", name);
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc > 1) return try_case(argv[1]);
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (!dev) { printf("no Metal device\n"); return 1; }
        printf("device: %s  family apple7=%d apple8=%d apple9=%d\n", dev.name.UTF8String,
               [dev supportsFamily:MTLGPUFamilyApple7], [dev supportsFamily:MTLGPUFamilyApple8],
               [dev supportsFamily:MTLGPUFamilyApple9]);
        printf("  maxBufferLength                %lu\n", (unsigned long)dev.maxBufferLength);   // Metal does not expose texture dimension limits as properties; the cases below find them
        printf("cases (each in a child process, so a validation abort cannot hide the rest):\n");
        const char *cases[] = {"2darray:768", "2darray:2048", "2darray:2049", "2darray:4608",
                               "cubearray:341", "cubearray:342", "cubearray:768", "cubearray_small:768",
                               "cube:1", "3d:2048", "1darray:768", "2dmsarray:768"};
        for (unsigned i = 0; i < sizeof cases / sizeof *cases; i++) spawn_case(argv[0], cases[i]);
        return 0;
    }
}
