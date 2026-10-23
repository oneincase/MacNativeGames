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

    /* The GPU switches and the mid-pass clear path the port was tested with (the runtime also defaults the latter).
       NIER_T2T_CONVERT: P0 (docs/repair-plan.md) — the HDR→window format-converting copy is the only present channel;
       it runs as an immediate render-pass composite now, proven under real GPU submission. */
    setenv("TOTK_GPU_ALLOW_SUBMIT", "1", 1);
    setenv("TOTK_GPU_MIDCLEAR", "1", 1);
    setenv("NIER_T2T_CONVERT", "1", 1);
    /* GPU 预算按机器物理内存定，不再写死一个数字：默认 55%，8 GB 机器也至少给 6 GB。
       写死 5000 时，序章飞行的装载突发会顶到闸门（docs/port-plan-2026-10-23.md §2）。 */
    unsigned long long physMB = NSProcessInfo.processInfo.physicalMemory >> 20;
    unsigned long long budgetMB = (physMB * 55ull) / 100ull;
    if (budgetMB < 6144ull) budgetMB = 6144ull;
    setenv("TOTK_GPU_BUDGET_MB", [NSString stringWithFormat:@"%llu", budgetMB].UTF8String, 1);
    /* GS 压缩：序章飞行场景的几何只在压缩路径上出画（stage-2 程序没有翻译链），默认开。
       捕获缓冲扩容时旧缓冲延迟退役（commit 6762950 修掉的页故障）。 */
    /* 临时诊断（2026-10-23）：用户报告「加载全章节存档 → 第一章节」后画面纯黑（只剩光效/UI）。
       先关掉 GS 压缩做对照：若世界几何恢复，说明该场景的 GS 程序被压缩路径丢掉了，
       需要按 P1 修「未捕获/超跨度」的退化路径，而不是永久关掉压缩。 */
    setenv("NIER_GS_COMPACT", "0", 1);
    /* 第二个对照（2026-10-23）：关掉压缩后世界几何出现了，但画面很暗、中间一块大的灰白矩形。
       怀疑是「被丢弃的 GS 绘制」里含有光照相关的东西（体积光/探针/贴花），所以再保留一份未翻译的 GS 直绘做对照。 */
    setenv("NIER_KEEP_GS_DRAWS", "1", 1);
    // 第一章节等暗场景：游戏本体没有亮度开关，先在上屏合成处压一条伽马曲线提亮暗部（真因待查：该场景不走延迟光照）。
    // 想要原亮度就把这里改成 "1.0"（或启动前设 NIER_GAMMA）。
    setenv("NIER_GAMMA", "0.30", 1);
    /* 性能（2026-10-23，c243 实测）：关掉 GPU 保活。保活是给"帧间 GPU 空转会被 M4 降频"
       兜底的自旋核（nvn_mtl.m:453 注释），但本作 GPU 只到 27-30%（游戏自身 20-22%），
       没有空转窗口，自旋核反而把 GPU 占满（93%）并拖慢出图。
       c243（关保活）5 分钟 9600 帧 ≈ 32 fps、0 崩溃；c239（开保活）30 分钟 38400 帧 ≈ 21 fps。 */
    setenv("TOTK_GPU_KEEPALIVE", "0", 1);
    /* 偏色修复（2026-10-23，docs/color-cast-root-cause-2026-10-23.md §10）：
       bloom 最粗级（ser=84, 50x28）被 84↔90 composite（shader 7b61c45a）写成非有限值，
       再经 88/89/90 上采样灌进 tonemap 的 bloom 通道，整片画面变成绿色（G/R 2.6）。
       修法 = nvn_mtl.m 的 shader_load 在编译 7b61c45a 前把输出夹到 0.2（默认开，
       NIER_KEEP_BLOOM_INF=1 可关）；c238 实测 G/R 1.00、G/B 1.00、亮度 88.6 正常。
       早先的临时办法 TOTK_EXP_SKIP_FS=7b61c45a（整条 composite 跳过）已被它取代：
       跳过会让 bloom 少一级（c236 的飞行帧 G/R 1.26 略绿），不再使用。 */
    unsetenv("TOTK_EXP_SKIP_FS");
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
