# (C) Datadog, Inc. 2015-2017
# All rights reserved
# Licensed under Simplified BSD License (see LICENSE)

"""Mesos Master check

Collects metrics from mesos master node, only the leader is sending metrics.
"""

import requests
from six import iteritems
from six.moves.urllib.parse import urlparse

from datadog_checks.checks import AgentCheck
from datadog_checks.config import _is_affirmative
from datadog_checks.errors import CheckException


class MesosMaster(AgentCheck):
    GAUGE = AgentCheck.gauge
    MONOTONIC_COUNT = AgentCheck.monotonic_count
    SERVICE_CHECK_NAME = "mesos_master.can_connect"
    service_check_needed = True

    FRAMEWORK_METRICS = {
        'cpus': ('mesos.framework.cpu', GAUGE),
        'mem': ('mesos.framework.mem', GAUGE),
        'disk': ('mesos.framework.disk', GAUGE),
    }

    ROLE_RESOURCES_METRICS = {
        'cpus': ('mesos.role.cpu', GAUGE),
        'mem': ('mesos.role.mem', GAUGE),
        'disk': ('mesos.role.disk', GAUGE),
    }

    # These metrics are aggregated only on the elected master
    CLUSTER_TASKS_METRICS = {
        'master/tasks_error': ('mesos.cluster.tasks_error', GAUGE),
        'master/tasks_failed': ('mesos.cluster.tasks_failed', MONOTONIC_COUNT),
        'master/tasks_finished': ('mesos.cluster.tasks_finished', MONOTONIC_COUNT),
        'master/tasks_killed': ('mesos.cluster.tasks_killed', MONOTONIC_COUNT),
        'master/tasks_lost': ('mesos.cluster.tasks_lost', MONOTONIC_COUNT),
        'master/tasks_running': ('mesos.cluster.tasks_running', GAUGE),
        'master/tasks_staging': ('mesos.cluster.tasks_staging', GAUGE),
        'master/tasks_starting': ('mesos.cluster.tasks_starting', GAUGE),
    }

    # These metrics are aggregated only on the elected master
    CLUSTER_SLAVES_METRICS = {
        'master/slave_registrations': ('mesos.cluster.slave_registrations', GAUGE),
        'master/slave_removals': ('mesos.cluster.slave_removals', GAUGE),
        'master/slave_reregistrations': ('mesos.cluster.slave_reregistrations', GAUGE),
        'master/slave_shutdowns_canceled': ('mesos.cluster.slave_shutdowns_canceled', GAUGE),
        'master/slave_shutdowns_scheduled': ('mesos.cluster.slave_shutdowns_scheduled', GAUGE),
        'master/slaves_active': ('mesos.cluster.slaves_active', GAUGE),
        'master/slaves_connected': ('mesos.cluster.slaves_connected', GAUGE),
        'master/slaves_disconnected': ('mesos.cluster.slaves_disconnected', GAUGE),
        'master/slaves_inactive': ('mesos.cluster.slaves_inactive', GAUGE),
        'master/recovery_slave_removals': ('mesos.cluster.recovery_slave_removals', GAUGE),
    }

    # These metrics are aggregated only on the elected master
    CLUSTER_RESOURCES_METRICS = {
        'master/cpus_percent': ('mesos.cluster.cpus_percent', GAUGE),
        'master/cpus_total': ('mesos.cluster.cpus_total', GAUGE),
        'master/cpus_used': ('mesos.cluster.cpus_used', GAUGE),
        'master/gpus_percent': ('mesos.cluster.gpus_percent', GAUGE),
        'master/gpus_total': ('mesos.cluster.gpus_total', GAUGE),
        'master/gpus_used': ('mesos.cluster.gpus_used', GAUGE),
        'master/disk_percent': ('mesos.cluster.disk_percent', GAUGE),
        'master/disk_total': ('mesos.cluster.disk_total', GAUGE),
        'master/disk_used': ('mesos.cluster.disk_used', GAUGE),
        'master/mem_percent': ('mesos.cluster.mem_percent', GAUGE),
        'master/mem_total': ('mesos.cluster.mem_total', GAUGE),
        'master/mem_used': ('mesos.cluster.mem_used', GAUGE),
    }

    # These metrics are aggregated only on the elected master
    CLUSTER_REGISTRAR_METRICS = {
        'registrar/queued_operations': ('mesos.registrar.queued_operations', GAUGE),
        'registrar/registry_size_bytes': ('mesos.registrar.registry_size_bytes', GAUGE),
        'registrar/state_fetch_ms': ('mesos.registrar.state_fetch_ms', GAUGE),
        'registrar/state_store_ms': ('mesos.registrar.state_store_ms', GAUGE),
        'registrar/state_store_ms/count': ('mesos.registrar.state_store_ms.count', GAUGE),
        'registrar/state_store_ms/max': ('mesos.registrar.state_store_ms.max', GAUGE),
        'registrar/state_store_ms/min': ('mesos.registrar.state_store_ms.min', GAUGE),
        'registrar/state_store_ms/p50': ('mesos.registrar.state_store_ms.p50', GAUGE),
        'registrar/state_store_ms/p90': ('mesos.registrar.state_store_ms.p90', GAUGE),
        'registrar/state_store_ms/p95': ('mesos.registrar.state_store_ms.p95', GAUGE),
        'registrar/state_store_ms/p99': ('mesos.registrar.state_store_ms.p99', GAUGE),
        'registrar/state_store_ms/p999': ('mesos.registrar.state_store_ms.p999', GAUGE),
        'registrar/state_store_ms/p9999': ('mesos.registrar.state_store_ms.p9999', GAUGE),
    }

    # These metrics are aggregated only on the elected master
    CLUSTER_FRAMEWORK_METRICS = {
        'master/frameworks_active': ('mesos.cluster.frameworks_active', GAUGE),
        'master/frameworks_connected': ('mesos.cluster.frameworks_connected', GAUGE),
        'master/frameworks_disconnected': ('mesos.cluster.frameworks_disconnected', GAUGE),
        'master/frameworks_inactive': ('mesos.cluster.frameworks_inactive', GAUGE),
    }

    # These metrics are aggregated on all nodes in the cluster
    SYSTEM_METRICS = {
        'system/cpus_total': ('mesos.stats.system.cpus_total', GAUGE),
        'system/load_15min': ('mesos.stats.system.load_15min', GAUGE),
        'system/load_1min': ('mesos.stats.system.load_1min', GAUGE),
        'system/load_5min': ('mesos.stats.system.load_5min', GAUGE),
        'system/mem_free_bytes': ('mesos.stats.system.mem_free_bytes', GAUGE),
        'system/mem_total_bytes': ('mesos.stats.system.mem_total_bytes', GAUGE),
        'master/elected': ('mesos.stats.elected', GAUGE),
        'master/uptime_secs': ('mesos.stats.uptime_secs', GAUGE),
        'registrar/log/recovered': ('mesos.registrar.log.recovered', GAUGE),
    }

    # These metrics are aggregated only on the elected master
    STATS_METRICS = {
        'master/dropped_messages': ('mesos.cluster.dropped_messages', GAUGE),
        'master/outstanding_offers': ('mesos.cluster.outstanding_offers', GAUGE),
        'master/event_queue_dispatches': ('mesos.cluster.event_queue_dispatches', GAUGE),
        'master/event_queue_http_requests': ('mesos.cluster.event_queue_http_requests', GAUGE),
        'master/event_queue_messages': ('mesos.cluster.event_queue_messages', GAUGE),
        'master/invalid_framework_to_executor_messages': (
            'mesos.cluster.invalid_framework_to_executor_messages',
            GAUGE,
        ),
        'master/invalid_status_update_acknowledgements': (
            'mesos.cluster.invalid_status_update_acknowledgements',
            GAUGE,
        ),
        'master/invalid_status_updates': ('mesos.cluster.invalid_status_updates', GAUGE),
        'master/valid_framework_to_executor_messages': ('mesos.cluster.valid_framework_to_executor_messages', GAUGE),
        'master/valid_status_update_acknowledgements': ('mesos.cluster.valid_status_update_acknowledgements', GAUGE),
        'master/valid_status_updates': ('mesos.cluster.valid_status_updates', GAUGE),
    }

    def __init__(self, name, init_config, agentConfig, instances=None):
        AgentCheck.__init__(self, name, init_config, agentConfig, instances)
        for instance in instances or []:
            url = instance.get('url', '')
            parsed_url = urlparse(url)
            ssl_verify = not _is_affirmative(instance.get('disable_ssl_validation', False))
            if not ssl_verify and parsed_url.scheme == 'https':
                self.log.warning('Skipping SSL cert validation for {0} based on configuration.'.format(url))

    def _get_json(self, url, timeout, verify=True, failure_expected=False, tags=None):
        tags = tags + ["url:%s" % url] if tags else ["url:%s" % url]
        msg = None
        status = None
        try:
            r = requests.get(url, timeout=timeout, verify=verify)
            if r.status_code != 200:
                status = AgentCheck.CRITICAL
                msg = "Got %s when hitting %s" % (r.status_code, url)
            else:
                status = AgentCheck.OK
                msg = "Mesos master instance detected at %s " % url
        except requests.exceptions.Timeout:
            # If there's a timeout
            msg = "%s seconds timeout when hitting %s" % (timeout, url)
            status = AgentCheck.CRITICAL
        except Exception as e:
            msg = str(e)
            status = AgentCheck.CRITICAL
        finally:
            self.log.debug('Request to url : {0}, timeout: {1}, message: {2}'.format(url, timeout, msg))
            self._send_service_check(url, r, status, failure_expected=failure_expected, tags=tags, message=msg)

        if r.encoding is None:
            r.encoding = 'UTF8'

        return r.json()

    def _send_service_check(self, url, response, status, failure_expected=False, tags=None, message=None):
        if status is AgentCheck.CRITICAL and failure_expected:
            status = AgentCheck.OK
            message = "Got %s when hitting %s" % (response.status_code, url)
            raise CheckException(message)
        elif status is AgentCheck.CRITICAL and not failure_expected:
            raise CheckException('Cannot connect to mesos. Error: {0}'.format(message))
        if self.service_check_needed:
            self.service_check(self.SERVICE_CHECK_NAME, status, tags=tags, message=message)
            self.service_check_needed = False

    def _get_master_state(self, url, timeout, verify, tags):
        try:
            # Mesos version >= 0.25
            endpoint = url + '/state'
            master_state = self._get_json(endpoint, timeout, verify=verify, failure_expected=True, tags=tags)
        except CheckException:
            # Mesos version < 0.25
            old_endpoint = endpoint + '.json'
            self.log.info(
                'Unable to fetch state from {0}. Retrying with the deprecated endpoint: {1}.'.format(
                    endpoint, old_endpoint
                )
            )
            master_state = self._get_json(old_endpoint, timeout, verify=verify, tags=tags)
        return master_state

    def _get_master_stats(self, url, timeout, verify, tags):
        if self.version >= [0, 22, 0]:
            endpoint = url + '/metrics/snapshot'
        else:
            endpoint = url + '/stats.json'
        return self._get_json(endpoint, timeout, verify, tags)

    def _get_master_roles(self, url, timeout, verify, tags):
        if self.version >= [1, 8, 0]:
            endpoint = url + '/roles'
        else:
            endpoint = url + '/roles.json'
        return self._get_json(endpoint, timeout, verify, tags)

    def _check_leadership(self, url, timeout, verify, tags=None):
        state_metrics = self._get_master_state(url, timeout, verify, tags)
        self.leader = False

        if state_metrics is not None:
            self.version = [int(i) for i in state_metrics['version'].split('.')]
            if state_metrics['leader'] == state_metrics['pid']:
                self.leader = True

        return state_metrics

    def check(self, instance):
        if 'url' not in instance:
            raise Exception('Mesos instance missing "url" value.')

        url = instance['url']
        instance_tags = instance.get('tags', [])
        if instance_tags is None:
            instance_tags = []
        default_timeout = self.init_config.get('default_timeout', 5)
        timeout = float(instance.get('timeout', default_timeout))
        ssl_verify = not _is_affirmative(instance.get('disable_ssl_validation', False))

        state_metrics = self._check_leadership(url, timeout, ssl_verify, instance_tags)
        if state_metrics:
            tags = ['mesos_pid:{0}'.format(state_metrics['pid']), 'mesos_node:master']
            if 'cluster' in state_metrics:
                tags.append('mesos_cluster:{0}'.format(state_metrics['cluster']))

            tags += instance_tags

            if self.leader:
                self.GAUGE('mesos.cluster.total_frameworks', len(state_metrics['frameworks']), tags=tags)

                for framework in state_metrics['frameworks']:
                    framework_tags = ['framework_name:' + framework['name']] + tags
                    self.GAUGE('mesos.framework.total_tasks', len(framework['tasks']), tags=framework_tags)
                    resources = framework['used_resources']
                    for key_name, (metric_name, metric_func) in iteritems(self.FRAMEWORK_METRICS):
                        metric_func(self, metric_name, resources[key_name], tags=framework_tags)

                role_metrics = self._get_master_roles(url, timeout, ssl_verify, instance_tags)
                if role_metrics is not None:
                    for role in role_metrics['roles']:
                        role_tags = ['mesos_role:' + role['name']] + tags
                        self.GAUGE('mesos.role.frameworks.count', len(role['frameworks']), tags=role_tags)
                        self.GAUGE('mesos.role.weight', role['weight'], tags=role_tags)
                        for key_name, (metric_name, metric_func) in iteritems(self.ROLE_RESOURCES_METRICS):
                            metric_func(self, metric_name, role['resources'][key_name], tags=role_tags)

            stats_metrics = self._get_master_stats(url, timeout, ssl_verify, instance_tags)
            if stats_metrics is not None:
                metrics = [self.SYSTEM_METRICS]
                if self.leader:
                    metrics += [
                        self.CLUSTER_TASKS_METRICS,
                        self.CLUSTER_SLAVES_METRICS,
                        self.CLUSTER_RESOURCES_METRICS,
                        self.CLUSTER_REGISTRAR_METRICS,
                        self.CLUSTER_FRAMEWORK_METRICS,
                        self.STATS_METRICS,
                    ]
                for m in metrics:
                    for key_name, (metric_name, metric_func) in iteritems(m):
                        if key_name in stats_metrics:
                            metric_func(self, metric_name, stats_metrics[key_name], tags=tags)

        self.service_check_needed = True
