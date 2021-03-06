#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: BSD-3-Clause
# Copyright © 2017-2019, GoodData Corporation. All rights reserved.

import json
import logging
import mock
import os
import pytest
import requests_mock
import sh
from testfixtures import log_capture

import ipamanager.tools.github_forwarder as tool
testdir = os.path.dirname(__file__)

modulename = 'ipamanager.tools.github_forwarder'
responses = os.path.join(testdir, 'api-responses')


class TestGitHubForwarder(object):
    def setup_method(self, method):
        self.ts = '2017-12-29T23-59-59'
        self.gh = 'https://api.github.com/repos'
        with mock.patch('%s.socket.getfqdn' % modulename, lambda: 'ipa.dummy'):
            with mock.patch('time.strftime', lambda _: self.ts):
                with mock.patch('sys.argv', ['ipamanager-pr', 'dump_repo']):
                    self.forwarder = tool.GitHubForwarder()
        self.forwarder.args.repo = 'config-repo'
        self.forwarder.name = 'ipa.dummy'
        if method.func_name.startswith('test_pull_request_'):
            method_end = method.func_name.replace('test_pull_request_', '')
            self.forwarder._push = mock.Mock()
            self.forwarder.changes = True
            self.gh_mock = requests_mock.mock()
            try:
                self.resp = self._load_resp('create_pr_%s' % method_end)
                self.gh_mock.post('%s/gooddata/config-repo/pulls' % self.gh,
                                  text=self.resp, status_code=422)
            except IOError:
                pass

    def _load_resp(self, name):
        with open('%s/%s.json' % (responses, name), 'r') as f:
            return f.read()

    def test_run_no_action(self):
        self.forwarder.args.commit = False
        self.forwarder._commit = mock.Mock()
        self.forwarder.args.pull_request = False
        self.forwarder._create_pull_request = mock.Mock()
        self.forwarder.run()
        self.forwarder._commit.assert_not_called()
        self.forwarder._create_pull_request.assert_not_called()

    def test_run_commit(self):
        self.forwarder.args.commit = True
        self.forwarder._commit = mock.Mock()
        self.forwarder.args.pull_request = False
        self.forwarder._create_pull_request = mock.Mock()
        self.forwarder.run()
        self.forwarder._commit.assert_called_with()
        self.forwarder._create_pull_request.assert_not_called()

    def test_run_pull_request(self):
        self.forwarder.args.commit = False
        self.forwarder._commit = mock.Mock()
        self.forwarder.args.pull_request = True
        self.forwarder._create_pull_request = mock.Mock()
        self.forwarder.run()
        self.forwarder._commit.assert_called_with()
        self.forwarder._create_pull_request.assert_called_with()

    def test_commit(self):
        def _mock_time(*args):
            return '01 Jan 2017 12:34:56'
        self.forwarder.git = mock.Mock()
        with mock.patch('%s.socket.getfqdn' % modulename, lambda: 'ipa.dummy'):
            with mock.patch('%s.time.strftime' % modulename, _mock_time):
                self.forwarder._commit()
                self.forwarder.git.checkout.assert_called_with(
                    ['-B', 'freeipa-dev'])
                self.forwarder.git.add.assert_called_with(['-A', '.'])
                msg = 'Entity dump from ipa.dummy at 01 Jan 2017 12:34:56'
                self.forwarder.git.commit.assert_called_with(['-m', msg])

    @log_capture('GitHubForwarder', level=logging.INFO)
    def test_commit_no_changes(self, captured_log):
        self.forwarder.git = mock.Mock()
        cmd = "/usr/bin/git commit -m 'ipa.dummy dump at 2017-12-29T23-59-59'"
        stdout = ("On branch master\nYour branch is up-to-date with "
                  "'origin/master'.\nnothing to commit, working tree clean\n")
        self.forwarder.git.commit.side_effect = sh.ErrorReturnCode_1(
            cmd, stdout, '', False)
        with mock.patch('%s.socket.getfqdn' % modulename, lambda: 'ipa.dummy'):
            self.forwarder._commit()
        captured_log.check(('GitHubForwarder', 'INFO',
                            'No changes, nothing to commit'))

    def test_commit_error(self):
        self.forwarder.git = mock.Mock()
        self.forwarder.git.commit.side_effect = sh.ErrorReturnCode_1(
            "/usr/bin/git commit -am 'msg'", '', 'an error occured', False)
        with pytest.raises(tool.ManagerError) as exc:
            self.forwarder._commit()
        assert exc.value[0] == 'Committing failed: an error occured'

    def test_commit_no_repo(self):
        self.forwarder.git = mock.Mock()
        self.forwarder.args.path = 'wrong_path'
        err_msg = "[Errno 2] No such file or directory: 'wrong_path'"
        self.forwarder.git.commit.side_effect = OSError(err_msg)
        with pytest.raises(tool.ManagerError) as exc:
            self.forwarder._commit()
        assert exc.value[0] == "Committing failed: %s" % err_msg

    def test_push(self):
        self.forwarder.git = mock.Mock()
        self.forwarder.args.branch = 'branch'
        self.forwarder._push()
        self.forwarder.git.push.assert_called_with(
            ['billie-jean', 'branch', '-f'])

    def test_push_error(self):
        self.forwarder.git = mock.Mock()
        cmd = '/usr/bin/git push billie-jean some-branch'
        stderr = ("error: src refspec master does not match any.\n"
                  "error: failed to push some refs to "
                  "'ssh://git@github.com/gooddata/gdc-ipa-utils.git'\n")
        self.forwarder.git.push.side_effect = sh.ErrorReturnCode_1(
            cmd, '', stderr, False)
        with pytest.raises(tool.ManagerError) as exc:
            self.forwarder._push()
        assert exc.value[0] == (
            "Pushing failed: error: src refspec master does not match any.\n"
            "error: failed to push some refs to "
            "'ssh://git@github.com/gooddata/gdc-ipa-utils.git'\n")

    @mock.patch.dict(os.environ, {'EC2DATA_ENVIRONMENT': 'int'})
    def test_generate_branch_name(self):
        assert self.forwarder._generate_branch_name() == 'freeipa-int'

    @mock.patch('%s.requests' % modulename)
    def test_make_request(self, mock_requests):
        def _mock_dump(x):
            return str(sorted(x.items()))
        with mock.patch('%s.json.dumps' % modulename, _mock_dump):
            self.forwarder._make_request()
        dumped_data = ("[('base', 'master'), ('body', 'Entity dump from "
                       "ipa.dummy'), ('head', 'billie-jean:freeipa-dev'), "
                       "('title', 'Entity dump from ipa.dummy')]")
        mock_requests.post.assert_called_with(
            'https://api.github.com/repos/gooddata/config-repo/pulls',
            data=dumped_data, headers={'Authorization': 'token None'})

    def test_parse_github_error_messageonly(self):
        data = {'message': 'Bad Credentials'}
        assert self.forwarder._parse_github_error(data) == 'Bad Credentials'

    def test_parse_github_error_errorlist_one(self):
        data = json.loads(self._load_resp('create_pr_already_exists'))
        assert self.forwarder._parse_github_error(data) == (
            'Validation Failed (A pull request '
            'already exists for billie-jean:same-branch.)')

    def test_parse_github_error_errorlist_several(self):
        data = {
            'message': 'Validation Failed',
            'errors': [{'message': 'some error'},
                       {'field': 'base', 'code': 'invalid'}]
        }
        assert self.forwarder._parse_github_error(data) == (
            'Validation Failed (some error; base invalid)')

    def test_parse_github_error_default(self):
        data = {
            'message': 'Error happened',
            'errors': [{'message': 'some error'},
                       {'help': 'More error info'}]
        }
        assert self.forwarder._parse_github_error(data) == (
            "Error happened (some error; {'help': 'More error info'})")

    @log_capture('GitHubForwarder', level=logging.INFO)
    def test_pull_request_no_changes(self, captured_log):
        self.forwarder.changes = False
        self.forwarder._create_pull_request()
        self.forwarder._push.assert_not_called()
        captured_log.check(('GitHubForwarder', 'INFO',
                            'Not creating PR because there were no changes'))

    @log_capture('GitHubForwarder', level=logging.INFO)
    def test_pull_request_success(self, captured_log):
        self.forwarder.args.repo = 'config-repo'
        with requests_mock.mock() as gh_mock:
            gh_mock.post('%s/gooddata/config-repo/pulls' % self.gh,
                         text=self._load_resp('create_pr_success'))
            self.forwarder._create_pull_request()
            self.forwarder._push.assert_called_with()
        captured_log.check(('GitHubForwarder', 'INFO',
                            ('Pull request https://github.com/gooddata/config-'
                             'repo/pull/42 created successfully')))

    @log_capture('GitHubForwarder', level=logging.INFO)
    def test_pull_request_already_exists(self, captured_log):
        with self.gh_mock:
            self.forwarder._create_pull_request()
        captured_log.check(('GitHubForwarder', 'INFO',
                            'PR already exists, not creating another one.'))

    def test_pull_request_bad_credentials(self):
        with self.gh_mock:
            with pytest.raises(tool.ManagerError) as exc:
                self.forwarder._create_pull_request()
        assert exc.value[0] == 'Creating PR failed: Bad credentials'

    def test_pull_request_base_invalid(self):
        with self.gh_mock:
            with pytest.raises(tool.ManagerError) as exc:
                self.forwarder._create_pull_request()
        assert exc.value[0] == (
            'Creating PR failed: Validation Failed (base invalid)')

    def test_pull_request_head_invalid(self):
        with self.gh_mock:
            with pytest.raises(tool.ManagerError) as exc:
                self.forwarder._create_pull_request()
        assert exc.value[0] == (
            'Creating PR failed: Validation Failed (head invalid)')

    def test_pull_request_no_commits(self):
        with self.gh_mock:
            with pytest.raises(tool.ManagerError) as exc:
                self.forwarder._create_pull_request()
        assert exc.value[0] == (
            'Creating PR failed: Validation Failed '
            '(No commits between gooddata:master and billie-jean:some-branch)')
