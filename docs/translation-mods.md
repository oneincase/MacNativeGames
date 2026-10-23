# Translation and dub mods

Fan translations (text, and sometimes voice) load as player RomFS mods (see "Mods" in the README). This page is what
it took to run one on a Mac: a Brazilian Portuguese text and voice mod for 1.2.1, 1,410 files, on a MacBook Air M1
(2026-10-19). Nothing here ships game content; get the mod from the people who made it.

## 1. The mod must match the app's version

A translation is made for one game version, and its file names say which. In the RomFS, `Mals/<language>.Product.<n>.sarc.zs`
holds the text and `System/Resource/ResourceSizeTable.Product.<n>.rsizetable.zs` the size table: `100` is 1.0.0, `121` is
1.2.1 (1.2.1 keeps its UI archives as `Common.Product.110` and `Title.Product.110`), and 1.4.3 has `Mals/*.Product.142`, the
size table `Product.143` and `Common.Product.140`. A 1.2.1 mod in a 1.0.0 app changes no
text (the 1.0.0 game reads `Product.100`), and its voice files paired with another version's size table may not work. Build
the app for the mod's version (`--update` with that update) and put the mod in `mods/<that version>`, for example
`mods/v121`.

## 2. Install

Copy the mod's `romfs` contents (the folders `Mals`, `UI`, `Voice`, `Sound`, ...) into the mods folder, then write the
manifest:

```
python3 games/totk/scripts/make_mod_manifest.py ~/Library/Application\ Support/MacNativeGames/TOTK/mods/v121 \
    --app "Tears of the Kingdom.app"
```

Files the RomFS lacks are added as new files; a path that differs from the game's only in case stops the tool. Some mods also ship an
uncompressed copy of an archive next to the `.zs` (for example `Common.Product.110.Nin_NX_NVN.blarc`). The game reads
the `.zs`; the extra copy would only be added as a new file, so it can be left out.

## 3. Pick the language the mod replaces

A translation usually replaces one of the game's languages, most often American English (`USen`: text in
`Mals/USen...`, voice in `Voice/Resource/USen`). The game follows the Mac's language, so unless the Mac is set to the language
the mod replaces, the mod's text does not show. Give only this game the replaced language:

```
defaults write org.macnativegames.totk AppleLanguages -array en-US
```

`defaults delete org.macnativegames.totk AppleLanguages` goes back to the Mac's language. Started from Terminal,
`TOTK_LANG=en-US` does the same for one run.

## 4. The UI archive and the build's own patch

Most translations also replace `UI/LayoutArchive/Common.Product.<n>.Nin_NX_NVN.blarc.zs` (the UI textures with words
in them). The build's RomFS patch (`make_romfs_patches.py`, the quick-menu capture timing) edits the same archive, and
two overlays may not replace one file. `make_mod_manifest.py` takes care of it (#73): when the mod has that archive, it
puts the build's edit into the mod's copy, keeps the mod's own file next to it as `<name>.mod-original` (used again on
the next run) and says so:

```
merged with the build's patch: UI/LayoutArchive/Common.Product.110.Nin_NX_NVN.blarc.zs (the mod's own file: ...mod-original)
```

At start the log then shows that the mod's archive takes the place of the build's:

```
RomFS overlay: UI/LayoutArchive/Common.Product.110.Nin_NX_NVN.blarc.zs (6275634 bytes) replaces the build's overlay extracted/v121/romfs-patches/... (the mod holds its edit)
```

A mod that changed the patched members itself is refused and nothing is written. If a later build changes its patch,
run `make_mod_manifest.py` again (the start says which file no longer matches).

## 5. Check

The log is off by default: turn it on with `defaults write org.macnativegames.totk TOTK_DEBUG_LOG -bool YES` and start the
game again. The log (`~/Library/Logs/MacNativeGames/totk.log`) then lists the mod at start:

```
RomFS overlay: 1410 files from .../mods/v121 (1394 more not listed)
RomFS overlay: .../mods/v121 checked, 1410 files, 144.5 MB hashed in 0.69 s
```

A file whose hash does not match stops the start with the file named in the log, so a changed mod never loads half-way. A
mod made for another version does not stop it: its files are listed as new files the game does not read, so it simply has
no effect.
