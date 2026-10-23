#include <mach-o/dyld.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(void) {
    char executable[PATH_MAX];
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) != 0) return 1;

    char resolved[PATH_MAX];
    if (!realpath(executable, resolved)) return 1;
    const char *suffix = "/Odyssey_build/Odyssey Test.app/Contents/MacOS/Odyssey Test";
    size_t path_length = strlen(resolved);
    size_t suffix_length = strlen(suffix);
    if (path_length <= suffix_length ||
        strcmp(resolved + path_length - suffix_length, suffix) != 0) {
        fputs("Odyssey Test must be launched from its local build folder.\n", stderr);
        return 1;
    }
    resolved[path_length - suffix_length] = '\0';

    char script[PATH_MAX];
    if (snprintf(script, sizeof(script), "%s/scripts/launch_manual_test.py", resolved) >= sizeof(script)) return 1;
    execl("/usr/bin/python3", "python3", script, (char *)NULL);
    perror("Could not start Odyssey Test");
    return 1;
}
