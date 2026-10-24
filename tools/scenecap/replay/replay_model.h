/* Typed, validated view of the scenecap scene manifest.
 *
 * The manifest contract lives in tools/scenecap/README.md and is enforced by tools/scenecap/python/scenecap_pkg.py.
 * The replay tool re-reads the same JSON but keeps Metal-facing types so the renderer never has to interpret raw
 * dictionaries.  Every rejection names the manifest path (and the pass/draw/pipeline id where there is one).
 */
#ifndef SCENECAP_REPLAY_MODEL_H
#define SCENECAP_REPLAY_MODEL_H

#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/* Small fixed-size vectors; Objective-C properties cannot have array types. */
typedef struct ScGrid {
    uint32_t v[3];
} ScGrid;

typedef struct ScVec4 {
    double v[4];
} ScVec4;

typedef struct ScInt4 {
    long v[4];
} ScInt4;

typedef NS_ENUM(NSInteger, ScPassKind) {
    ScPassKindRender = 0,
    ScPassKindCompute,
    ScPassKindBlit,
};

typedef NS_ENUM(NSInteger, ScLoadAction) {
    ScLoadActionLoad = 0,
    ScLoadActionClear,
    ScLoadActionDontCare,
};

typedef NS_ENUM(NSInteger, ScStoreAction) {
    ScStoreActionStore = 0,
    ScStoreActionDontCare,
};

typedef NS_ENUM(NSInteger, ScPrimitiveType) {
    ScPrimitivePoint = 0,
    ScPrimitiveLine,
    ScPrimitiveLineStrip,
    ScPrimitiveTriangle,
    ScPrimitiveTriangleStrip,
};

typedef NS_ENUM(NSInteger, ScCompareFunction) {
    ScCompareNever = 0,
    ScCompareLess,
    ScCompareEqual,
    ScCompareLessEqual,
    ScCompareGreater,
    ScCompareNotEqual,
    ScCompareGreaterEqual,
    ScCompareAlways,
};

typedef NS_ENUM(NSInteger, ScCullMode) {
    ScCullNone = 0,
    ScCullFront,
    ScCullBack,
};

typedef NS_ENUM(NSInteger, ScFillMode) {
    ScFillSolid = 0,
    ScFillLines,
    ScFillPoints,
};

typedef NS_ENUM(NSInteger, ScWinding) {
    ScWindingCW = 0,
    ScWindingCCW,
};

typedef NS_ENUM(NSInteger, ScBlendOperation) {
    ScBlendAdd = 0,
    ScBlendSubtract,
    ScBlendReverseSubtract,
    ScBlendMin,
    ScBlendMax,
};

typedef NS_ENUM(NSInteger, ScBlendFactor) {
    ScBlendZero = 0,
    ScBlendOne,
    ScBlendSourceColor,
    ScBlendOneMinusSourceColor,
    ScBlendDestinationColor,
    ScBlendOneMinusDestinationColor,
    ScBlendSourceAlpha,
    ScBlendOneMinusSourceAlpha,
    ScBlendDestinationAlpha,
    ScBlendOneMinusDestinationAlpha,
    ScBlendSourceAlphaSaturated,
    ScBlendBlendColor,
    ScBlendOneMinusBlendColor,
    ScBlendBlendAlpha,
    ScBlendOneMinusBlendAlpha,
};

typedef NS_ENUM(NSInteger, ScStageKind) {
    ScStageVertex = 0,
    ScStageFragment,
    ScStageKernel,
    ScStageObject,
    ScStageMesh,
    ScStageTile,
};

typedef NS_ENUM(NSInteger, ScTextureType) {
    ScTexture1D = 0,
    ScTexture2D,
    ScTexture3D,
    ScTextureCube,
    ScTexture2DArray,
    ScTextureCubeArray,
    ScTexture2DMultisample,
};

typedef NS_ENUM(NSInteger, ScFilter) {
    ScFilterNearest = 0,
    ScFilterLinear,
};

typedef NS_ENUM(NSInteger, ScAddressMode) {
    ScAddressClampToEdge = 0,
    ScAddressRepeat,
    ScAddressMirrorRepeat,
    ScAddressClampToZero,
    ScAddressMirrorClampToEdge,
};

typedef NS_OPTIONS(NSUInteger, ScResourceUsage) {
    ScResourceUsageShaderRead = 1u << 0,
    ScResourceUsageShaderWrite = 1u << 1,
    ScResourceUsageRenderTarget = 1u << 2,
};

@interface ScResource : NSObject
@property (nonatomic, copy) NSString *ident;
@property (nonatomic, copy) NSString *type; /* "texture" or "buffer" */
@property (nonatomic, copy) NSString *blobId;
@property (nonatomic) uint64_t blobOffset;
@property (nonatomic) uint64_t blobSize;
@property (nonatomic, copy) NSString *pixelFormat;
@property (nonatomic) unsigned width;
@property (nonatomic) unsigned height;
@property (nonatomic) unsigned depth;
@property (nonatomic) unsigned mipLevels;
@property (nonatomic) unsigned arrayLayers;
@property (nonatomic) unsigned sampleCount;
@property (nonatomic) ScTextureType textureType;
@property (nonatomic) unsigned mipFirst;
@property (nonatomic) unsigned mipCount;
@property (nonatomic) unsigned layerFirst;
@property (nonatomic) unsigned layerCount;
@property (nonatomic) ScResourceUsage usage;
@property (nonatomic, readonly) BOOL isTexture;
@end

@interface ScStage : NSObject
@property (nonatomic) ScStageKind kind;
@property (nonatomic, copy, nullable) NSString *mslBlobId;
@property (nonatomic, copy, nullable) NSString *reflectionBlobId;
@property (nonatomic, copy, nullable) NSString *entry;
@property (nonatomic, readonly) NSString *kindName;
@end

@interface ScBlendAttachment : NSObject
@property (nonatomic) unsigned index;
@property (nonatomic) BOOL enabled;
@property (nonatomic) ScBlendOperation rgbOperation;
@property (nonatomic) ScBlendOperation alphaOperation;
@property (nonatomic) ScBlendFactor rgbSource;
@property (nonatomic) ScBlendFactor rgbDestination;
@property (nonatomic) ScBlendFactor alphaSource;
@property (nonatomic) ScBlendFactor alphaDestination;
@end

@interface ScVertexAttribute : NSObject
@property (nonatomic) unsigned location;
@property (nonatomic) unsigned format; /* MTLVertexFormat value recorded by the capture */
@property (nonatomic) unsigned offset;
@property (nonatomic) unsigned bufferIndex;
@end

@interface ScVertexLayout : NSObject
@property (nonatomic) unsigned bufferIndex;
@property (nonatomic) unsigned stride;
@property (nonatomic) unsigned stepFunction; /* MTLVertexStepFunction value */
@property (nonatomic) unsigned stepRate;
@end

@interface ScPipeline : NSObject
@property (nonatomic, copy) NSString *ident;
@property (nonatomic, copy) NSArray<ScVertexAttribute *> *vertexAttributes;
@property (nonatomic, copy) NSArray<ScVertexLayout *> *vertexLayouts;
@property (nonatomic, copy) NSArray<ScStage *> *stages;
@property (nonatomic) BOOL blendEnabled;
@property (nonatomic, copy) NSArray<ScBlendAttachment *> *blendAttachments;
@property (nonatomic) BOOL depthTest;
@property (nonatomic) BOOL depthWrite;
@property (nonatomic) ScCompareFunction depthCompare;
@property (nonatomic) ScCullMode cull;
@property (nonatomic) ScFillMode fill;
@property (nonatomic) double depthBias;
@property (nonatomic) double depthSlopeScale;
@property (nonatomic) double depthClamp;
@property (nonatomic) BOOL hasViewport;
@property (nonatomic) ScVec4 viewport;
@property (nonatomic) BOOL hasScissor;
@property (nonatomic) ScInt4 scissor;
@property (nonatomic) ScWinding winding;
@property (nonatomic) ScPrimitiveType primitiveType;
@end

@interface ScAttachment : NSObject
@property (nonatomic) unsigned slot;
@property (nonatomic, copy) NSString *textureId;
@property (nonatomic) unsigned level;
@property (nonatomic) unsigned layer;
@property (nonatomic) ScLoadAction load;
@property (nonatomic) ScStoreAction store;
/* Clear colour for colour attachments; v[0] doubles as the depth clear value. */
@property (nonatomic) ScVec4 clearColor;
@property (nonatomic) double clearDepth;
@property (nonatomic) unsigned clearStencil;
@end

@interface ScBufferBinding : NSObject
@property (nonatomic) unsigned index;
@property (nonatomic, strong) NSNumber *stage; /* nil: the capture recorded no stage */
@property (nonatomic, copy) NSString *resourceId;
@property (nonatomic) uint64_t offset;
@property (nonatomic) uint64_t length;
@end

@interface ScTextureBinding : NSObject
@property (nonatomic) unsigned index;
@property (nonatomic, strong) NSNumber *stage; /* nil: the capture recorded no stage */
@property (nonatomic, copy) NSString *textureId;
@end

@interface ScSamplerBinding : NSObject
@property (nonatomic) unsigned index;
@property (nonatomic, strong) NSNumber *stage; /* nil: the capture recorded no stage */
@property (nonatomic) ScFilter minFilter;
@property (nonatomic) ScFilter magFilter;
@property (nonatomic) ScAddressMode addressU;
@property (nonatomic) ScAddressMode addressV;
@property (nonatomic) ScAddressMode addressW;
@end

@interface ScArgumentTable : NSObject
@property (nonatomic, copy) NSArray<ScBufferBinding *> *vertexBuffers;
@property (nonatomic, copy) NSArray<ScBufferBinding *> *uniformBuffers;
@property (nonatomic, copy) NSArray<ScBufferBinding *> *fragmentBuffers;
@property (nonatomic, copy) NSArray<ScTextureBinding *> *textures;
@property (nonatomic, copy) NSArray<ScSamplerBinding *> *samplers;
@end

@interface ScIndexBuffer : NSObject
@property (nonatomic) BOOL wide; /* YES for uint32 indices */
@property (nonatomic) uint64_t start;
@property (nonatomic) uint64_t count;
@property (nonatomic) uint64_t offset;
@property (nonatomic, copy, nullable) NSString *bufferId;
@end

@interface ScDraw : NSObject
@property (nonatomic, copy) NSString *ident;
@property (nonatomic, copy) NSString *passId;
@property (nonatomic, copy) NSString *pipelineId;
@property (nonatomic) uint64_t vertexStart;
@property (nonatomic) uint64_t vertexCount;
@property (nonatomic) uint64_t instanceCount;
@property (nonatomic) BOOL hasPrimitiveType;
@property (nonatomic) ScPrimitiveType primitiveType;
@property (nonatomic, strong, nullable) ScIndexBuffer *index;
@property (nonatomic, strong, nullable) ScArgumentTable *argumentTable;
@property (nonatomic, readonly) BOOL indexed;
@end

@interface ScDispatch : NSObject
@property (nonatomic, copy) NSString *ident;
@property (nonatomic, copy) NSString *passId;
@property (nonatomic, copy) NSString *pipelineId;
@property (nonatomic) ScGrid grid;
@property (nonatomic, strong, nullable) ScArgumentTable *argumentTable;
@end

@interface ScPass : NSObject
@property (nonatomic, copy) NSString *ident;
@property (nonatomic) BOOL hasFrame;
@property (nonatomic) NSInteger frame;
@property (nonatomic) ScPassKind kind;
@property (nonatomic) unsigned width;
@property (nonatomic) unsigned height;
@property (nonatomic, copy) NSString *pixelFormat;
@property (nonatomic, copy) NSArray<ScAttachment *> *attachments;
@property (nonatomic, copy) NSArray<NSString *> *pipelineIds;
@property (nonatomic, copy) NSArray<NSString *> *drawIds;
@property (nonatomic, readonly) NSString *kindName;
@end

@interface ScScene : NSObject
@property (nonatomic, strong) NSDictionary<NSString *, id> *manifest;
@property (nonatomic, copy) NSString *game;
@property (nonatomic, copy) NSString *gameVersion;
@property (nonatomic, copy) NSString *buildVersion;
@property (nonatomic, copy) NSString *titleId;
@property (nonatomic, copy) NSString *capturedAt;
@property (nonatomic, copy) NSString *gpu;
@property (nonatomic, copy) NSString *macOSVersion;
@property (nonatomic, copy) NSString *metalVersion;
@property (nonatomic) NSInteger frameFirst;
@property (nonatomic) NSInteger frameCount;
@property (nonatomic, copy) NSArray<ScResource *> *resources;
@property (nonatomic, copy) NSArray<ScPipeline *> *pipelines;
@property (nonatomic, copy) NSArray<ScPass *> *passes;
@property (nonatomic, copy, readonly) NSArray<ScDispatch *> *dispatches;
@property (nonatomic, copy) NSArray<ScDraw *> *draws;
@property (nonatomic, copy) NSArray<NSDictionary<NSString *, id> *> *frames;

/* Parses and cross-checks the manifest; returns nil and appends human readable errors when it cannot. */
+ (nullable instancetype)sceneWithManifest:(NSDictionary<NSString *, id> *)manifest
                                    errors:(NSMutableArray<NSString *> *)errors;

- (nullable ScResource *)resourceNamed:(NSString *)ident;
- (nullable ScPipeline *)pipelineNamed:(NSString *)ident;
- (nullable ScPass *)passNamed:(NSString *)ident;
- (nullable ScDraw *)drawNamed:(NSString *)ident;

@end

NS_ASSUME_NONNULL_END

#endif /* SCENECAP_REPLAY_MODEL_H */
