/* Metal side of the offline replayer: resources, pipeline compilation, pass encoding and read-back.
 *
 * Nothing here knows about the game or the NVN/Metal port: every object is created from the manifest and the blob
 * payloads of one .nvs package.  Failures always name the pass, draw, pipeline or resource they belong to.
 */
#ifndef SCENECAP_REPLAY_METAL_H
#define SCENECAP_REPLAY_METAL_H

#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#import "replay_model.h"
#import "replay_pkg.h"

NS_ASSUME_NONNULL_BEGIN

@interface ScPassImage : NSObject
@property (nonatomic) unsigned width;
@property (nonatomic) unsigned height;
/* RGBA8, sRGB-encoded colour channels, linear alpha, top-left origin. */
@property (nonatomic, strong) NSData *rgba;
@end

@interface ScRendererOptions : NSObject
@property (nonatomic) BOOL cacheEnabled;
@property (nonatomic, copy, nullable) NSString *cacheDirectory;
@property (nonatomic) BOOL verbose;
@end

@interface ScRenderer : NSObject
@property (nonatomic, strong, readonly, nullable) id<MTLDevice> device;
@property (nonatomic, copy, readonly) NSString *deviceName;
@property (nonatomic, copy, readonly) NSString *metalVersion;
@property (nonatomic, copy, readonly) NSArray<NSString *> *warnings;
@property (nonatomic, readonly) NSUInteger pipelineCount;
@property (nonatomic, readonly) NSUInteger archiveHits;
@property (nonatomic, readonly) NSUInteger archiveRejects;

+ (nullable instancetype)rendererWithScene:(ScScene *)scene
                                   package:(ScPackage *)package
                                   options:(ScRendererOptions *)options
                                     error:(NSError **)error;

/* Package/manifest-only validation used by --validate-only: pixel formats, texture slice layouts, attachment
 * consistency and whether every referenced blob decodes to the size the manifest implies.  No Metal device. */
+ (BOOL)validateScene:(ScScene *)scene
              package:(ScPackage *)package
             problems:(NSMutableArray<NSString *> *)problems;

/* Creates every texture and buffer the scene references and compiles every pipeline the render passes use.
 * Fatal problems (missing blob, unknown format, MSL diagnostics) are reported before any pixel is produced. */
- (BOOL)prepareWithError:(NSError **)error;

/* Encodes one render pass and reads its main colour attachment back.  Returns nil with *error set on failure, and
 * nil without *error when the pass has no colour attachment to export (the caller records it as unsupported). */
- (nullable ScPassImage *)renderPass:(ScPass *)pass drawCount:(NSUInteger *)drawCount error:(NSError **)error;

/* Serialises the pipeline archive when caching is enabled.  Failures are warnings, never fatal. */
- (BOOL)flushCacheWithError:(NSError **)error;

@end

NS_ASSUME_NONNULL_END

#endif /* SCENECAP_REPLAY_METAL_H */
