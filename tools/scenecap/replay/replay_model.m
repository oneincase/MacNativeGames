/* See replay_model.h. */
#import "replay_model.h"

#include <limits.h>
#include <math.h>

#include <CoreFoundation/CFNumber.h>

@implementation ScResource
- (BOOL)isTexture
{
    return [self.type isEqualToString:@"texture"];
}
@end

@implementation ScStage
- (NSString *)kindName
{
    switch (self.kind) {
        case ScStageVertex: return @"vertex";
        case ScStageFragment: return @"fragment";
        case ScStageKernel: return @"kernel";
        case ScStageObject: return @"object";
        case ScStageMesh: return @"mesh";
        case ScStageTile: return @"tile";
    }
    return @"unknown";
}
@end

@implementation ScBlendAttachment
@end

@implementation ScVertexAttribute
@end

@implementation ScVertexLayout
@end

@implementation ScPipeline
@end

@implementation ScAttachment
@end

@implementation ScBufferBinding
@end

@implementation ScTextureBinding
@end

@implementation ScSamplerBinding
@end

@implementation ScArgumentTable
@end

@implementation ScIndexBuffer
@end

@implementation ScDraw
- (BOOL)indexed
{
    return self.index != nil;
}
@end

@implementation ScPass
- (NSString *)kindName
{
    switch (self.kind) {
        case ScPassKindRender: return @"render";
        case ScPassKindCompute: return @"compute";
        case ScPassKindBlit: return @"blit";
    }
    return @"unknown";
}
@end

#pragma mark - accessors

typedef struct {
    const char *name;
    NSInteger value;
} ScNameValue;

static NSInteger sc_lookup(const ScNameValue *table, size_t count, id value, NSString *where, NSString *what,
                           NSMutableArray<NSString *> *errors, BOOL *ok)
{
    NSString *text;
    size_t i;
    if (![value isKindOfClass:[NSString class]]) {
        [errors addObject:[NSString stringWithFormat:@"%@: %@ must be a string", where, what]];
        if (ok) *ok = NO;
        return 0;
    }
    text = (NSString *)value;
    for (i = 0; i < count; i++) {
        if ([text isEqualToString:[NSString stringWithUTF8String:table[i].name]]) {
            if (ok) *ok = YES;
            return table[i].value;
        }
    }
    [errors addObject:[NSString stringWithFormat:@"%@: %@ has the unknown value \"%@\"", where, what, text]];
    if (ok) *ok = NO;
    return 0;
}

static NSDictionary *sc_dict(NSDictionary *dict, NSString *key, NSString *where, NSMutableArray *errors, BOOL required)
{
    id value = dict[key];
    if (!value || value == [NSNull null]) {
        if (required) [errors addObject:[NSString stringWithFormat:@"%@: missing \"%@\" object", where, key]];
        return nil;
    }
    if (![value isKindOfClass:[NSDictionary class]]) {
        [errors addObject:[NSString stringWithFormat:@"%@.%@: expected an object", where, key]];
        return nil;
    }
    return value;
}

static NSArray *sc_array(NSDictionary *dict, NSString *key, NSString *where, NSMutableArray *errors, BOOL required)
{
    id value = dict[key];
    if (!value || value == [NSNull null]) {
        if (required) [errors addObject:[NSString stringWithFormat:@"%@: missing \"%@\" array", where, key]];
        return nil;
    }
    if (![value isKindOfClass:[NSArray class]]) {
        [errors addObject:[NSString stringWithFormat:@"%@.%@: expected an array", where, key]];
        return nil;
    }
    return value;
}

/* 返回 nil 表示缺失（required 时记录错误）；返回 NSNull 表示类型非法。 */
static id sc_value(NSDictionary *dict, NSString *key, NSString *where, NSMutableArray *errors, BOOL required)
{
    id value = dict[key];
    if (!value || value == [NSNull null]) {
        if (required) [errors addObject:[NSString stringWithFormat:@"%@: missing \"%@\"", where, key]];
        return nil;
    }
    return value;
}

static NSString *sc_string(NSDictionary *dict, NSString *key, NSString *where, NSMutableArray *errors, BOOL required)
{
    id value = sc_value(dict, key, where, errors, required);
    if (!value) return nil;
    if (![value isKindOfClass:[NSString class]]) {
        [errors addObject:[NSString stringWithFormat:@"%@.%@: expected a string", where, key]];
        return nil;
    }
    return value;
}

static NSString *sc_string_or(NSDictionary *dict, NSString *key, NSString *where, NSMutableArray *errors,
                              NSString *fallback)
{
    NSString *value = sc_string(dict, key, where, errors, NO);
    return value ?: fallback;
}

static BOOL sc_integer_value(id value, long long *out)
{
    double number;
    if (![value isKindOfClass:[NSNumber class]]) return NO;
    number = [value doubleValue];
    if (!isfinite(number)) return NO;
    if (number != floor(number)) return NO;
    if (number < -9.2e18 || number > 9.2e18) return NO;
    if (out) *out = (long long)number;
    return YES;
}

static NSNumber *sc_integer(NSDictionary *dict, NSString *key, NSString *where, NSMutableArray *errors, BOOL required,
                            long long minimum)
{
    id value = sc_value(dict, key, where, errors, required);
    long long number = 0;
    if (!value) return nil;
    if (!sc_integer_value(value, &number)) {
        [errors addObject:[NSString stringWithFormat:@"%@.%@: expected an integer", where, key]];
        return nil;
    }
    if (minimum > LLONG_MIN && number < minimum) {
        [errors addObject:[NSString stringWithFormat:@"%@.%@: %lld is below the minimum %lld", where, key, number, minimum]];
        return nil;
    }
    return @(number);
}

static NSNumber *sc_number(NSDictionary *dict, NSString *key, NSString *where, NSMutableArray *errors, BOOL required)
{
    id value = sc_value(dict, key, where, errors, required);
    if (!value) return nil;
    if (![value isKindOfClass:[NSNumber class]]) {
        [errors addObject:[NSString stringWithFormat:@"%@.%@: expected a number", where, key]];
        return nil;
    }
    return value;
}

static BOOL sc_bool(NSDictionary *dict, NSString *key, NSString *where, NSMutableArray *errors, BOOL required,
                    BOOL fallback)
{
    id value = dict[key];
    if (!value || value == [NSNull null]) {
        if (required) [errors addObject:[NSString stringWithFormat:@"%@: missing \"%@\" boolean", where, key]];
        return fallback;
    }
    if ([value isKindOfClass:[NSNumber class]] && CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID()) {
        return [(NSNumber *)value boolValue];
    }
    [errors addObject:[NSString stringWithFormat:@"%@.%@: expected a boolean", where, key]];
    return fallback;
}

static NSString *sc_join_where(NSString *where, NSString *key)
{
    return [NSString stringWithFormat:@"%@.%@", where, key];
}

static NSString *sc_ident_where(NSString *where, NSString *ident)
{
    if (!ident.length) return where;
    return [NSString stringWithFormat:@"%@ (\"%@\")", where, ident];
}

#pragma mark - enums

static const ScNameValue kPassKinds[] = {{"render", ScPassKindRender}, {"compute", ScPassKindCompute}, {"blit", ScPassKindBlit}};
static const ScNameValue kLoads[] = {{"load", ScLoadActionLoad}, {"clear", ScLoadActionClear}, {"dontcare", ScLoadActionDontCare}};
static const ScNameValue kStores[] = {{"store", ScStoreActionStore}, {"dontcare", ScStoreActionDontCare}};
static const ScNameValue kPrimitives[] = {{"point", ScPrimitivePoint},
                                          {"line", ScPrimitiveLine},
                                          {"line_strip", ScPrimitiveLineStrip},
                                          {"triangle", ScPrimitiveTriangle},
                                          {"triangle_strip", ScPrimitiveTriangleStrip}};
static const ScNameValue kCompares[] = {{"never", ScCompareNever},
                                        {"less", ScCompareLess},
                                        {"equal", ScCompareEqual},
                                        {"less_equal", ScCompareLessEqual},
                                        {"greater", ScCompareGreater},
                                        {"not_equal", ScCompareNotEqual},
                                        {"greater_equal", ScCompareGreaterEqual},
                                        {"always", ScCompareAlways}};
static const ScNameValue kCulls[] = {{"none", ScCullNone}, {"front", ScCullFront}, {"back", ScCullBack}};
static const ScNameValue kFills[] = {{"fill", ScFillSolid}, {"lines", ScFillLines}, {"points", ScFillPoints}};
static const ScNameValue kWindings[] = {{"cw", ScWindingCW}, {"ccw", ScWindingCCW}};
static const ScNameValue kBlendOps[] = {{"add", ScBlendAdd},
                                        {"subtract", ScBlendSubtract},
                                        {"reverse_subtract", ScBlendReverseSubtract},
                                        {"min", ScBlendMin},
                                        {"max", ScBlendMax}};
static const ScNameValue kBlendFactors[] = {{"zero", ScBlendZero},
                                            {"one", ScBlendOne},
                                            {"source_color", ScBlendSourceColor},
                                            {"one_minus_source_color", ScBlendOneMinusSourceColor},
                                            {"destination_color", ScBlendDestinationColor},
                                            {"one_minus_destination_color", ScBlendOneMinusDestinationColor},
                                            {"source_alpha", ScBlendSourceAlpha},
                                            {"one_minus_source_alpha", ScBlendOneMinusSourceAlpha},
                                            {"destination_alpha", ScBlendDestinationAlpha},
                                            {"one_minus_destination_alpha", ScBlendOneMinusDestinationAlpha},
                                            {"source_alpha_saturated", ScBlendSourceAlphaSaturated},
                                            {"blend_color", ScBlendBlendColor},
                                            {"one_minus_blend_color", ScBlendOneMinusBlendColor},
                                            {"blend_alpha", ScBlendBlendAlpha},
                                            {"one_minus_blend_alpha", ScBlendOneMinusBlendAlpha}};
static const ScNameValue kStages[] = {{"vertex", ScStageVertex},     {"fragment", ScStageFragment}, {"kernel", ScStageKernel},
                                      {"object", ScStageObject},     {"mesh", ScStageMesh},         {"tile", ScStageTile}};
static const ScNameValue kTextureTypes[] = {{"1d", ScTexture1D},       {"2d", ScTexture2D},
                                            {"3d", ScTexture3D},       {"cube", ScTextureCube},
                                            {"2d_array", ScTexture2DArray}, {"cube_array", ScTextureCubeArray},
                                            {"2d_multisample", ScTexture2DMultisample}};
static const ScNameValue kFilters[] = {{"nearest", ScFilterNearest}, {"linear", ScFilterLinear}};
static const ScNameValue kAddresses[] = {{"clamp_to_edge", ScAddressClampToEdge},
                                         {"repeat", ScAddressRepeat},
                                         {"mirror_repeat", ScAddressMirrorRepeat},
                                         {"clamp_to_zero", ScAddressClampToZero},
                                         {"mirror_clamp_to_edge", ScAddressMirrorClampToEdge}};

#define SC_LOOKUP(table, value, where, what, errors, ok) \
    sc_lookup(table, sizeof table / sizeof table[0], value, where, what, errors, ok)

static ScResourceUsage sc_usage_from_strings(NSArray *strings)
{
    ScResourceUsage usage = ScResourceUsageShaderRead | ScResourceUsageRenderTarget;
    if (![strings isKindOfClass:[NSArray class]]) return usage;
    for (id item in strings) {
        if (![item isKindOfClass:[NSString class]]) continue;
        if ([item isEqualToString:@"shader_write"] || [item isEqualToString:@"storage"]) {
            usage |= ScResourceUsageShaderWrite;
        }
    }
    return usage;
}

#pragma mark - scene

@interface ScScene ()
- (void)parseResources:(NSDictionary *)manifest errors:(NSMutableArray<NSString *> *)errors;
- (void)parsePipelines:(NSDictionary *)manifest errors:(NSMutableArray<NSString *> *)errors;
- (void)parsePasses:(NSDictionary *)manifest errors:(NSMutableArray<NSString *> *)errors;
- (void)parseDraws:(NSDictionary *)manifest errors:(NSMutableArray<NSString *> *)errors;
- (NSArray<ScBufferBinding *> *)parseBufferBindings:(NSArray *)entries
                                               where:(NSString *)where
                                              errors:(NSMutableArray<NSString *> *)errors;
- (void)crossCheck:(NSMutableArray<NSString *> *)errors;
@property (nonatomic, strong) NSMutableDictionary<NSString *, ScResource *> *resourceMap;
@property (nonatomic, strong) NSMutableDictionary<NSString *, ScPipeline *> *pipelineMap;
@property (nonatomic, strong) NSMutableDictionary<NSString *, ScPass *> *passMap;
@property (nonatomic, strong) NSMutableDictionary<NSString *, ScDraw *> *drawMap;
@end

@implementation ScScene

+ (instancetype)sceneWithManifest:(NSDictionary<NSString *, id> *)manifest
                           errors:(NSMutableArray<NSString *> *)errors
{
    ScScene *scene = [[ScScene alloc] init];
    scene.manifest = manifest;
    scene.resourceMap = [NSMutableDictionary dictionary];
    scene.pipelineMap = [NSMutableDictionary dictionary];
    scene.passMap = [NSMutableDictionary dictionary];
    scene.drawMap = [NSMutableDictionary dictionary];

    NSDictionary *capture = sc_dict(manifest, @"capture", @"manifest", errors, YES);
    if (capture) {
        scene.game = sc_string_or(capture, @"game", @"manifest.capture", errors, @"unknown");
        scene.gameVersion = sc_string_or(capture, @"game_version", @"manifest.capture", errors, @"unknown");
        scene.buildVersion = sc_string_or(capture, @"build_version", @"manifest.capture", errors, @"unknown");
        scene.titleId = sc_string_or(capture, @"title_id", @"manifest.capture", errors, @"unknown");
        scene.capturedAt = sc_string_or(capture, @"captured_at", @"manifest.capture", errors, @"unknown");
        scene.gpu = sc_string_or(capture, @"gpu", @"manifest.capture", errors, @"unknown");
        scene.macOSVersion = sc_string_or(capture, @"macos", @"manifest.capture", errors, @"unknown");
        scene.metalVersion = sc_string_or(capture, @"metal", @"manifest.capture", errors, @"unknown");
        NSNumber *first = sc_integer(capture, @"frame_first", @"manifest.capture", errors, YES, 0);
        NSNumber *count = sc_integer(capture, @"frame_count", @"manifest.capture", errors, YES, 1);
        scene.frameFirst = first ? first.integerValue : 0;
        scene.frameCount = count ? count.integerValue : 1;
    }

    NSArray *frames = sc_array(manifest, @"frames", @"manifest", errors, NO);
    NSMutableArray *parsedFrames = [NSMutableArray array];
    for (NSUInteger i = 0; frames && i < frames.count; i++) {
        NSString *where = [NSString stringWithFormat:@"manifest.frames[%lu]", (unsigned long)i];
        if (![frames[i] isKindOfClass:[NSDictionary class]]) {
            [errors addObject:[NSString stringWithFormat:@"%@: expected an object", where]];
            continue;
        }
        [parsedFrames addObject:frames[i]];
    }
    scene.frames = parsedFrames;

    [scene parseResources:manifest errors:errors];
    [scene parsePipelines:manifest errors:errors];
    [scene parsePasses:manifest errors:errors];
    [scene parseDraws:manifest errors:errors];
    [scene crossCheck:errors];
    return scene;
}

- (void)parseResources:(NSDictionary *)manifest errors:(NSMutableArray<NSString *> *)errors
{
    NSArray *resources = sc_array(manifest, @"resources", @"manifest", errors, YES);
    NSMutableArray<ScResource *> *parsed = [NSMutableArray array];
    for (NSUInteger i = 0; resources && i < resources.count; i++) {
        NSString *where = [NSString stringWithFormat:@"manifest.resources[%lu]", (unsigned long)i];
        if (![resources[i] isKindOfClass:[NSDictionary class]]) {
            [errors addObject:[NSString stringWithFormat:@"%@: expected an object", where]];
            continue;
        }
        NSDictionary *dict = resources[i];
        ScResource *resource = [[ScResource alloc] init];
        resource.ident = sc_string(dict, @"id", where, errors, YES);
        if (!resource.ident.length) continue;
        where = sc_ident_where(where, resource.ident);
        if (self.resourceMap[resource.ident]) {
            [errors addObject:[NSString stringWithFormat:@"%@: duplicate resource id", where]];
            continue;
        }
        resource.type = sc_string(dict, @"type", where, errors, YES);
        if (![resource.type isEqualToString:@"texture"] && ![resource.type isEqualToString:@"buffer"]) {
            [errors addObject:[NSString stringWithFormat:@"%@.type: expected \"texture\" or \"buffer\"", where]];
            self.resourceMap[resource.ident] = resource;
            continue;
        }
        resource.blobId = sc_string(dict, @"blob", where, errors, YES);
        NSDictionary *bytes = sc_dict(dict, @"bytes", where, errors, YES);
        if (bytes) {
            NSNumber *offset = sc_integer(bytes, @"offset", sc_join_where(where, @"bytes"), errors, YES, 0);
            NSNumber *size = sc_integer(bytes, @"size", sc_join_where(where, @"bytes"), errors, YES, 0);
            resource.blobOffset = offset ? offset.unsignedLongLongValue : 0;
            resource.blobSize = size ? size.unsignedLongLongValue : 0;
        }
        resource.usage = sc_usage_from_strings(sc_array(dict, @"usage", where, errors, NO));

        if (resource.isTexture) {
            NSString *format = sc_string(dict, @"pixel_format", where, errors, YES);
            resource.pixelFormat = format;
            NSNumber *width = sc_integer(dict, @"width", where, errors, YES, 1);
            NSNumber *height = sc_integer(dict, @"height", where, errors, YES, 1);
            NSNumber *depth = sc_integer(dict, @"depth", where, errors, NO, 1);
            NSNumber *mips = sc_integer(dict, @"mip_levels", where, errors, NO, 1);
            NSNumber *layers = sc_integer(dict, @"array_layers", where, errors, NO, 1);
            NSNumber *samples = sc_integer(dict, @"sample_count", where, errors, NO, 1);
            resource.width = width ? width.unsignedIntValue : 0;
            resource.height = height ? height.unsignedIntValue : 0;
            resource.depth = depth ? depth.unsignedIntValue : 1;
            resource.mipLevels = mips ? mips.unsignedIntValue : 1;
            resource.arrayLayers = layers ? layers.unsignedIntValue : 1;
            resource.sampleCount = samples ? samples.unsignedIntValue : 1;
            NSString *type = sc_string(dict, @"texture_type", where, errors, NO);
            if (type) {
                BOOL ok = NO;
                resource.textureType = SC_LOOKUP(kTextureTypes, type, sc_join_where(where, @"texture_type"), @"texture_type", errors, &ok);
            } else {
                resource.textureType = resource.sampleCount > 1 ? ScTexture2DMultisample
                                       : resource.arrayLayers > 1 ? ScTexture2DArray
                                                                  : ScTexture2D;
            }
            NSDictionary *mipRange = sc_dict(dict, @"mip_range", where, errors, NO);
            resource.mipFirst = 0;
            resource.mipCount = resource.mipLevels;
            if (mipRange) {
                NSNumber *first = sc_integer(mipRange, @"first", sc_join_where(where, @"mip_range"), errors, YES, 0);
                NSNumber *count = sc_integer(mipRange, @"count", sc_join_where(where, @"mip_range"), errors, YES, 1);
                resource.mipFirst = first ? first.unsignedIntValue : 0;
                resource.mipCount = count ? count.unsignedIntValue : resource.mipLevels;
            }
            NSDictionary *layerRange = sc_dict(dict, @"layer_range", where, errors, NO);
            resource.layerFirst = 0;
            resource.layerCount = resource.arrayLayers;
            if (layerRange) {
                NSNumber *first = sc_integer(layerRange, @"first", sc_join_where(where, @"layer_range"), errors, YES, 0);
                NSNumber *count = sc_integer(layerRange, @"count", sc_join_where(where, @"layer_range"), errors, YES, 1);
                resource.layerFirst = first ? first.unsignedIntValue : 0;
                resource.layerCount = count ? count.unsignedIntValue : resource.arrayLayers;
            }
            if (resource.mipFirst + resource.mipCount > resource.mipLevels) {
                [errors addObject:[NSString stringWithFormat:@"%@.mip_range: %u+%u exceeds mip_levels %u", where,
                                                             resource.mipFirst, resource.mipCount, resource.mipLevels]];
            }
            if (resource.layerFirst + resource.layerCount > resource.arrayLayers) {
                [errors addObject:[NSString stringWithFormat:@"%@.layer_range: %u+%u exceeds array_layers %u", where,
                                                             resource.layerFirst, resource.layerCount,
                                                             resource.arrayLayers]];
            }
        }
        self.resourceMap[resource.ident] = resource;
        [parsed addObject:resource];
    }
    self.resources = parsed;
}

- (void)parsePipelines:(NSDictionary *)manifest errors:(NSMutableArray<NSString *> *)errors
{
    NSArray *pipelines = sc_array(manifest, @"pipelines", @"manifest", errors, YES);
    NSMutableArray<ScPipeline *> *parsed = [NSMutableArray array];
    for (NSUInteger i = 0; pipelines && i < pipelines.count; i++) {
        NSString *where = [NSString stringWithFormat:@"manifest.pipelines[%lu]", (unsigned long)i];
        if (![pipelines[i] isKindOfClass:[NSDictionary class]]) {
            [errors addObject:[NSString stringWithFormat:@"%@: expected an object", where]];
            continue;
        }
        NSDictionary *dict = pipelines[i];
        ScPipeline *pipeline = [[ScPipeline alloc] init];
        pipeline.ident = sc_string(dict, @"id", where, errors, YES);
        if (!pipeline.ident.length) continue;
        where = sc_ident_where(where, pipeline.ident);
        if (self.pipelineMap[pipeline.ident]) {
            [errors addObject:[NSString stringWithFormat:@"%@: duplicate pipeline id", where]];
            continue;
        }
        pipeline.depthCompare = ScCompareAlways;
        pipeline.cull = ScCullNone;
        pipeline.fill = ScFillSolid;
        pipeline.winding = ScWindingCCW;
        pipeline.primitiveType = ScPrimitiveTriangle;
        pipeline.hasViewport = NO;
        pipeline.hasScissor = NO;

        NSArray *stages = sc_array(dict, @"stages", where, errors, YES);
        NSMutableArray<ScStage *> *parsedStages = [NSMutableArray array];
        BOOL anyShader = NO;
        for (NSUInteger s = 0; stages && s < stages.count; s++) {
            NSString *stageWhere = [NSString stringWithFormat:@"%@.stages[%lu]", where, (unsigned long)s];
            if (![stages[s] isKindOfClass:[NSDictionary class]]) {
                [errors addObject:[NSString stringWithFormat:@"%@: expected an object", stageWhere]];
                continue;
            }
            NSDictionary *stageDict = stages[s];
            ScStage *stage = [[ScStage alloc] init];
            NSString *kind = sc_string(stageDict, @"stage", stageWhere, errors, YES);
            if (kind) {
                BOOL ok = NO;
                stage.kind = (ScStageKind)SC_LOOKUP(kStages, kind, sc_join_where(stageWhere, @"stage"), @"stage", errors, &ok);
            }
            stage.mslBlobId = sc_string(stageDict, @"msl_blob", stageWhere, errors, NO);
            stage.reflectionBlobId = sc_string(stageDict, @"reflection_blob", stageWhere, errors, NO);
            stage.entry = sc_string(stageDict, @"entry", stageWhere, errors, NO);
            if (stage.mslBlobId.length) anyShader = YES;
            [parsedStages addObject:stage];
        }
        pipeline.stages = parsedStages;
        if (!parsedStages.count) {
            [errors addObject:[NSString stringWithFormat:@"%@.stages: at least one stage is required", where]];
        }
        if (!anyShader) {
            [errors addObject:[NSString stringWithFormat:@"%@.stages: no stage references an MSL chunk", where]];
        }

        NSDictionary *state = sc_dict(dict, @"state", where, errors, YES);
        if (state) {
            NSDictionary *blend = sc_dict(state, @"blend", sc_join_where(where, @"state"), errors, NO);
            pipeline.blendEnabled = NO;
            pipeline.blendAttachments = @[];
            if (blend) {
                pipeline.blendEnabled = sc_bool(blend, @"enabled", sc_join_where(where, @"state.blend"), errors, NO, NO);
                NSArray *attachments = sc_array(blend, @"attachments", sc_join_where(where, @"state.blend"), errors, NO);
                NSMutableArray *parsedAttachments = [NSMutableArray array];
                for (NSUInteger a = 0; attachments && a < attachments.count; a++) {
                    NSString *attachWhere = [NSString stringWithFormat:@"%@.state.blend.attachments[%lu]", where,
                                                                       (unsigned long)a];
                    if (![attachments[a] isKindOfClass:[NSDictionary class]]) {
                        [errors addObject:[NSString stringWithFormat:@"%@: expected an object", attachWhere]];
                        continue;
                    }
                    NSDictionary *attachDict = attachments[a];
                    ScBlendAttachment *attachment = [[ScBlendAttachment alloc] init];
                    NSNumber *index = sc_integer(attachDict, @"index", attachWhere, errors, YES, 0);
                    attachment.index = index ? index.unsignedIntValue : 0;
                    attachment.enabled = sc_bool(attachDict, @"enabled", attachWhere, errors, YES, NO);
                    attachment.rgbOperation = ScBlendAdd;
                    attachment.alphaOperation = ScBlendAdd;
                    attachment.rgbSource = ScBlendOne;
                    attachment.rgbDestination = ScBlendZero;
                    attachment.alphaSource = ScBlendOne;
                    attachment.alphaDestination = ScBlendZero;
                    NSString *text;
                    if ((text = sc_string(attachDict, @"rgb_operation", attachWhere, errors, NO))) {
                        BOOL ok = NO;
                        attachment.rgbOperation = (ScBlendOperation)SC_LOOKUP(kBlendOps, text, sc_join_where(attachWhere, @"rgb_operation"), @"blend operation", errors, &ok);
                    }
                    if ((text = sc_string(attachDict, @"alpha_operation", attachWhere, errors, NO))) {
                        BOOL ok = NO;
                        attachment.alphaOperation = (ScBlendOperation)SC_LOOKUP(kBlendOps, text, sc_join_where(attachWhere, @"alpha_operation"), @"blend operation", errors, &ok);
                    }
                    if ((text = sc_string(attachDict, @"rgb_source", attachWhere, errors, NO))) {
                        BOOL ok = NO;
                        attachment.rgbSource = (ScBlendFactor)SC_LOOKUP(kBlendFactors, text, sc_join_where(attachWhere, @"rgb_source"), @"blend factor", errors, &ok);
                    }
                    if ((text = sc_string(attachDict, @"rgb_destination", attachWhere, errors, NO))) {
                        BOOL ok = NO;
                        attachment.rgbDestination = (ScBlendFactor)SC_LOOKUP(kBlendFactors, text, sc_join_where(attachWhere, @"rgb_destination"), @"blend factor", errors, &ok);
                    }
                    if ((text = sc_string(attachDict, @"alpha_source", attachWhere, errors, NO))) {
                        BOOL ok = NO;
                        attachment.alphaSource = (ScBlendFactor)SC_LOOKUP(kBlendFactors, text, sc_join_where(attachWhere, @"alpha_source"), @"blend factor", errors, &ok);
                    }
                    if ((text = sc_string(attachDict, @"alpha_destination", attachWhere, errors, NO))) {
                        BOOL ok = NO;
                        attachment.alphaDestination = (ScBlendFactor)SC_LOOKUP(kBlendFactors, text, sc_join_where(attachWhere, @"alpha_destination"), @"blend factor", errors, &ok);
                    }
                    [parsedAttachments addObject:attachment];
                }
                pipeline.blendAttachments = parsedAttachments;
            }

            NSDictionary *depth = sc_dict(state, @"depth_stencil", sc_join_where(where, @"state"), errors, NO);
            if (depth) {
                NSString *depthWhere = sc_join_where(where, @"state.depth_stencil");
                pipeline.depthTest = sc_bool(depth, @"depth_test", depthWhere, errors, NO, NO);
                pipeline.depthWrite = sc_bool(depth, @"depth_write", depthWhere, errors, NO, NO);
                NSString *compare = sc_string(depth, @"depth_compare", depthWhere, errors, NO);
                if (compare) {
                    BOOL ok = NO;
                    pipeline.depthCompare = (ScCompareFunction)SC_LOOKUP(kCompares, compare, sc_join_where(depthWhere, @"depth_compare"), @"compare function", errors, &ok);
                }
            }

            NSDictionary *raster = sc_dict(state, @"raster", sc_join_where(where, @"state"), errors, NO);
            if (raster) {
                NSString *rasterWhere = sc_join_where(where, @"state.raster");
                NSString *text;
                if ((text = sc_string(raster, @"cull", rasterWhere, errors, NO))) {
                    BOOL ok = NO;
                    pipeline.cull = (ScCullMode)SC_LOOKUP(kCulls, text, sc_join_where(rasterWhere, @"cull"), @"cull mode", errors, &ok);
                }
                if ((text = sc_string(raster, @"fill", rasterWhere, errors, NO))) {
                    BOOL ok = NO;
                    pipeline.fill = (ScFillMode)SC_LOOKUP(kFills, text, sc_join_where(rasterWhere, @"fill"), @"fill mode", errors, &ok);
                }
                NSNumber *number;
                if ((number = sc_number(raster, @"depth_bias", rasterWhere, errors, NO))) pipeline.depthBias = number.doubleValue;
                if ((number = sc_number(raster, @"depth_slope_scale", rasterWhere, errors, NO))) pipeline.depthSlopeScale = number.doubleValue;
                if ((number = sc_number(raster, @"depth_clamp", rasterWhere, errors, NO))) pipeline.depthClamp = number.doubleValue;
            }

            NSString *viewport = sc_join_where(where, @"state");
            NSArray *viewportValues = sc_array(state, @"viewport", viewport, errors, NO);
            if (viewportValues.count == 4) {
                NSMutableArray<NSNumber *> *numbers = [NSMutableArray array];
                for (id item in viewportValues) {
                    if (![item isKindOfClass:[NSNumber class]]) {
                        [errors addObject:[NSString stringWithFormat:@"%@.viewport: expected four numbers", viewport]];
                        numbers = nil;
                        break;
                    }
                    [numbers addObject:item];
                }
                if (numbers) {
                    /* A struct-valued property returns a temporary, so fill a local and assign it once. */
                    ScVec4 viewportValue = {{0.0, 0.0, 0.0, 0.0}};
                    pipeline.hasViewport = YES;
                    for (int k = 0; k < 4; k++) viewportValue.v[k] = numbers[(NSUInteger)k].doubleValue;
                    pipeline.viewport = viewportValue;
                }
            } else if (viewportValues) {
                [errors addObject:[NSString stringWithFormat:@"%@.viewport: expected four numbers", viewport]];
            }
            NSArray *scissorValues = sc_array(state, @"scissor", viewport, errors, NO);
            if (scissorValues.count == 4) {
                long long values[4];
                BOOL ok = YES;
                for (NSUInteger k = 0; k < 4; k++) {
                    if (!sc_integer_value(scissorValues[k], &values[k])) ok = NO;
                }
                if (ok) {
                    ScInt4 scissorValue = {{0, 0, 0, 0}};
                    pipeline.hasScissor = YES;
                    for (int k = 0; k < 4; k++) scissorValue.v[k] = (long)values[k];
                    pipeline.scissor = scissorValue;
                } else {
                    [errors addObject:[NSString stringWithFormat:@"%@.scissor: expected four integers", viewport]];
                }
            } else if (scissorValues) {
                [errors addObject:[NSString stringWithFormat:@"%@.scissor: expected four integers", viewport]];
            }

            NSString *text;
            text = sc_string(state, @"frontface", viewport, errors, NO);
            if (text) {
                BOOL ok = NO;
                pipeline.winding = (ScWinding)SC_LOOKUP(kWindings, text, sc_join_where(viewport, @"frontface"), @"winding", errors, &ok);
            }
            text = sc_string(state, @"primitive_type", viewport, errors, NO);
            if (text) {
                BOOL ok = NO;
                pipeline.primitiveType = (ScPrimitiveType)SC_LOOKUP(kPrimitives, text, sc_join_where(viewport, @"primitive_type"), @"primitive type", errors, &ok);
            }
        }

        NSArray *attributes = sc_array(dict, @"vertex_attributes", where, errors, NO);
        NSMutableArray<ScVertexAttribute *> *parsedAttributes = [NSMutableArray array];
        for (NSUInteger i = 0; attributes && i < attributes.count; i++) {
            NSString *aw = [NSString stringWithFormat:@"%@.vertex_attributes[%lu]", where, (unsigned long)i];
            if (![attributes[i] isKindOfClass:[NSDictionary class]]) {
                [errors addObject:[NSString stringWithFormat:@"%@: expected an object", aw]];
                continue;
            }
            ScVertexAttribute *attribute = [[ScVertexAttribute alloc] init];
            NSNumber *location = sc_integer(attributes[i], @"location", aw, errors, YES, 0);
            NSNumber *format = sc_integer(attributes[i], @"format", aw, errors, YES, 0);
            NSNumber *offset = sc_integer(attributes[i], @"offset", aw, errors, NO, 0);
            NSNumber *buffer = sc_integer(attributes[i], @"buffer_index", aw, errors, YES, 0);
            attribute.location = location ? location.unsignedIntValue : 0;
            attribute.format = format ? format.unsignedIntValue : 0;
            attribute.offset = offset ? offset.unsignedIntValue : 0;
            attribute.bufferIndex = buffer ? buffer.unsignedIntValue : 0;
            [parsedAttributes addObject:attribute];
        }
        pipeline.vertexAttributes = parsedAttributes;

        NSArray *layouts = sc_array(dict, @"vertex_layouts", where, errors, NO);
        NSMutableArray<ScVertexLayout *> *parsedLayouts = [NSMutableArray array];
        for (NSUInteger i = 0; layouts && i < layouts.count; i++) {
            NSString *lw = [NSString stringWithFormat:@"%@.vertex_layouts[%lu]", where, (unsigned long)i];
            if (![layouts[i] isKindOfClass:[NSDictionary class]]) {
                [errors addObject:[NSString stringWithFormat:@"%@: expected an object", lw]];
                continue;
            }
            ScVertexLayout *layout = [[ScVertexLayout alloc] init];
            NSNumber *buffer = sc_integer(layouts[i], @"buffer_index", lw, errors, YES, 0);
            NSNumber *stride = sc_integer(layouts[i], @"stride", lw, errors, NO, 0);
            NSNumber *step = sc_integer(layouts[i], @"step_function", lw, errors, NO, 0);
            NSNumber *rate = sc_integer(layouts[i], @"step_rate", lw, errors, NO, 0);
            layout.bufferIndex = buffer ? buffer.unsignedIntValue : 0;
            layout.stride = stride ? stride.unsignedIntValue : 0;
            layout.stepFunction = step ? step.unsignedIntValue : 0;
            layout.stepRate = rate ? rate.unsignedIntValue : 0;
            [parsedLayouts addObject:layout];
        }
        pipeline.vertexLayouts = parsedLayouts;

        self.pipelineMap[pipeline.ident] = pipeline;
        [parsed addObject:pipeline];
    }
    self.pipelines = parsed;
}

- (void)parsePasses:(NSDictionary *)manifest errors:(NSMutableArray<NSString *> *)errors
{
    NSArray *passes = sc_array(manifest, @"passes", @"manifest", errors, YES);
    NSMutableArray<ScPass *> *parsed = [NSMutableArray array];
    for (NSUInteger i = 0; passes && i < passes.count; i++) {
        NSString *where = [NSString stringWithFormat:@"manifest.passes[%lu]", (unsigned long)i];
        if (![passes[i] isKindOfClass:[NSDictionary class]]) {
            [errors addObject:[NSString stringWithFormat:@"%@: expected an object", where]];
            continue;
        }
        NSDictionary *dict = passes[i];
        ScPass *pass = [[ScPass alloc] init];
        pass.ident = sc_string(dict, @"id", where, errors, YES);
        if (!pass.ident.length) continue;
        where = sc_ident_where(where, pass.ident);
        if (self.passMap[pass.ident]) {
            [errors addObject:[NSString stringWithFormat:@"%@: duplicate pass id", where]];
            continue;
        }
        NSString *kind = sc_string(dict, @"kind", where, errors, YES);
        if (kind) {
            BOOL ok = NO;
            pass.kind = (ScPassKind)SC_LOOKUP(kPassKinds, kind, sc_join_where(where, @"kind"), @"pass kind", errors, &ok);
        }
        NSNumber *width = sc_integer(dict, @"width", where, errors, YES, 1);
        NSNumber *height = sc_integer(dict, @"height", where, errors, YES, 1);
        pass.width = width ? width.unsignedIntValue : 0;
        pass.height = height ? height.unsignedIntValue : 0;
        pass.pixelFormat = sc_string(dict, @"pixel_format", where, errors, YES);
        NSNumber *frame = sc_integer(dict, @"frame", where, errors, NO, 0);
        pass.hasFrame = frame != nil;
        pass.frame = frame ? frame.integerValue : 0;

        NSArray *attachments = sc_array(dict, @"attachments", where, errors, NO);
        NSMutableArray<ScAttachment *> *parsedAttachments = [NSMutableArray array];
        for (NSUInteger a = 0; attachments && a < attachments.count; a++) {
            NSString *attachWhere = [NSString stringWithFormat:@"%@.attachments[%lu]", where, (unsigned long)a];
            if (![attachments[a] isKindOfClass:[NSDictionary class]]) {
                [errors addObject:[NSString stringWithFormat:@"%@: expected an object", attachWhere]];
                continue;
            }
            NSDictionary *attachDict = attachments[a];
            ScAttachment *attachment = [[ScAttachment alloc] init];
            NSNumber *slot = sc_integer(attachDict, @"slot", attachWhere, errors, YES, 0);
            attachment.slot = slot ? slot.unsignedIntValue : 0;
            attachment.textureId = sc_string(attachDict, @"texture", attachWhere, errors, YES);
            NSNumber *level = sc_integer(attachDict, @"level", attachWhere, errors, NO, 0);
            NSNumber *layer = sc_integer(attachDict, @"layer", attachWhere, errors, NO, 0);
            attachment.level = level ? level.unsignedIntValue : 0;
            attachment.layer = layer ? layer.unsignedIntValue : 0;
            NSString *load = sc_string(attachDict, @"load", attachWhere, errors, YES);
            if (load) {
                BOOL ok = NO;
                attachment.load = (ScLoadAction)SC_LOOKUP(kLoads, load, sc_join_where(attachWhere, @"load"), @"load action", errors, &ok);
            }
            NSString *store = sc_string(attachDict, @"store", attachWhere, errors, YES);
            if (store) {
                BOOL ok = NO;
                attachment.store = (ScStoreAction)SC_LOOKUP(kStores, store, sc_join_where(attachWhere, @"store"), @"store action", errors, &ok);
            }
            ScVec4 clearColor = {{0.0, 0.0, 0.0, 1.0}};
            attachment.clearDepth = 1.0;
            if (attachment.load == ScLoadActionClear) {
                NSArray *clear = sc_array(attachDict, @"clear", attachWhere, errors, YES);
                if (clear.count < 1 || clear.count > 4) {
                    if (clear) [errors addObject:[NSString stringWithFormat:@"%@.clear: expected one to four numbers", attachWhere]];
                } else {
                    for (NSUInteger c = 0; c < clear.count; c++) {
                        if (![clear[c] isKindOfClass:[NSNumber class]]) {
                            [errors addObject:[NSString stringWithFormat:@"%@.clear: expected one to four numbers", attachWhere]];
                            break;
                        }
                        clearColor.v[c] = [clear[c] doubleValue];
                    }
                    if (clear.count == 1) attachment.clearDepth = [clear[0] doubleValue];
                }
            }
            attachment.clearColor = clearColor;
            [parsedAttachments addObject:attachment];
        }
        pass.attachments = parsedAttachments;

        NSArray *pipelineIds = sc_array(dict, @"pipelines", where, errors, NO);
        NSMutableArray<NSString *> *parsedPipelines = [NSMutableArray array];
        for (id item in pipelineIds) {
            if (![item isKindOfClass:[NSString class]]) {
                [errors addObject:[NSString stringWithFormat:@"%@.pipelines: expected strings", where]];
                continue;
            }
            [parsedPipelines addObject:item];
        }
        pass.pipelineIds = parsedPipelines;
        NSArray *drawIds = sc_array(dict, @"draws", where, errors, NO);
        NSMutableArray<NSString *> *parsedDraws = [NSMutableArray array];
        for (id item in drawIds) {
            if (![item isKindOfClass:[NSString class]]) {
                [errors addObject:[NSString stringWithFormat:@"%@.draws: expected strings", where]];
                continue;
            }
            [parsedDraws addObject:item];
        }
        pass.drawIds = parsedDraws;
        if (pass.drawIds.count && !pass.pipelineIds.count) {
            [errors addObject:[NSString stringWithFormat:@"%@: lists draws but no pipeline to render them with", where]];
        }
        self.passMap[pass.ident] = pass;
        [parsed addObject:pass];
    }
    self.passes = parsed;
}

- (void)parseDraws:(NSDictionary *)manifest errors:(NSMutableArray<NSString *> *)errors
{
    NSArray *draws = sc_array(manifest, @"draws", @"manifest", errors, NO);
    NSMutableArray<ScDraw *> *parsed = [NSMutableArray array];
    for (NSUInteger i = 0; draws && i < draws.count; i++) {
        NSString *where = [NSString stringWithFormat:@"manifest.draws[%lu]", (unsigned long)i];
        if (![draws[i] isKindOfClass:[NSDictionary class]]) {
            [errors addObject:[NSString stringWithFormat:@"%@: expected an object", where]];
            continue;
        }
        NSDictionary *dict = draws[i];
        ScDraw *draw = [[ScDraw alloc] init];
        draw.ident = sc_string(dict, @"id", where, errors, YES);
        if (!draw.ident.length) continue;
        where = sc_ident_where(where, draw.ident);
        if (self.drawMap[draw.ident]) {
            [errors addObject:[NSString stringWithFormat:@"%@: duplicate draw id", where]];
            continue;
        }
        draw.passId = sc_string(dict, @"pass", where, errors, YES);
        draw.pipelineId = sc_string(dict, @"pipeline", where, errors, YES);
        draw.instanceCount = 1;
        NSDictionary *vertex = sc_dict(dict, @"vertex", where, errors, NO);
        if (vertex) {
            NSNumber *start = sc_integer(vertex, @"start", sc_join_where(where, @"vertex"), errors, NO, 0);
            NSNumber *count = sc_integer(vertex, @"count", sc_join_where(where, @"vertex"), errors, NO, 0);
            draw.vertexStart = start ? start.unsignedLongLongValue : 0;
            draw.vertexCount = count ? count.unsignedLongLongValue : 0;
        }
        NSDictionary *index = sc_dict(dict, @"index", where, errors, NO);
        if (index) {
            ScIndexBuffer *buffer = [[ScIndexBuffer alloc] init];
            NSString *indexWhere = sc_join_where(where, @"index");
            NSString *type = sc_string(index, @"type", indexWhere, errors, YES);
            if (type) {
                if ([type isEqualToString:@"uint32"]) {
                    buffer.wide = YES;
                } else if ([type isEqualToString:@"uint16"]) {
                    buffer.wide = NO;
                } else {
                    [errors addObject:[NSString stringWithFormat:@"%@.type: expected \"uint16\" or \"uint32\"", indexWhere]];
                }
            }
            NSNumber *start = sc_integer(index, @"start", indexWhere, errors, NO, 0);
            NSNumber *count = sc_integer(index, @"count", indexWhere, errors, YES, 1);
            NSNumber *offset = sc_integer(index, @"offset", indexWhere, errors, NO, 0);
            buffer.start = start ? start.unsignedLongLongValue : 0;
            buffer.count = count ? count.unsignedLongLongValue : 0;
            buffer.offset = offset ? offset.unsignedLongLongValue : 0;
            buffer.bufferId = sc_string(index, @"buffer", indexWhere, errors, NO);
            draw.index = buffer;
        }
        if (!index && draw.vertexCount == 0) {
            [errors addObject:[NSString stringWithFormat:@"%@: a non-indexed draw needs vertex.count", where]];
        }
        NSNumber *instances = sc_integer(dict, @"instances", where, errors, NO, 1);
        if (instances) draw.instanceCount = instances.unsignedLongLongValue;
        if (draw.instanceCount == 0) draw.instanceCount = 1;
        NSString *primitive = sc_string(dict, @"primitive_type", where, errors, NO);
        if (primitive) {
            BOOL ok = NO;
            draw.primitiveType = (ScPrimitiveType)SC_LOOKUP(kPrimitives, primitive, sc_join_where(where, @"primitive_type"), @"primitive type", errors, &ok);
            draw.hasPrimitiveType = YES;
        }

        NSDictionary *table = sc_dict(dict, @"argument_table", where, errors, NO);
        if (table) {
            ScArgumentTable *parsedTable = [[ScArgumentTable alloc] init];
            NSString *tableWhere = sc_join_where(where, @"argument_table");
            parsedTable.vertexBuffers = [self parseBufferBindings:sc_array(table, @"vertex_buffers", tableWhere, errors, NO)
                                                           where:sc_join_where(tableWhere, @"vertex_buffers")
                                                          errors:errors];
            parsedTable.uniformBuffers = [self parseBufferBindings:sc_array(table, @"uniform_buffers", tableWhere, errors, NO)
                                                            where:sc_join_where(tableWhere, @"uniform_buffers")
                                                           errors:errors];
            parsedTable.fragmentBuffers = [self parseBufferBindings:sc_array(table, @"fragment_buffers", tableWhere, errors, NO)
                                                             where:sc_join_where(tableWhere, @"fragment_buffers")
                                                            errors:errors];
            NSArray *textures = sc_array(table, @"textures", tableWhere, errors, NO);
            NSMutableArray<ScTextureBinding *> *parsedTextures = [NSMutableArray array];
            for (NSUInteger t = 0; textures && t < textures.count; t++) {
                NSString *textureWhere = [NSString stringWithFormat:@"%@.textures[%lu]", tableWhere, (unsigned long)t];
                if (![textures[t] isKindOfClass:[NSDictionary class]]) {
                    [errors addObject:[NSString stringWithFormat:@"%@: expected an object", textureWhere]];
                    continue;
                }
                ScTextureBinding *binding = [[ScTextureBinding alloc] init];
                NSNumber *slot = sc_integer(textures[t], @"index", textureWhere, errors, YES, 0);
                binding.index = slot ? slot.unsignedIntValue : 0;
                binding.stage = sc_integer(textures[t], @"stage", textureWhere, errors, NO, 0);
                binding.textureId = sc_string(textures[t], @"texture", textureWhere, errors, YES);
                [parsedTextures addObject:binding];
            }
            parsedTable.textures = parsedTextures;

            NSArray *samplers = sc_array(table, @"samplers", tableWhere, errors, NO);
            NSMutableArray<ScSamplerBinding *> *parsedSamplers = [NSMutableArray array];
            for (NSUInteger s = 0; samplers && s < samplers.count; s++) {
                NSString *samplerWhere = [NSString stringWithFormat:@"%@.samplers[%lu]", tableWhere, (unsigned long)s];
                if (![samplers[s] isKindOfClass:[NSDictionary class]]) {
                    [errors addObject:[NSString stringWithFormat:@"%@: expected an object", samplerWhere]];
                    continue;
                }
                ScSamplerBinding *binding = [[ScSamplerBinding alloc] init];
                NSNumber *slot = sc_integer(samplers[s], @"index", samplerWhere, errors, YES, 0);
                binding.index = slot ? slot.unsignedIntValue : 0;
                binding.stage = sc_integer(samplers[s], @"stage", samplerWhere, errors, NO, 0);
                binding.minFilter = ScFilterNearest;
                binding.magFilter = ScFilterNearest;
                binding.addressU = ScAddressClampToEdge;
                binding.addressV = ScAddressClampToEdge;
                binding.addressW = ScAddressClampToEdge;
                NSString *text;
                if ((text = sc_string(samplers[s], @"min_filter", samplerWhere, errors, NO))) {
                    BOOL ok = NO;
                    binding.minFilter = (ScFilter)SC_LOOKUP(kFilters, text, sc_join_where(samplerWhere, @"min_filter"), @"filter", errors, &ok);
                }
                if ((text = sc_string(samplers[s], @"mag_filter", samplerWhere, errors, NO))) {
                    BOOL ok = NO;
                    binding.magFilter = (ScFilter)SC_LOOKUP(kFilters, text, sc_join_where(samplerWhere, @"mag_filter"), @"filter", errors, &ok);
                }
                if ((text = sc_string(samplers[s], @"address_u", samplerWhere, errors, NO))) {
                    BOOL ok = NO;
                    binding.addressU = (ScAddressMode)SC_LOOKUP(kAddresses, text, sc_join_where(samplerWhere, @"address_u"), @"address mode", errors, &ok);
                }
                if ((text = sc_string(samplers[s], @"address_v", samplerWhere, errors, NO))) {
                    BOOL ok = NO;
                    binding.addressV = (ScAddressMode)SC_LOOKUP(kAddresses, text, sc_join_where(samplerWhere, @"address_v"), @"address mode", errors, &ok);
                }
                if ((text = sc_string(samplers[s], @"address_w", samplerWhere, errors, NO))) {
                    BOOL ok = NO;
                    binding.addressW = (ScAddressMode)SC_LOOKUP(kAddresses, text, sc_join_where(samplerWhere, @"address_w"), @"address mode", errors, &ok);
                }
                [parsedSamplers addObject:binding];
            }
            parsedTable.samplers = parsedSamplers;
            draw.argumentTable = parsedTable;
        }
        self.drawMap[draw.ident] = draw;
        [parsed addObject:draw];
    }
    self.draws = parsed;
}

- (NSArray<ScBufferBinding *> *)parseBufferBindings:(NSArray *)entries
                                               where:(NSString *)where
                                              errors:(NSMutableArray<NSString *> *)errors
{
    NSMutableArray<ScBufferBinding *> *parsed = [NSMutableArray array];
    for (NSUInteger i = 0; entries && i < entries.count; i++) {
        NSString *entryWhere = [NSString stringWithFormat:@"%@[%lu]", where, (unsigned long)i];
        if (![entries[i] isKindOfClass:[NSDictionary class]]) {
            [errors addObject:[NSString stringWithFormat:@"%@: expected an object", entryWhere]];
            continue;
        }
        ScBufferBinding *binding = [[ScBufferBinding alloc] init];
        NSNumber *slot = sc_integer(entries[i], @"index", entryWhere, errors, YES, 0);
        binding.index = slot ? slot.unsignedIntValue : 0;
        binding.stage = sc_integer(entries[i], @"stage", entryWhere, errors, NO, 0);
        binding.resourceId = sc_string(entries[i], @"buffer", entryWhere, errors, YES);
        NSNumber *offset = sc_integer(entries[i], @"offset", entryWhere, errors, NO, 0);
        NSNumber *length = sc_integer(entries[i], @"length", entryWhere, errors, NO, 0);
        binding.offset = offset ? offset.unsignedLongLongValue : 0;
        binding.length = length ? length.unsignedLongLongValue : 0;
        [parsed addObject:binding];
    }
    return parsed;
}

- (void)crossCheck:(NSMutableArray<NSString *> *)errors
{
    for (ScPass *pass in self.passes) {
        for (NSString *pipelineId in pass.pipelineIds) {
            if (!self.pipelineMap[pipelineId]) {
                [errors addObject:[NSString stringWithFormat:@"pass \"%@\": unknown pipeline \"%@\"", pass.ident, pipelineId]];
            }
        }
        for (NSString *drawId in pass.drawIds) {
            ScDraw *draw = self.drawMap[drawId];
            if (!draw) {
                [errors addObject:[NSString stringWithFormat:@"pass \"%@\": unknown draw \"%@\"", pass.ident, drawId]];
                continue;
            }
            if (![draw.passId isEqualToString:pass.ident]) {
                [errors addObject:[NSString stringWithFormat:@"pass \"%@\": draw \"%@\" points at pass \"%@\"", pass.ident,
                                                             drawId, draw.passId]];
            }
            if (!self.pipelineMap[draw.pipelineId]) {
                [errors addObject:[NSString stringWithFormat:@"draw \"%@\": unknown pipeline \"%@\"", drawId,
                                                             draw.pipelineId]];
            } else if (![pass.pipelineIds containsObject:draw.pipelineId]) {
                [errors addObject:[NSString stringWithFormat:@"draw \"%@\": pipeline \"%@\" is not listed by pass \"%@\"", drawId,
                                                             draw.pipelineId, pass.ident]];
            }
        }
        for (ScAttachment *attachment in pass.attachments) {
            ScResource *resource = self.resourceMap[attachment.textureId];
            if (!resource) {
                [errors addObject:[NSString stringWithFormat:@"pass \"%@\": attachment slot %u references the unknown texture \"%@\"",
                                                             pass.ident, attachment.slot, attachment.textureId]];
                continue;
            }
            if (!resource.isTexture) {
                [errors addObject:[NSString stringWithFormat:@"pass \"%@\": attachment \"%@\" is a %@, not a texture", pass.ident,
                                                             attachment.textureId, resource.type]];
            }
        }
    }
    for (ScDraw *draw in self.draws) {
        if (!self.passMap[draw.passId]) {
            [errors addObject:[NSString stringWithFormat:@"draw \"%@\": unknown pass \"%@\"", draw.ident, draw.passId]];
        }
        ScArgumentTable *table = draw.argumentTable;
        NSArray<NSArray *> *lists = @[table.vertexBuffers ?: @[], table.uniformBuffers ?: @[], table.fragmentBuffers ?: @[]];
        for (NSArray *list in lists) {
            for (ScBufferBinding *binding in list) {
                ScResource *resource = self.resourceMap[binding.resourceId];
                if (!resource) {
                    [errors addObject:[NSString stringWithFormat:@"draw \"%@\": buffer binding %u references the unknown resource \"%@\"",
                                                                 draw.ident, binding.index, binding.resourceId]];
                } else if (resource.isTexture) {
                    [errors addObject:[NSString stringWithFormat:@"draw \"%@\": buffer binding %u references the texture \"%@\"",
                                                                 draw.ident, binding.index, binding.resourceId]];
                }
            }
        }
        for (ScTextureBinding *binding in table.textures) {
            ScResource *resource = self.resourceMap[binding.textureId];
            if (!resource) {
                [errors addObject:[NSString stringWithFormat:@"draw \"%@\": texture binding %u references the unknown texture \"%@\"",
                                                             draw.ident, binding.index, binding.textureId]];
            } else if (!resource.isTexture) {
                [errors addObject:[NSString stringWithFormat:@"draw \"%@\": texture binding %u references \"%@\", which is a %@",
                                                             draw.ident, binding.index, binding.textureId, resource.type]];
            }
        }
        if (draw.indexed && draw.index.bufferId.length) {
            ScResource *resource = self.resourceMap[draw.index.bufferId];
            if (!resource) {
                [errors addObject:[NSString stringWithFormat:@"draw \"%@\": index buffer references the unknown resource \"%@\"",
                                                             draw.ident, draw.index.bufferId]];
            } else if (resource.isTexture) {
                [errors addObject:[NSString stringWithFormat:@"draw \"%@\": index buffer \"%@\" is a texture", draw.ident,
                                                             draw.index.bufferId]];
            }
        }
    }
    for (NSUInteger i = 0; i < self.frames.count; i++) {
        NSDictionary *frame = self.frames[i];
        id index = frame[@"index"];
        long long value = 0;
        if (!sc_integer_value(index, &value)) {
            [errors addObject:[NSString stringWithFormat:@"manifest.frames[%lu].index: expected an integer", (unsigned long)i]];
            continue;
        }
        if (value != self.frameFirst + (long long)i) {
            [errors addObject:[NSString stringWithFormat:@"manifest.frames[%lu].index: %lld does not continue frame_first %ld",
                                                         (unsigned long)i, value, (long)self.frameFirst]];
        }
        NSArray *listed = frame[@"passes"];
        for (id item in listed) {
            if (![item isKindOfClass:[NSString class]]) {
                [errors addObject:[NSString stringWithFormat:@"manifest.frames[%lu].passes: expected strings", (unsigned long)i]];
                continue;
            }
            if (!self.passMap[item]) {
                [errors addObject:[NSString stringWithFormat:@"manifest.frames[%lu].passes: unknown pass \"%@\"",
                                                             (unsigned long)i, item]];
            }
        }
    }
    if (self.frames.count && (NSInteger)self.frames.count != self.frameCount) {
        [errors addObject:[NSString stringWithFormat:@"manifest.frames: %lu entries but capture.frame_count is %ld",
                                                     (unsigned long)self.frames.count, (long)self.frameCount]];
    }
    for (ScPass *pass in self.passes) {
        if (pass.hasFrame && !self.frames.count) {
            [errors addObject:[NSString stringWithFormat:@"pass \"%@\": frame %ld is set but manifest.frames is empty",
                                                         pass.ident, (long)pass.frame]];
        }
    }
}

- (ScResource *)resourceNamed:(NSString *)ident
{
    return self.resourceMap[ident];
}

- (ScPipeline *)pipelineNamed:(NSString *)ident
{
    return self.pipelineMap[ident];
}

- (ScPass *)passNamed:(NSString *)ident
{
    return self.passMap[ident];
}

- (ScDraw *)drawNamed:(NSString *)ident
{
    return self.drawMap[ident];
}

@end
