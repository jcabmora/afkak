import functools
import logging
import os
import random
import socket
import string
import time
import unittest2
import uuid

from nose.twistedtools import deferred

from twisted.internet.defer import inlineCallbacks, Deferred, returnValue

from afkak import KafkaClient
from afkak.common import OffsetRequest

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

__all__ = [
    'random_string',
    'get_open_port',
    'kafka_versions',
    'KafkaIntegrationTestCase',
    'Timer',
]


# This must only be called from the reactor thread (that is, something
# decorated with @nose.twistedtools.deferred)
def asyncDelay(timeout=0.01, clock=None):
    if clock is None:
        from twisted.internet import reactor as clock

    timeout = timeout

    def succeed():
        d.callback(timeout)

    def cancel(_):
        delayedCall.cancel()

    d = Deferred(cancel)
    delayedCall = clock.callLater(timeout, succeed)
    return d


def random_string(l):
    # Random.choice can be very slow for large amounts of data, so 'cheat'
    if l <= 50:
        s = "".join(random.choice(string.letters) for i in xrange(l))
    else:
        r = random_string(50)
        s = "".join(r for i in xrange(l / 50))
        if l % 50:
            s += r[0:(l % 50)]
    assert len(s) == l
    return s


def kafka_versions(*versions):
    def kafka_versions(func):
        @functools.wraps(func)
        def wrapper(self):
            kafka_version = os.environ.get('KAFKA_VERSION')

            if not kafka_version:
                self.skipTest("no kafka version specified")
            elif 'all' not in versions and kafka_version not in versions:
                self.skipTest("unsupported kafka version")

            return func(self)
        return wrapper
    return kafka_versions


@inlineCallbacks
def ensure_topic_creation(client, topic_name, timeout=5, reactor=None):
    '''
    With the default Kafka configuration, just querying for the metatdata
    for a particular topic will auto-create that topic.
    NOTE: This must only be called from the reactor thread (that is, something
    decorated with @nose.twistedtools.deferred)
    '''
    start_time = time.time()
    yield client.load_metadata_for_topics(topic_name)
    while not client.has_metadata_for_topic(topic_name):
        yield asyncDelay(clock=reactor)
        if time.time() > start_time + timeout:
            raise Exception("Unable to create topic %s" % topic_name)
        yield client.load_metadata_for_topics(topic_name)


def get_open_port():
    sock = socket.socket()
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class KafkaIntegrationTestCase(unittest2.TestCase):
    create_client = True
    topic = None

    @deferred(timeout=10)
    @inlineCallbacks
    def setUp(self):
        super(KafkaIntegrationTestCase, self).setUp()
        if not os.environ.get('KAFKA_VERSION'):
            log.debug('KAFKA_VERSION unset!')
            return

        if not self.topic:
            self.topic = "%s-%s" % (
                self.id()[self.id().rindex(".") + 1:], random_string(10))

        if self.create_client:
            self.client = KafkaClient(
                '%s:%d' % (self.server.host, self.server.port),
                clientId=self.topic)

        yield ensure_topic_creation(self.client, self.topic,
                                    reactor=self.reactor)

        self._messages = {}

    @deferred(timeout=10)
    @inlineCallbacks
    def tearDown(self):
        super(KafkaIntegrationTestCase, self).tearDown()
        if not os.environ.get('KAFKA_VERSION'):
            log.debug('KAFKA_VERSION unset!')
            return

        if self.create_client:
            yield self.client.close()
            # Check for outstanding delayedCalls. Note, this may yield
            # spurious errors if the class's client has an outstanding
            # delayed call due to reconnecting.
            if self.reactor.getDelayedCalls():
                log.debug("Intermitent failure debugging: %s\n\n",
                          ' '.join([str(dc) for dc in
                                    self.reactor.getDelayedCalls()]))
            self.assertFalse(self.reactor.getDelayedCalls())

    @inlineCallbacks
    def current_offset(self, topic, partition):
        offsets, = yield self.client.send_offset_request(
            [OffsetRequest(topic, partition, -1, 1)])
        returnValue(offsets.offsets[0])

    def msgs(self, iterable):
        return [self.msg(x) for x in iterable]

    def msg(self, s):
        if s not in self._messages:
            self._messages[s] = '%s-%s-%s' % (s, self.id(), str(uuid.uuid4()))

        return self._messages[s]


class Timer(object):
    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args):
        self.end = time.time()
        self.interval = self.end - self.start