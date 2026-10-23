#!/usr/bin/env python3
"""Generate an Xcode navigator and external-build target for the Odyssey Makefile."""
from hashlib import sha1
from pathlib import Path

root = Path(__file__).resolve().parents[1]
out = root / 'Odyssey.xcodeproj' / 'project.pbxproj'
folders = ('runtime', 'headless', 'scripts', 'assets', 'third_party/d2r_gfx/gfx/nvn', 'third_party/d2r_gfx/gfx/tex')
files = [root / name for name in ('Makefile', 'README.md')]
for folder in folders:
    files.extend(p for p in sorted((root / folder).rglob('*')) if p.is_file() and not any(x in p.parts for x in ('__pycache__', 'Odyssey_build')))

def ident(kind, path):
    return sha1(f'Odyssey:{kind}:{path}'.encode()).hexdigest()[:24].upper()

def quoted(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'

def filetype(p):
    return {'.c':'sourcecode.c.c','.h':'sourcecode.c.h','.m':'sourcecode.c.objc','.mm':'sourcecode.cpp.objcpp','.cpp':'sourcecode.cpp.cpp','.S':'sourcecode.asm','.py':'text.script.python','.json':'text.json','.md':'net.daringfireball.markdown'}.get(p.suffix,'text')

objects = {}
def add(kind, key, body):
    uid=ident(kind,key); objects[uid]=f'\t\t{uid} /* {key} */ = {{isa = {kind}; {body} }};'; return uid

children = {".": []}
for folder in folders:
    part = Path(folder)
    while str(part) != ".":
        children.setdefault(part.as_posix(), [])
        part = part.parent
for p in files:
    rel=p.relative_to(root); parent=rel.parent.as_posix(); children.setdefault(parent,[]).append(rel)
    add('PBXFileReference',rel.as_posix(),f'lastKnownFileType = {filetype(p)}; path = {quoted(p.name)}; sourceTree = "<group>";')
for folder in sorted(children, key=lambda s:s.count('/'), reverse=True):
    if folder == '.': continue
    direct=[ident('PBXFileReference',p.as_posix()) for p in children.get(folder,[])]
    nested=[ident('PBXGroup',f) for f in children if f != folder and Path(f).parent.as_posix()==folder]
    entries=', '.join(f'{x} /* {next((p.name for p in children.get(folder,[]) if ident("PBXFileReference",p.as_posix())==x), next((Path(f).name for f in children if ident("PBXGroup",f)==x),""))} */' for x in direct+nested)
    add('PBXGroup',folder,f'children = ({entries}); path = {quoted(Path(folder).name)}; sourceTree = "<group>";')
roots=[ident('PBXFileReference',p.as_posix()) for p in children.get('.',[])]
top=[f for f in children if f != '.' and '/' not in f]
root_children=', '.join(f'{x} /* {name} */' for x,name in [(x,next(p.name for p in children['.'] if ident('PBXFileReference',p.as_posix())==x)) for x in roots]+[(ident('PBXGroup',f),f) for f in top])
main=add('PBXGroup','main',f'children = ({root_children}); sourceTree = "<group>";')
project_configs=[]; target_configs=[]
for name in ('Debug','Release'):
    project_configs.append(add('XCBuildConfiguration',f'project-{name}',f'buildSettings = {{ MACOSX_DEPLOYMENT_TARGET = 26.0; SDKROOT = macosx; }}; name = {name};'))
    target_configs.append(add('XCBuildConfiguration',f'target-{name}',f'buildSettings = {{ PRODUCT_NAME = "Odyssey Native"; }}; name = {name};'))
pc=add('XCConfigurationList','project',f'buildConfigurations = ({", ".join(project_configs)}); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;')
tc=add('XCConfigurationList','target',f'buildConfigurations = ({", ".join(target_configs)}); defaultConfigurationIsVisible = 0; defaultConfigurationName = Release;')
target=add('PBXLegacyTarget','Odyssey Native',f'buildArgumentsString = runtime; buildConfigurationList = {tc}; buildPhases = (); buildToolPath = /usr/bin/make; buildWorkingDirectory = "$(SRCROOT)"; dependencies = (); name = "Odyssey Native"; passBuildSettingsInEnvironment = 1; productName = "Odyssey Native";')
project=add('PBXProject','Odyssey',f'attributes = {{ LastUpgradeCheck = 2710; }}; buildConfigurationList = {pc}; compatibilityVersion = "Xcode 14.0"; developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en); mainGroup = {main}; projectDirPath = ""; projectRoot = ""; targets = ({target});')
text='// !$*UTF8*$!\n{\n\tarchiveVersion = 1;\n\tclasses = {};\n\tobjectVersion = 56;\n\tobjects = {\n'+'\n'.join(objects.values())+'\n\t};\n\trootObject = '+project+';\n}\n'
out.parent.mkdir(exist_ok=True)
out.write_text(text)
print(out)
