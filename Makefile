# Source gate for this repository: fast, offline, and it changes no source file.
# It mirrors .github/workflows/tests.yml, so a commit that passes here passes CI.
#
#   make check
#
# The Python tests need the packages in games/totk/requirements.txt; install them once
# (the CI does the same with pip) and this stays offline.
#
# The offline scene replayer (tools/scenecap/replay) is Objective-C and needs the macOS SDK's Foundation and Metal
# frameworks; its tests skip themselves on a host without a Metal device. Real zstd frames are decoded through a
# runtime libzstd lookup, which WITH_LIBZSTD=0 turns off (packages with such frames then fail with an explicit error).

# The tests import compression.zstd (games/totk/scripts/make_romfs_patches.py), which is standard only from Python 3.14;
# the README and the CI use that version, so prefer it when it is installed. PYTHON still overrides this.
PYTHON ?= $(shell command -v python3.14 >/dev/null 2>&1 && echo python3.14 || echo python3)
CC ?= cc
CLANG ?= clang
WITH_LIBZSTD ?= 1

SCENECAP_REPLAY_DIR := tools/scenecap/replay
SCENECAP_REPLAY_BUILD := $(SCENECAP_REPLAY_DIR)/build
SCENECAP_REPLAY_BIN := $(SCENECAP_REPLAY_BUILD)/scenecap_replay
SCENECAP_REPLAY_PROBE := $(SCENECAP_REPLAY_BUILD)/nvs_reader_probe
SCENECAP_REPLAY_DEFINES := -DSCENECAP_WITH_LIBZSTD=$(WITH_LIBZSTD)
SCENECAP_REPLAY_INCLUDES := -Itools/scenecap/format -I$(SCENECAP_REPLAY_DIR)
SCENECAP_REPLAY_CFLAGS := -std=c11 -Wall -Wextra -Werror -mmacosx-version-min=13.0 $(SCENECAP_REPLAY_INCLUDES) \
	$(SCENECAP_REPLAY_DEFINES) -fsanitize=undefined -fno-sanitize-recover=all
SCENECAP_REPLAY_OBJC := -fobjc-arc $(SCENECAP_REPLAY_CFLAGS)
SCENECAP_REPLAY_OBJECTS := $(addprefix $(SCENECAP_REPLAY_BUILD)/,nvs_format.o replay_zstd.o replay_pixels.o \
	replay_png.o replay_pkg.o replay_model.o replay_metal.o scenecap_replay.o)

.PHONY: check clean-replay

check: $(SCENECAP_REPLAY_BIN) $(SCENECAP_REPLAY_PROBE)
	$(PYTHON) tools/scenecap/sync.py --check
	$(PYTHON) -m unittest discover -s tests -q
	$(PYTHON) -m unittest discover -s tools/scenecap/tests -q
	SCENECAP_REPLAY=$(abspath $(SCENECAP_REPLAY_BIN)) SCENECAP_PROBE=$(abspath $(SCENECAP_REPLAY_PROBE)) \
	  SCENECAP_WITH_LIBZSTD=$(WITH_LIBZSTD) $(PYTHON) -m unittest discover -s tools/scenecap/replay/tests -q
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT; \
	 $(CC) -std=c11 -Wall -Wextra -Werror -fsanitize=undefined -fno-sanitize-recover=all \
	   tests/test_romfs_paths.c -o "$$tmp/test-romfs-paths" && "$$tmp/test-romfs-paths" && \
	 $(CC) -std=c11 -Wall -Wextra -Werror -fsanitize=undefined -fno-sanitize-recover=all \
	   tests/test_controller_map.c -o "$$tmp/test-controller-map" && "$$tmp/test-controller-map" && \
	 $(CC) -std=c11 -Wall -Wextra -Werror -fsanitize=undefined -fno-sanitize-recover=all \
	   -Itools/scenecap/format tools/scenecap/format/nvs_format.c tools/scenecap/tests/test_nvs_format.c \
	   -o "$$tmp/test-nvs-format" && "$$tmp/test-nvs-format" && \
	 $(CC) -std=c11 -Wall -Wextra -Werror -fsanitize=undefined -fno-sanitize-recover=all \
	   -Itools/scenecap/format tools/scenecap/format/nvs_format.c tools/scenecap/format/nvs_tool.c \
	   -o "$$tmp/nvs-tool"

clean-replay:
	rm -rf $(SCENECAP_REPLAY_BUILD)

$(SCENECAP_REPLAY_BUILD):
	mkdir -p $@

$(SCENECAP_REPLAY_BUILD)/nvs_format.o: tools/scenecap/format/nvs_format.c Makefile | $(SCENECAP_REPLAY_BUILD)
	$(CC) $(SCENECAP_REPLAY_CFLAGS) -c $< -o $@

$(SCENECAP_REPLAY_BUILD)/%.o: $(SCENECAP_REPLAY_DIR)/%.c Makefile | $(SCENECAP_REPLAY_BUILD)
	$(CC) $(SCENECAP_REPLAY_CFLAGS) -c $< -o $@

$(SCENECAP_REPLAY_BUILD)/%.o: $(SCENECAP_REPLAY_DIR)/%.m Makefile | $(SCENECAP_REPLAY_BUILD)
	$(CLANG) $(SCENECAP_REPLAY_OBJC) -c $< -o $@

$(SCENECAP_REPLAY_BIN): $(SCENECAP_REPLAY_OBJECTS)
	$(CLANG) -fobjc-arc -mmacosx-version-min=13.0 -fsanitize=undefined $(SCENECAP_REPLAY_OBJECTS) \
	  -framework Foundation -framework Metal -lz -o $@

$(SCENECAP_REPLAY_PROBE): tools/scenecap/replay/tests/nvs_reader_probe.c \
		$(SCENECAP_REPLAY_BUILD)/nvs_format.o $(SCENECAP_REPLAY_BUILD)/replay_zstd.o
	$(CC) $(SCENECAP_REPLAY_CFLAGS) $< $(SCENECAP_REPLAY_BUILD)/nvs_format.o \
	  $(SCENECAP_REPLAY_BUILD)/replay_zstd.o -o $@
