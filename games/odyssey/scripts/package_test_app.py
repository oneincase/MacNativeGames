#!/usr/bin/env python3
"""Package the local, bounded Odyssey test launcher as an icon-bearing Mac app.

The app and official title icon stay in ignored Odyssey_build; it is not a
standalone distribution and still depends on this local project checkout.
"""
from pathlib import Path
import plistlib
import subprocess

GAME = Path(__file__).resolve().parents[1]
BUILD = GAME / 'Odyssey_build'
ICON = BUILD / 'app-assets/OdysseyIcon.jpg'
APP = BUILD / 'Odyssey Test.app'


def main():
    if not ICON.is_file():
        raise SystemExit('Local verified title icon missing; run extract_app_icon.py first')
    if not (BUILD / 'build/odyssey-native').is_file():
        raise SystemExit('Native runtime missing; build the Odyssey Native target first')
    macos = APP / 'Contents/MacOS'
    resources = APP / 'Contents/Resources'
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    subprocess.run(['sips', '-s', 'format', 'icns', str(ICON), '--out',
                    str(resources / 'OdysseyIcon.icns')], check=True,
                   stdout=subprocess.DEVNULL)
    info = {
        'CFBundleDevelopmentRegion': 'en',
        'CFBundleDisplayName': 'Odyssey Test',
        'CFBundleExecutable': 'Odyssey Test',
        'CFBundleIconFile': 'OdysseyIcon',
        'CFBundleIdentifier': 'local.projectidea.odyssey-test',
        'CFBundleInfoDictionaryVersion': '6.0',
        'CFBundleName': 'Odyssey Test',
        'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': '0.1',
        'CFBundleVersion': '1',
        'NSHighResolutionCapable': True,
    }
    with (APP / 'Contents/Info.plist').open('wb') as file:
        plistlib.dump(info, file)
    launcher = macos / 'Odyssey Test'
    subprocess.run(['xcrun', 'clang', '-O2', '-o', str(launcher),
                    str(GAME / 'scripts/odyssey_test_launcher.c')], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(APP)], check=True,
                   stdout=subprocess.DEVNULL)
    print(APP)


if __name__ == '__main__':
    main()
