# (C) Datadog, Inc. 2018
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)

import logging

import pytest
from six import iteritems

from datadog_checks.base import AgentCheck
from datadog_checks.ibm_mq import IbmMqCheck

from . import common
from .common import METRICS, OPTIONAL_METRICS, QUEUE_METRICS

log = logging.getLogger(__file__)


@pytest.mark.usefixtures("dd_environment")
def test_service_check_connection_issues(aggregator, instance, seed_data):
    check = IbmMqCheck('ibm_mq', {}, {})
    check.check(instance)

    for metric in METRICS:
        aggregator.assert_metric(metric)

    for metric in OPTIONAL_METRICS:
        aggregator.assert_metric(metric, at_least=0)

    aggregator.assert_all_metrics_covered()

    tags = [
        'queue_manager:{}'.format(common.QUEUE_MANAGER),
        'mq_host:{}'.format(common.HOST),
        'port:{}'.format(common.PORT),
    ]

    channel_tags = tags + ['channel:{}'.format(common.CHANNEL)]
    aggregator.assert_service_check('ibm_mq.channel', check.OK, tags=channel_tags)
    bad_channel_tags = tags + ['channel:{}'.format(common.BAD_CHANNEL)]
    aggregator.assert_service_check('ibm_mq.channel', check.CRITICAL, tags=bad_channel_tags)


@pytest.mark.usefixtures("dd_environment")
def test_service_check_from_status(aggregator, instance, seed_data):
    # Late import to not require it for e2e
    import pymqi

    check = IbmMqCheck('ibm_mq', {}, {})

    service_check_map = {
        pymqi.CMQCFC.MQCHS_INACTIVE: AgentCheck.CRITICAL,
        pymqi.CMQCFC.MQCHS_BINDING: AgentCheck.WARNING,
        pymqi.CMQCFC.MQCHS_STARTING: AgentCheck.WARNING,
        pymqi.CMQCFC.MQCHS_RUNNING: AgentCheck.OK,
        pymqi.CMQCFC.MQCHS_STOPPING: AgentCheck.CRITICAL,
        pymqi.CMQCFC.MQCHS_RETRYING: AgentCheck.WARNING,
        pymqi.CMQCFC.MQCHS_STOPPED: AgentCheck.CRITICAL,
        pymqi.CMQCFC.MQCHS_REQUESTING: AgentCheck.WARNING,
        pymqi.CMQCFC.MQCHS_PAUSED: AgentCheck.WARNING,
        pymqi.CMQCFC.MQCHS_INITIALIZING: AgentCheck.WARNING,
    }

    for status in service_check_map:
        check._submit_status_check('my_channel', status, ["channel:my_channel_{}".format(status)])

    for status, service_check_status in iteritems(service_check_map):
        aggregator.assert_service_check(
            'ibm_mq.channel.status', service_check_status, tags=["channel:my_channel_{}".format(status)]
        )


@pytest.mark.usefixtures("dd_environment")
def test_check_queue_pattern(aggregator, instance_queue_pattern, seed_data):
    check = IbmMqCheck('ibm_mq', {}, {})
    check.check(instance_queue_pattern)

    for metric in METRICS:
        aggregator.assert_metric(metric)

    for metric in OPTIONAL_METRICS:
        aggregator.assert_metric(metric, at_least=0)

    aggregator.assert_all_metrics_covered()


@pytest.mark.usefixtures("dd_environment")
def test_check_queue_regex(aggregator, instance_queue_regex, seed_data):
    check = IbmMqCheck('ibm_mq', {}, {})
    check.check(instance_queue_regex)

    for metric in METRICS:
        aggregator.assert_metric(metric)

    for metric in OPTIONAL_METRICS:
        aggregator.assert_metric(metric, at_least=0)

    aggregator.assert_all_metrics_covered()


@pytest.mark.usefixtures("dd_environment")
def test_check_all(aggregator, instance_collect_all, seed_data):
    check = IbmMqCheck('ibm_mq', {}, {})
    check.check(instance_collect_all)

    for metric in METRICS:
        aggregator.assert_metric(metric)

    for metric in OPTIONAL_METRICS:
        aggregator.assert_metric(metric, at_least=0)

    aggregator.assert_all_metrics_covered()


def test_check_regex_tag(aggregator, instance_queue_regex_tag, seed_data):
    check = IbmMqCheck('ibm_mq', {}, {})
    check.check(instance_queue_regex_tag)

    tags = [
        'queue_manager:{}'.format(common.QUEUE_MANAGER),
        'mq_host:{}'.format(common.HOST),
        'port:{}'.format(common.PORT),
        'channel:{}'.format(common.CHANNEL),
        'queue:{}'.format(common.QUEUE),
        'foo:bar',
    ]

    for metric in QUEUE_METRICS:
        aggregator.assert_metric(metric, tags=tags)


@pytest.mark.usefixtures("dd_environment")
def test_check_channel_count(aggregator, instance_queue_regex_tag, seed_data):
    # Late import to not require it for e2e
    import pymqi

    metrics_to_assert = {
        "inactive": 0,
        "binding": 0,
        "starting": 0,
        "running": 1,
        "stopping": 0,
        "retrying": 0,
        "stopped": 0,
        "requesting": 0,
        "paused": 0,
        "initializing": 0,
        "unknown": 0,
    }

    check = IbmMqCheck('ibm_mq', {}, {})
    check._submit_channel_count('my_channel', pymqi.CMQCFC.MQCHS_RUNNING, ["channel:my_channel"])

    for status, expected_value in iteritems(metrics_to_assert):
        aggregator.assert_metric(
            'ibm_mq.channel.count', expected_value, tags=["channel:my_channel", "status:" + status]
        )
    aggregator.assert_metric('ibm_mq.channel.count', 0, tags=["channel:my_channel", "status:unknown"])


@pytest.mark.usefixtures("dd_environment")
def test_check_channel_count_status_unknown(aggregator, instance_queue_regex_tag, seed_data):
    metrics_to_assert = {
        "inactive": 0,
        "binding": 0,
        "starting": 0,
        "running": 0,
        "stopping": 0,
        "retrying": 0,
        "stopped": 0,
        "requesting": 0,
        "paused": 0,
        "initializing": 0,
        "unknown": 1,
    }

    check = IbmMqCheck('ibm_mq', {}, {})
    check._submit_channel_count('my_channel', 123, ["channel:my_channel"])

    for status, expected_value in iteritems(metrics_to_assert):
        aggregator.assert_metric(
            'ibm_mq.channel.count', expected_value, tags=["channel:my_channel", "status:" + status]
        )
