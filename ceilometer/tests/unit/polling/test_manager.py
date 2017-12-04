#
# Copyright 2013 Intel Corp.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
"""Tests for ceilometer agent manager"""
import fixtures
from keystoneauth1 import exceptions as ka_exceptions
import mock
from oslotest import base
from stevedore import extension

from ceilometer.compute import discovery as nova_discover
from ceilometer.hardware import discovery
from ceilometer.polling import manager
from ceilometer.polling import plugin_base
from ceilometer import sample
from ceilometer import service
from ceilometer.tests.unit.polling import agentbase


def fakedelayed(delay, target, *args, **kwargs):
    return target(*args, **kwargs)


class PollingException(Exception):
    pass


class TestPollsterBuilder(agentbase.TestPollster):
    @classmethod
    def build_pollsters(cls, conf):
        return [('builder1', cls(conf)), ('builder2', cls(conf))]


class TestManager(base.BaseTestCase):
    def setUp(self):
        super(TestManager, self).setUp()
        self.conf = service.prepare_service([], [])

    def test_load_plugins(self):
        mgr = manager.AgentManager(0, self.conf)
        self.assertIsNotNone(list(mgr.extensions))

    # Test plugin load behavior based on Node Manager pollsters.
    @mock.patch('ceilometer.ipmi.pollsters.node._Base.__init__',
                mock.Mock(return_value=None))
    @mock.patch('ceilometer.ipmi.pollsters.sensor.SensorPollster.__init__',
                mock.Mock(return_value=None))
    def test_load_normal_plugins(self):
        mgr = manager.AgentManager(0, self.conf,
                                   namespaces=['ipmi'])
        # 8 pollsters for Node Manager
        self.assertEqual(12, len(mgr.extensions))

    # Skip loading pollster upon ExtensionLoadError
    @mock.patch('ceilometer.ipmi.pollsters.node._Base.__init__',
                mock.Mock(side_effect=plugin_base.ExtensionLoadError))
    @mock.patch('ceilometer.ipmi.pollsters.sensor.SensorPollster.__init__',
                mock.Mock(return_value=None))
    @mock.patch('ceilometer.polling.manager.LOG')
    def test_load_failed_plugins(self, LOG):
        # Here we additionally check that namespaces will be converted to the
        # list if param was not set as a list.
        try:
            manager.AgentManager(0, self.conf,
                                 namespaces='ipmi')
        except manager.EmptyPollstersList:
            err_msg = 'Skip loading extension for %s'
            pollster_names = [
                'power', 'temperature', 'outlet_temperature',
                'airflow', 'cups', 'cpu_util', 'mem_util', 'io_util']
            calls = [mock.call(err_msg, 'hardware.ipmi.node.%s' % n)
                     for n in pollster_names]
            LOG.exception.assert_has_calls(calls=calls, any_order=True)

    # Skip loading pollster upon ImportError
    @mock.patch('ceilometer.ipmi.pollsters.node._Base.__init__',
                mock.Mock(side_effect=ImportError))
    @mock.patch('ceilometer.ipmi.pollsters.sensor.SensorPollster.__init__',
                mock.Mock(return_value=None))
    def test_import_error_in_plugin(self):
        self.assertRaisesRegex(
            manager.EmptyPollstersList,
            'No valid pollsters can be loaded with the startup parameter'
            ' polling-namespaces.',
            manager.AgentManager, 0, self.conf, {"namespaces": ['ipmi']})

    # Exceptions other than ExtensionLoadError are propagated
    @mock.patch('ceilometer.ipmi.pollsters.node._Base.__init__',
                mock.Mock(side_effect=PollingException))
    @mock.patch('ceilometer.ipmi.pollsters.sensor.SensorPollster.__init__',
                mock.Mock(return_value=None))
    def test_load_exceptional_plugins(self):
        self.assertRaises(PollingException,
                          manager.AgentManager,
                          0, self.conf,
                          ['ipmi'])

    def test_builder(self):
        @staticmethod
        def fake_get_ext_mgr(namespace, *args, **kwargs):
            if 'builder' in namespace:
                return extension.ExtensionManager.make_test_instance(
                    [
                        extension.Extension('builder',
                                            None,
                                            TestPollsterBuilder,
                                            None),
                    ]
                )
            else:
                return extension.ExtensionManager.make_test_instance(
                    [
                        extension.Extension('test',
                                            None,
                                            None,
                                            agentbase.TestPollster(
                                                self.conf)),
                    ]
                )

        with mock.patch.object(manager.AgentManager, '_get_ext_mgr',
                               new=fake_get_ext_mgr):
            mgr = manager.AgentManager(0, self.conf, namespaces=['central'])
            self.assertEqual(3, len(mgr.extensions))
            for ext in mgr.extensions:
                self.assertIn(ext.name, ['builder1', 'builder2', 'test'])
                self.assertIsInstance(ext.obj, agentbase.TestPollster)


class TestPollsterKeystone(agentbase.TestPollster):
    def get_samples(self, manager, cache, resources):
        # Just try to use keystone, that will raise an exception
        manager.keystone.projects.list()


class TestPollsterPollingException(agentbase.TestPollster):
    discovery = 'test'
    polling_failures = 0

    def get_samples(self, manager, cache, resources):
        func = super(TestPollsterPollingException, self).get_samples
        sample = func(manager=manager,
                      cache=cache,
                      resources=resources)

        # Raise polling exception after 2 times
        self.polling_failures += 1
        if self.polling_failures > 2:
            raise plugin_base.PollsterPermanentError(resources)

        return sample


class TestRunTasks(agentbase.BaseAgentManagerTestCase):

    class PollsterKeystone(TestPollsterKeystone):
        samples = []
        resources = []
        test_data = sample.Sample(
            name='testkeystone',
            type=agentbase.default_test_data.type,
            unit=agentbase.default_test_data.unit,
            volume=agentbase.default_test_data.volume,
            user_id=agentbase.default_test_data.user_id,
            project_id=agentbase.default_test_data.project_id,
            resource_id=agentbase.default_test_data.resource_id,
            timestamp=agentbase.default_test_data.timestamp,
            resource_metadata=agentbase.default_test_data.resource_metadata)

    class PollsterPollingException(TestPollsterPollingException):
        samples = []
        resources = []
        test_data = sample.Sample(
            name='testpollingexception',
            type=agentbase.default_test_data.type,
            unit=agentbase.default_test_data.unit,
            volume=agentbase.default_test_data.volume,
            user_id=agentbase.default_test_data.user_id,
            project_id=agentbase.default_test_data.project_id,
            resource_id=agentbase.default_test_data.resource_id,
            timestamp=agentbase.default_test_data.timestamp,
            resource_metadata=agentbase.default_test_data.resource_metadata)

    def create_manager(self):
        return manager.AgentManager(0, self.CONF)

    def fake_notifier_sample(self, ctxt, event_type, payload):
        for m in payload['samples']:
            del m['message_signature']
            self.notified_samples.append(m)

    def setUp(self):
        self.notified_samples = []
        self.notifier = mock.Mock()
        self.notifier.sample.side_effect = self.fake_notifier_sample
        self.useFixture(fixtures.MockPatch('oslo_messaging.Notifier',
                                           return_value=self.notifier))
        super(TestRunTasks, self).setUp()
        self.useFixture(fixtures.MockPatch(
            'keystoneclient.v2_0.client.Client',
            return_value=mock.Mock()))

    def tearDown(self):
        self.PollsterKeystone.samples = []
        self.PollsterKeystone.resources = []
        self.PollsterPollingException.samples = []
        self.PollsterPollingException.resources = []
        super(TestRunTasks, self).tearDown()

    def create_extension_list(self):
        exts = super(TestRunTasks, self).create_extension_list()
        exts.extend([extension.Extension('testkeystone',
                                         None,
                                         None,
                                         self.PollsterKeystone(self.CONF), ),
                     extension.Extension('testpollingexception',
                                         None,
                                         None,
                                         self.PollsterPollingException(
                                             self.CONF), )])
        return exts

    def _build_and_set_new_polling(self):
        name = self.cfg2file(self.poll_cfg)
        self.CONF.set_override('cfg_file', name, group='polling')

    def test_get_sample_resources(self):
        polling_tasks = self.mgr.setup_polling_tasks()
        self.mgr.interval_task(list(polling_tasks.values())[0])
        self.assertTrue(self.Pollster.resources)

    def test_when_keystone_fail(self):
        """Test for bug 1316532."""
        self.useFixture(fixtures.MockPatch(
            'keystoneclient.v2_0.client.Client',
            side_effect=ka_exceptions.ClientException))
        self.poll_cfg = {
            'sources': [{
                'name': "test_keystone",
                'interval': 10,
                'meters': ['testkeystone'],
                'resources': ['test://'],
                'sinks': ['test_sink']}],
            'sinks': [{
                'name': 'test_sink',
                'transformers': [],
                'publishers': ["test"]}]
        }
        self._build_and_set_new_polling()
        self.mgr.polling_manager = manager.PollingManager(self.CONF)
        polling_tasks = self.mgr.setup_polling_tasks()
        self.mgr.interval_task(list(polling_tasks.values())[0])
        self.assertFalse(self.PollsterKeystone.samples)
        self.assertFalse(self.notified_samples)

    @mock.patch('ceilometer.polling.manager.LOG')
    @mock.patch('ceilometer.nova_client.LOG')
    def test_hardware_discover_fail_minimize_logs(self, novalog, baselog):
        class PollsterHardware(agentbase.TestPollster):
            discovery = 'tripleo_overcloud_nodes'

        class PollsterHardwareAnother(agentbase.TestPollster):
            discovery = 'tripleo_overcloud_nodes'

        self.mgr.extensions.extend([
            extension.Extension('testhardware',
                                None,
                                None,
                                PollsterHardware(self.CONF), ),
            extension.Extension('testhardware2',
                                None,
                                None,
                                PollsterHardwareAnother(self.CONF), )
        ])
        ext = extension.Extension('tripleo_overcloud_nodes',
                                  None,
                                  None,
                                  discovery.NodesDiscoveryTripleO(self.CONF))
        self.mgr.discoveries = (extension.ExtensionManager
                                .make_test_instance([ext]))

        self.poll_cfg = {
            'sources': [{
                'name': "test_hardware",
                'interval': 10,
                'meters': ['testhardware', 'testhardware2'],
                'sinks': ['test_sink']}],
            'sinks': [{
                'name': 'test_sink',
                'transformers': [],
                'publishers': ["test"]}]
        }
        self._build_and_set_new_polling()
        self.mgr.polling_manager = manager.PollingManager(self.CONF)
        polling_tasks = self.mgr.setup_polling_tasks()
        self.mgr.interval_task(list(polling_tasks.values())[0])
        self.assertEqual(1, novalog.exception.call_count)
        self.assertFalse(baselog.exception.called)

    @mock.patch('ceilometer.polling.manager.LOG')
    def test_polling_exception(self, LOG):
        source_name = 'test_pollingexception'
        res_list = ['test://']
        self.poll_cfg = {
            'sources': [{
                'name': source_name,
                'interval': 10,
                'meters': ['testpollingexception'],
                'resources': res_list,
                'sinks': ['test_sink']}],
            'sinks': [{
                'name': 'test_sink',
                'transformers': [],
                'publishers': ["test"]}]
        }
        self._build_and_set_new_polling()
        self.mgr.polling_manager = manager.PollingManager(self.CONF)
        polling_task = list(self.mgr.setup_polling_tasks().values())[0]
        pollster = list(polling_task.pollster_matches[source_name])[0]

        # 2 samples after 4 pollings, as pollster got disabled upon exception
        for x in range(0, 4):
            self.mgr.interval_task(polling_task)
        samples = self.notified_samples
        self.assertEqual(2, len(samples))
        LOG.error.assert_called_once_with((
            'Prevent pollster %(name)s from '
            'polling %(res_list)s on source %(source)s anymore!'),
            dict(name=pollster.name, res_list=str(res_list),
                 source=source_name))

    @mock.patch('ceilometer.polling.manager.LOG')
    def test_polling_novalike_exception(self, LOG):
        source_name = 'test_pollingexception'
        self.poll_cfg = {
            'sources': [{
                'name': source_name,
                'interval': 10,
                'meters': ['testpollingexception'],
                'sinks': ['test_sink']}],
            'sinks': [{
                'name': 'test_sink',
                'transformers': [],
                'publishers': ["test"]}]
        }
        self._build_and_set_new_polling()
        self.mgr.polling_manager = manager.PollingManager(self.CONF)
        polling_task = list(self.mgr.setup_polling_tasks().values())[0]
        pollster = list(polling_task.pollster_matches[source_name])[0]

        with mock.patch.object(polling_task.manager, 'discover') as disco:
            # NOTE(gordc): polling error on 3rd poll
            for __ in range(4):
                disco.return_value = (
                    [nova_discover.NovaLikeServer(**{'id': 1})])
                self.mgr.interval_task(polling_task)
        LOG.error.assert_called_once_with((
            'Prevent pollster %(name)s from '
            'polling %(res_list)s on source %(source)s anymore!'),
            dict(name=pollster.name,
                 res_list="[<NovaLikeServer: unknown-name>]",
                 source=source_name))

    def test_batching_polled_samples_false(self):
        self.CONF.set_override('batch_polled_samples', False)
        self._batching_samples(4, 4)

    def test_batching_polled_samples_true(self):
        self.CONF.set_override('batch_polled_samples', True)
        self._batching_samples(4, 1)

    def test_batching_polled_samples_default(self):
        self._batching_samples(4, 1)

    def _batching_samples(self, expected_samples, call_count):
        self.useFixture(fixtures.MockPatchObject(manager.utils, 'delayed',
                                                 side_effect=fakedelayed))
        self.poll_cfg = {
            'sources': [{
                'name': 'test_pipeline',
                'interval': 1,
                'meters': ['testbatch'],
                'resources': ['alpha', 'beta', 'gamma', 'delta'],
                'sinks': ['test_sink']}],
            'sinks': [{
                'name': 'test_sink',
                'transformers': [],
                'publishers': ["test"]}]
        }
        self._build_and_set_new_polling()
        self.mgr.polling_manager = manager.PollingManager(self.CONF)
        polling_task = list(self.mgr.setup_polling_tasks().values())[0]

        self.mgr.interval_task(polling_task)
        samples = self.notified_samples
        self.assertEqual(expected_samples, len(samples))
        self.assertEqual(call_count, self.notifier.sample.call_count)
