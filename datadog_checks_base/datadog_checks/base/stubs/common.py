# (C) Datadog, Inc. 2018
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)
from __future__ import division

from collections import namedtuple

MetricStub = namedtuple('MetricStub', 'name type value tags hostname')
ServiceCheckStub = namedtuple('ServiceCheckStub', 'check_id name status tags hostname message')
