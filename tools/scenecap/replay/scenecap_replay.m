/* scenecap_replay: offline Metal replayer for scenecap .nvs scene packages.
 *
 * Reads a self-contained capture, rebuilds its textures, buffers and pipeline states with Metal, replays every pass
 * in manifest order and writes one PNG per pass plus index.json (schema "scenecap.replay/1").
 *
 * Exit codes: 0 everything replayed, 1 validation/render failure, 2 usage or input error, 3 replayed but at least one
 * pass is explicitly unsupported (compute, blit, unexportable attachment).
 */
#import <Foundation/Foundation.h>

#include <stdio.h>
#include <string.h>

#import "replay_metal.h"
#import "replay_model.h"
#import "replay_pkg.h"
#import "replay_pixels.h"
#import "replay_png.h"

enum {
    ScExitOk = 0,
    ScExitFailure = 1,
    ScExitUsage = 2,
    ScExitUnsupported = 3,
};

static void ScPrintUsage(FILE *stream) {
    fprintf(stream,
            "usage: scenecap_replay <package.nvs> -o <output-dir> [options]\n"
            "\n"
            "  -o, --output DIR   directory for index.json and pass_<nnn>_<slug>.png\n"
            "      --no-cache     do not read or write the pipeline cache next to the package\n"
            "      --validate-only  validate the package and manifest without touching Metal\n"
            "      --verbose      print per-pass progress and every warning\n"
            "  -h, --help         this message\n");
}

/* PNG file stem: lower case, [a-z0-9_], short enough to stay readable in a directory listing. */
static NSString *ScSlugForPass(NSString *ident) {
    NSMutableString *slug = [NSMutableString string];
    BOOL lastUnderscore = NO;
    for (NSUInteger i = 0; i < ident.length && slug.length < 32; i++) {
        unichar c = [ident characterAtIndex:i];
        if (c >= 'A' && c <= 'Z') c = (unichar)(c - 'A' + 'a');
        BOOL valid = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
        if (valid) {
            [slug appendFormat:@"%C", c];
            lastUnderscore = NO;
        } else if (!lastUnderscore && slug.length > 0) {
            [slug appendString:@"_"];
            lastUnderscore = YES;
        }
    }
    while (slug.length > 0 && [slug hasSuffix:@"_"]) {
        [slug deleteCharactersInRange:NSMakeRange(slug.length - 1, 1)];
    }
    return slug.length > 0 ? slug : @"pass";
}

static NSString *ScCacheDirectoryForPackage(NSString *packagePath) {
    NSString *directory = [packagePath stringByDeletingLastPathComponent];
    NSString *stem = [[packagePath lastPathComponent] stringByDeletingPathExtension];
    return [directory stringByAppendingPathComponent:[stem stringByAppendingPathExtension:@"scenecap-cache"]];
}

/* The replayer maps a draw's argument table from the manifest, but it only trusts that table when the capture kept
 * the shader reflection next to the MSL.  A stage without one is refused instead of bound by guesswork. */
static NSArray<NSDictionary *> *ScMissingReflectionErrors(ScScene *scene) {
    NSMutableArray<NSDictionary *> *errors = [NSMutableArray array];
    for (NSUInteger index = 0; index < scene.passes.count; index++) {
        ScPass *pass = scene.passes[index];
        if (pass.kind != ScPassKindRender) continue; /* compute and blit passes are reported as unsupported */
        for (NSString *drawId in pass.drawIds) {
            ScDraw *draw = [scene drawNamed:drawId];
            if (draw == nil) continue; /* the manifest cross-check already reported the unknown draw */
            ScPipeline *pipeline = [scene pipelineNamed:draw.pipelineId];
            if (pipeline == nil) continue;
            for (ScStage *stage in pipeline.stages) {
                if (stage.mslBlobId.length == 0 || stage.reflectionBlobId.length > 0) continue;
                [errors addObject:@{
                    @"pass" : @(index),
                    @"pass_id" : pass.ident,
                    @"draw_id" : draw.ident,
                    @"pipeline_id" : pipeline.ident,
                    @"stage" : stage.kindName,
                    @"message" : [NSString stringWithFormat:@"draw \"%@\": pipeline \"%@\" stage %@ has no reflection_blob; "
                                                              @"the argument table cannot be mapped without guessing",
                                                           draw.ident, pipeline.ident, stage.kindName],
                }];
            }
        }
    }
    return errors;
}

/* index.json is written with sorted keys so two runs of the same package produce identical bytes. */
static BOOL ScWriteIndex(NSString *outputDirectory, NSDictionary *index, NSError **error) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:index
                                                   options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys
                                                     error:error];
    if (data == nil) return NO;
    NSMutableData *terminated = [data mutableCopy];
    [terminated appendBytes:"\n" length:1];
    NSString *path = [outputDirectory stringByAppendingPathComponent:@"index.json"];
    if (![terminated writeToFile:path atomically:YES]) {
        if (error) {
            *error = ScReplayError(@"cannot write \"%@\"", path);
        }
        return NO;
    }
    return YES;
}

static int ScRun(NSArray<NSString *> *arguments) {
    NSString *packagePath = nil;
    NSString *outputDirectory = nil;
    BOOL cacheEnabled = YES;
    BOOL validateOnly = NO;
    BOOL verbose = NO;

    for (NSUInteger i = 0; i < arguments.count; i++) {
        NSString *argument = arguments[i];
        if ([argument isEqualToString:@"-h"] || [argument isEqualToString:@"--help"]) {
            ScPrintUsage(stdout);
            return ScExitOk;
        } else if ([argument isEqualToString:@"-o"] || [argument isEqualToString:@"--output"]) {
            if (++i >= arguments.count) {
                fprintf(stderr, "scenecap_replay: %s needs a directory\n", argument.UTF8String);
                return ScExitUsage;
            }
            outputDirectory = arguments[i];
        } else if ([argument hasPrefix:@"--output="]) {
            outputDirectory = [argument substringFromIndex:strlen("--output=")];
        } else if ([argument isEqualToString:@"--no-cache"]) {
            cacheEnabled = NO;
        } else if ([argument isEqualToString:@"--validate-only"]) {
            validateOnly = YES;
        } else if ([argument isEqualToString:@"--verbose"]) {
            verbose = YES;
        } else if ([argument hasPrefix:@"-"]) {
            fprintf(stderr, "scenecap_replay: unknown option \"%s\"\n", argument.UTF8String);
            ScPrintUsage(stderr);
            return ScExitUsage;
        } else if (packagePath == nil) {
            packagePath = argument;
        } else {
            fprintf(stderr, "scenecap_replay: more than one package given (\"%s\" and \"%s\")\n",
                    packagePath.UTF8String, argument.UTF8String);
            return ScExitUsage;
        }
    }
    if (packagePath == nil) {
        ScPrintUsage(stderr);
        return ScExitUsage;
    }
    if (!validateOnly && outputDirectory.length == 0) {
        fprintf(stderr, "scenecap_replay: an output directory is required (use -o)\n");
        ScPrintUsage(stderr);
        return ScExitUsage;
    }

    NSError *error = nil;
    ScPackage *package = [ScPackage openPackageAtPath:packagePath error:&error];
    if (package == nil) {
        fprintf(stderr, "scenecap_replay: %s: %s\n", packagePath.UTF8String,
                error.localizedDescription.UTF8String ?: "cannot open package");
        return ScExitUsage;
    }
    if (![package verifyHashes:&error]) {
        fprintf(stderr, "scenecap_replay: %s: %s\n", packagePath.UTF8String,
                error.localizedDescription.UTF8String ?: "package hashes do not match");
        return ScExitFailure;
    }

    NSMutableArray<NSString *> *manifestErrors = [NSMutableArray array];
    ScScene *scene = [ScScene sceneWithManifest:package.manifest errors:manifestErrors];
    if (scene == nil) {
        fprintf(stderr, "scenecap_replay: %s: the scene manifest is invalid\n", packagePath.UTF8String);
        for (NSString *message in manifestErrors) fprintf(stderr, "  %s\n", message.UTF8String);
        return ScExitUsage;
    }

    if (validateOnly) {
        NSMutableArray<NSString *> *problems = [NSMutableArray array];
        BOOL ok = [ScRenderer validateScene:scene package:package problems:problems];
        for (NSDictionary *entry in ScMissingReflectionErrors(scene)) {
            [problems addObject:entry[@"message"]];
            ok = NO;
        }
        for (NSString *message in problems) fprintf(stderr, "scenecap_replay: %s\n", message.UTF8String);
        if (!ok) return ScExitFailure;
        printf("validate-only: %s is replayable (%lu pass(es), %lu resource(s), %lu pipeline(s))\n",
               packagePath.UTF8String, (unsigned long)scene.passes.count, (unsigned long)scene.resources.count,
               (unsigned long)scene.pipelines.count);
        return ScExitOk;
    }

    NSFileManager *fileManager = [NSFileManager defaultManager];
    NSError *directoryError = nil;
    if (![fileManager createDirectoryAtPath:outputDirectory
                withIntermediateDirectories:YES
                                 attributes:nil
                                      error:&directoryError]) {
        fprintf(stderr, "scenecap_replay: cannot create \"%s\": %s\n", outputDirectory.UTF8String,
                directoryError.localizedDescription.UTF8String ?: "io error");
        return ScExitUsage;
    }

    NSMutableArray<NSDictionary *> *passEntries = [NSMutableArray array];
    NSMutableArray<NSDictionary *> *unsupported = [NSMutableArray array];
    NSMutableArray<NSDictionary *> *errors = [NSMutableArray arrayWithArray:ScMissingReflectionErrors(scene)];
    NSMutableDictionary *device = [NSMutableDictionary dictionaryWithDictionary:@{@"name" : @"unknown", @"metal_version" : @"unknown"}];
    int result = errors.count > 0 ? ScExitFailure : ScExitOk;
    for (NSDictionary *entry in errors) {
        fprintf(stderr, "scenecap_replay: %s\n", [entry[@"message"] UTF8String]);
    }

    ScRenderer *renderer = nil;
    ScRendererOptions *options = [ScRendererOptions new];
    options.cacheEnabled = cacheEnabled;
    options.cacheDirectory = cacheEnabled ? ScCacheDirectoryForPackage(packagePath) : nil;
    options.verbose = verbose;

    if (result == ScExitOk) {
        renderer = [ScRenderer rendererWithScene:scene package:package options:options error:&error];
        if (renderer == nil) {
            fprintf(stderr, "scenecap_replay: %s: %s\n", packagePath.UTF8String,
                    error.localizedDescription.UTF8String ?: "cannot create the Metal renderer");
            [errors addObject:@{@"pass" : @(-1), @"message" : error.localizedDescription ?: @"metal unavailable"}];
            result = ScExitFailure;
        } else {
            device[@"name"] = renderer.deviceName;
            device[@"metal_version"] = renderer.metalVersion;
            if (verbose) {
                printf("device: %s (%s)\n", renderer.deviceName.UTF8String, renderer.metalVersion.UTF8String);
            }
            if (![renderer prepareWithError:&error]) {
                fprintf(stderr, "scenecap_replay: %s\n", error.localizedDescription.UTF8String);
                [errors addObject:@{@"pass" : @(-1), @"message" : error.localizedDescription ?: @"preparation failed"}];
                result = ScExitFailure;
            }
        }
    }

    NSMutableSet<NSString *> *usedSlugs = [NSMutableSet set];
    if (result == ScExitOk) {
        for (NSUInteger index = 0; index < scene.passes.count; index++) {
            ScPass *pass = scene.passes[index];
            if (pass.kind != ScPassKindRender) {
                NSString *reason = [NSString stringWithFormat:@"%@ passes are not replayed yet", pass.kindName];
                [unsupported addObject:@{@"pass" : @(index), @"reason" : reason, @"pass_id" : pass.ident}];
                if (verbose) printf("pass %03lu %s: unsupported (%s)\n", (unsigned long)index,
                                    pass.ident.UTF8String, reason.UTF8String);
                result = ScExitUnsupported;
                continue;
            }
            NSUInteger draws = 0;
            error = nil;
            ScPassImage *image = [renderer renderPass:pass drawCount:&draws error:&error];
            if (image == nil && error != nil) {
                fprintf(stderr, "scenecap_replay: pass \"%s\": %s\n", pass.ident.UTF8String,
                        error.localizedDescription.UTF8String ?: "render failed");
                [errors addObject:@{@"pass" : @(index),
                                    @"pass_id" : pass.ident,
                                    @"message" : error.localizedDescription ?: @"render failed"}];
                result = ScExitFailure;
                break;
            }
            if (image == nil) {
                [unsupported addObject:@{@"pass" : @(index),
                                         @"reason" : @"no colour attachment to export",
                                         @"pass_id" : pass.ident}];
                if (verbose) printf("pass %03lu %s: unsupported (no colour attachment)\n", (unsigned long)index,
                                    pass.ident.UTF8String);
                result = ScExitUnsupported;
                continue;
            }
            NSString *slug = ScSlugForPass(pass.ident);
            if ([usedSlugs containsObject:slug]) {
                slug = [NSString stringWithFormat:@"%@_%lu", slug, (unsigned long)index];
            }
            [usedSlugs addObject:slug];
            NSString *pngName = [NSString stringWithFormat:@"pass_%03lu_%@.png", (unsigned long)index, slug];
            NSString *pngPath = [outputDirectory stringByAppendingPathComponent:pngName];
            char why[256] = {0};
            if (!sc_png_write_rgba8(pngPath.fileSystemRepresentation, image.rgba.bytes, image.width, image.height, why,
                                    sizeof(why))) {
                fprintf(stderr, "scenecap_replay: pass \"%s\": cannot write \"%s\": %s\n", pass.ident.UTF8String,
                        pngPath.UTF8String, why);
                [errors addObject:@{@"pass" : @(index), @"pass_id" : pass.ident, @"message" : [NSString stringWithUTF8String:why]}];
                result = ScExitFailure;
                break;
            }
            sc_rgba8_stats stats;
            sc_rgba8_stats_compute(image.rgba.bytes, (size_t)image.width * (size_t)image.height, &stats);
            [passEntries addObject:@{
                @"index" : @(index),
                @"kind" : @"render",
                @"slug" : slug,
                @"frame" : pass.hasFrame ? @(pass.frame) : [NSNull null],
                @"width" : @(image.width),
                @"height" : @(image.height),
                @"pixel_format" : pass.pixelFormat.length > 0 ? pass.pixelFormat : @"unknown",
                @"encoding" : @"srgb",
                @"png" : pngName,
                @"draws" : @(draws),
                @"status" : @"ok",
                /* Per-pass statistics of the exported PNG bytes, so a report can review a pass without decoding it. */
                @"metrics" : @{
                    @"pixels" : @(stats.count),
                    @"min" : @[@(stats.lo[0]), @(stats.lo[1]), @(stats.lo[2]), @(stats.lo[3])],
                    @"max" : @[@(stats.hi[0]), @(stats.hi[1]), @(stats.hi[2]), @(stats.hi[3])],
                    @"mean" : @[@(round(stats.mean[0] * 10000.0) / 10000.0),
                                @(round(stats.mean[1] * 10000.0) / 10000.0),
                                @(round(stats.mean[2] * 10000.0) / 10000.0),
                                @(round(stats.mean[3] * 10000.0) / 10000.0)],
                },
            }];
            if (verbose) {
                printf("pass %03lu %s: %ux%u %s, %lu draw(s) -> %s\n", (unsigned long)index, pass.ident.UTF8String,
                       image.width, image.height, pass.pixelFormat.UTF8String, (unsigned long)draws, pngName.UTF8String);
            }
        }
    }

    if (renderer != nil) {
        NSError *cacheError = nil;
        if (![renderer flushCacheWithError:&cacheError]) {
            fprintf(stderr, "scenecap_replay: warning: %s\n", cacheError.localizedDescription.UTF8String);
        }
        for (NSString *warning in renderer.warnings) {
            if (verbose) fprintf(stderr, "scenecap_replay: warning: %s\n", warning.UTF8String);
        }
    }

    NSDictionary *index = @{
        @"schema" : @"scenecap.replay/1",
        @"package" : @{
            @"path" : packagePath,
            @"sha256" : package.sha256Hex ?: @"",
            @"captured_frame" : @(scene.frameFirst),
            @"game" : scene.game ?: @"unknown",
            @"build" : scene.buildVersion ?: @"unknown",
        },
        @"device" : device,
        @"passes" : passEntries,
        @"unsupported" : unsupported,
        @"errors" : errors,
    };
    NSError *indexError = nil;
    if (!ScWriteIndex(outputDirectory, index, &indexError)) {
        fprintf(stderr, "scenecap_replay: %s\n", indexError.localizedDescription.UTF8String);
        return ScExitFailure;
    }
    if (result == ScExitOk) {
        printf("replayed %lu pass(es) from %s into %s\n", (unsigned long)passEntries.count, packagePath.UTF8String,
               outputDirectory.UTF8String);
    }
    return result;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSMutableArray<NSString *> *arguments = [NSMutableArray arrayWithCapacity:(NSUInteger)MAX(argc - 1, 0)];
        for (int i = 1; i < argc; i++) {
            [arguments addObject:[NSString stringWithUTF8String:argv[i]] ?: @""];
        }
        return ScRun(arguments);
    }
}
