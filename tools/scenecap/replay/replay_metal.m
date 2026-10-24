/* Metal side of the offline replayer.
 *
 * Everything below is built from the package alone: textures and buffers from blob payloads, pipeline states from
 * the captured MSL, render passes from the manifest.  Any failure names the manifest object it belongs to.
 */
#import "replay_metal.h"

#include <stdlib.h>
#include <string.h>
#include <strings.h>

#import "replay_pixels.h"

/* ------------------------------------------------------------------ pixel formats */

typedef struct ScFormatInfo {
    const char *name;
    MTLPixelFormat format;
    sc_pixel_kind kind; /* SC_PIXEL_KIND_NONE: Metal format exists but cannot be exported as RGBA8 */
    unsigned bytesPerPixel;
    BOOL isDepth;
    BOOL isStencil;
    BOOL isBGRA;
} ScFormatInfo;

static const ScFormatInfo kScFormats[] = {
    {"rgba8unorm", MTLPixelFormatRGBA8Unorm, SC_PIXEL_KIND_U8_LINEAR, 4, NO, NO, NO},
    {"rgba8unorm_srgb", MTLPixelFormatRGBA8Unorm_sRGB, SC_PIXEL_KIND_U8_SRGB, 4, NO, NO, NO},
    {"bgra8unorm", MTLPixelFormatBGRA8Unorm, SC_PIXEL_KIND_U8_LINEAR, 4, NO, NO, YES},
    {"bgra8unorm_srgb", MTLPixelFormatBGRA8Unorm_sRGB, SC_PIXEL_KIND_U8_SRGB, 4, NO, NO, YES},
    {"rgba8snorm", MTLPixelFormatRGBA8Snorm, SC_PIXEL_KIND_NONE, 4, NO, NO, NO},
    {"r8unorm", MTLPixelFormatR8Unorm, SC_PIXEL_KIND_R8UNORM, 1, NO, NO, NO},
    {"rg8unorm", MTLPixelFormatRG8Unorm, SC_PIXEL_KIND_RG8UNORM, 2, NO, NO, NO},
    {"rgba16unorm", MTLPixelFormatRGBA16Unorm, SC_PIXEL_KIND_UNORM16, 8, NO, NO, NO},
    {"rgba16float", MTLPixelFormatRGBA16Float, SC_PIXEL_KIND_FLOAT16, 8, NO, NO, NO},
    {"rgba32float", MTLPixelFormatRGBA32Float, SC_PIXEL_KIND_FLOAT32, 16, NO, NO, NO},
    {"r32float", MTLPixelFormatR32Float, SC_PIXEL_KIND_R32FLOAT, 4, NO, NO, NO},
    {"rg32float", MTLPixelFormatRG32Float, SC_PIXEL_KIND_NONE, 8, NO, NO, NO},
    {"rgb10a2unorm", MTLPixelFormatRGB10A2Unorm, SC_PIXEL_KIND_RGB10A2, 4, NO, NO, NO},
    {"bgr10a2unorm", MTLPixelFormatBGR10A2Unorm, SC_PIXEL_KIND_NONE, 4, NO, NO, NO},
    {"bc1_rgba", MTLPixelFormatBC1_RGBA, SC_PIXEL_KIND_NONE, 1, NO, NO, NO},
    /* The capture layer writes "unknown" when it meets a MTLPixelFormat it has no name for. Replaying it as
     * rgba8unorm keeps the pipeline alive; the note printed on stderr tells the caller the contents are a guess. */
    {"unknown", MTLPixelFormatRGBA8Unorm, SC_PIXEL_KIND_NONE, 4, NO, NO, NO},
    {"rg11b10float", MTLPixelFormatRG11B10Float, SC_PIXEL_KIND_RG11B10, 4, NO, NO, NO},
    {"r16float", MTLPixelFormatR16Float, SC_PIXEL_KIND_R16FLOAT, 2, NO, NO, NO},
    {"depth16unorm", MTLPixelFormatDepth16Unorm, SC_PIXEL_KIND_UNORM16, 2, YES, NO, NO},
    {"depth32float", MTLPixelFormatDepth32Float, SC_PIXEL_KIND_R32FLOAT, 4, YES, NO, NO},
    {"depth24_stencil8", MTLPixelFormatDepth24Unorm_Stencil8, SC_PIXEL_KIND_NONE, 4, YES, YES, NO},
    {"depth32float_stencil8", MTLPixelFormatDepth32Float_Stencil8, SC_PIXEL_KIND_FLOAT32, 8, YES, YES, NO},
    {"stencil8", MTLPixelFormatStencil8, SC_PIXEL_KIND_NONE, 1, NO, YES, NO},
};

static const ScFormatInfo *ScFormatNamed(NSString *name) {
    if (name.length == 0) return NULL;
    const char *utf8 = name.UTF8String;
    if (utf8 == NULL) return NULL;
    for (size_t i = 0; i < sizeof(kScFormats) / sizeof(kScFormats[0]); i++) {
        if (strcasecmp(kScFormats[i].name, utf8) == 0) return &kScFormats[i];
    }
    return NULL;
}

/* Human readable list for diagnostics; keeps the failure actionable when a capture uses an unlisted format. */
static NSString *ScKnownFormatList(void) {
    NSMutableArray<NSString *> *names = [NSMutableArray array];
    for (size_t i = 0; i < sizeof(kScFormats) / sizeof(kScFormats[0]); i++) {
        [names addObject:[NSString stringWithUTF8String:kScFormats[i].name]];
    }
    return [names componentsJoinedByString:@", "];
}

/* ------------------------------------------------------------------ small types */

@implementation ScPassImage
@end

@implementation ScRendererOptions
- (instancetype)init {
    self = [super init];
    if (self) {
        _cacheEnabled = YES;
    }
    return self;
}
@end

static MTLTextureType ScMetalTextureType(ScTextureType type) {
    switch (type) {
        case ScTexture1D: return MTLTextureType1D;
        case ScTexture3D: return MTLTextureType3D;
        case ScTextureCube: return MTLTextureTypeCube;
        case ScTexture2DArray: return MTLTextureType2DArray;
        case ScTextureCubeArray: return MTLTextureTypeCubeArray;
        case ScTexture2DMultisample: return MTLTextureType2DMultisample;
        case ScTexture2D:
        default: return MTLTextureType2D;
    }
}

static MTLPrimitiveType ScMetalPrimitive(ScPrimitiveType type) {
    switch (type) {
        case ScPrimitivePoint: return MTLPrimitiveTypePoint;
        case ScPrimitiveLine: return MTLPrimitiveTypeLine;
        case ScPrimitiveLineStrip: return MTLPrimitiveTypeLineStrip;
        case ScPrimitiveTriangleStrip: return MTLPrimitiveTypeTriangleStrip;
        case ScPrimitiveTriangle:
        default: return MTLPrimitiveTypeTriangle;
    }
}


/* SCENECAP_MSL_PATCH=<file>: literal "old<TAB>new" replacements before MSL compilation.
   Diagnostic only; the package is never modified. */
static NSString *ScPatchSource(NSString *src) {
    const char *path = getenv("SCENECAP_MSL_PATCH");
    if (path == NULL || *path == 0) return src;
    static NSArray<NSArray<NSString *> *> *rules = nil;
    if (rules == nil) {
        NSString *text = [NSString stringWithContentsOfFile:[NSString stringWithUTF8String:path]
                                                   encoding:NSUTF8StringEncoding
                                                      error:NULL];
        NSMutableArray *rs = [NSMutableArray array];
        for (NSString *line in [text componentsSeparatedByString:@"\n"]) {
            NSRange tab = [line rangeOfString:@"\t"];
            if (tab.location == NSNotFound || tab.location == 0) continue;
            [rs addObject:@[[line substringToIndex:tab.location], [line substringFromIndex:tab.location + 1]]];
        }
        rules = rs;
        fprintf(stderr, "[scenecap] msl patch: %lu rule(s) from %s\n", (unsigned long)rules.count, path);
    }
    NSMutableString *out = [src mutableCopy];
    for (NSArray<NSString *> *r in rules) {
        NSUInteger hits = [out replaceOccurrencesOfString:r[0] withString:r[1] options:0 range:NSMakeRange(0, out.length)];
        if (hits) fprintf(stderr, "[scenecap] msl patch: %lu replacement(s) of \"%s\"\n", (unsigned long)hits, r[0].UTF8String);
    }
    return out;
}

static MTLCompareFunction ScMetalCompare(ScCompareFunction fn) {
    /* SCENECAP_DEPTH_ALWAYS=1: diagnostic override, force every depth compare to Always */
    if (getenv("SCENECAP_DEPTH_ALWAYS")) return MTLCompareFunctionAlways;
    switch (fn) {
        case ScCompareNever: return MTLCompareFunctionNever;
        case ScCompareLess: return MTLCompareFunctionLess;
        case ScCompareEqual: return MTLCompareFunctionEqual;
        case ScCompareLessEqual: return MTLCompareFunctionLessEqual;
        case ScCompareGreater: return MTLCompareFunctionGreater;
        case ScCompareNotEqual: return MTLCompareFunctionNotEqual;
        case ScCompareGreaterEqual: return MTLCompareFunctionGreaterEqual;
        case ScCompareAlways:
        default: return MTLCompareFunctionAlways;
    }
}

static MTLCullMode ScMetalCull(ScCullMode mode) {
    switch (mode) {
        case ScCullFront: return MTLCullModeFront;
        case ScCullBack: return MTLCullModeBack;
        case ScCullNone:
        default: return MTLCullModeNone;
    }
}

static MTLWinding ScMetalWinding(ScWinding winding) {
    return winding == ScWindingCW ? MTLWindingClockwise : MTLWindingCounterClockwise;
}

static MTLTriangleFillMode ScMetalFill(ScFillMode mode) {
    switch (mode) {
        case ScFillLines: return MTLTriangleFillModeLines;
        case ScFillPoints:
        case ScFillSolid:
        default: return MTLTriangleFillModeFill;
    }
}

static MTLBlendOperation ScMetalBlendOp(ScBlendOperation op) {
    switch (op) {
        case ScBlendSubtract: return MTLBlendOperationSubtract;
        case ScBlendReverseSubtract: return MTLBlendOperationReverseSubtract;
        case ScBlendMin: return MTLBlendOperationMin;
        case ScBlendMax: return MTLBlendOperationMax;
        case ScBlendAdd:
        default: return MTLBlendOperationAdd;
    }
}

static BOOL ScMetalBlendFactor(ScBlendFactor factor, MTLBlendFactor *out) {
    switch (factor) {
        case ScBlendZero: *out = MTLBlendFactorZero; return YES;
        case ScBlendOne: *out = MTLBlendFactorOne; return YES;
        case ScBlendSourceColor: *out = MTLBlendFactorSourceColor; return YES;
        case ScBlendOneMinusSourceColor: *out = MTLBlendFactorOneMinusSourceColor; return YES;
        case ScBlendDestinationColor: *out = MTLBlendFactorDestinationColor; return YES;
        case ScBlendOneMinusDestinationColor: *out = MTLBlendFactorOneMinusDestinationColor; return YES;
        case ScBlendSourceAlpha: *out = MTLBlendFactorSourceAlpha; return YES;
        case ScBlendOneMinusSourceAlpha: *out = MTLBlendFactorOneMinusSourceAlpha; return YES;
        case ScBlendDestinationAlpha: *out = MTLBlendFactorDestinationAlpha; return YES;
        case ScBlendOneMinusDestinationAlpha: *out = MTLBlendFactorOneMinusDestinationAlpha; return YES;
        case ScBlendSourceAlphaSaturated: *out = MTLBlendFactorSourceAlphaSaturated; return YES;
        case ScBlendBlendColor: *out = MTLBlendFactorBlendColor; return YES;
        case ScBlendOneMinusBlendColor: *out = MTLBlendFactorOneMinusBlendColor; return YES;
        case ScBlendBlendAlpha: *out = MTLBlendFactorBlendAlpha; return YES;
        case ScBlendOneMinusBlendAlpha: *out = MTLBlendFactorOneMinusBlendAlpha; return YES;
        default: return NO;
    }
}

static NSString *ScMetalVersionString(id<MTLDevice> device) {
    /* The newest supported family is the closest thing Metal exposes to a version number. */
    if (@available(macOS 26.0, *)) {
        if ([device supportsFamily:MTLGPUFamilyMetal4]) return @"Metal4";
    }
    struct {
        MTLGPUFamily family;
        const char *label;
    } families[] = {
        {MTLGPUFamilyMetal3, "Metal3"},
        {MTLGPUFamilyApple11, "Apple11"},
        {MTLGPUFamilyApple10, "Apple10"},
        {MTLGPUFamilyApple9, "Apple9"},
        {MTLGPUFamilyApple8, "Apple8"},
        {MTLGPUFamilyApple7, "Apple7"},
    };
    for (size_t i = 0; i < sizeof(families) / sizeof(families[0]); i++) {
        if ([device supportsFamily:families[i].family]) {
            return [NSString stringWithFormat:@"%s", families[i].label];
        }
    }
    return @"unknown";
}

/* Byte size of the declared snapshot window of a texture: slices outermost, mip levels innermost, tightly packed.
 * Shared by the uploader and by --validate-only so both agree on what a complete snapshot looks like. */
static BOOL ScTextureLayoutBytes(ScResource *resource, const ScFormatInfo *format, uint64_t *outNeeded,
                                 NSString **outWhy) {
    unsigned mipCount = resource.mipCount > 0 ? resource.mipCount : 1;
    unsigned layerCount = resource.layerCount > 0 ? resource.layerCount : 1;
    BOOL is3D = resource.textureType == ScTexture3D;
    BOOL isCube = resource.textureType == ScTextureCube || resource.textureType == ScTextureCubeArray;
    unsigned sliceCount = is3D ? 1 : (isCube ? 6u * layerCount : layerCount);
    uint64_t needed = 0;
    for (unsigned slice = 0; slice < sliceCount; slice++) {
        for (unsigned level = 0; level < mipCount; level++) {
            unsigned levelIndex = resource.mipFirst + level;
            uint64_t w = levelIndex >= 32 ? 0 : (uint64_t)(resource.width >> levelIndex);
            uint64_t h = levelIndex >= 32 ? 0 : (uint64_t)(resource.height >> levelIndex);
            uint64_t d = is3D ? (uint64_t)((resource.depth > 1 ? resource.depth : 1) >> levelIndex) : 1;
            if (w == 0) w = 1;
            if (h == 0) h = 1;
            if (d == 0) d = 1;
            BOOL isBlock = format->name != NULL && strncmp(format->name, "bc1", 3) == 0;
            uint64_t extent = w * h;
            uint64_t image;
            if (isBlock) {
                /* BC1: one 8-byte block per 4x4 texels, the last block padded even when the mip is smaller. */
                image = ((w + 3) / 4) * ((h + 3) / 4) * 8;
            } else {
                image = extent * format->bytesPerPixel;
            }
            if (!isBlock && (format->bytesPerPixel == 0 || image / format->bytesPerPixel != extent)) {
                if (outWhy) *outWhy = @"extent arithmetic overflowed";
                return NO;
            }
            uint64_t sliceBytes = image * d;
            if (sliceBytes / image != d) {
                if (outWhy) *outWhy = @"extent arithmetic overflowed";
                return NO;
            }
            if (needed + sliceBytes < needed) {
                if (outWhy) *outWhy = @"extent arithmetic overflowed";
                return NO;
            }
            needed += sliceBytes;
        }
    }
    *outNeeded = needed;
    return YES;
}

/* Function-name convention: an explicit stage.entry wins, then a single-function library, then the documented
 * candidate names of tools/scenecap/replay/README.md. */
static NSArray<NSString *> *ScCandidateFunctionNames(ScStageKind kind) {
    switch (kind) {
        case ScStageVertex: return @[@"vs_main", @"vertex_main", @"main_vs", @"main0", @"main"];
        case ScStageFragment: return @[@"fs_main", @"fragment_main", @"main_fs", @"main0", @"main"];
        case ScStageKernel: return @[@"kernel_main", @"main0", @"main"];
        case ScStageObject: return @[@"object_main", @"main0", @"main"];
        case ScStageMesh: return @[@"mesh_main", @"main0", @"main"];
        case ScStageTile: return @[@"tile_main", @"main0", @"main"];
    }
    return @[@"main"];
}

/* ------------------------------------------------------------------ renderer */

@interface ScRenderer () {
    ScScene *_scene;
    ScPackage *_package;
    ScRendererOptions *_options;
    id<MTLDevice> _device;
    id<MTLCommandQueue> _queue;
    NSString *_deviceName;
    NSString *_metalVersion;

    id<MTLBinaryArchive> _archive;
    NSURL *_archiveURL;
    NSString *_archiveMetaPath;
    NSUInteger _archiveHits;
    NSUInteger _archiveRejects;
    BOOL _archiveDirty;

    NSMutableDictionary<NSString *, id<MTLTexture>> *_textures;
    NSMutableDictionary<NSString *, id<MTLBuffer>> *_buffers;
    NSMutableDictionary<NSString *, id<MTLLibrary>> *_libraries;
    NSMutableDictionary<NSString *, id<MTLRenderPipelineState>> *_pipelines;
    NSMutableDictionary<NSString *, id<MTLDepthStencilState>> *_depthStates;
    NSMutableDictionary<NSString *, id<MTLSamplerState>> *_samplers;
    NSMutableArray<NSString *> *_warnings;
}

- (BOOL)encodeDrawsOfPass:(ScPass *)pass
              intoEncoder:(id<MTLRenderCommandEncoder>)encoder
                 hasDepth:(BOOL)hasDepth
                drawCount:(NSUInteger *)drawCount
                    error:(NSError **)error;
@end

@implementation ScRenderer

+ (nullable instancetype)rendererWithScene:(ScScene *)scene
                                   package:(ScPackage *)package
                                   options:(ScRendererOptions *)options
                                     error:(NSError **)error {
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (device == nil) {
        if (error) *error = ScReplayError(@"no Metal device is available on this host");
        return nil;
    }
    ScRenderer *renderer = [[ScRenderer alloc] init];
    renderer->_scene = scene;
    renderer->_package = package;
    renderer->_options = options;
    renderer->_device = device;
    renderer->_queue = [device newCommandQueue];
    if (renderer->_queue == nil) {
        if (error) *error = ScReplayError(@"device \"%@\": cannot create a Metal command queue", device.name);
        return nil;
    }
    renderer->_deviceName = [device.name copy] ?: @"unknown";
    renderer->_metalVersion = ScMetalVersionString(device);
    renderer->_textures = [NSMutableDictionary dictionary];
    renderer->_buffers = [NSMutableDictionary dictionary];
    renderer->_libraries = [NSMutableDictionary dictionary];
    renderer->_pipelines = [NSMutableDictionary dictionary];
    renderer->_depthStates = [NSMutableDictionary dictionary];
    renderer->_samplers = [NSMutableDictionary dictionary];
    renderer->_warnings = [NSMutableArray array];
    renderer->_archiveHits = 0;
    renderer->_archiveRejects = 0;
    if (options.cacheEnabled && options.cacheDirectory.length > 0) {
        [renderer openArchive];
    }
    return renderer;
}

/* The header exposes these as readonly properties; the getters below are hand-written because the warnings
 * array and the counters are copied/unwrapped rather than stored as properties. */
- (NSArray<NSString *> *)warnings {
    return [_warnings copy];
}

- (NSUInteger)pipelineCount {
    return _pipelines.count;
}

- (NSUInteger)archiveHits {
    return _archiveHits;
}

- (NSUInteger)archiveRejects {
    return _archiveRejects;
}

#pragma mark - pipeline cache

/* The archive lives next to the package so a second replay of the same capture can skip most pipeline compilation.
 * It is keyed on device name plus the package digest: anything else is discarded instead of risking stale code. */
- (void)openArchive {
    NSFileManager *fm = [NSFileManager defaultManager];
    NSString *directory = _options.cacheDirectory;
    NSError *dirError = nil;
    if (![fm createDirectoryAtPath:directory withIntermediateDirectories:YES attributes:nil error:&dirError]) {
        [_warnings addObject:[NSString stringWithFormat:@"pipeline cache disabled: cannot create \"%@\": %@",
                                                        directory, dirError.localizedDescription]];
        return;
    }
    _archiveURL = [NSURL fileURLWithPath:[directory stringByAppendingPathComponent:@"pipelines.metallib"]];
    NSString *metaPath = [directory stringByAppendingPathComponent:@"cache.json"];
    _archiveMetaPath = metaPath;

    NSDictionary *meta = nil;
    NSData *metaData = [NSData dataWithContentsOfFile:metaPath];
    if (metaData.length > 0) {
        id parsed = [NSJSONSerialization JSONObjectWithData:metaData options:0 error:NULL];
        if ([parsed isKindOfClass:[NSDictionary class]]) meta = parsed;
    }
    BOOL reusable = [meta[@"schema"] isEqual:@"scenecap.replay.cache/1"]
                    && [meta[@"package_sha256"] isEqual:_package.sha256Hex]
                    && [meta[@"device"] isEqual:_deviceName];
    NSError *archiveError = nil;
    if (reusable && [fm fileExistsAtPath:_archiveURL.path]) {
        MTLBinaryArchiveDescriptor *descriptor = [MTLBinaryArchiveDescriptor new];
        descriptor.url = _archiveURL;
        _archive = [_device newBinaryArchiveWithDescriptor:descriptor error:&archiveError];
        if (_archive == nil) {
            [_warnings addObject:[NSString stringWithFormat:@"pipeline cache ignored: %@",
                                                            archiveError.localizedDescription ?: @"cannot open archive"]];
        }
    } else {
        if (metaData.length > 0 || [fm fileExistsAtPath:_archiveURL.path]) {
            [_warnings addObject:@"pipeline cache ignored: it was written for a different package or device"];
        }
        [fm removeItemAtURL:_archiveURL error:NULL];
    }
    if (_archive == nil) {
        /* No usable archive on disk: start an empty one so this run can populate it for the next replay. */
        MTLBinaryArchiveDescriptor *descriptor = [MTLBinaryArchiveDescriptor new];
        archiveError = nil;
        _archive = [_device newBinaryArchiveWithDescriptor:descriptor error:&archiveError];
        if (_archive == nil) {
            [_warnings addObject:[NSString stringWithFormat:@"pipeline cache disabled: %@",
                                                            archiveError.localizedDescription ?: @"cannot create archive"]];
        }
    }
}

- (BOOL)flushCacheWithError:(NSError **)error {
    if (_archive == nil || _archiveURL == nil) return YES;
    if (!_archiveDirty) return YES;
    NSError *serializeError = nil;
    if (![_archive serializeToURL:_archiveURL error:&serializeError]) {
        if (error) {
            *error = ScReplayError(@"cannot write pipeline cache \"%@\": %@", _archiveURL.path,
                                   serializeError.localizedDescription ?: @"unknown error");
        }
        return NO;
    }
    NSDictionary *meta = @{
        @"schema" : @"scenecap.replay.cache/1",
        @"device" : _deviceName,
        @"package_sha256" : _package.sha256Hex,
        @"pipelines" : @(_pipelines.count),
        @"archive_hits" : @(_archiveHits),
        @"archive_rejects" : @(_archiveRejects),
    };
    NSData *metaData = [NSJSONSerialization dataWithJSONObject:meta options:NSJSONWritingPrettyPrinted error:NULL];
    [metaData writeToFile:_archiveMetaPath atomically:YES];
    return YES;
}

#pragma mark - resources

- (nullable id<MTLTexture>)textureForResource:(ScResource *)resource error:(NSError **)error {
    id<MTLTexture> cached = _textures[resource.ident];
    if (cached != nil) return cached;

    const ScFormatInfo *format = ScFormatNamed(resource.pixelFormat);
    if (format == NULL) {
        if (error) {
            *error = ScReplayError(@"texture \"%@\": unknown pixel format \"%@\" (known formats: %@)", resource.ident,
                                   resource.pixelFormat, ScKnownFormatList());
        }
        return nil;
    }
    if (resource.sampleCount > 1) {
        if (error) {
            *error = ScReplayError(@"texture \"%@\": multisample textures (%u samples) are not replayed yet",
                                   resource.ident, resource.sampleCount);
        }
        return nil;
    }
    if (resource.width == 0 || resource.height == 0) {
        if (error) {
            *error = ScReplayError(@"texture \"%@\": %ux%u has an empty extent", resource.ident, resource.width,
                                   resource.height);
        }
        return nil;
    }

    NSError *chunkError = nil;
    NSData *blob = [_package payloadOfChunk:resource.blobId error:&chunkError];
    if (blob == nil) {
        if (error) {
            *error = ScReplayError(@"texture \"%@\": %@", resource.ident, chunkError.localizedDescription);
        }
        return nil;
    }
    if (resource.blobOffset > blob.length || resource.blobSize > blob.length - resource.blobOffset) {
        if (error) {
            *error = ScReplayError(@"texture \"%@\": bytes {offset %llu, size %llu} lie outside blob \"%@\" (%llu bytes)",
                                   resource.ident, (unsigned long long)resource.blobOffset,
                                   (unsigned long long)resource.blobSize, resource.blobId,
                                   (unsigned long long)blob.length);
        }
        return nil;
    }

    /* Contents are stored per slice, then per mip level inside the declared mip range. */
    unsigned mipCount = resource.mipCount > 0 ? resource.mipCount : 1;
    unsigned layerCount = resource.layerCount > 0 ? resource.layerCount : 1;
    BOOL is3D = resource.textureType == ScTexture3D;
    BOOL isCube = resource.textureType == ScTextureCube || resource.textureType == ScTextureCubeArray;
    unsigned sliceCount = is3D ? 1 : (isCube ? 6u * layerCount : layerCount);

    uint64_t needed = 0;
    NSString *layoutWhy = nil;
    if (!ScTextureLayoutBytes(resource, format, &needed, &layoutWhy)) {
        if (error) *error = ScReplayError(@"texture \"%@\": %@", resource.ident, layoutWhy ?: @"invalid layout");
        return nil;
    }
    if (resource.blobSize < needed) {
        fprintf(stderr, "[scenecap] note: texture \"%s\" blob holds %llu of %llu bytes (%s); zero-filling\n",
                resource.ident.UTF8String, (unsigned long long)resource.blobSize, (unsigned long long)needed,
                resource.pixelFormat.UTF8String);
    } else if (resource.blobSize < needed) {
        if (error) {
            *error = ScReplayError(
                @"texture \"%@\": blob \"%@\" holds %llu bytes but %ux%u %@ with %u mip level(s) from %u and %u "
                @"layer(s) from %u needs %llu",
                resource.ident, resource.blobId, (unsigned long long)resource.blobSize, resource.width,
                resource.height, resource.pixelFormat ?: @"?", mipCount, resource.mipFirst, layerCount,
                resource.layerFirst, (unsigned long long)needed);
        }
        return nil;
    }

    MTLTextureDescriptor *descriptor = [MTLTextureDescriptor new];
    descriptor.textureType = ScMetalTextureType(resource.textureType);
    descriptor.pixelFormat = format->format;
    descriptor.width = resource.width;
    descriptor.height = is3D || resource.textureType == ScTexture1D ? resource.height : resource.height;
    descriptor.depth = is3D ? (resource.depth > 0 ? resource.depth : 1) : 1;
    descriptor.mipmapLevelCount = resource.mipLevels > 0 ? resource.mipLevels : 1;
    if (resource.textureType == ScTextureCubeArray) {
        descriptor.arrayLength = 6u * layerCount;   /* Metal expresses a cube array as 6 * layers faces */
    } else if (resource.textureType == ScTexture2DArray) {
        descriptor.arrayLength = layerCount;
    } else {
        descriptor.arrayLength = 1;                  /* cube (and every non-array type) rejects more than 1 */
    }
    descriptor.sampleCount = 1;
    descriptor.storageMode = MTLStorageModeShared;
    MTLTextureUsage usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget;
    if (resource.usage & ScResourceUsageShaderWrite) usage |= MTLTextureUsageShaderWrite;
    descriptor.usage = usage;

    id<MTLTexture> texture = [_device newTextureWithDescriptor:descriptor];
    if (texture == nil) {
        if (error) {
            *error = ScReplayError(@"texture \"%@\": Metal refused a %ux%u %@ (%u mip level(s), %u layer(s))",
                                   resource.ident, resource.width, resource.height, resource.pixelFormat,
                                   descriptor.mipmapLevelCount, layerCount);
        }
        return nil;
    }
    texture.label = resource.ident;

    const unsigned char *base = (const unsigned char *)blob.bytes + (size_t)resource.blobOffset;
    size_t cursor = 0;
    for (unsigned slice = 0; slice < sliceCount; slice++) {
        for (unsigned level = 0; level < mipCount; level++) {
            unsigned levelIndex = resource.mipFirst + level;
            unsigned w = (unsigned)(resource.width >> levelIndex);
            unsigned h = (unsigned)(resource.height >> levelIndex);
            unsigned d = is3D ? (unsigned)((resource.depth > 1 ? resource.depth : 1) >> levelIndex) : 1;
            if (w == 0) w = 1;
            if (h == 0) h = 1;
            if (d == 0) d = 1;
            BOOL isBlock = format->name != NULL && strncmp(format->name, "bc1", 3) == 0;
            NSUInteger rowBytes;
            NSUInteger imageBytes;
            if (isBlock) {
                rowBytes = (NSUInteger)(((w + 3) / 4) * 8);
                imageBytes = rowBytes * (NSUInteger)((h + 3) / 4);
            } else {
                rowBytes = (NSUInteger)w * format->bytesPerPixel;
                imageBytes = rowBytes * h;
            }
            MTLRegion region = MTLRegionMake3D(0, 0, 0, w, MAX(h, 1u), MAX(d, 1u));
            /* replaceRegion takes tightly packed rows; the 256-byte alignment requirement only applies to
             * MTLBuffer-backed copies, which this replayer never uses for texture upload. */
            size_t imageTotal = (size_t)imageBytes * d;
            if (cursor + imageTotal > (size_t)resource.blobSize) {
                /* Short snapshot (see the note in the validation pass): keep what exists, leave the rest zero. */
                unsigned char *padded = (unsigned char *)calloc(imageTotal ? imageTotal : 1, 1);
                size_t copy = (size_t)resource.blobSize > cursor ? (size_t)resource.blobSize - cursor : 0;
                if (copy > imageTotal) copy = imageTotal;
                if (padded == NULL) {
                    if (error) *error = ScReplayError(@"texture \"%@\": out of memory padding a short snapshot", resource.ident);
                    return nil;
                }
                if (copy > 0) memcpy(padded, base + cursor, copy);
                [texture replaceRegion:region
                           mipmapLevel:levelIndex
                                 slice:slice
                             withBytes:padded
                           bytesPerRow:rowBytes
                         bytesPerImage:imageBytes];
                free(padded);
            } else {
                [texture replaceRegion:region
                           mipmapLevel:levelIndex
                                 slice:slice
                             withBytes:base + cursor
                           bytesPerRow:rowBytes
                         bytesPerImage:imageBytes];
            }
            cursor += imageTotal;
        }
    }
    _textures[resource.ident] = texture;
    return texture;
}

- (nullable id<MTLBuffer>)bufferForResource:(ScResource *)resource error:(NSError **)error {
    id<MTLBuffer> cached = _buffers[resource.ident];
    if (cached != nil) return cached;

    NSError *chunkError = nil;
    NSData *blob = [_package payloadOfChunk:resource.blobId error:&chunkError];
    if (blob == nil) {
        if (error) *error = ScReplayError(@"buffer \"%@\": %@", resource.ident, chunkError.localizedDescription);
        return nil;
    }
    if (resource.blobOffset > blob.length || resource.blobSize > blob.length - resource.blobOffset) {
        if (error) {
            *error = ScReplayError(@"buffer \"%@\": bytes {offset %llu, size %llu} lie outside blob \"%@\" (%llu bytes)",
                                   resource.ident, (unsigned long long)resource.blobOffset,
                                   (unsigned long long)resource.blobSize, resource.blobId,
                                   (unsigned long long)blob.length);
        }
        return nil;
    }
    if (resource.blobSize == 0) {
        if (error) {
            *error = ScReplayError(@"buffer \"%@\": the snapshot is empty", resource.ident);
        }
        return nil;
    }
    id<MTLBuffer> buffer = [_device newBufferWithLength:(NSUInteger)resource.blobSize
                                                options:MTLResourceStorageModeShared];
    if (buffer == nil) {
        if (error) {
            *error = ScReplayError(@"buffer \"%@\": Metal refused a %llu byte allocation", resource.ident,
                                   (unsigned long long)resource.blobSize);
        }
        return nil;
    }
    buffer.label = resource.ident;
    memcpy(buffer.contents, (const unsigned char *)blob.bytes + (size_t)resource.blobOffset,
           (size_t)resource.blobSize);
    _buffers[resource.ident] = buffer;
    return buffer;
}

#pragma mark - libraries and pipelines

- (nullable id<MTLLibrary>)libraryForStage:(ScStage *)stage ofPipeline:(ScPipeline *)pipeline error:(NSError **)error {
    id<MTLLibrary> cached = _libraries[stage.mslBlobId];
    if (cached != nil) return cached;

    NSError *chunkError = nil;
    NSData *sourceData = [_package payloadOfChunk:stage.mslBlobId error:&chunkError];
    if (sourceData == nil) {
        if (error) {
            *error = ScReplayError(@"pipeline \"%@\" %@ stage: %@", pipeline.ident, stage.kindName,
                                   chunkError.localizedDescription);
        }
        return nil;
    }
    NSString *source = [[NSString alloc] initWithData:sourceData encoding:NSUTF8StringEncoding];
    if (source.length == 0) {
        if (error) {
            *error = ScReplayError(@"pipeline \"%@\" %@ stage: blob \"%@\" is not UTF-8 Metal source", pipeline.ident,
                                   stage.kindName, stage.mslBlobId);
        }
        return nil;
    }

    source = ScPatchSource(source);
    MTLCompileOptions *options = [MTLCompileOptions new];
    NSError *compileError = nil;
    id<MTLLibrary> library = [_device newLibraryWithSource:source options:options error:&compileError];
    if (library == nil) {
        if (error) {
            NSString *diagnostics = compileError.localizedDescription ?: @"no diagnostics";
            NSString *reason = compileError.localizedFailureReason;
            if (reason.length > 0) diagnostics = [diagnostics stringByAppendingFormat:@" (%@)", reason];
            *error = ScReplayError(@"pipeline \"%@\" %@ stage: MSL blob \"%@\" failed to compile:\n%@", pipeline.ident,
                                   stage.kindName, stage.mslBlobId, diagnostics);
        }
        return nil;
    }
    _libraries[stage.mslBlobId] = library;
    return library;
}

- (nullable id<MTLFunction>)functionForStage:(ScStage *)stage
                                  ofPipeline:(ScPipeline *)pipeline
                                       error:(NSError **)error {
    id<MTLLibrary> library = [self libraryForStage:stage ofPipeline:pipeline error:error];
    if (library == nil) return nil;

    NSArray<NSString *> *available = library.functionNames;
    id<MTLFunction> function = nil;
    if (stage.entry.length > 0) {
        function = [library newFunctionWithName:stage.entry];
        if (function == nil) {
            if (error) {
                *error = ScReplayError(@"pipeline \"%@\" %@ stage: entry \"%@\" is not in blob \"%@\" (available: %@)",
                                       pipeline.ident, stage.kindName, stage.entry, stage.mslBlobId,
                                       [available componentsJoinedByString:@", "] ?: @"none");
            }
            return nil;
        }
        return function;
    }
    if (available.count == 1) {
        return [library newFunctionWithName:available.firstObject];
    }
    for (NSString *candidate in ScCandidateFunctionNames(stage.kind)) {
        function = [library newFunctionWithName:candidate];
        if (function != nil) return function;
    }
    if (error) {
        *error = ScReplayError(
            @"pipeline \"%@\" %@ stage: blob \"%@\" has no documented entry point (looked for %@; available: %@)",
            pipeline.ident, stage.kindName, stage.mslBlobId,
            [ScCandidateFunctionNames(stage.kind) componentsJoinedByString:@", "],
            [available componentsJoinedByString:@", "] ?: @"none");
    }
    return nil;
}

/* Collects the pixel formats a pass renders into; used as part of the pipeline cache key. */
- (BOOL)formatsForPass:(ScPass *)pass
           colorSlots:(MTLPixelFormat *)colorSlots
           colorCount:(NSUInteger *)colorCount
           depthFormat:(MTLPixelFormat *)depthFormat
           stencilFormat:(MTLPixelFormat *)stencilFormat
                 error:(NSError **)error {
    MTLPixelFormat slots[8];
    for (size_t i = 0; i < 8; i++) slots[i] = MTLPixelFormatInvalid;
    NSUInteger count = 0;
    MTLPixelFormat depth = MTLPixelFormatInvalid;
    MTLPixelFormat stencil = MTLPixelFormatInvalid;
    for (ScAttachment *attachment in pass.attachments) {
        ScResource *resource = [_scene resourceNamed:attachment.textureId];
        const ScFormatInfo *format = ScFormatNamed(resource.pixelFormat);
        if (format == NULL) {
            if (error) {
                *error = ScReplayError(@"pass \"%@\" slot %u: texture \"%@\" has unknown pixel format \"%@\"",
                                       pass.ident, attachment.slot, resource.ident, resource.pixelFormat);
            }
            return NO;
        }
        if (format->isDepth) {
            if (depth != MTLPixelFormatInvalid && depth != format->format) {
                if (error) {
                    *error = ScReplayError(@"pass \"%@\": two depth attachments with different formats", pass.ident);
                }
                return NO;
            }
            depth = format->format;
            if (format->isStencil) stencil = format->format;
            continue;
        }
        if (format->isStencil) {
            stencil = format->format;
            continue;
        }
        if (attachment.slot >= 8) {
            if (error) {
                *error = ScReplayError(@"pass \"%@\": colour slot %u is out of range (max 7)", pass.ident,
                                       attachment.slot);
            }
            return NO;
        }
        slots[attachment.slot] = format->format;
        if (attachment.slot + 1 > count) count = attachment.slot + 1;
    }
    memcpy(colorSlots, slots, sizeof(slots));
    *colorCount = count;
    *depthFormat = depth;
    *stencilFormat = stencil;
    return YES;
}

- (nullable id<MTLRenderPipelineState>)pipelineStateForPipeline:(ScPipeline *)pipeline
                                                            pass:(ScPass *)pass
                                                           error:(NSError **)error {
    MTLPixelFormat colorSlots[8];
    NSUInteger colorCount = 0;
    MTLPixelFormat depthFormat = MTLPixelFormatInvalid;
    MTLPixelFormat stencilFormat = MTLPixelFormatInvalid;
    if (![self formatsForPass:pass
                  colorSlots:colorSlots
                  colorCount:&colorCount
                  depthFormat:&depthFormat
                  stencilFormat:&stencilFormat
                        error:error]) {
        return nil;
    }

    NSMutableString *key = [pipeline.ident mutableCopy];
    for (NSUInteger i = 0; i < colorCount; i++) [key appendFormat:@"|c%lu=%d", (unsigned long)i, (int)colorSlots[i]];
    [key appendFormat:@"|d=%d|s=%d", (int)depthFormat, (int)stencilFormat];
    id<MTLRenderPipelineState> cached = _pipelines[key];
    if (cached != nil) return cached;

    id<MTLFunction> vertexFunction = nil;
    id<MTLFunction> fragmentFunction = nil;
    for (ScStage *stage in pipeline.stages) {
        if (stage.kind == ScStageVertex && stage.mslBlobId.length > 0) {
            vertexFunction = [self functionForStage:stage ofPipeline:pipeline error:error];
            if (vertexFunction == nil) return nil;
        } else if (stage.kind == ScStageFragment && stage.mslBlobId.length > 0) {
            fragmentFunction = [self functionForStage:stage ofPipeline:pipeline error:error];
            if (fragmentFunction == nil) return nil;
        }
    }
    if (vertexFunction == nil) {
        if (error) *error = ScReplayError(@"pipeline \"%@\": no vertex stage with Metal source", pipeline.ident);
        return nil;
    }

    MTLRenderPipelineDescriptor *descriptor = [MTLRenderPipelineDescriptor new];
    descriptor.label = pipeline.ident;
    descriptor.vertexFunction = vertexFunction;
    descriptor.fragmentFunction = fragmentFunction;
    descriptor.rasterSampleCount = 1;
    if (pipeline.vertexAttributes.count > 0) {
        /* Metal refuses a pipeline whose vertex function declares attributes but has no descriptor.
           The capture keeps NVN stream indices verbatim (they start at 16 for the real game), while the
           argument table binds vertex buffers at 0..N-1. Normalize the attribute/layout buffer index by
           the minimum declared one so both sides refer to the same slots. */
        NSUInteger attrBase = NSUIntegerMax;
        for (ScVertexAttribute *attribute in pipeline.vertexAttributes) {
            if (attribute.bufferIndex < attrBase) attrBase = attribute.bufferIndex;
        }
        if (attrBase == NSUIntegerMax) attrBase = 0;
        MTLVertexDescriptor *vertexDescriptor = [MTLVertexDescriptor vertexDescriptor];
        for (ScVertexAttribute *attribute in pipeline.vertexAttributes) {
            NSUInteger slot = attribute.bufferIndex - attrBase;
            if (attribute.location >= 31 || slot >= 31) continue;
            vertexDescriptor.attributes[attribute.location].format = (MTLVertexFormat)attribute.format;
            vertexDescriptor.attributes[attribute.location].offset = attribute.offset;
            vertexDescriptor.attributes[attribute.location].bufferIndex = slot;
        }
        NSMutableSet<NSNumber *> *usedBuffers = [NSMutableSet set];
        for (ScVertexAttribute *attribute in pipeline.vertexAttributes) {
            NSUInteger slot = attribute.bufferIndex - attrBase;
            if (attribute.location < 31 && slot < 31) [usedBuffers addObject:@(slot)];
        }
        for (ScVertexLayout *layout in pipeline.vertexLayouts) {
            if (layout.bufferIndex < attrBase) continue;
            NSUInteger slot = layout.bufferIndex - attrBase;
            if (slot >= 31 || ![usedBuffers containsObject:@(slot)]) continue;
            vertexDescriptor.layouts[slot].stride = layout.stride;
            vertexDescriptor.layouts[slot].stepFunction = (MTLVertexStepFunction)layout.stepFunction;
            vertexDescriptor.layouts[slot].stepRate = layout.stepRate;
        }
        descriptor.vertexDescriptor = vertexDescriptor;
    }
    for (NSUInteger i = 0; i < colorCount; i++) {
        descriptor.colorAttachments[i].pixelFormat = colorSlots[i];
    }
    if (depthFormat != MTLPixelFormatInvalid) descriptor.depthAttachmentPixelFormat = depthFormat;
    if (stencilFormat != MTLPixelFormatInvalid) descriptor.stencilAttachmentPixelFormat = stencilFormat;

    if (pipeline.blendEnabled) {
        for (ScBlendAttachment *blend in pipeline.blendAttachments) {
            if (!blend.enabled || blend.index >= colorCount) continue;
            MTLRenderPipelineColorAttachmentDescriptor *target = descriptor.colorAttachments[blend.index];
            MTLBlendFactor rgbSource = MTLBlendFactorOne, rgbDestination = MTLBlendFactorZero;
            MTLBlendFactor alphaSource = MTLBlendFactorOne, alphaDestination = MTLBlendFactorZero;
            if (!ScMetalBlendFactor(blend.rgbSource, &rgbSource) || !ScMetalBlendFactor(blend.rgbDestination, &rgbDestination)
                || !ScMetalBlendFactor(blend.alphaSource, &alphaSource)
                || !ScMetalBlendFactor(blend.alphaDestination, &alphaDestination)) {
                if (error) {
                    *error = ScReplayError(@"pipeline \"%@\": unsupported blend factor on colour slot %u",
                                           pipeline.ident, blend.index);
                }
                return nil;
            }
            target.blendingEnabled = YES;
            target.rgbBlendOperation = ScMetalBlendOp(blend.rgbOperation);
            target.alphaBlendOperation = ScMetalBlendOp(blend.alphaOperation);
            target.sourceRGBBlendFactor = rgbSource;
            target.destinationRGBBlendFactor = rgbDestination;
            target.sourceAlphaBlendFactor = alphaSource;
            target.destinationAlphaBlendFactor = alphaDestination;
        }
    }

    NSError *pipelineError = nil;
    id<MTLRenderPipelineState> state = nil;
    if (_archive != nil) {
        descriptor.binaryArchives = @[ _archive ];
        state = [_device newRenderPipelineStateWithDescriptor:descriptor error:&pipelineError];
        if (state != nil) {
            _archiveHits++;
        } else {
            _archiveRejects++;
            descriptor.binaryArchives = nil;
            pipelineError = nil;
            state = [_device newRenderPipelineStateWithDescriptor:descriptor error:&pipelineError];
        }
    } else {
        state = [_device newRenderPipelineStateWithDescriptor:descriptor error:&pipelineError];
    }
    if (state == nil) {
        if (error) {
            *error = ScReplayError(@"pipeline \"%@\": Metal refused the render pipeline state: %@", pipeline.ident,
                                   pipelineError.localizedDescription ?: @"no diagnostics");
        }
        return nil;
    }
    _pipelines[key] = state;

    if (_archive != nil) {
        NSError *addError = nil;
        if ([_archive addRenderPipelineFunctionsWithDescriptor:descriptor error:&addError]) {
            _archiveDirty = YES;
        } else if (_options.verbose) {
            [_warnings addObject:[NSString stringWithFormat:@"pipeline \"%@\" not added to the archive: %@",
                                                            pipeline.ident,
                                                            addError.localizedDescription ?: @"unknown error"]];
        }
    }
    return state;
}

- (nullable id<MTLDepthStencilState>)depthStateForPipeline:(ScPipeline *)pipeline error:(NSError **)error {
    NSString *key = [NSString stringWithFormat:@"%@|%d|%d|%d", pipeline.ident, (int)pipeline.depthTest,
                                               (int)pipeline.depthWrite, (int)pipeline.depthCompare];
    id<MTLDepthStencilState> cached = _depthStates[key];
    if (cached != nil) return cached;

    MTLDepthStencilDescriptor *descriptor = [MTLDepthStencilDescriptor new];
    descriptor.label = pipeline.ident;
    descriptor.depthCompareFunction = pipeline.depthTest ? ScMetalCompare(pipeline.depthCompare) : MTLCompareFunctionAlways;
    descriptor.depthWriteEnabled = pipeline.depthWrite ? YES : NO;
    id<MTLDepthStencilState> state = [_device newDepthStencilStateWithDescriptor:descriptor];
    if (state == nil) {
        if (error) {
            *error = ScReplayError(@"pipeline \"%@\": Metal refused the depth/stencil state", pipeline.ident);
        }
        return nil;
    }
    _depthStates[key] = state;
    return state;
}

- (nullable id<MTLSamplerState>)samplerStateForBinding:(ScSamplerBinding *)binding error:(NSError **)error {
    NSString *key = [NSString stringWithFormat:@"%d|%d|%d|%d|%d", (int)binding.minFilter, (int)binding.magFilter,
                                               (int)binding.addressU, (int)binding.addressV, (int)binding.addressW];
    id<MTLSamplerState> cached = _samplers[key];
    if (cached != nil) return cached;

    MTLSamplerDescriptor *descriptor = [MTLSamplerDescriptor new];
    descriptor.minFilter = binding.minFilter == ScFilterLinear ? MTLSamplerMinMagFilterLinear : MTLSamplerMinMagFilterNearest;
    descriptor.magFilter = binding.magFilter == ScFilterLinear ? MTLSamplerMinMagFilterLinear : MTLSamplerMinMagFilterNearest;
    descriptor.mipFilter = MTLSamplerMipFilterNotMipmapped;
    MTLSamplerAddressMode modes[3];
    ScAddressMode sources[3] = {binding.addressU, binding.addressV, binding.addressW};
    for (int i = 0; i < 3; i++) {
        switch (sources[i]) {
            case ScAddressRepeat: modes[i] = MTLSamplerAddressModeRepeat; break;
            case ScAddressMirrorRepeat: modes[i] = MTLSamplerAddressModeMirrorRepeat; break;
            case ScAddressClampToZero: modes[i] = MTLSamplerAddressModeClampToZero; break;
            case ScAddressMirrorClampToEdge: modes[i] = MTLSamplerAddressModeMirrorClampToEdge; break;
            case ScAddressClampToEdge:
            default: modes[i] = MTLSamplerAddressModeClampToEdge; break;
        }
    }
    descriptor.sAddressMode = modes[0];
    descriptor.tAddressMode = modes[1];
    descriptor.rAddressMode = modes[2];
    id<MTLSamplerState> state = [_device newSamplerStateWithDescriptor:descriptor];
    if (state == nil) {
        if (error) *error = ScReplayError(@"sampler slot %u: Metal refused the sampler state", binding.index);
        return nil;
    }
    _samplers[key] = state;
    return state;
}

#pragma mark - preparation

+ (BOOL)validateScene:(ScScene *)scene
              package:(ScPackage *)package
             problems:(NSMutableArray<NSString *> *)problems {
    BOOL ok = YES;
    for (ScPass *pass in scene.passes) {
        for (ScAttachment *attachment in pass.attachments) {
            ScResource *resource = [scene resourceNamed:attachment.textureId];
            if (resource == nil || !resource.isTexture) {
                [problems addObject:[NSString stringWithFormat:@"pass \"%@\" slot %u: \"%@\" is not a texture resource",
                                                               pass.ident, attachment.slot, attachment.textureId]];
                ok = NO;
                continue;
            }
            const ScFormatInfo *format = ScFormatNamed(resource.pixelFormat);
            if (format == NULL) {
                [problems addObject:[NSString stringWithFormat:@"pass \"%@\" slot %u: texture \"%@\" has unknown pixel "
                                                               @"format \"%@\" (known formats: %@)",
                                                               pass.ident, attachment.slot, resource.ident,
                                                               resource.pixelFormat, ScKnownFormatList()]];
                ok = NO;
                continue;
            }
            unsigned mipLevels = resource.mipLevels > 0 ? resource.mipLevels : 1;
            if (attachment.level >= mipLevels) {
                [problems addObject:[NSString stringWithFormat:@"pass \"%@\" slot %u: level %u is outside texture "
                                                               @"\"%@\" (%u mip level(s))",
                                                               pass.ident, attachment.slot, attachment.level,
                                                               resource.ident, mipLevels]];
                ok = NO;
            }
            if (resource.sampleCount > 1) {
                [problems addObject:[NSString stringWithFormat:@"pass \"%@\" slot %u: multisample attachment \"%@\" "
                                                               @"is not replayed yet",
                                                               pass.ident, attachment.slot, resource.ident]];
                ok = NO;
            }
        }
        ScAttachment *main = nil;
        for (ScAttachment *attachment in pass.attachments) {
            if (attachment.slot != 0) continue;
            ScResource *resource = [scene resourceNamed:attachment.textureId];
            const ScFormatInfo *format = ScFormatNamed(resource.pixelFormat);
            if (format != NULL && !format->isDepth && !format->isStencil) main = attachment;
        }
        if (main != nil) {
            ScResource *resource = [scene resourceNamed:main.textureId];
            if (pass.pixelFormat.length > 0 && ![pass.pixelFormat isEqualToString:resource.pixelFormat]) {
                [problems addObject:[NSString stringWithFormat:@"pass \"%@\": pixel_format \"%@\" disagrees with colour "
                                                               @"attachment \"%@\" (\"%@\")",
                                                               pass.ident, pass.pixelFormat, resource.ident,
                                                               resource.pixelFormat]];
                ok = NO;
            }
            unsigned expectedWidth = MAX(resource.width >> main.level, 1u);
            unsigned expectedHeight = MAX(resource.height >> main.level, 1u);
            if (pass.width != expectedWidth || pass.height != expectedHeight) {
                [problems addObject:[NSString stringWithFormat:@"pass \"%@\": %ux%u disagrees with colour attachment "
                                                               @"\"%@\" level %u (%ux%u)",
                                                               pass.ident, pass.width, pass.height, resource.ident,
                                                               main.level, expectedWidth, expectedHeight]];
                ok = NO;
            }
        }
    }

    for (ScResource *resource in scene.resources) {
        NSError *chunkError = nil;
        NSData *blob = [package payloadOfChunk:resource.blobId error:&chunkError];
        if (blob == nil) {
            NSString *kind = resource.isTexture ? @"texture" : @"buffer";
            [problems addObject:[NSString stringWithFormat:@"%@ \"%@\": %@", kind, resource.ident,
                                                           chunkError.localizedDescription ?: @"cannot load the blob"]];
            ok = NO;
            continue;
        }
        if (resource.blobOffset > blob.length || resource.blobSize > blob.length - resource.blobOffset) {
            NSString *kind = resource.isTexture ? @"texture" : @"buffer";
            [problems addObject:[NSString stringWithFormat:@"%@ \"%@\": bytes {offset %llu, size %llu} lie outside blob "
                                                           @"\"%@\" (%llu bytes)",
                                                           kind, resource.ident, (unsigned long long)resource.blobOffset,
                                                           (unsigned long long)resource.blobSize, resource.blobId,
                                                           (unsigned long long)blob.length]];
            ok = NO;
            continue;
        }
        if (resource.isTexture) {
            const ScFormatInfo *format = ScFormatNamed(resource.pixelFormat);
            if (format == NULL) continue; /* already reported through its attachments or below */
            uint64_t needed = 0;
            NSString *why = nil;
            if (!ScTextureLayoutBytes(resource, format, &needed, &why)) {
                [problems addObject:[NSString stringWithFormat:@"texture \"%@\": %@", resource.ident,
                                                               why ?: @"invalid layout"]];
                ok = NO;
                continue;
            }
            if (resource.blobSize < needed) {
                /* The capture layer records the GPU row layout, and this machine reports 5 bytes per texel for
                 * depth32float_stencil8 instead of the 8 its format implies. Upload what exists and zero-fill the
                 * rest: a depth/stencil target is cleared by the pass before it is read in every scene seen so far.
                 * Reported on stderr so a caller can tell the snapshot is short. */
                fprintf(stderr, "[scenecap] note: texture \"%s\" blob holds %llu of %llu bytes (%s); zero-filling\n",
                        resource.ident.UTF8String, (unsigned long long)resource.blobSize,
                        (unsigned long long)needed, resource.pixelFormat.UTF8String);
            } else if (resource.blobSize < needed) {
                [problems addObject:[NSString stringWithFormat:
                                                      @"texture \"%@\": blob \"%@\" holds %llu bytes but %ux%u %@ with "
                                                      @"%u mip level(s) from %u and %u layer(s) needs %llu",
                                                      resource.ident, resource.blobId,
                                                      (unsigned long long)resource.blobSize, resource.width,
                                                      resource.height, resource.pixelFormat, MAX(resource.mipCount, 1u),
                                                      resource.mipFirst, MAX(resource.layerCount, 1u),
                                                      (unsigned long long)needed]];
                ok = NO;
            }
        } else if (resource.blobSize == 0) {
            [problems addObject:[NSString stringWithFormat:@"buffer \"%@\": the snapshot is empty", resource.ident]];
            ok = NO;
        }
    }
    return ok;
}

- (BOOL)prepareWithError:(NSError **)error {
    /* Validate every attachment before touching the GPU so format problems point at a pass, not at a later draw. */
    for (ScPass *pass in _scene.passes) {
        if (pass.kind != ScPassKindRender) continue;
        for (ScAttachment *attachment in pass.attachments) {
            ScResource *resource = [_scene resourceNamed:attachment.textureId];
            if (resource == nil || !resource.isTexture) {
                if (error) {
                    *error = ScReplayError(@"pass \"%@\" slot %u: \"%@\" is not a texture resource", pass.ident,
                                           attachment.slot, attachment.textureId);
                }
                return NO;
            }
            const ScFormatInfo *format = ScFormatNamed(resource.pixelFormat);
            if (format == NULL) {
                if (error) {
                    *error = ScReplayError(@"pass \"%@\" slot %u: texture \"%@\" has unknown pixel format \"%@\"",
                                           pass.ident, attachment.slot, resource.ident, resource.pixelFormat);
                }
                return NO;
            }
            if (attachment.level >= resource.mipLevels) {
                if (error) {
                    *error = ScReplayError(@"pass \"%@\" slot %u: level %u is outside texture \"%@\" (%u mip level(s))",
                                           pass.ident, attachment.slot, attachment.level, resource.ident,
                                           resource.mipLevels);
                }
                return NO;
            }
            unsigned layerLimit = resource.textureType == ScTextureCube ? 6u * MAX(resource.arrayLayers, 1u)
                                 : (resource.textureType == ScTextureCubeArray ? 6u * MAX(resource.arrayLayers, 1u)
                                                                               : MAX(resource.arrayLayers, 1u));
            if (resource.textureType == ScTexture3D) layerLimit = 1;
            if (attachment.layer >= layerLimit) {
                if (error) {
                    *error = ScReplayError(@"pass \"%@\" slot %u: layer %u is outside texture \"%@\" (%u layer(s))",
                                           pass.ident, attachment.slot, attachment.layer, resource.ident, layerLimit);
                }
                return NO;
            }
        }
        /* A render pass with no colour attachment still runs (depth-only shadows), it just has nothing to export. */
        ScAttachment *main = nil;
        for (ScAttachment *attachment in pass.attachments) {
            if (attachment.slot == 0) {
                ScResource *resource = [_scene resourceNamed:attachment.textureId];
                const ScFormatInfo *format = ScFormatNamed(resource.pixelFormat);
                if (format != NULL && !format->isDepth && !format->isStencil) main = attachment;
            }
        }
        if (main != nil) {
            ScResource *resource = [_scene resourceNamed:main.textureId];
            if (pass.pixelFormat.length > 0 && ![pass.pixelFormat isEqualToString:resource.pixelFormat]) {
                if (error) {
                    *error = ScReplayError(@"pass \"%@\": pixel_format \"%@\" disagrees with colour attachment \"%@\" (%@)",
                                           pass.ident, pass.pixelFormat, resource.ident, resource.pixelFormat);
                }
                return NO;
            }
            unsigned expectedWidth = MAX(resource.width >> main.level, 1u);
            unsigned expectedHeight = MAX(resource.height >> main.level, 1u);
            if (pass.width != expectedWidth || pass.height != expectedHeight) {
                if (error) {
                    *error = ScReplayError(
                        @"pass \"%@\": %ux%u disagrees with colour attachment \"%@\" level %u (%ux%u)", pass.ident,
                        pass.width, pass.height, resource.ident, main.level, expectedWidth, expectedHeight);
                }
                return NO;
            }
            if (resource.sampleCount > 1) {
                if (error) {
                    *error = ScReplayError(@"pass \"%@\": multisample colour attachment \"%@\" is not replayed yet",
                                           pass.ident, resource.ident);
                }
                return NO;
            }
        }
        for (NSString *pipelineId in pass.pipelineIds) {
            ScPipeline *pipeline = [_scene pipelineNamed:pipelineId];
            if (pipeline == nil) {
                if (error) {
                    *error = ScReplayError(@"pass \"%@\": unknown pipeline \"%@\"", pass.ident, pipelineId);
                }
                return NO;
            }
            if ([self pipelineStateForPipeline:pipeline pass:pass error:error] == nil) return NO;
        }
    }

    /* Materialise every resource the capture declares: a missing or truncated blob must fail here by name rather
     * than producing a plausible looking image. */
    for (ScResource *resource in _scene.resources) {
        if (resource.isTexture) {
            if ([self textureForResource:resource error:error] == nil) return NO;
        } else {
            if ([self bufferForResource:resource error:error] == nil) return NO;
        }
    }
    return YES;
}

#pragma mark - binding helpers

/* Resolves a buffer binding, enforcing that the requested window exists inside the snapshot. */
- (nullable id<MTLBuffer>)bufferForBinding:(ScBufferBinding *)binding
                                  draw:(ScDraw *)draw
                               purpose:(NSString *)purpose
                                 error:(NSError **)error {
    ScResource *resource = [_scene resourceNamed:binding.resourceId];
    if (resource == nil || resource.isTexture) {
        if (error) {
            *error = ScReplayError(@"draw \"%@\": %@ slot %u names \"%@\", which is not a buffer resource", draw.ident,
                                   purpose, binding.index, binding.resourceId);
        }
        return nil;
    }
    id<MTLBuffer> buffer = [self bufferForResource:resource error:error];
    if (buffer == nil) {
        if (error) {
            *error = ScReplayError(@"draw \"%@\": %@ slot %u: %@", draw.ident, purpose, binding.index,
                                   (*error).localizedDescription ?: @"cannot load the buffer");
        }
        return nil;
    }
    uint64_t length = binding.length > 0 ? binding.length : (uint64_t)buffer.length - binding.offset;
    if (binding.offset > buffer.length || length > (uint64_t)buffer.length - binding.offset) {
        if (error) {
            *error = ScReplayError(
                @"draw \"%@\": %@ slot %u binds {offset %llu, length %llu} of buffer \"%@\" (%llu bytes)",
                draw.ident, purpose, binding.index, (unsigned long long)binding.offset, (unsigned long long)length,
                resource.ident, (unsigned long long)buffer.length);
        }
        return nil;
    }
    return buffer;
}

#pragma mark - pass encoding

/* Encodes every draw of one render pass.  Kept separate so the caller can always close the encoder, including on the
 * error path (letting a render encoder fall out of scope aborts the process inside Metal). */
- (BOOL)encodeDrawsOfPass:(ScPass *)pass
              intoEncoder:(id<MTLRenderCommandEncoder>)encoder
                 hasDepth:(BOOL)hasDepth
                drawCount:(NSUInteger *)drawCount
                    error:(NSError **)error {
    NSUInteger encoded = 0;
    MTLPrimitiveType currentPrimitive = MTLPrimitiveTypeTriangle;
    for (NSString *drawId in pass.drawIds) {
        ScDraw *draw = [_scene drawNamed:drawId];
        if (draw == nil) {
            if (error) *error = ScReplayError(@"pass \"%@\": unknown draw \"%@\"", pass.ident, drawId);
            return NO;
        }
        ScPipeline *pipeline = [_scene pipelineNamed:draw.pipelineId];
        if (pipeline == nil) {
            if (error) *error = ScReplayError(@"draw \"%@\": unknown pipeline \"%@\"", draw.ident, draw.pipelineId);
            return NO;
        }
        id<MTLRenderPipelineState> state = [self pipelineStateForPipeline:pipeline pass:pass error:error];
        if (state == nil) return NO;
        [encoder setRenderPipelineState:state];

        if (hasDepth) {
            id<MTLDepthStencilState> depthState = [self depthStateForPipeline:pipeline error:error];
            if (depthState == nil) return NO;
            [encoder setDepthStencilState:depthState];
        }

        /* SCENECAP_CULL_NONE=1: diagnostic override (isolate winding/cull effects) */
        [encoder setCullMode:getenv("SCENECAP_CULL_NONE") ? MTLCullModeNone : ScMetalCull(pipeline.cull)];
        [encoder setFrontFacingWinding:ScMetalWinding(pipeline.winding)];
        [encoder setTriangleFillMode:ScMetalFill(pipeline.fill)];
        if (pipeline.depthBias != 0.0 || pipeline.depthSlopeScale != 0.0 || pipeline.depthClamp != 0.0) {
            [encoder setDepthBias:(float)pipeline.depthBias
                       slopeScale:(float)pipeline.depthSlopeScale
                            clamp:(float)pipeline.depthClamp];
        }

        /* viewport/scissor are per pipeline in the capture; the pass extent is the default. */
        if (pipeline.hasViewport) {
            double x = pipeline.viewport.v[0], y = pipeline.viewport.v[1];
            double w = pipeline.viewport.v[2], h = pipeline.viewport.v[3];
            if (w <= 0.0 || h <= 0.0) {
                if (error) {
                    *error = ScReplayError(@"draw \"%@\": pipeline \"%@\" has a degenerate viewport (%gx%g)", draw.ident,
                                           pipeline.ident, w, h);
                }
                return NO;
            }
            MTLViewport viewport = {x, y, w, h, 0.0, 1.0};
            [encoder setViewport:viewport];
        } else {
            MTLViewport viewport = {0.0, 0.0, (double)pass.width, (double)pass.height, 0.0, 1.0};
            [encoder setViewport:viewport];
        }
        if (pipeline.hasScissor) {
            long x = pipeline.scissor.v[0], y = pipeline.scissor.v[1];
            long w = pipeline.scissor.v[2], h = pipeline.scissor.v[3];
            if (x < 0) { w += x; x = 0; }
            if (y < 0) { h += y; y = 0; }
            if (w < 0) w = 0;
            if (h < 0) h = 0;
            if (x + w > (long)pass.width) w = (long)pass.width - x;
            if (y + h > (long)pass.height) h = (long)pass.height - y;
            if (x > (long)pass.width || y > (long)pass.height || w == 0 || h == 0) {
                if (error) {
                    *error = ScReplayError(@"draw \"%@\": pipeline \"%@\" scissor {x %ld, y %ld, width %ld, height %ld} "
                                           @"does not intersect the %ux%u pass",
                                           draw.ident, pipeline.ident, (long)pipeline.scissor.v[0],
                                           (long)pipeline.scissor.v[1], (long)pipeline.scissor.v[2],
                                           (long)pipeline.scissor.v[3], pass.width, pass.height);
                }
                return NO;
            }
            MTLScissorRect scissor = {(NSUInteger)x, (NSUInteger)y, (NSUInteger)w, (NSUInteger)h};
            [encoder setScissorRect:scissor];
        }

        ScArgumentTable *table = draw.argumentTable;
        for (ScBufferBinding *binding in table.vertexBuffers) {
            id<MTLBuffer> buffer = [self bufferForBinding:binding draw:draw purpose:@"vertex buffer" error:error];
            if (buffer == nil) return NO;
            [encoder setVertexBuffer:buffer offset:(NSUInteger)binding.offset atIndex:binding.index];
        }
        for (ScBufferBinding *binding in table.uniformBuffers) {
            id<MTLBuffer> buffer = [self bufferForBinding:binding draw:draw purpose:@"uniform buffer" error:error];
            if (buffer == nil) return NO;
            /* A uniform buffer belongs to the stage that bound it; when the capture recorded no stage, bind both. */
            if (!binding.stage || binding.stage.unsignedIntValue != 1) {
                [encoder setVertexBuffer:buffer offset:(NSUInteger)binding.offset atIndex:binding.index];
            }
            if (!binding.stage || binding.stage.unsignedIntValue != 0) {
                [encoder setFragmentBuffer:buffer offset:(NSUInteger)binding.offset atIndex:binding.index];
            }
        }
        for (ScBufferBinding *binding in table.fragmentBuffers) {
            id<MTLBuffer> buffer = [self bufferForBinding:binding draw:draw purpose:@"fragment buffer" error:error];
            if (buffer == nil) return NO;
            [encoder setFragmentBuffer:buffer offset:(NSUInteger)binding.offset atIndex:binding.index];
        }
        for (ScTextureBinding *binding in table.textures) {
            ScResource *resource = [_scene resourceNamed:binding.textureId];
            if (resource == nil || !resource.isTexture) {
                if (error) {
                    *error = ScReplayError(@"draw \"%@\": texture slot %u names \"%@\", which is not a texture resource",
                                           draw.ident, binding.index, binding.textureId);
                }
                return NO;
            }
            NSError *textureError = nil;
            id<MTLTexture> texture = [self textureForResource:resource error:&textureError];
            if (texture == nil) {
                if (error) {
                    *error = ScReplayError(@"draw \"%@\": texture slot %u: %@", draw.ident, binding.index,
                                           textureError.localizedDescription ?: @"cannot load the texture");
                }
                return NO;
            }
            if (!binding.stage || binding.stage.unsignedIntValue != 1) {
                [encoder setVertexTexture:texture atIndex:binding.index];
            }
            if (!binding.stage || binding.stage.unsignedIntValue != 0) {
                [encoder setFragmentTexture:texture atIndex:binding.index];
            }
        }
        for (ScSamplerBinding *binding in table.samplers) {
            NSError *samplerError = nil;
            id<MTLSamplerState> sampler = [self samplerStateForBinding:binding error:&samplerError];
            if (sampler == nil) {
                if (error) {
                    *error = ScReplayError(@"draw \"%@\": %@", draw.ident,
                                           samplerError.localizedDescription ?: @"cannot create the sampler");
                }
                return NO;
            }
            if (!binding.stage || binding.stage.unsignedIntValue != 1) {
                [encoder setVertexSamplerState:sampler atIndex:binding.index];
            }
            if (!binding.stage || binding.stage.unsignedIntValue != 0) {
                [encoder setFragmentSamplerState:sampler atIndex:binding.index];
            }
        }

        if (draw.hasPrimitiveType) {
            currentPrimitive = ScMetalPrimitive(draw.primitiveType);
        } else {
            currentPrimitive = ScMetalPrimitive(pipeline.primitiveType);
        }
        if (draw.indexed) {
            ScIndexBuffer *index = draw.index;
            if (index.bufferId.length == 0) {
                if (error) {
                    *error = ScReplayError(@"draw \"%@\": indexed draw has no index buffer (manifest needs "
                                           @"draw.index.buffer)",
                                           draw.ident);
                }
                return NO;
            }
            ScResource *resource = [_scene resourceNamed:index.bufferId];
            if (resource == nil || resource.isTexture) {
                if (error) {
                    *error = ScReplayError(@"draw \"%@\": index buffer \"%@\" is not a buffer resource", draw.ident,
                                           index.bufferId);
                }
                return NO;
            }
            NSError *bufferError = nil;
            id<MTLBuffer> buffer = [self bufferForResource:resource error:&bufferError];
            if (buffer == nil) {
                if (error) {
                    *error = ScReplayError(@"draw \"%@\": index buffer: %@", draw.ident,
                                           bufferError.localizedDescription ?: @"cannot load the buffer");
                }
                return NO;
            }
            NSUInteger indexSize = index.wide ? 4u : 2u;
            uint64_t byteOffset = index.offset + index.start * indexSize;
            uint64_t byteEnd = byteOffset + index.count * indexSize;
            if (byteEnd > buffer.length) {
                if (error) {
                    *error = ScReplayError(
                        @"draw \"%@\": indices {start %llu, count %llu} at offset %llu overrun index buffer \"%@\" "
                        @"(%llu bytes)",
                        draw.ident, (unsigned long long)index.start, (unsigned long long)index.count,
                        (unsigned long long)index.offset, resource.ident, (unsigned long long)buffer.length);
                }
                return NO;
            }
            MTLIndexType indexType = index.wide ? MTLIndexTypeUInt32 : MTLIndexTypeUInt16;
            [encoder drawIndexedPrimitives:currentPrimitive
                                indexCount:(NSUInteger)index.count
                                 indexType:indexType
                               indexBuffer:buffer
                         indexBufferOffset:(NSUInteger)byteOffset
                             instanceCount:(NSUInteger)MAX(draw.instanceCount, 1u)];
        } else {
            if (draw.vertexCount == 0) {
                if (error) {
                    *error = ScReplayError(@"draw \"%@\": vertex.count is zero", draw.ident);
                }
                return NO;
            }
            [encoder drawPrimitives:currentPrimitive
                        vertexStart:(NSUInteger)draw.vertexStart
                        vertexCount:(NSUInteger)draw.vertexCount
                      instanceCount:(NSUInteger)MAX(draw.instanceCount, 1u)];
        }
        encoded++;
    }

    if (drawCount != NULL) *drawCount = encoded;
    return YES;
}

- (nullable ScPassImage *)renderPass:(ScPass *)pass drawCount:(NSUInteger *)drawCount error:(NSError **)error {
    if (pass.kind != ScPassKindRender) {
        if (error) {
            *error = ScReplayError(@"pass \"%@\": %@ passes are not replayed yet", pass.ident, pass.kindName);
        }
        return nil;
    }

    /* Pick the colour attachment to export: slot 0, which prepareWithError already matched against the pass. */
    ScAttachment *main = nil;
    for (ScAttachment *attachment in pass.attachments) {
        if (attachment.slot != 0) continue;
        ScResource *resource = [_scene resourceNamed:attachment.textureId];
        const ScFormatInfo *format = ScFormatNamed(resource.pixelFormat);
        if (format != NULL && !format->isDepth && !format->isStencil) main = attachment;
    }
    if (main == nil) {
        return nil; /* no colour output: the caller records it as unsupported */
    }

    MTLRenderPassDescriptor *descriptor = [MTLRenderPassDescriptor renderPassDescriptor];
    descriptor.visibilityResultBuffer = nil;

    for (ScAttachment *attachment in pass.attachments) {
        ScResource *resource = [_scene resourceNamed:attachment.textureId];
        const ScFormatInfo *format = ScFormatNamed(resource.pixelFormat);
        id<MTLTexture> texture = [self textureForResource:resource error:error];
        if (texture == nil) return nil;

        MTLRenderPassAttachmentDescriptor *target = nil;
        if (format->isDepth) {
            target = descriptor.depthAttachment;
        } else if (format->isStencil) {
            target = descriptor.stencilAttachment;
        } else if (attachment.slot < 8) {
            target = descriptor.colorAttachments[attachment.slot];
        } else {
            if (error) {
                *error = ScReplayError(@"pass \"%@\": colour slot %u is out of range (max 7)", pass.ident,
                                       attachment.slot);
            }
            return nil;
        }
        target.texture = texture;
        target.level = attachment.level;
        target.slice = resource.textureType == ScTexture3D ? 0 : attachment.layer;
        target.depthPlane = resource.textureType == ScTexture3D ? attachment.layer : 0;

        switch (attachment.load) {
            case ScLoadActionLoad:
                target.loadAction = MTLLoadActionLoad;
                break;
            case ScLoadActionDontCare:
                target.loadAction = MTLLoadActionDontCare;
                break;
            case ScLoadActionClear:
            default:
                target.loadAction = MTLLoadActionClear;
                break;
        }
        if (attachment.load == ScLoadActionClear) {
            if (format->isDepth) {
                descriptor.depthAttachment.clearDepth = attachment.clearDepth;
                descriptor.stencilAttachment.clearStencil = attachment.clearStencil;
            } else {
                descriptor.colorAttachments[attachment.slot].clearColor =
                    MTLClearColorMake(attachment.clearColor.v[0], attachment.clearColor.v[1],
                                      attachment.clearColor.v[2], attachment.clearColor.v[3]);
            }
        }
        BOOL isMainDepthStencil = format->isDepth && format->isStencil;
        target.storeAction = attachment.store == ScStoreActionDontCare ? MTLStoreActionDontCare : MTLStoreActionStore;
        if (isMainDepthStencil) {
            descriptor.stencilAttachment.texture = texture;
            descriptor.stencilAttachment.level = target.level;
            descriptor.stencilAttachment.slice = target.slice;
            descriptor.stencilAttachment.loadAction = target.loadAction;
            descriptor.stencilAttachment.storeAction = target.storeAction;
        }
        if (attachment == main && target.storeAction == MTLStoreActionDontCare) {
            /* The replay contract exports this attachment, so its contents must survive the pass. */
            target.storeAction = MTLStoreActionStore;
            [_warnings addObject:[NSString stringWithFormat:
                                                   @"pass \"%@\": store=dontcare on the exported attachment; storing it "
                                                   @"to read the replay result back",
                                                   pass.ident]];
        }
    }

    id<MTLCommandBuffer> commandBuffer = [_queue commandBuffer];
    commandBuffer.label = pass.ident;
    id<MTLRenderCommandEncoder> encoder = [commandBuffer renderCommandEncoderWithDescriptor:descriptor];
    if (encoder == nil) {
        if (error) {
            *error = ScReplayError(@"pass \"%@\": cannot create a render command encoder", pass.ident);
        }
        return nil;
    }
    encoder.label = pass.ident;

    NSUInteger encoded = 0;
    BOOL encodedOK = [self encodeDrawsOfPass:pass
                                 intoEncoder:encoder
                                    hasDepth:descriptor.depthAttachment.texture != nil
                                   drawCount:&encoded
                                       error:error];
    [encoder endEncoding];
    if (!encodedOK) return nil;
    [commandBuffer commit];
    [commandBuffer waitUntilCompleted];
    if (commandBuffer.status == MTLCommandBufferStatusError) {
        if (error) {
            *error = ScReplayError(@"pass \"%@\": the command buffer failed: %@", pass.ident,
                                   commandBuffer.error.localizedDescription ?: @"unknown Metal error");
        }
        return nil;
    }

    if (drawCount != NULL) *drawCount = encoded;

    /* Read the exported attachment back and convert it to the PNG contract (RGBA8, sRGB, top-left origin). */
    ScResource *mainResource = [_scene resourceNamed:main.textureId];
    id<MTLTexture> mainTexture = _textures[main.textureId];
    const ScFormatInfo *mainFormat = ScFormatNamed(mainResource.pixelFormat);
    unsigned width = MAX(mainResource.width >> main.level, 1u);
    unsigned height = MAX(mainResource.height >> main.level, 1u);
    if (mainFormat->kind == SC_PIXEL_KIND_NONE) {
        if (error) {
            *error = ScReplayError(@"pass \"%@\": pixel format \"%@\" cannot be exported as RGBA8", pass.ident,
                                   mainResource.pixelFormat);
        }
        return nil;
    }
    size_t rowBytes = (size_t)width * mainFormat->bytesPerPixel;
    size_t imageBytes = rowBytes * height;
    void *raw = calloc(1, imageBytes);
    if (raw == NULL) {
        if (error) *error = ScReplayError(@"pass \"%@\": out of memory for the read-back image", pass.ident);
        return nil;
    }
    MTLRegion region = MTLRegionMake3D(0, 0, 0, width, height, 1);
    BOOL sliced = mainResource.textureType == ScTexture3D || mainResource.textureType == ScTexture2DArray
                  || mainResource.textureType == ScTextureCube || mainResource.textureType == ScTextureCubeArray;
    if (sliced) {
        [mainTexture getBytes:raw
                  bytesPerRow:rowBytes
                bytesPerImage:imageBytes
                   fromRegion:region
                  mipmapLevel:main.level
                        slice:main.layer];
    } else {
        [mainTexture getBytes:raw bytesPerRow:rowBytes fromRegion:region mipmapLevel:main.level];
    }

    NSMutableData *rgba = [NSMutableData dataWithLength:(NSUInteger)width * height * 4u];
    char why[256] = {0};
    if (!sc_pixels_to_rgba8_srgb(mainFormat->kind, mainFormat->isBGRA, raw, imageBytes, width, height, rowBytes,
                                 rgba.mutableBytes, why, sizeof(why))) {
        free(raw);
        if (error) {
            *error = ScReplayError(@"pass \"%@\": cannot convert \"%@\" to RGBA8: %s", pass.ident,
                                   mainResource.pixelFormat, why);
        }
        return nil;
    }
    free(raw);

    ScPassImage *image = [[ScPassImage alloc] init];
    image.width = width;
    image.height = height;
    image.rgba = rgba;
    return image;
}

@end
