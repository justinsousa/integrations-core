# (C) Datadog, Inc. 2018
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
from __future__ import division

from collections import OrderedDict, defaultdict

from six import binary_type, iteritems

from datadog_checks.base.stubs.common import MetricStub, ServiceCheckStub
from datadog_checks.base.stubs.similar import build_similar_elements_msg

from ..utils.common import ensure_unicode, to_string


def normalize_tags(tags, sort=False):
    # The base class ensures the Agent receives bytes, so to avoid
    # prefacing our asserted tags like b'foo:bar' we'll convert back.
    if tags:
        if sort:
            return sorted(ensure_unicode(tag) for tag in tags)
        else:
            return [ensure_unicode(tag) for tag in tags]
    return tags


class AggregatorStub(object):
    """
    Mainly used for unit testing checks, this stub makes possible to execute
    a check without a running Agent.
    """

    # Replicate the Enum we have on the Agent
    METRIC_ENUM_MAP = OrderedDict(
        (
            ('gauge', 0),
            ('rate', 1),
            ('count', 2),
            ('monotonic_count', 3),
            ('counter', 4),
            ('histogram', 5),
            ('historate', 6),
        )
    )
    GAUGE, RATE, COUNT, MONOTONIC_COUNT, COUNTER, HISTOGRAM, HISTORATE = list(METRIC_ENUM_MAP.values())
    AGGREGATE_TYPES = {COUNT, COUNTER}

    def __init__(self):
        self._metrics = defaultdict(list)
        self._asserted = set()
        self._service_checks = defaultdict(list)
        self._events = []

    @classmethod
    def is_aggregate(cls, mtype):
        return mtype in cls.AGGREGATE_TYPES

    def submit_metric(self, check, check_id, mtype, name, value, tags, hostname):
        self._metrics[name].append(MetricStub(name, mtype, value, tags, hostname))

    def submit_service_check(self, check, check_id, name, status, tags, hostname, message):
        self._service_checks[name].append(ServiceCheckStub(check_id, name, status, tags, hostname, message))

    def submit_event(self, check, check_id, event):
        self._events.append(event)

    def metrics(self, name):
        """
        Return the metrics received under the given name
        """
        return [
            MetricStub(
                ensure_unicode(stub.name),
                stub.type,
                stub.value,
                normalize_tags(stub.tags),
                ensure_unicode(stub.hostname),
            )
            for stub in self._metrics.get(to_string(name), [])
        ]

    def service_checks(self, name):
        """
        Return the service checks received under the given name
        """
        return [
            ServiceCheckStub(
                ensure_unicode(stub.check_id),
                ensure_unicode(stub.name),
                stub.status,
                normalize_tags(stub.tags),
                ensure_unicode(stub.hostname),
                ensure_unicode(stub.message),
            )
            for stub in self._service_checks.get(to_string(name), [])
        ]

    @property
    def events(self):
        """
        Return all events
        """
        all_events = [{ensure_unicode(key): value for key, value in iteritems(ev)} for ev in self._events]

        for ev in all_events:
            to_decode = []
            for key, value in iteritems(ev):
                if isinstance(value, binary_type) and key != 'host':
                    to_decode.append(key)
            for key in to_decode:
                ev[key] = ensure_unicode(ev[key])

            if ev.get('tags'):
                ev['tags'] = normalize_tags(ev['tags'])

        return all_events

    def assert_metric_has_tag(self, metric_name, tag, count=None, at_least=1):
        """
        Assert a metric is tagged with tag
        """
        self._asserted.add(metric_name)

        candidates = []
        for metric in self.metrics(metric_name):
            if tag in metric.tags:
                candidates.append(metric)

        if count is not None:
            assert len(candidates) == count
        else:
            assert len(candidates) >= at_least

    # Potential kwargs: aggregation_key, alert_type, event_type,
    # msg_title, source_type_name
    def assert_event(self, msg_text, count=None, at_least=1, exact_match=True, tags=None, **kwargs):
        candidates = []
        for e in self.events:
            if exact_match and msg_text != e['msg_text'] or msg_text not in e['msg_text']:
                continue
            if tags and set(tags) != set(e['tags']):
                continue
            for name, value in iteritems(kwargs):
                if e[name] != value:
                    break
            else:
                candidates.append(e)

        msg = ("Candidates size assertion for {0}, count: {1}, " "at_least: {2}) failed").format(
            msg_text, count, at_least
        )
        if count is not None:
            assert len(candidates) == count, msg
        else:
            assert len(candidates) >= at_least, msg

    def assert_metric(self, name, value=None, tags=None, count=None, at_least=1, hostname=None, metric_type=None):
        """
        Assert a metric was processed by this stub
        """

        self._asserted.add(name)
        tags = normalize_tags(tags, sort=True)

        candidates = []
        for metric in self.metrics(name):
            if value is not None and not self.is_aggregate(metric.type) and value != metric.value:
                continue

            if tags and tags != sorted(metric.tags):
                continue

            if hostname and hostname != metric.hostname:
                continue

            if metric_type is not None and metric_type != metric.type:
                continue

            candidates.append(metric)

        expected_metric = MetricStub(name, metric_type, value, tags, hostname)

        if value is not None and candidates and all(self.is_aggregate(m.type) for m in candidates):
            got = sum(m.value for m in candidates)
            msg = "Expected count value for '{}': {}, got {}".format(name, value, got)
            condition = value == got
        elif count is not None:
            msg = "Needed exactly {} candidates for '[{} tags:{} hostname:{}]', got {}".format(
                count, name, tags, hostname, len(candidates))
            condition = len(candidates) == count
        else:
            msg = "Needed at least {} candidates for '{}', got {}".format(at_least, name, len(candidates))
            condition = len(candidates) >= at_least
        self._assert(condition, msg=msg, expected_stub=expected_metric, submitted_elements=self._metrics)

    def assert_service_check(self, name, status=None, tags=None, count=None, at_least=1, hostname=None, message=None):
        """
        Assert a service check was processed by this stub
        """
        tags = normalize_tags(tags, sort=True)
        candidates = []
        for sc in self.service_checks(name):
            if status is not None and status != sc.status:
                continue

            if tags and tags != sorted(sc.tags):
                continue

            if hostname is not None and hostname != sc.hostname:
                continue

            if message is not None and message != sc.message:
                continue

            candidates.append(sc)

        expected_service_check = ServiceCheckStub(
            None, name=name, status=status, tags=tags, hostname=hostname, message=message
        )

        if count is not None:
            msg = "Needed exactly {} candidates for '{}', got {}".format(count, name, len(candidates))
            condition = len(candidates) == count
        else:
            msg = "Needed at least {} candidates for '{}', got {}".format(at_least, name, len(candidates))
            condition = len(candidates) >= at_least
        self._assert(
            condition=condition, msg=msg, expected_stub=expected_service_check, submitted_elements=self._service_checks
        )

    @staticmethod
    def _assert(condition, msg, expected_stub, submitted_elements):
        new_msg = msg
        if not condition:  # It's costly to build the message with similar metrics, so it's built only on failure.
            new_msg = "{}\n{}".format(msg, build_similar_elements_msg(expected_stub, submitted_elements))
        assert condition, new_msg

    def assert_all_metrics_covered(self):
        missing_metrics = ''
        if self.metrics_asserted_pct < 100.0:
            missing_metrics = self.not_asserted()
        assert self.metrics_asserted_pct >= 100.0, 'Missing metrics: {}'.format(missing_metrics)

    def reset(self):
        """
        Set the stub to its initial state
        """
        self._metrics = defaultdict(list)
        self._asserted = set()
        self._service_checks = defaultdict(list)
        self._events = []

    def all_metrics_asserted(self):
        assert self.metrics_asserted_pct >= 100.0

    def not_asserted(self):
        present_metrics = {ensure_unicode(m) for m in self._metrics}
        return present_metrics - set(self._asserted)

    def assert_metric_has_tag_prefix(self, metric_name, tag_prefix, count=None, at_least=1):
        candidates = []
        self._asserted.add(metric_name)

        for metric in self.metrics(metric_name):
            tags = metric.tags
            gtags = [t for t in tags if t.startswith(tag_prefix)]
            if len(gtags) > 0:
                candidates.append(metric)

        if count is not None:
            assert len(candidates) == count
        else:
            assert len(candidates) >= at_least

    @property
    def metrics_asserted_pct(self):
        """
        Return the metrics assertion coverage
        """
        num_metrics = len(self._metrics)
        num_asserted = len(self._asserted)

        if num_metrics == 0:
            if num_asserted == 0:
                return 100
            else:
                return 0

        # If it there have been assertions with at_least=0 the length of the num_metrics and num_asserted can match
        # even if there are different metrics in each set
        not_asserted = self.not_asserted()
        return (num_metrics - len(not_asserted)) / num_metrics * 100

    @property
    def metric_names(self):
        """
        Return all the metric names we've seen so far
        """
        return [ensure_unicode(name) for name in self._metrics.keys()]

    @property
    def service_check_names(self):
        """
        Return all the service checks names seen so far
        """
        return [ensure_unicode(name) for name in self._service_checks.keys()]


# Use the stub as a singleton
aggregator = AggregatorStub()
