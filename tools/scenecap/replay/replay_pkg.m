/* See replay_pkg.h. */
#import "replay_pkg.h"

#import <stdarg.h>

#include "replay_zstd.h"

NSString *const ScReplayErrorDomain = @"scenecap.replay";

NSError *ScReplayError(NSString *format, ...)
{
    va_list args;
    NSString *message;
    va_start(args, format);
    message = [[NSString alloc] initWithFormat:format arguments:args];
    va_end(args);
    return [NSError errorWithDomain:ScReplayErrorDomain
                               code:1
                           userInfo:@{NSLocalizedDescriptionKey : message}];
}

static NSError *sc_error_from_nvs(const nvs_error *err, NSString *context)
{
    NSString *detail = [NSString stringWithUTF8String:err->message];
    if (!detail) detail = @"unknown container error";
    if (context.length) return ScReplayError(@"%@: %@", context, detail);
    return ScReplayError(@"%@", detail);
}

static NSString *sc_nvs_reason(const nvs_error *err)
{
    NSString *detail = [NSString stringWithUTF8String:err->message];
    return detail ?: @"unknown error";
}

@implementation ScChunk
- (NSString *)kindName
{
    const char *name = nvs_kind_name(self.kind);
    return name ? [NSString stringWithUTF8String:name] : @"unknown";
}
- (BOOL)compressed
{
    return (self.flags & NVS_CHUNK_FLAG_ZSTD) != 0;
}
@end

@interface ScPackage () {
    nvs_reader *_reader;
}
@end

@implementation ScPackage

+ (instancetype)openPackageAtPath:(NSString *)path error:(NSError **)error
{
    nvs_error err;
    nvs_reader *reader = NULL;
    int status;
    if (!path.length) {
        if (error) *error = ScReplayError(@"no package path given");
        return nil;
    }
    memset(&err, 0, sizeof err);
    status = nvs_read_open(path.fileSystemRepresentation, &reader, &err);
    if (status != NVS_OK || !reader) {
        if (error) *error = sc_error_from_nvs(&err, path.lastPathComponent);
        return nil;
    }
    /* Real zstd frames need the bridge; without it every compressed block fails loudly, never silently. */
    nvs_read_set_decompressor(reader, replay_zstd_hook, NULL);
    ScPackage *package = [[ScPackage alloc] init];
    package->_reader = reader;
    package->_path = [path copy];
    package->_formatMajor = nvs_read_header(reader)->major;
    package->_formatMinor = nvs_read_header(reader)->minor;
    /* The reader now belongs to the package: every later error path returns nil and lets -dealloc close it once. */

    NSMutableArray<ScChunk *> *chunks = [NSMutableArray array];
    size_t count = nvs_read_chunk_count(reader);
    for (size_t index = 0; index < count; index++) {
        nvs_chunk_info info;
        memset(&info, 0, sizeof info);
        memset(&err, 0, sizeof err);
        if (nvs_read_chunk_at(reader, index, &info, &err) != NVS_OK) {
            if (error) *error = sc_error_from_nvs(&err, @"chunk directory");
            return nil;
        }
        ScChunk *chunk = [[ScChunk alloc] init];
        chunk.ident = [NSString stringWithUTF8String:info.id];
        chunk.kind = info.kind;
        chunk.flags = info.flags;
        chunk.storedSize = info.stored_size;
        chunk.rawSize = info.raw_size;
        if (!chunk.ident) {
            if (error) *error = ScReplayError(@"chunk %zu has a non-UTF-8 id", index);
            return nil;
        }
        [chunks addObject:chunk];
    }
    package->_chunks = chunks;

    char *json = NULL;
    size_t json_len = 0;
    memset(&err, 0, sizeof err);
    status = nvs_read_manifest(reader, &json, &json_len, &err);
    if (status != NVS_OK) {
        NSString *reason = sc_nvs_reason(&err);
        NSString *zstd = replay_zstd_available() ? nil : [NSString stringWithUTF8String:replay_zstd_unavailable_reason()];
        /* nvs_read_manifest already prefixes "manifest:"; only the missing decoder capability is added here. */
        if (error) {
            *error = zstd ? ScReplayError(@"%@ (%@)", reason, zstd) : ScReplayError(@"%@", reason);
        }
        free(json);
        return nil;
    }
    NSData *manifestData = [NSData dataWithBytes:json length:json_len];
    free(json);
    NSError *jsonError = nil;
    id root = [NSJSONSerialization JSONObjectWithData:manifestData options:0 error:&jsonError];
    if (![root isKindOfClass:[NSDictionary class]]) {
        if (error) *error = ScReplayError(@"manifest is not a JSON object: %@", jsonError.localizedDescription ?: @"parse failure");
        return nil;
    }
    NSDictionary *envelope = (NSDictionary *)root;
    NSDictionary *nvs = envelope[@"nvs"];
    if (![nvs isKindOfClass:[NSDictionary class]]) {
        if (error) *error = ScReplayError(@"manifest envelope is missing the \"nvs\" object");
        return nil;
    }
    NSString *format = nvs[@"format"];
    if (![format isKindOfClass:[NSString class]] || ![format isEqualToString:@"scenecap"]) {
        if (error) *error = ScReplayError(@"manifest envelope format is %@, expected \"scenecap\"", format ?: @"missing");
        return nil;
    }
    NSDictionary *manifest = envelope[@"manifest"];
    if (![manifest isKindOfClass:[NSDictionary class]]) {
        if (error) *error = ScReplayError(@"manifest envelope is missing the \"manifest\" object");
        return nil;
    }
    package->_envelope = envelope;
    package->_manifest = manifest;

    /* Package digest: SHA-256 of the whole .nvs file, recorded in index.json. */
    NSData *fileData = [NSData dataWithContentsOfFile:path options:NSDataReadingMappedIfSafe error:&jsonError];
    if (!fileData) {
        if (error) *error = ScReplayError(@"cannot re-read %@: %@", path, jsonError.localizedDescription ?: @"io error");
        return nil;
    }
    uint8_t digest[32];
    char hex[65];
    nvs_sha256_digest(fileData.bytes, (size_t)fileData.length, digest);
    nvs_hex(digest, sizeof digest, hex);
    package->_sha256Hex = [NSString stringWithUTF8String:hex];
    return package;
}

- (void)dealloc
{
    if (_reader) nvs_read_close(_reader);
}

- (ScChunk *)chunkNamed:(NSString *)chunkId
{
    for (ScChunk *chunk in _chunks) {
        if ([chunk.ident isEqualToString:chunkId]) return chunk;
    }
    return nil;
}

- (BOOL)verifyHashes:(NSError **)error
{
    nvs_error err;
    memset(&err, 0, sizeof err);
    if (nvs_read_verify_hashes(_reader, &err) != NVS_OK) {
        if (error) *error = sc_error_from_nvs(&err, @"package");
        return NO;
    }
    return YES;
}

- (NSData *)payloadOfChunk:(NSString *)chunkId error:(NSError **)error
{
    nvs_error err;
    void *data = NULL;
    size_t len = 0;
    int status;
    if (![self chunkNamed:chunkId]) {
        if (error) *error = ScReplayError(@"package has no chunk \"%@\"", chunkId);
        return nil;
    }
    memset(&err, 0, sizeof err);
    status = nvs_read_blob(_reader, chunkId.fileSystemRepresentation, &data, &len, &err);
    if (status != NVS_OK) {
        NSString *reason = sc_nvs_reason(&err);
        NSString *zstd = replay_zstd_available() ? nil : [NSString stringWithUTF8String:replay_zstd_unavailable_reason()];
        if (error) {
            *error = zstd ? ScReplayError(@"chunk \"%@\": %@ (%@)", chunkId, reason, zstd)
                          : ScReplayError(@"chunk \"%@\": %@", chunkId, reason);
        }
        free(data);
        return nil;
    }
    NSData *payload = [NSData dataWithBytes:data length:len];
    free(data);
    return payload;
}

@end
