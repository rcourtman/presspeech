#!/usr/bin/env python3
"""Real SDK Git objects and physical files; no Swift build or remote repository."""
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HELPER = Path(__file__).with_name('dependency-provenance.py')
spec = importlib.util.spec_from_file_location('dependency_provenance', HELPER)
provenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provenance)


def prepare_fixture(bench, production):
    checkout = bench / ".build/checkouts/FluidAudio"
    (checkout / "Sources/FluidAudio").mkdir(parents=True)
    (checkout / "Package.swift").write_text(
        '// swift-tools-version: 5.9\nimport PackageDescription\n'
        'let package = Package(name: "FluidAudio", products: ['
        '.library(name: "FluidAudio", targets: ["FluidAudio"])], '
        'targets: [.target(name: "FluidAudio")])\n')
    (checkout / "Sources/FluidAudio/Fixture.swift").write_text('public let fixture = true\n')
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    def git(*arguments, directory=checkout):
        return subprocess.check_output(
            ["git", "-C", str(directory), "-c", "user.name=Fixture", "-c",
             "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
             "-c", "core.hooksPath=" + os.devnull, *arguments], env=environment,
            stderr=subprocess.PIPE, text=True).strip()
    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "Fixture SDK")
    revision = git("rev-parse", "HEAD")
    url = "https://github.com/FluidInference/FluidAudio.git"
    for directory in (bench, production):
        package = directory / "Package.swift"
        manifest, count = re.subn(
            r'(\.package\(\s*url:\s*"' + re.escape(url) +
            r'"\s*,\s*revision:\s*")[0-9a-f]{40}("\s*\))',
            lambda match: match[1] + revision + match[2], package.read_text())
        assert count == 1, "fixture requires the real canonical dependency declaration"
        package.write_text(manifest)
        lock = package.with_name("Package.resolved")
        resolved = json.loads(lock.read_text())
        pins = [pin for pin in resolved["pins"] if pin["identity"] == "fluidaudio"]
        assert len(pins) == 1 and pins[0]["location"] == url
        pins[0]["state"]["revision"] = revision
        lock.write_text(json.dumps(resolved))
    (bench / ".build/workspace-state.json").write_text(json.dumps({"version": 6, "object": {
        "dependencies": [{"packageRef": {"identity": "fluidaudio", "location": url,
            "kind": "remoteSourceControl"}, "state": {"name": "sourceControlCheckout",
            "checkoutState": {"revision": revision}}, "subpath": "FluidAudio", "basedOn": None}]}}))
    repository = bench.parents[1]
    (repository / ".gitignore").write_text(".build/\n")
    git("init", "-q", directory=repository)
    git("add", ".", directory=repository)
    git("commit", "-qm", "Fixture benchmark", directory=repository)


class BuiltSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root/'benchmark'/'Package.swift'
        self.checkout = self.package.parent/'.build/checkouts/FluidAudio'
        self.checkout.mkdir(parents=True)
        self.git('init', '-q')
        (self.checkout/'Sources').mkdir()
        self.source = self.checkout/'Sources/FluidAudio.swift'
        self.source.write_text('public let value = 1\n')
        (self.checkout/'.gitignore').write_text('Sources/Ignored.swift\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Fixture baseline')
        self.sha = self.git('rev-parse', 'HEAD').strip()
        self.write_package(self.package, self.sha)
        self.production = self.root/'application/Package.swift'
        self.write_package(self.production, self.sha)
        self.state = {'version':6, 'object':{'dependencies':[{
            'packageRef':{'identity':'fluidaudio', 'location':provenance.URL,
                          'kind':'remoteSourceControl'},
            'state':{'name':'sourceControlCheckout','checkoutState':{'revision':self.sha}},
            'subpath':'FluidAudio', 'basedOn':None}]}}
        self.write_workspace()

    def git(self, *args):
        env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        return subprocess.check_output(['git','-C',str(self.checkout),
            '-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
            '-c','commit.gpgsign=false','-c','core.hooksPath='+os.devnull,*args],
            env=env, stderr=subprocess.PIPE, text=True)

    def write_package(self, package, sha):
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_text(f'.package(url: "{provenance.URL}", revision: "{sha}")\n')
        package.with_name('Package.resolved').write_text(json.dumps({'pins':[{
            'identity':'fluidaudio','location':provenance.URL,'state':{'revision':sha}}]}))

    def write_workspace(self):
        (self.package.parent/'.build/workspace-state.json').write_text(json.dumps(self.state))

    def verify(self):
        provenance.verify_built(self.package, self.sha)

    def cli(self, *args, env=None):
        return subprocess.run([sys.executable,str(HELPER),'--benchmark-package',str(self.package),
            '--production-package',str(self.production),'--verify-built',*args],
            capture_output=True,text=True,env=env)

    def test_clean_checkout_preserves_exact_three_column_output(self):
        result = self.cli()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,self.sha+'\t'+self.sha+'\tproduction-dependency\n')

    def test_clean_candidate_is_identified_and_not_a_production_baseline(self):
        self.write_package(self.production,'a'*40)
        result = self.cli()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,self.sha+'\t'+'a'*40+'\tcandidate-dependency\n')
        rejected = self.cli('--require-production')
        self.assertEqual(rejected.returncode,1)
        self.assertFalse(rejected.stdout)

    def test_modified_bytes_fail_even_with_assume_unchanged(self):
        self.git('update-index','--assume-unchanged','Sources/FluidAudio.swift')
        self.source.write_text('public let value = 99\n')
        self.assertEqual(self.git('status','--porcelain'),'')
        with self.assertRaisesRegex(ValueError,'source bytes'):self.verify()

    def test_staged_modified_bytes_fail(self):
        self.source.write_text('public let value = 99\n');self.git('add','.')
        with self.assertRaisesRegex(ValueError,'source bytes'):self.verify()

    def test_index_changes_do_not_replace_inspection_of_actual_source(self):
        self.source.write_text('public let value = 99\n');self.git('add','.')
        self.source.write_text('public let value = 1\n')
        self.verify()

    def test_ignored_added_source_fails(self):
        (self.checkout/'Sources/Ignored.swift').write_text('public let hidden = 99\n')
        self.assertEqual(self.git('status','--porcelain'),'')
        with self.assertRaisesRegex(ValueError,'untracked'):self.verify()

    def test_deleted_source_fails(self):
        self.source.unlink()
        with self.assertRaises(OSError):self.verify()

    def test_file_mode_change_fails(self):
        self.source.chmod(0o755)
        with self.assertRaisesRegex(ValueError,'mode'):self.verify()

    def test_different_head_fails_even_when_workspace_declares_old_revision(self):
        self.git('commit','--allow-empty','-qm','Fixture second revision')
        with self.assertRaisesRegex(ValueError,'HEAD'):self.verify()

    def test_inherited_git_directory_cannot_hide_a_different_actual_head(self):
        alternate = self.root/'alternate.git'
        subprocess.run(['git','clone','--bare','-q',str(self.checkout),str(alternate)],check=True)
        self.git('commit','--allow-empty','-qm','Fixture second revision')
        with mock.patch.dict(os.environ,{'GIT_DIR':str(alternate),'GIT_WORK_TREE':str(self.checkout),
                                        'GIT_COMMON_DIR':str(alternate)}):
            with self.assertRaisesRegex(ValueError,'HEAD'):self.verify()

    def test_injected_git_config_and_fsmonitor_are_not_executed(self):
        marker=self.root/'executed';program=self.root/'unsafe-hook'
        program.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\n');program.chmod(0o755)
        self.git('config','core.fsmonitor',str(program))
        with mock.patch.dict(os.environ,{'GIT_CONFIG_COUNT':'1',
              'GIT_CONFIG_KEY_0':'include.path','GIT_CONFIG_VALUE_0':str(self.root/'missing-config'),
              'GIT_INDEX_FILE':str(self.root/'other-index')}):
            self.verify()
        self.assertFalse(marker.exists())

    def test_workspace_edited_noncanonical_and_duplicate_dependencies_fail(self):
        original=json.loads(json.dumps(self.state))
        for mutation in ('edited','revision','location','kind','basedOn','duplicate','subpath'):
            with self.subTest(mutation=mutation):
                self.state=json.loads(json.dumps(original));selected=self.state['object']['dependencies'][0]
                if mutation=='edited':selected['state']['name']='edited'
                elif mutation=='revision':selected['state']['checkoutState']['revision']='b'*40
                elif mutation=='location':selected['packageRef']['location']='https://example.invalid/FluidAudio.git'
                elif mutation=='kind':selected['packageRef']['kind']='fileSystem'
                elif mutation=='basedOn':selected['basedOn']={}
                elif mutation=='duplicate':self.state['object']['dependencies'].append(selected.copy())
                elif mutation=='subpath':selected['subpath']='../FluidAudio'
                self.write_workspace()
                with self.assertRaises(ValueError):self.verify()

    def test_malformed_workspace_shapes_refuse_without_traceback_or_stdout(self):
        valid=json.loads(json.dumps(self.state))
        malformed=[[],None,{'object':[]},{'object':None},{'object':{'dependencies':{}}}]
        for state in (None,[],{'checkoutState':None},{'checkoutState':[]}):
            item=json.loads(json.dumps(valid));item['object']['dependencies'][0]['state']=state;malformed.append(item)
        for workspace in malformed:
            with self.subTest(workspace=workspace):
                self.state=workspace;self.write_workspace();result=self.cli()
                self.assertEqual(result.returncode,1)
                self.assertFalse(result.stdout)
                self.assertIn('Dependency provenance refused:',result.stderr)
                self.assertNotIn('Traceback',result.stderr)

    def test_external_symlink_cannot_supply_tracked_source(self):
        other=self.root/'external.swift';other.write_text(self.source.read_text())
        self.source.unlink();self.source.symlink_to(other)
        self.git('add','.');self.git('commit','-qm','Fixture external link')
        self.sha=self.git('rev-parse','HEAD').strip()
        self.state['object']['dependencies'][0]['state']['checkoutState']['revision']=self.sha
        self.write_workspace()
        with self.assertRaises(ValueError):self.verify()

    def test_checkout_git_metadata_redirection_is_refused(self):
        (self.checkout/'.git/commondir').write_text(str(self.root/'outside'))
        with self.assertRaisesRegex(ValueError,'redirect'):self.verify()

    def test_filesystem_scan_errors_are_not_silently_ignored(self):
        def broken_walk(*args,**kwargs):
            kwargs['onerror'](PermissionError('unreadable directory'))
            return iter(())
        with mock.patch.object(provenance.os,'walk',side_effect=broken_walk):
            with self.assertRaises(PermissionError):self.verify()

    def test_ambiguous_duplicate_metadata_keys_are_rejected(self):
        workspace = self.package.parent / '.build/workspace-state.json'
        original = workspace.read_text()
        workspace.write_text(original.replace('"dependencies":', '"dependencies": [], "dependencies":', 1))
        with self.assertRaisesRegex(ValueError, 'duplicate JSON keys'):
            self.verify()
        workspace.write_text(original)
        lock = self.package.with_name('Package.resolved')
        lock.write_text(lock.read_text().replace('"pins":', '"pins": [], "pins":', 1))
        rejected = self.cli()
        self.assertEqual(rejected.returncode, 1)
        self.assertFalse(rejected.stdout)
        self.assertIn('duplicate JSON keys', rejected.stderr)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--prepare-fixture':
        prepare_fixture(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        unittest.main()
