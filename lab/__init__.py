#
# Copyright 2017 Sangoma Technologies Inc.
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
from .provider import get_providers
from .model import Environment, Facts

# built-in plugin loading
pytest_plugins = (
    'lab.roles',
    'lab.runnerctl',
    'lab.logwatch',
    'lab.log',
    'lab.warnreporter',
    'lab.network.plugin',
    'lab.ctl.rpc',
)
