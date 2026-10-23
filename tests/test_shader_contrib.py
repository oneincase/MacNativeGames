"""The player's contribution file (totk_shader_contrib.h): written by the runtime, merged by scripts/shader_list.py."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOTK = Path(__file__).resolve().parents[1] / 'games/totk'
sys.path.insert(0, str(TOTK / 'scripts'))
from shader_list import ShaderList, canonical  # noqa: E402
from prepare_shader_seed import answers_for, suffix  # noqa: E402


@unittest.skipUnless(shutil.which('xcrun'), 'needs the Xcode command line tools')
class ContributionFileTests(unittest.TestCase):
    def test_probe_writes_rows_the_shared_list_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp); probe = tmp / 'probe'; contrib = tmp / 'shader-list-contribution.txt'
            build = subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                                    f'-I{TOTK}/third_party/d2r_gfx/gfx/nvn', str(TOTK / 'tests/shader_contrib/probe.m'),
                                    '-framework', 'Foundation', '-o', str(probe)], capture_output=True, text=True)
            if build.returncode:
                self.skipTest(f'clang could not build the probe: {build.stderr[-300:]}')
            run = subprocess.run([str(probe), str(contrib)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)['status'], 'pass')
            rows = [line.split('\t') for line in contrib.read_text().splitlines() if line.startswith('V\t')]
            self.assertTrue(rows and all(canonical(json.loads(deps)) == deps for _, _, deps in rows), 'deps as canonical() writes them')
            shared = ShaderList()
            shared.add_rows(contrib, 'm')
            self.assertEqual(len(shared.variants), 2 * 100 + 60)   # TOTK_CONTRIB_STEP 100
            self.assertEqual([row[0] for row in shared.warm], ['R', 'B'])   # render pipelines merge with the shaders
            deps = {'tex': {'9': '*', '10': '*_depth', '3': 'cube'}, 'attr': {'6': 'uint'}, 'fout': {'0': 'float'}}
            self.assertEqual(json.loads(run.stdout)['listed'], f'{7:064x}-' + suffix(answers_for(deps)),
                             'a pipeline names its shaders as prepare_shader_seed.py names list rows')
            noseed = subprocess.run([str(probe), str(contrib), 'noseed'], capture_output=True, text=True)
            self.assertEqual(noseed.returncode, 0, noseed.stderr)
            self.assertEqual(json.loads(noseed.stdout)['status'], 'pass')
            self.assertFalse(contrib.exists(), 'a run without the seed writes no contribution file')


if __name__ == '__main__':
    unittest.main()


from collect_shader_issues import attachment_urls, contribution_pipelines, contribution_rows  # noqa: E402


class IssueCollectionTests(unittest.TestCase):
    KEY_A, KEY_B = 'a' * 64, 'b' * 64

    def test_attachment_links_in_order_without_repeats(self):
        body = ('### shader-list-contribution.txt\n\n[shader-list-contribution.txt](https://github.com/user-attachments/files/123/'
                'shader-list-contribution.txt)\nagain: https://github.com/user-attachments/files/123/shader-list-contribution.txt '
                'old style: https://github.com/someone/MacNativeGames/files/456/contrib.txt).\nnot one: https://example.com/files/1/x.txt')
        self.assertEqual(attachment_urls(body), ['https://github.com/user-attachments/files/123/shader-list-contribution.txt',
                                                 'https://github.com/someone/MacNativeGames/files/456/contrib.txt'])
        self.assertEqual(attachment_urls(None), [])

    def test_an_automatic_submission_is_read_like_a_form_issue(self):
        link = watch.gist_raw('https://gist.github.com/someone/0123abcd\n')
        self.assertEqual(link, 'https://gist.githubusercontent.com/someone/0123abcd/raw/shader-list-contribution.txt')
        self.assertIsNone(watch.gist_raw('error: HTTP 401'))
        body = watch.issue_body('iPad', link)
        self.assertEqual(attachment_urls(body), [link])
        self.assertIn('### Device / 设备\n\niPad\n', body)
        self.assertNotIn('udid', body.lower())

    def test_only_shader_rows_are_taken(self):
        text = '\n'.join([
            '# header', '',
            f'V\t{self.KEY_A}\t{{"tex":{{"9":"2d","10":"3d"}}}}',     # canonicalised
            f'V\t{self.KEY_A}\t{{"tex":{{"10":"3d","9":"2d"}}}}',     # the same row
            f'V\t{self.KEY_B}\t{{}}',
            f'S\t{self.KEY_A}-0123456789ab_b\t0\tm',                  # shader libraries are not taken from contributions
            f'L\t{self.KEY_A}\tShader/x.bnsh\t0\t880\t100',            # nor locators
            'R\tabcd', 'R\tabcd', 'B\tabcd\t-\t3:7:0x1p-1,0x1p-1,0x1p-1,0x1p-1;',   # render pipelines are (a row once)
            'R\tnot hex', 'B\tabcd\t-\t-',
            'V\tnot-a-key\t{}', f'V\t{self.KEY_B}\t{{"local":[1]}}', f'V\t{self.KEY_B}\tnot json'])
        rows, dropped = contribution_rows(text)
        self.assertEqual(rows, {(self.KEY_A, '{"tex":{"10":"3d","9":"2d"}}'), (self.KEY_B, '{}')})
        self.assertEqual(dropped, 7)
        self.assertEqual(contribution_pipelines(text), [('R', 'abcd'), ('B', 'abcd', '-', '3:7:0x1p-1,0x1p-1,0x1p-1,0x1p-1;')])


sys.path.insert(0, str(TOTK / 'ios/tools'))
import shader_contrib_watch as watch  # noqa: E402


class MacReminderForDevicesTests(unittest.TestCase):
    def test_only_paired_reachable_ios_devices(self):
        listing = {'result': {'devices': [
            {'identifier': 'A', 'deviceProperties': {'name': 'iPhone'}, 'hardwareProperties': {'platform': 'iOS', 'udid': 'u-a'},
             'connectionProperties': {'pairingState': 'paired', 'transportType': 'localNetwork', 'tunnelState': 'disconnected'}},
            {'identifier': 'B', 'deviceProperties': {'name': 'iPad'}, 'hardwareProperties': {'platform': 'iOS', 'udid': 'u-b'},
             'connectionProperties': {'pairingState': 'paired'}},                                   # not reachable now
            {'identifier': 'E', 'deviceProperties': {'name': 'Tablet'}, 'hardwareProperties': {'platform': 'iOS', 'udid': 'u-e', 'deviceType': 'iPad'},
             'connectionProperties': {'pairingState': 'paired', 'transportType': 'wired'}},
            {'identifier': 'C', 'hardwareProperties': {'platform': 'watchOS'}, 'connectionProperties': {'pairingState': 'paired', 'transportType': 'wired'}},
            {'identifier': 'D', 'hardwareProperties': {'platform': 'iOS'}, 'connectionProperties': {'pairingState': 'unpaired', 'transportType': 'wired'}}]}}
        self.assertEqual(watch.reachable_devices(listing), [('A', 'u-a', 'iPhone', 'iPhone'), ('E', 'u-e', 'Tablet', 'iPad')])
        self.assertEqual(watch.reachable_devices({}), [])

    def test_the_game_is_found_by_name_or_bundle(self):
        self.assertEqual(watch.game_bundle({'result': {'apps': [{'name': 'Other', 'bundleIdentifier': 'x.y'},
                                                                 {'name': 'Tears of the Kingdom', 'bundleIdentifier': 'com.someone.totk'}]}}), 'com.someone.totk')
        self.assertEqual(watch.game_bundle({'result': {'apps': [{'name': 'TOTK', 'bundleIdentifier': 'org.macnativegames.totk'}]}}), 'org.macnativegames.totk')
        self.assertIsNone(watch.game_bundle({'result': {'apps': [{'name': 'Other', 'bundleIdentifier': 'x.y'}]}}))

    def test_rows_and_every_100(self):
        text = '# header\n' + ''.join(f'V\t{i:064x}\t{{}}\n' for i in range(3)) + f'V\t{0:064x}\t{{}}\n' + 'R\tab\n' + 'R\tab\n'
        self.assertEqual(watch.contribution_rows(text), 4)   # shaders and pipelines together
        self.assertFalse(watch.due(99, 0)); self.assertTrue(watch.due(100, 0))
        self.assertFalse(watch.due(199, 100)); self.assertTrue(watch.due(200, 100))

    def test_a_device_s_rows_are_merged_into_the_one_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); phone, mine = d / 'phone.txt', d / 'shader-list-contribution.txt'
            mine.write_text('# Mac\nR\tab\nV\t' + 'a' * 64 + '\t{}\n')
            phone.write_text('# phone\nR\tab\nR\tcd\nR\tcd\nB\tcd\t-\t-\n')
            self.assertEqual(watch.merge_rows(phone, mine), 4)      # ab and the V row, then cd and its B row once
            self.assertEqual(watch.merge_rows(phone, mine), 4)      # nothing new the second time
            fresh = d / 'new.txt'
            self.assertEqual(watch.merge_rows(phone, fresh), 3)
            self.assertTrue(fresh.read_text().startswith('# '))

    def test_agent_runs_check_every_ten_minutes_with_the_xcode(self):
        plist = watch.agent_plist('/usr/bin/python3', Path('/x/shader_contrib_watch.py'), '/X/Xcode.app/Contents/Developer')
        self.assertEqual(plist['ProgramArguments'], ['/usr/bin/python3', '/x/shader_contrib_watch.py', 'check'])
        self.assertEqual(plist['StartInterval'], 600)
        stored = watch.agent_plist('/usr/bin/python3', Path('/x/w.py'), '/X', Path('/Users/me/MacNativeGames/shader-list-contribution'))
        self.assertEqual(stored['ProgramArguments'][2:], ['check', '--store', '/Users/me/MacNativeGames/shader-list-contribution'])   # the clone
        self.assertEqual(plist['EnvironmentVariables'], {'DEVELOPER_DIR': '/X/Xcode.app/Contents/Developer'})
        self.assertFalse(plist['RunAtLoad'])

    def test_never_stops_the_reminders(self):
        self.assertEqual(watch.reminded_at(''), 0)
        self.assertEqual(watch.reminded_at('500\n'), 500)
        self.assertIsNone(watch.reminded_at('never\n'))
        self.assertEqual(watch.message(500, 'iPad')[2:], ('Remind me next time', "Don't remind me again", 'Send')
                         if not watch.chinese() else ('下次提醒', '不要提醒了', '发送'))

    def test_gh_replaces_remind_me_next_time_with_submit(self):
        self.assertEqual(watch.message(500, 'iPad', auto=True)[2:], ('Submit automatically', "Don't remind me again", 'Send')
                         if not watch.chinese() else ('自动提交', '不要提醒了', '发送'))
        with tempfile.TemporaryDirectory() as tmp:   # no gh on PATH or in the usual places: no automatic button
            old = (watch.GH_CANDIDATES, watch.shutil.which)
            watch.GH_CANDIDATES, watch.shutil.which = (str(Path(tmp) / 'gh'),), lambda name: None
            try:
                self.assertIsNone(watch.gh_path())
            finally:
                watch.GH_CANDIDATES, watch.shutil.which = old
