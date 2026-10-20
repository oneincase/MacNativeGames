/* NieR:Automata launcher: one window that starts the runtime in the work folder named by Info.plist's NIERWorkFolder,
   so nothing large is copied into the bundle (the game's extracted data, rewritten modules and caches stay outside).
   The runtime needs its GPU submission switches (see nvn_mtl.m nogpu(): the NVN replay keeps submission off by default
   because unproven GPU work kernel-panicked macOS in another title) and the mid-pass colour clear path, so the launcher
   sets the same switches the port was tested with, redirects the runtime's output to <work>/logs/nier.log and execs
   build/nier-native rewritten/v103 boot. */
#import <Cocoa/Cocoa.h>
#include <errno.h>
#include <string.h>
#include <unistd.h>

static NSString *work_folder(void) {
    const char *e = getenv("NIER_WORK");
    return e && *e ? @(e) : [NSBundle.mainBundle objectForInfoDictionaryKey:@"NIERWorkFolder"];
}

@interface Launcher : NSObject <NSApplicationDelegate>
@property NSWindow *window;
@property NSTextField *status;
@property NSButton *play;
@property NSString *work;
@end

@implementation Launcher

- (void)alert:(NSString *)text {
    NSAlert *a = [NSAlert new];
    a.messageText = @"NieR:Automata";
    a.informativeText = text;
    [a runModal];
}

- (void)quitNow:(id)sender { (void)sender; [NSApp terminate:nil]; }

- (void)play:(id)sender {
    (void)sender;
    NSString *work = self.work;
    NSString *binary = [work stringByAppendingPathComponent:@"build/nier-native"];
    NSString *code = [work stringByAppendingPathComponent:@"rewritten/v103"];
    if (![NSFileManager.defaultManager fileExistsAtPath:binary]) { [self alert:[NSString stringWithFormat:@"runtime missing:\n%@\n\nBuild it with make runtime WORK=…", binary]]; return; }
    if (![NSFileManager.defaultManager fileExistsAtPath:code]) { [self alert:[NSString stringWithFormat:@"rewritten modules missing:\n%@", code]]; return; }

    NSString *logs = [work stringByAppendingPathComponent:@"logs"], *profile = [work stringByAppendingPathComponent:@"user-data"];
    [NSFileManager.defaultManager createDirectoryAtPath:logs withIntermediateDirectories:YES attributes:nil error:nil];
    [NSFileManager.defaultManager createDirectoryAtPath:profile withIntermediateDirectories:YES attributes:nil error:nil];

    /* The GPU switches and the mid-pass clear path the port was tested with (the runtime also defaults the latter). */
    setenv("TOTK_GPU_ALLOW_SUBMIT", "1", 1);
    setenv("TOTK_GPU_MIDCLEAR", "1", 1);
    setenv("TOTK_GPU_BUDGET_MB", "5000", 1);
    setenv("DM_PRESENT_LINK_FALLBACK", "1", 1);
    setenv("TOTK_SLOT_OWNER", "user", 1);
    setenv("TOTK_SLOT_PURPOSE", "player", 1);
    setenv("TOTK_PROFILE_DIR", profile.fileSystemRepresentation, 1);
    unsetenv("TOTK_HEADLESS");
    unsetenv("TOTK_PROBE_GPU");
    NSString *preferred = NSLocale.preferredLanguages.firstObject;
    if (preferred) setenv("TOTK_LOCALE", preferred.UTF8String, 1);

    /* One diagnostic command file per launch: `echo "shot /tmp/a.png" >> <work>/logs/input.txt`. */
    NSString *cmds = [logs stringByAppendingPathComponent:@"input.txt"];
    FILE *cf = fopen(cmds.fileSystemRepresentation, "w");
    if (cf) fclose(cf);
    setenv("TOTK_INPUT_FILE", cmds.fileSystemRepresentation, 1);

    if (chdir(work.fileSystemRepresentation)) { [self alert:[NSString stringWithFormat:@"cannot enter %@", work]]; return; }   // the runtime reads extracted/ and rewritten/ relative to the work folder

    NSString *log = [logs stringByAppendingPathComponent:@"nier.log"], *prev = [logs stringByAppendingPathComponent:@"nier.previous.log"];
    [NSFileManager.defaultManager removeItemAtPath:prev error:nil];
    [NSFileManager.defaultManager moveItemAtPath:log toPath:prev error:nil];
    FILE *lf = fopen(log.fileSystemRepresentation, "w");
    if (lf) { dup2(fileno(lf), STDOUT_FILENO); dup2(fileno(lf), STDERR_FILENO); fclose(lf); }
    fprintf(stderr, "[launcher] work %s, binary %s\n", work.UTF8String, binary.UTF8String);

    char *const args[] = {(char *)binary.fileSystemRepresentation, "rewritten/v103", "boot", NULL};
    execv(args[0], args);
    [self alert:[NSString stringWithFormat:@"starting the game failed: %s\n\nsee %@", strerror(errno), log]];
}

- (void)applicationDidFinishLaunching:(NSNotification *)note {
    (void)note;
    self.work = work_folder() ?: @"";

    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 620, 300)
                                              styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
                                                backing:NSBackingStoreBuffered
                                                  defer:NO];
    self.window.title = @"NieR:Automata (Switch) — MacNativeGames";
    [self.window center];

    NSView *v = self.window.contentView;
    NSTextField *title = [NSTextField labelWithString:@"NieR:Automata\nThe End of YoRHa Edition"];
    title.font = [NSFont systemFontOfSize:22 weight:NSFontWeightSemibold];
    title.frame = NSMakeRect(24, 236, 500, 56);
    [v addSubview:title];
    NSTextField *sub = [NSTextField labelWithString:@"Switch 1.0.3 · runtime 1.0.3 (v103) · Apple Silicon native"];
    sub.font = [NSFont systemFontOfSize:12];
    sub.textColor = NSColor.secondaryLabelColor;
    sub.frame = NSMakeRect(24, 206, 500, 20);
    [v addSubview:sub];
    NSTextField *work = [NSTextField labelWithString:[NSString stringWithFormat:@"数据目录 / data: %@", self.work]];
    work.font = [NSFont monospacedSystemFontOfSize:10.5 weight:NSFontWeightRegular];
    work.textColor = NSColor.secondaryLabelColor;
    work.lineBreakMode = NSLineBreakByTruncatingMiddle;
    work.frame = NSMakeRect(24, 184, 500, 18);
    [v addSubview:work];
    self.status = [NSTextField labelWithString:@"实验性兼容层：开场（电影 / 标题菜单 / 难度选择 / 序章）已验证，30 fps。\n几何着色器绘制被跳过、部分驱动常量按 0 读，画面与碰撞可能与主机不同。"];
    self.status.font = [NSFont systemFontOfSize:11];
    self.status.textColor = NSColor.secondaryLabelColor;
    self.status.frame = NSMakeRect(24, 112, 560, 56);
    self.status.maximumNumberOfLines = 3;
    [v addSubview:self.status];

    self.play = [NSButton buttonWithTitle:@"开始游戏 / Play" target:self action:@selector(play:)];
    self.play.frame = NSMakeRect(414, 20, 176, 34);
    self.play.keyEquivalent = @"\r";
    [v addSubview:self.play];
    NSButton *quit = [NSButton buttonWithTitle:@"退出 / Quit" target:self action:@selector(quitNow:)];
    quit.frame = NSMakeRect(318, 20, 86, 34);
    [v addSubview:quit];

    NSMenu *bar = [NSMenu new];
    NSMenuItem *appItem = [NSMenuItem new];
    [bar addItem:appItem];
    NSMenu *appMenu = [NSMenu new];
    [appMenu addItemWithTitle:@"退出 / Quit" action:@selector(quitNow:) keyEquivalent:@"q"];
    appItem.submenu = appMenu;
    NSApp.mainMenu = bar;

    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];

    /* NIER_AUTOSTART=1: start the game without a click (unattended checks: the build's own acceptance run). */
    if (getenv("NIER_AUTOSTART")) dispatch_async(dispatch_get_main_queue(), ^{ [self play:nil]; });
}
@end

int main(void) {
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        Launcher *d = [Launcher new];
        app.delegate = d;
        [app run];
    }
    return 0;
}
