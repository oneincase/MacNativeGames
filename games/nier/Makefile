# NieR:Automata native runtime. Self-contained: the Horizon host (runtime/), the rewriter (third_party/d2r), the NVN-on-Metal
# layer (third_party/d2r_gfx), the shader translator (prebuilt/) and the scripts are this folder's own copies, forked from
# games/totk; nothing here reads games/totk. Everything generated goes to the work folder.
# Inputs come from scripts/extract_code.py, scripts/merge_romfs.py and the rewrite (see README.md), then:
#   make prepare   # scripts/prepare_runtime.py: tables, RomFS index, fonts config
#   make runtime   # BY3_build/build/nier-native
#   make boot      # run it from the work folder (cwd: extracted/ and rewritten/ are read relative to it)

WORK ?= NIER_build
OUT := $(WORK)/build
PY ?= python3
NATIVE_OPT ?= -O2

GFX_SRC := $(wildcard third_party/d2r_gfx/gfx/nvn/*.c third_party/d2r_gfx/gfx/nvn/*.m third_party/d2r_gfx/gfx/nvn/*.S third_party/d2r_gfx/gfx/tex/*.c third_party/d2r_gfx/gfx/tex/*.m)
GFX_OBJECTS := $(patsubst %,$(OUT)/obj/%.o,$(GFX_SRC))
GFX_HEADERS := $(wildcard third_party/d2r_gfx/gfx/nvn/*.h third_party/d2r_gfx/gfx/nvn/*.inc third_party/d2r_gfx/gfx/nvn/include/*.h third_party/d2r_gfx/host/*.h)
SCENECAP ?= 0
GFX_FLAGS := -std=gnu11 -DSCENECAP_HOOKS=$(SCENECAP) -Wno-error -Wno-unused-function -Wno-unused-parameter -fobjc-arc -Ithird_party/d2r_gfx/gfx/nvn/include

ifeq ($(SCENECAP),0)
GFX_SRC := $(filter-out %/scenecap_capture.c %/nvs_format.c,$(GFX_SRC))
GFX_OBJECTS := $(patsubst %,$(OUT)/obj/%.o,$(GFX_SRC))
endif

RUNTIME_SRC := $(filter-out runtime/frontend_ios.m,$(wildcard runtime/*.c runtime/*.m runtime/*.S))
RUNTIME_HEADERS := $(wildcard runtime/*.h)
GENERATED := $(OUT)/runtime_tables.h $(OUT)/romfs_config.h $(OUT)/shared_fonts_config.h

.PHONY: prepare runtime boot clean code
prepare:
	"$(PY)" scripts/prepare_runtime.py --work "$(WORK)"

# Code and data from the game's own containers (once per game version). BASE/UPDATE are the base and update NSPs (an
# update NSZ must be decompressed first: nsz -D --keys prod.keys), KEYS is prod.keys. Rewrites with the same ruleset the
# other ports use; the resulting hashes are pinned in assets/expected-v1.0.3.json and checked by prepare_runtime.
BASE ?=
UPDATE ?=
KEYS ?=
code:
	test -n "$(BASE)" -a -n "$(UPDATE)" -a -n "$(KEYS)" || { echo "usage: make code BASE=<base.nsp> UPDATE=<update.nsp> KEYS=<prod.keys>"; false; }
	"$(PY)" scripts/extract_exefs.py "$(BASE)" --keys "$(KEYS)" --title 0100b8e016f76000 --output "$(WORK)/extracted/base/exefs"
	"$(PY)" scripts/extract_exefs.py "$(UPDATE)" --keys "$(KEYS)" --title 0100b8e016f76000 --update --output "$(WORK)/extracted/update/exefs"
	rm -rf "$(WORK)/extracted/v103/exefs" && mkdir -p "$(WORK)/extracted/v103" && cp -R "$(WORK)/extracted/update/exefs" "$(WORK)/extracted/v103/exefs"
	"$(PY)" scripts/merge_romfs.py "$(BASE)" "$(UPDATE)" --keys "$(KEYS)" --output "$(WORK)/extracted/v103/romfs"
	for m in sdk subsdk0 main; do DM_RULESET=native DM_PRIVATE_GATES=0 "$(PY)" third_party/d2r/patch_nso.py "$(WORK)/extracted/v103/exefs/$$m" "$(WORK)/rewritten/v103" "$$m"; done
	"$(PY)" scripts/prepare_modules.py "$(WORK)/extracted/v103/exefs" --output "$(WORK)/extracted/v103/elf"

$(GENERATED):
	@echo "missing $@: run make prepare" && false

$(OUT)/obj/%.o: % $(GFX_HEADERS) Makefile
	@mkdir -p "$(dir $@)"
	xcrun clang $(NATIVE_OPT) -g -Wall -Wextra $(GFX_FLAGS) -c "$<" -o "$@"

$(OUT)/nier-native: $(RUNTIME_SRC) $(RUNTIME_HEADERS) $(GENERATED) $(GFX_OBJECTS) Makefile
	xcrun clang -std=c11 $(NATIVE_OPT) -g -Wall -Wextra -Werror -Wno-deprecated-declarations \
	  -DSCENECAP_HOOKS=$(SCENECAP) \
	  -Ithird_party/d2r_gfx/gfx/nvn/include -I$(OUT) -I/opt/homebrew/opt/opus/include/opus -I/opt/homebrew/opt/libvpx/include \
	  $(RUNTIME_SRC) $(GFX_OBJECTS) /opt/homebrew/opt/opus/lib/libopus.a /opt/homebrew/opt/libvpx/lib/libvpx.a \
	  -framework Metal -framework MetalFX -framework Foundation -framework QuartzCore -framework ImageIO -framework CoreGraphics \
	  -framework CoreText -framework UniformTypeIdentifiers -fobjc-arc -framework Cocoa -framework CoreVideo -framework CoreFoundation \
	  -framework AudioToolbox -framework GameController -framework CoreHaptics -framework IOKit -o "$@"

runtime: $(OUT)/nier-native

# The runtime shader translation the graphics layer starts on first use: <work>/build/shader-service/totk_xlate (the
# repository's prebuilt NativeAOT translator, checked against its SHA-256) and <work>/build/spirv-cross/spirv-cross,
# built like build_app.py does (same pinned commit, sources and flags as SPIRV-Cross's CMake Release build).
SPIRV_CROSS_COMMIT := aa217aeb6c9f0ace7a0ab233b28807edf45eb165
SPIRV_CROSS_SOURCES := main.cpp spirv_cfg.cpp spirv_cpp.cpp spirv_cross.cpp spirv_cross_parsed_ir.cpp spirv_cross_util.cpp \
  spirv_glsl.cpp spirv_hlsl.cpp spirv_msl.cpp spirv_parser.cpp spirv_reflect.cpp
.PHONY: shader-tools
shader-tools: $(OUT)/shader-service/totk_xlate $(OUT)/spirv-cross/spirv-cross
$(OUT)/shader-service/totk_xlate: prebuilt/macos-arm64/totk_xlate prebuilt/macos-arm64/totk_xlate.sha256
	@mkdir -p "$(dir $@)"
	test "$$(shasum -a 256 "$<" | cut -d' ' -f1)" = "$$(cut -d' ' -f1 "prebuilt/macos-arm64/totk_xlate.sha256")"
	cp "$<" "$@" && chmod +x "$@"
$(OUT)/spirv-cross/spirv-cross:
	@mkdir -p "$(WORK)/src" "$(dir $@)obj"
	test -d "$(WORK)/src/SPIRV-Cross" || git clone -q https://github.com/KhronosGroup/SPIRV-Cross.git "$(WORK)/src/SPIRV-Cross"
	git -C "$(WORK)/src/SPIRV-Cross" checkout -q $(SPIRV_CROSS_COMMIT)
	for f in $(SPIRV_CROSS_SOURCES); do xcrun clang++ -O3 -DNDEBUG -std=c++11 -arch arm64 -Wno-deprecated-declarations \
	  -c "$(WORK)/src/SPIRV-Cross/$$f" -o "$(dir $@)obj/$$f.o" & done; wait
	xcrun clang++ -arch arm64 $(addprefix $(dir $@)obj/,$(addsuffix .o,$(SPIRV_CROSS_SOURCES))) -o "$@"

# NIER_LOG: where the boot's stderr goes (the host is loud on purpose; every abort names what is missing).
NIER_LOG ?= $(WORK)/logs/boot.log
boot: runtime
	@mkdir -p "$(dir $(NIER_LOG))"
	cd "$(WORK)" && TOTK_HEADLESS=$${TOTK_HEADLESS-} ./build/nier-native rewritten/v103 boot 2> "$(abspath $(NIER_LOG))"; echo "exit $$?; log: $(NIER_LOG)"

# The player's mods, made into checked tables: exefs patches (mods/1.2.0/<name>/exefs/*.pchtxt) -> <work>/patches,
# RomFS mods (mods/<name>/romfs/...) -> their manifest.tsv (sizes and SHA-256 of the replacements and the originals).
.PHONY: patches mods app
patches:
	"$(PY)" scripts/prepare_patches.py --work "$(WORK)"
mods:
	for d in mods/*/romfs; do [ -d "$$d" ] && "$(PY)" scripts/make_mod_manifest.py "$$d" --romfs "$(WORK)/extracted/v103/romfs"; done; true

# The app: a small launcher bundle (settings window with presets, then the game) that runs the game from the work
# folder, which it names in Info.plist (NIERWorkFolder): nothing large is copied. Its icon comes from the game's own
# control data (GAME=<base or update NSP> KEYS=<prod.keys>), or the app goes without one.
APP ?= NieR Automata.app
GAME ?=
KEYS ?=
$(OUT)/launcher: app/launcher.m Makefile
	xcrun clang -std=gnu11 -O2 -Wall -Wextra -Werror -fobjc-arc app/launcher.m -framework Cocoa -o "$@"
$(OUT)/AppIcon.icns:
	@if [ -n "$(GAME)" ] && [ -n "$(KEYS)" ]; then cd scripts && "$(PY)" extract_app_icon.py "$(GAME)" --keys "$(KEYS)" --title 0100b8e016f76000 --output "$(abspath $@)"; \
	 else echo "no GAME= / KEYS=: the app gets no icon"; fi
app: runtime shader-tools $(OUT)/launcher $(OUT)/AppIcon.icns
	rm -rf "$(APP)" && mkdir -p "$(APP)/Contents/MacOS" "$(APP)/Contents/Resources"
	cp "$(OUT)/launcher" "$(APP)/Contents/MacOS/NieR Automata"
	[ -f "$(OUT)/AppIcon.icns" ] && cp "$(OUT)/AppIcon.icns" "$(APP)/Contents/Resources/" || true
	printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>' \
	  '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
	  '<plist version="1.0"><dict>' \
	  '<key>CFBundleExecutable</key><string>NieR Automata</string>' \
	  '<key>CFBundleIdentifier</key><string>org.macnativegames.nier</string>' \
	  '<key>CFBundleName</key><string>NieR Automata</string>' \
	  '<key>CFBundlePackageType</key><string>APPL</string>' \
	  '<key>CFBundleShortVersionString</key><string>1.0.3</string>' \
	  '<key>CFBundleVersion</key><string>1</string>' \
	  '<key>CFBundleIconFile</key><string>AppIcon</string>' \
	  '<key>LSMinimumSystemVersion</key><string>26.0</string>' \
	  '<key>LSApplicationCategoryType</key><string>public.app-category.action-games</string>' \
	  '<key>NSHighResolutionCapable</key><true/>' \
	  '<key>NIERWorkFolder</key><string>$(abspath $(WORK))</string>' \
	  '</dict></plist>' > "$(APP)/Contents/Info.plist"
	codesign --force --sign - "$(APP)"
	@echo "built $(APP) (game data stay in $(abspath $(WORK)))"

# Install the (small) app where Launchpad and Spotlight look: /Applications, or ~/Applications when that is not writable.
INSTALL_DIR ?= $(shell [ -w /Applications ] && echo /Applications || echo $(HOME)/Applications)
.PHONY: install
install: app
	mkdir -p "$(INSTALL_DIR)" && rm -rf "$(INSTALL_DIR)/$(APP)" && cp -R "$(APP)" "$(INSTALL_DIR)/"
	/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$(INSTALL_DIR)/$(APP)"
	@echo "installed $(INSTALL_DIR)/$(APP)"

clean:
	rm -rf "$(OUT)/obj" "$(OUT)/nier-native"
