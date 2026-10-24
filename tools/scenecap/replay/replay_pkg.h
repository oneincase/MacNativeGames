/* Read side of a .nvs scene package for the replay tool.
 *
 * Wraps the container reader from tools/scenecap/format/nvs_format.c, installs the optional libzstd bridge and
 * exposes the manifest plus decoded chunk payloads.  Every failure carries the chunk id (or the container detail)
 * so the replayer can name exactly what is missing instead of substituting empty data.
 */
#ifndef SCENECAP_REPLAY_PKG_H
#define SCENECAP_REPLAY_PKG_H

#import <Foundation/Foundation.h>

#include "nvs_format.h"

NS_ASSUME_NONNULL_BEGIN

extern NSString *const ScReplayErrorDomain;

/* Convenience: an NSError in ScReplayErrorDomain with a human readable reason. */
NSError *ScReplayError(NSString *format, ...);

@interface ScChunk : NSObject
@property (nonatomic, copy) NSString *ident;
@property (nonatomic) nvs_kind kind;
@property (nonatomic) uint32_t flags;
@property (nonatomic) uint64_t storedSize;
@property (nonatomic) uint64_t rawSize;
@property (nonatomic, readonly) BOOL compressed;
/* Kind name as written by the format module ("json", "msl", "reflection", "texture", "buffer", "binary"). */
@property (nonatomic, readonly) NSString *kindName;
@end

@interface ScPackage : NSObject
@property (nonatomic, copy, readonly) NSString *path;
@property (nonatomic, copy, readonly) NSString *sha256Hex;
@property (nonatomic, readonly) uint16_t formatMajor;
@property (nonatomic, readonly) uint16_t formatMinor;
@property (nonatomic, strong, readonly) NSArray<ScChunk *> *chunks;
/* Envelope as stored: {"nvs": {...}, "chunks": [...], "manifest": {...}}. */
@property (nonatomic, strong, readonly) NSDictionary<NSString *, id> *envelope;
/* The scene manifest section of the envelope. */
@property (nonatomic, strong, readonly) NSDictionary<NSString *, id> *manifest;

+ (nullable instancetype)openPackageAtPath:(NSString *)path error:(NSError **)error;

/* Decoded, hash-verified payload of one chunk; nil with a reason that names the chunk. */
- (nullable NSData *)payloadOfChunk:(NSString *)chunkId error:(NSError **)error;

/* Looks a chunk up by id. */
- (nullable ScChunk *)chunkNamed:(NSString *)chunkId;

/* Verifies the SHA-256 recorded for every stored chunk and for the manifest. */
- (BOOL)verifyHashes:(NSError **)error;

@end

NS_ASSUME_NONNULL_END

#endif /* SCENECAP_REPLAY_PKG_H */
