# (C) Datadog, Inc. 2019
# All rights reserved
# Licensed under Simplified BSD License (see LICENSE)
from __future__ import division

from collections import defaultdict
from time import time

from kafka import errors as kafka_errors
from kafka.client import KafkaClient
from kafka.protocol.commit import GroupCoordinatorRequest, OffsetFetchRequest
from kafka.protocol.offset import OffsetRequest, OffsetResetStrategy
from kafka.structs import TopicPartition
from kazoo.client import KazooClient
from kazoo.exceptions import NoNodeError
from six import iteritems, string_types, text_type

from datadog_checks.base import AgentCheck, ConfigurationError, is_affirmative

from .constants import CONTEXT_UPPER_BOUND, DEFAULT_KAFKA_TIMEOUT


class LegacyKafkaCheck_0_10_2(AgentCheck):
    """
    Check the offsets and lag of Kafka consumers.

    This check also returns broker highwater offsets.

    This is used if the `post_0_10_2` config is set to false
    """

    SOURCE_TYPE_NAME = 'kafka'

    def __init__(self, name, init_config, instances):
        super(LegacyKafkaCheck_0_10_2, self).__init__(name, init_config, instances)
        self._zk_timeout = int(init_config.get('zk_timeout', 5))
        self._kafka_timeout = int(init_config.get('kafka_timeout', DEFAULT_KAFKA_TIMEOUT))
        self._kafka_client = self._create_kafka_client()
        self.context_limit = int(init_config.get('max_partition_contexts', CONTEXT_UPPER_BOUND))

    def check(self, instance):
        # For calculating lag, we have to fetch offsets from both kafka and
        # zookeeper. There's a potential race condition because whichever one we
        # check first may be outdated by the time we check the other. Better to
        # check consumer offset before checking broker offset because worst case
        # is that overstates consumer lag a little. Doing it the other way can
        # understate consumer lag to the point of having negative consumer lag,
        # which just creates confusion because it's theoretically impossible.

        # Fetch consumer group offsets from Zookeeper
        zk_hosts_ports = instance.get('zk_connect_str')
        zk_prefix = instance.get('zk_prefix', '')
        get_kafka_consumer_offsets = is_affirmative(instance.get('kafka_consumer_offsets', zk_hosts_ports is None))

        custom_tags = instance.get('tags', [])

        # If monitor_unlisted_consumer_groups is True, fetch all groups stored in ZK
        consumer_groups = None
        if instance.get('monitor_unlisted_consumer_groups', False):
            consumer_groups = None
        elif 'consumer_groups' in instance:
            consumer_groups = instance.get('consumer_groups')
            self._validate_explicit_consumer_groups(consumer_groups)

        zk_consumer_offsets = None
        if zk_hosts_ports:
            zk_consumer_offsets, consumer_groups = self._get_zk_consumer_offsets(
                zk_hosts_ports, consumer_groups, zk_prefix
            )

        topics = defaultdict(set)
        kafka_consumer_offsets = None

        self._kafka_client._maybe_refresh_metadata()

        if get_kafka_consumer_offsets:
            # For now, consumer groups are mandatory if not using ZK
            if not zk_hosts_ports and not consumer_groups:
                raise ConfigurationError(
                    'Invalid configuration - if you are not collecting '
                    'offsets from ZK you _must_ specify consumer groups'
                )
            # kafka-python automatically probes the cluster for broker version
            # and then stores it. Note that this returns the first version
            # found, so in a mixed-version cluster this will be a
            # non-deterministic result.
            #
            # Kafka 0.8.2 added support for storing consumer offsets in Kafka.
            if self._kafka_client.config.get('api_version') >= (0, 8, 2):
                kafka_consumer_offsets, topics = self._get_kafka_consumer_offsets(instance, consumer_groups)

        if not topics:
            # val = {'consumer_group': {'topic': [0, 1]}}
            for _, tps in iteritems(consumer_groups):
                for topic, partitions in iteritems(tps):
                    topics[topic].update(partitions)

        warn_msg = """ Discovered %s partition contexts - this exceeds the maximum
                       number of contexts permitted by the check. Please narrow your
                       target by specifying in your YAML what consumer groups, topics
                       and partitions you wish to monitor."""
        if zk_consumer_offsets and len(zk_consumer_offsets) > self.context_limit:
            self.warning(warn_msg % len(zk_consumer_offsets))
            return
        if kafka_consumer_offsets and len(kafka_consumer_offsets) > self.context_limit:
            self.warning(warn_msg % len(kafka_consumer_offsets))
            return

        # Fetch the broker highwater offsets
        try:
            highwater_offsets, topic_partitions_without_a_leader = self._get_broker_offsets(topics)
        except Exception:
            self.log.exception('There was a problem collecting the high watermark offsets')
            return

        # Report the broker highwater offset
        for (topic, partition), highwater_offset in iteritems(highwater_offsets):
            broker_tags = ['topic:%s' % topic, 'partition:%s' % partition] + custom_tags
            self.gauge('kafka.broker_offset', highwater_offset, tags=broker_tags)

        # Report the consumer group offsets and consumer lag
        if zk_consumer_offsets:
            self._report_consumer_metrics(
                highwater_offsets,
                zk_consumer_offsets,
                topic_partitions_without_a_leader,
                tags=custom_tags + ['source:zk'],
            )
        if kafka_consumer_offsets:
            self._report_consumer_metrics(
                highwater_offsets,
                kafka_consumer_offsets,
                topic_partitions_without_a_leader,
                tags=custom_tags + ['source:kafka'],
            )

    def _create_kafka_client(self):
        kafka_conn_str = self.instance.get('kafka_connect_str')
        if not isinstance(kafka_conn_str, (string_types, list)):
            raise ConfigurationError('kafka_connect_str should be string or list of strings')
        return KafkaClient(
            bootstrap_servers=kafka_conn_str,
            client_id='dd-agent',
            request_timeout_ms=self.init_config.get('kafka_timeout', DEFAULT_KAFKA_TIMEOUT) * 1000,
            api_version=self.instance.get('kafka_client_api_version'),
            # While we check for SSL params, if not present they will default
            # to the kafka-python values for plaintext connections
            security_protocol=self.instance.get('security_protocol', 'PLAINTEXT'),
            sasl_mechanism=self.instance.get('sasl_mechanism'),
            sasl_plain_username=self.instance.get('sasl_plain_username'),
            sasl_plain_password=self.instance.get('sasl_plain_password'),
            sasl_kerberos_service_name=self.instance.get('sasl_kerberos_service_name', 'kafka'),
            sasl_kerberos_domain_name=self.instance.get('sasl_kerberos_domain_name'),
            ssl_cafile=self.instance.get('ssl_cafile'),
            ssl_check_hostname=self.instance.get('ssl_check_hostname', True),
            ssl_certfile=self.instance.get('ssl_certfile'),
            ssl_keyfile=self.instance.get('ssl_keyfile'),
            ssl_crlfile=self.instance.get('ssl_crlfile'),
            ssl_password=self.instance.get('ssl_password'),
        )

    def _make_blocking_req(self, request, node_id=None):
        if node_id is None:
            node_id = self._kafka_client.least_loaded_node()

        while not self._kafka_client.ready(node_id):
            # poll until the connection to broker is ready, otherwise send()
            # will fail with NodeNotReadyError
            self._kafka_client.poll()

        future = self._kafka_client.send(node_id, request)
        self._kafka_client.poll(future=future)  # block until we get response.
        assert future.succeeded()
        response = future.value
        return response

    def _process_highwater_offsets(self, response):
        highwater_offsets = {}
        topic_partitions_without_a_leader = []

        for tp in response.topics:
            topic = tp[0]
            partitions = tp[1]
            for partition, error_code, offsets in partitions:
                error_type = kafka_errors.for_code(error_code)
                if error_type is kafka_errors.NoError:
                    highwater_offsets[(topic, partition)] = offsets[0]
                    # Valid error codes:
                    # https://cwiki.apache.org/confluence/display/KAFKA/A+Guide+To+The+Kafka+Protocol#AGuideToTheKafkaProtocol-PossibleErrorCodes.2
                elif error_type is kafka_errors.NotLeaderForPartitionError:
                    self.log.warn(
                        "Kafka broker returned %s (error_code %s) for topic %s, partition: %s. This should only happen "
                        "if the broker that was the partition leader when kafka_admin_client last fetched metadata is "
                        "no longer the leader.",
                        error_type.message,
                        error_type.errno,
                        topic,
                        partition,
                    )
                    topic_partitions_without_a_leader.append((topic, partition))
                elif error_type is kafka_errors.UnknownTopicOrPartitionError:
                    self.log.warn(
                        "Kafka broker returned %s (error_code %s) for topic: %s, partition: %s. This should only "
                        "happen if the topic is currently being deleted or the check configuration lists non-existent "
                        "topic partitions.",
                        error_type.message,
                        error_type.errno,
                        topic,
                        partition,
                    )
                else:
                    raise error_type(
                        "Unexpected error encountered while attempting to fetch the highwater offsets for topic: %s, "
                        "partition: %s." % (topic, partition)
                    )

        return highwater_offsets, topic_partitions_without_a_leader

    def _get_broker_offsets(self, topics):
        """
        Fetch highwater offsets for each topic/partition from Kafka cluster.

        Do this for all partitions in the cluster because even if it has no
        consumers, we may want to measure whether producers are successfully
        producing. No need to limit this for performance because fetching broker
        offsets from Kafka is a relatively inexpensive operation.

        Sends one OffsetRequest per broker to get offsets for all partitions
        where that broker is the leader:
        https://cwiki.apache.org/confluence/display/KAFKA/A+Guide+To+The+Kafka+Protocol#AGuideToTheKafkaProtocol-OffsetAPI(AKAListOffset)

        Can we cleanup connections on agent restart?
        Brokers before 0.9 - accumulate stale connections on restarts.
        In 0.9 Kafka added connections.max.idle.ms
        https://issues.apache.org/jira/browse/KAFKA-1282
        """

        # Connect to Kafka
        highwater_offsets = {}
        topic_partitions_without_a_leader = []
        topics_to_fetch = defaultdict(set)

        for topic, partitions in iteritems(topics):
            # if no partitions are provided
            # we're falling back to all available partitions (?)
            if len(partitions) == 0:
                partitions = self._kafka_client.cluster.available_partitions_for_topic(topic)
            topics_to_fetch[topic].update(partitions)

        leader_tp = defaultdict(lambda: defaultdict(set))
        for topic, partitions in iteritems(topics_to_fetch):
            for partition in partitions:
                partition_leader = self._kafka_client.cluster.leader_for_partition(TopicPartition(topic, partition))
                if partition_leader is not None and partition_leader >= 0:
                    leader_tp[partition_leader][topic].add(partition)

        max_offsets = 1
        for node_id, tps in iteritems(leader_tp):
            # Construct the OffsetRequest
            request = OffsetRequest[0](
                replica_id=-1,
                topics=[
                    (topic, [(partition, OffsetResetStrategy.LATEST, max_offsets) for partition in partitions])
                    for topic, partitions in iteritems(tps)
                ],
            )

            response = self._make_blocking_req(request, node_id=node_id)
            offsets, unled = self._process_highwater_offsets(response)
            highwater_offsets.update(offsets)
            topic_partitions_without_a_leader.extend(unled)

        return highwater_offsets, list(set(topic_partitions_without_a_leader))

    def _report_consumer_metrics(self, highwater_offsets, consumer_offsets, unled_topic_partitions=None, tags=None):
        if unled_topic_partitions is None:
            unled_topic_partitions = []
        if tags is None:
            tags = []
        for (consumer_group, topic, partition), consumer_offset in iteritems(consumer_offsets):
            # Report the consumer group offsets and consumer lag
            if (topic, partition) not in highwater_offsets:
                self.log.warn(
                    "[%s] topic: %s partition: %s was not available in the consumer - skipping consumer submission",
                    consumer_group,
                    topic,
                    partition,
                )
                if (topic, partition) not in unled_topic_partitions:
                    self.log.warn(
                        "Consumer group: %s has offsets for topic: %s "
                        "partition: %s, but that topic partition doesn't actually "
                        "exist in the cluster.",
                        consumer_group,
                        topic,
                        partition,
                    )
                continue

            consumer_group_tags = ['topic:%s' % topic, 'partition:%s' % partition, 'consumer_group:%s' % consumer_group]
            consumer_group_tags.extend(tags)
            self.gauge('kafka.consumer_offset', consumer_offset, tags=consumer_group_tags)

            consumer_lag = highwater_offsets[(topic, partition)] - consumer_offset
            if consumer_lag < 0:
                # this will result in data loss, so emit an event for max visibility
                title = "Negative consumer lag for group: {group}.".format(group=consumer_group)
                message = (
                    "Consumer lag for consumer group: {group}, topic: {topic}, "
                    "partition: {partition} is negative. This should never happen.".format(
                        group=consumer_group, topic=topic, partition=partition
                    )
                )
                key = "{}:{}:{}".format(consumer_group, topic, partition)
                self._send_event(title, message, consumer_group_tags, 'consumer_lag', key, severity="error")
                self.log.debug(message)

            self.gauge('kafka.consumer_lag', consumer_lag, tags=consumer_group_tags)

    def _get_zk_path_children(self, zk_conn, zk_path, name_for_error):
        """Fetch child nodes for a given Zookeeper path."""
        children = []
        try:
            children = zk_conn.get_children(zk_path)
        except NoNodeError:
            self.log.info('No zookeeper node at %s', zk_path)
        except Exception:
            self.log.exception('Could not read %s from %s', name_for_error, zk_path)
        return children

    def _get_zk_consumer_offsets(self, zk_hosts_ports, consumer_groups=None, zk_prefix=''):
        """
        Fetch Consumer Group offsets from Zookeeper.

        Also fetch consumer_groups, topics, and partitions if not
        already specified in consumer_groups.

        :param dict consumer_groups: The consumer groups, topics, and partitions
            that you want to fetch offsets for. If consumer_groups is None, will
            fetch offsets for all consumer_groups. For examples of what this
            dict can look like, see _validate_explicit_consumer_groups().
        """
        zk_consumer_offsets = {}

        # Construct the Zookeeper path pattern
        # /consumers/[groupId]/offsets/[topic]/[partitionId]
        zk_path_consumer = zk_prefix + '/consumers/'
        zk_path_topic_tmpl = zk_path_consumer + '{group}/offsets/'
        zk_path_partition_tmpl = zk_path_topic_tmpl + '{topic}/'

        zk_conn = KazooClient(zk_hosts_ports, timeout=self._zk_timeout)
        zk_conn.start()
        try:
            if consumer_groups is None:
                # If consumer groups aren't specified, fetch them from ZK
                consumer_groups = {
                    consumer_group: None
                    for consumer_group in self._get_zk_path_children(zk_conn, zk_path_consumer, 'consumer groups')
                }

            for consumer_group, topics in iteritems(consumer_groups):
                if not topics:
                    # If topics are't specified, fetch them from ZK
                    zk_path_topics = zk_path_topic_tmpl.format(group=consumer_group)
                    topics = {topic: None for topic in self._get_zk_path_children(zk_conn, zk_path_topics, 'topics')}
                    consumer_groups[consumer_group] = topics

                for topic, partitions in iteritems(topics):
                    if partitions:
                        partitions = set(partitions)  # defend against bad user input
                    else:
                        # If partitions aren't specified, fetch them from ZK
                        zk_path_partitions = zk_path_partition_tmpl.format(group=consumer_group, topic=topic)
                        # Zookeeper returns the partition IDs as strings because
                        # they are extracted from the node path
                        partitions = [
                            int(x) for x in self._get_zk_path_children(zk_conn, zk_path_partitions, 'partitions')
                        ]
                        consumer_groups[consumer_group][topic] = partitions

                    # Fetch consumer offsets for each partition from ZK
                    for partition in partitions:
                        zk_path = (zk_path_partition_tmpl + '{partition}/').format(
                            group=consumer_group, topic=topic, partition=partition
                        )
                        try:
                            consumer_offset = int(zk_conn.get(zk_path)[0])
                            key = (consumer_group, topic, partition)
                            zk_consumer_offsets[key] = consumer_offset
                        except NoNodeError:
                            self.log.info('No zookeeper node at %s', zk_path)
                        except Exception:
                            self.log.exception('Could not read consumer offset from %s', zk_path)
        finally:
            try:
                zk_conn.stop()
                zk_conn.close()
            except Exception:
                self.log.exception('Error cleaning up Zookeeper connection')
        return zk_consumer_offsets, consumer_groups

    def _get_kafka_consumer_offsets(self, instance, consumer_groups):
        """
        Get offsets for all consumer groups from Kafka.

        These offsets are stored in the __consumer_offsets topic rather than in Zookeeper.
        """
        consumer_offsets = {}
        topics = defaultdict(set)

        for consumer_group, topic_partitions in iteritems(consumer_groups):
            try:
                single_group_offsets = self._get_single_group_offsets_from_kafka(consumer_group, topic_partitions)
                for (topic, partition), offset in iteritems(single_group_offsets):
                    topics[topic].update([partition])
                    key = (consumer_group, topic, partition)
                    consumer_offsets[key] = offset
            except Exception:
                self.log.exception('Could not read consumer offsets from kafka for group: ' % consumer_group)

        return consumer_offsets, topics

    def _get_group_coordinator(self, group):
        """Determine which broker is the Group Coordinator for a specific consumer group."""
        request = GroupCoordinatorRequest[0](group)
        response = self._make_blocking_req(request)
        error_type = kafka_errors.for_code(response.error_code)
        if error_type is kafka_errors.NoError:
            return response.coordinator_id

    def _get_single_group_offsets_from_kafka(self, consumer_group, topic_partitions):
        """Get offsets for a single consumer group from Kafka"""
        consumer_offsets = {}
        tps = defaultdict(set)
        for topic, partitions in iteritems(topic_partitions):
            if len(partitions) == 0:
                partitions = self._kafka_client.cluster.available_partitions_for_topic(topic)
            tps[topic] = tps[text_type(topic)].union(set(partitions))

        coordinator_id = self._get_group_coordinator(consumer_group)
        if coordinator_id is not None:
            # Kafka protocol uses OffsetFetchRequests to retrieve consumer offsets:
            # https://kafka.apache.org/protocol#The_Messages_OffsetFetch
            # https://cwiki.apache.org/confluence/display/KAFKA/A+Guide+To+The+Kafka+Protocol#AGuideToTheKafkaProtocol-OffsetFetchRequest
            request = OffsetFetchRequest[1](consumer_group, list(iteritems(tps)))
            response = self._make_blocking_req(request, node_id=coordinator_id)
            for (topic, partition_offsets) in response.topics:
                for partition, offset, _, error_code in partition_offsets:
                    error_type = kafka_errors.for_code(error_code)
                    if error_type is not kafka_errors.NoError:
                        continue
                    consumer_offsets[(topic, partition)] = offset
        else:
            self.log.info("unable to find group coordinator for %s", consumer_group)

        return consumer_offsets

    @classmethod
    def _validate_explicit_consumer_groups(cls, val):
        """Validate any explicitly specified consumer groups.

        While the check does not require specifying consumer groups,
        if they are specified this method should be used to validate them.

        val = {'consumer_group': {'topic': [0, 1]}}
        """
        assert isinstance(val, dict)
        for consumer_group, topics in iteritems(val):
            assert isinstance(consumer_group, string_types)
            # topics are optional
            assert isinstance(topics, dict) or topics is None
            if topics is not None:
                for topic, partitions in iteritems(topics):
                    assert isinstance(topic, string_types)
                    # partitions are optional
                    assert isinstance(partitions, (list, tuple)) or partitions is None
                    if partitions is not None:
                        for partition in partitions:
                            assert isinstance(partition, int)

    def _send_event(self, title, text, tags, event_type, aggregation_key, severity='info'):
        """Emit an event to the Datadog Event Stream."""
        event_dict = {
            'timestamp': int(time()),
            'source_type_name': self.SOURCE_TYPE_NAME,
            'msg_title': title,
            'event_type': event_type,
            'alert_type': severity,
            'msg_text': text,
            'tags': tags,
            'aggregation_key': aggregation_key,
        }
        self.event(event_dict)
