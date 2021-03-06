#
# Copyright 2017 Sangoma Technologies Inc.
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
from builtins import str
from builtins import object
import pytest
import logging
import socket
from collections import MutableMapping, OrderedDict
from cached_property import cached_property
from _pytest.runner import TerminalRepr
import lab
from .lock import ResourceLocker


logger = logging.getLogger(__name__)


class EquipmentLookupError(LookupError):
    pass


class EnvironmentLookupError(LookupError):
    pass


def canonical_name(obj):
    """Attempt to return a sensible name for this object.
    """
    return getattr(obj, "__name__", None) or str(id(obj))


class RolesLookupErrorRepr(TerminalRepr):
    def __init__(self, filename, firstlineno, tblines, errorstring):
        self.tblines = tblines
        self.errorstring = errorstring
        self.filename = filename
        self.firstlineno = firstlineno
        self.argname = None

    def toterminal(self, tw):
        for tbline in self.tblines:
            tw.line(tbline.rstrip())
        for line in self.errorstring.split("\n"):
            tw.line("        " + line.strip(), red=True)
        tw.line()
        tw.line("%s:%d" % (self.filename, self.firstlineno + 1))


class RoleManager(object):
    def __init__(self):
        self.roles2factories = {}

    def register(self, name, factory):
        self.roles2factories[name] = factory

    def build(self, rolename, location, **kwargs):
        ctl = self.roles2factories[rolename](pytest.config, location, **kwargs)
        ctl.name = rolename
        return ctl


class Location(object):
    """A software hosting location contactable via its hostname
    """
    def __init__(self, hostname, facts, envmng):
        self.hostname = hostname
        if facts and not isinstance(facts, MutableMapping):
            raise ValueError('facts must be a mapping type')
        self.facts = facts or {}
        self.envmng = envmng
        self.roles = OrderedDict()
        self.log = logging.getLogger(hostname)

    def __repr__(self):
        return "{}(hostname={}, facts={})".format(
            type(self).__name__, self.hostname, dict(self.facts))

    def role(self, name, **kwargs):
        """Load and return the software role instance that was registered for
        `name` in the `pytest_lab_addroles` hook from this location.
        The role is cached based on the name and additional arguments.
        """
        config = pytest.config
        key = name, tuple(kwargs.items())
        try:
            return self.roles[key]
        except KeyError:
            self.log.debug("Loading {}@{}".format(name, self.hostname))

        # instantiate the registered role ctl from its factory
        role = pytest.rolemanager.build(name, self, **kwargs)
        role._key = key
        self.roles[key] = role
        try:
            loc = getattr(role, 'location')
            assert loc is self, 'Location mismatch for control {}'.format(role)
        except AttributeError:
            raise AttributeError(
                'Role control {} must define a `.location` attribute'
                .format(role)
            )

        # register sw role controls as pytest plugins
        config.pluginmanager.register(
            role, name="{}@{}".format(name, self.hostname)
        )

        # XXX I propose we change this name to pytest_lab_ctl_loaded
        # much like pytest's pytest_plugin_registered
        config.hook.pytest_lab_role_created.call_historic(
            kwargs=dict(config=config, ctl=role)
        )
        return role

    def _close_role(self, role):
        config = self.envmng.config
        config.hook.pytest_lab_role_destroyed(config=config, ctl=role)
        close = getattr(role, 'close', None)
        if close:
            self.log.debug(
                "Calling {} for teardown".format(close))
            close()
        else:
            self.log.debug(
                "{} does not define a teardown `close()` method".format(role))

        pytest.env.config.pluginmanager.unregister(plugin=role)

    def destroy(self, role):
        self.log.debug("Destroying role {}".format(role._key))
        role = self.roles.pop(role._key, None)
        if role:
            self._close_role(role)

    def cleanup(self):
        for role in reversed(self.roles.copy().values()):
            self.destroy(role)
        # sanity
        assert not len(self.roles), "Some roles weren't destroyed {}?".format(
            self.roles)

    @cached_property
    def addrinfo(self):
        'addr info according to a dns lookup.'
        def query_dns(family):
            try:
                info = socket.getaddrinfo(self.hostname, 0, family,
                                          socket.SOCK_STREAM, 0,
                                          socket.AI_ADDRCONFIG)
            except socket.gaierror as e:
                self.log.warning(
                    "Failed to resolve {0} on {2}: {1}".format(
                        self.hostname, e, {socket.AF_INET: 'ipv4',
                                           socket.AF_INET6: 'ipv6'}[family]
                    )
                )
                return None
            return info[0][4][0]

        def lookup():
            for family in (socket.AF_INET, socket.AF_INET6):
                addr = query_dns(family)
                if addr:
                    yield family, addr

        return dict(lookup())

    @cached_property
    def ip_addr(self):
        'ipv4 addr info according to a dns lookup.'
        return self.addrinfo.get(socket.AF_INET)

    @cached_property
    def ip6_addr(self):
        'ipv6 addr info according to a dns lookup.'
        return self.addrinfo.get(socket.AF_INET6)


class EnvManager(object):
    def __init__(self, name, config, providers, locker=None, neverlock=None):
        self.config = config
        self.name = name
        self._providers = list(providers)
        self.env = lab.Environment(self.name, self._providers)
        self.locker = locker
        self.neverlock = set(neverlock) if neverlock else set()
        config.hook.pytest_lab_addroles.call_historic(
            kwargs=dict(config=self.config, rolemanager=pytest.rolemanager)
        )

        # local cache
        self.locations = OrderedDict()

    def manage(self, hostname, facts=None, lock=True, timeout=None):
        """Manage a new software hosting location by `hostname`.
        `facts` is an optional dictionary of data.
        """
        try:
            location = self.locations[hostname]
        except KeyError:
            location = Location(hostname, facts, self)
            self.locations[hostname] = location

            if lock and self.locker:
                self.locker.acquire(location.hostname, timeout=timeout)
        else:
            if facts and dict(location.facts) != facts:
                location.facts.update(facts)

        return location

    def __getitem__(self, rolename, timeout=None):
        return self.get_locations(rolename, timeout=timeout)

    def get_locations(self, rolename, timeout=None):
        """Return all locations hosting a role with ``rolename`` in a list.
        """
        locations = self.env.get(rolename)
        if not locations:
            raise EquipmentLookupError(
                "'{}' not found in environment '{}'"
                "\nDid you pass the wrong environment name with --env=NAME ?"
                .format(rolename, self.name))

        lock = rolename not in self.neverlock

        # NOTE: for locations models their `name` should be a hostname
        return [self.manage(loc.name, facts=loc, lock=lock,
                timeout=timeout) for loc in locations]

    def find(self, rolename, timeout=None):
        """Lookup and return a list of all role instance ctls registered with
        ``rolename`` from all equipment in the test environment.
        """
        locations = self.get_locations(rolename, timeout=timeout)
        return [loc.role(rolename) for loc in locations]

    def find_one(self, rolename, timeout=None):
        """Find and return the first role instance registered with ``rolename``
        in the test environment.
        """
        return self.find(rolename, timeout=timeout)[0]

    def destroy(self, location):
        """Release a location and remove it from the internal cache.
        """
        assert isinstance(location, Location)
        loc = self.locations.pop(location.hostname, None)
        if not loc:
            raise ValueError(
                "Can't destroy unknown {}".format(location))

        logger.info("Destroying {}".format(location))

        # sanity
        if loc is not location:
            logger.warning("Destroying unknown location {}".format(location))

        location.cleanup()  # close all role controls @ location
        self.config.pluginmanager.hook.pytest_lab_location_destroyed(
            config=self.config, location=location)
        if self.locker:
            self.locker.release(loc.hostname)

    def cleanup(self):
        try:
            for location in reversed(self.locations.copy().values()):
                self.destroy(location)
        finally:
            if self.locker:
                self.locker.release_all()

    def __contains__(self, location):
        return location.hostname in self.locations


@pytest.hookimpl
def pytest_namespace():
    return {'env': None,
            'rolemanager': RoleManager(),
            'EquipmentLookupError': EquipmentLookupError}


@pytest.hookimpl
def pytest_addhooks(pluginmanager):
    from . import hookspec
    pluginmanager.add_hookspecs(hookspec)


@pytest.hookimpl
def pytest_addoption(parser):
    group = parser.getgroup('environment')
    group.addoption('--env', action='store', default='anonymous',
                    help='Test environment name')
    group.addoption('--user', action='store',
                    help='Explicit user to lock the test environment with')
    group.addoption(
        '--no-locks', action='store_true',
        help='Tell pytestlab to never lock environments or locations')
    group.addoption('--discovery-srv', action='store', required=False,
                    help='The domain to use when looking up SRV records')


def get_srv(pytestconfig, yamlconfig, skip=False):
    ds = yamlconfig.get('discovery-srv')
    if not ds:
        ds = pytestconfig.getoption('discovery_srv', skip=skip)

    return ds


@pytest.hookimpl
def pytest_configure(config):
    """Set up the test environment.
    """
    providers = []
    envname = config.option.env
    yamlconf = lab.config.load_yaml_config()
    providers = lab.get_providers(yamlconf=yamlconf, pytestconfig=config)
    discovery_srv = get_srv(config, yamlconf)

    locker = ResourceLocker(
        envname,
        discovery_srv,
    ) if not config.option.no_locks and discovery_srv else None

    envmng = EnvManager(
        envname, config, providers, locker,
        neverlock=yamlconf.get('neverlock')
    )

    # ensure the env defines at least some data
    if envname != 'anonymous' and not envmng.env.view:
        raise EnvironmentLookupError(envname)

    config.pluginmanager.register(envmng, 'environment')
    config.add_cleanup(envmng.cleanup)

    config.hook.pytest_lab_configure(envmanager=envmng)

    # make globally accessible
    pytest.env = envmng


@pytest.fixture(scope='session')
def discovery_srv(pytestconfig):
    """Discover srv string as passing in to the CLI or parse from lab.yaml.
    """
    yamlconf = lab.config.load_yaml_config()
    ds = get_srv(pytestconfig, yamlconf, skip=True)
    # sanity
    assert ds
    assert pytest.env.locker.discovery_srv == ds
    return ds


@pytest.fixture(scope='session')
def dut_host(dut_ctl):
    'Retreive the hostname str for the current dut'
    host = dut_ctl.location.hostname
    assert host
    return host


@pytest.fixture(scope='session')
def dut_ip(dut_ctl, addr_family):
    return dut_ctl.location.addrinfo[addr_family]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    excinfo = call.excinfo
    if excinfo:
        exc = excinfo.value or 'Model not found'
        if call.excinfo and call.excinfo.errisinstance(EnvironmentLookupError):
            rep.longrepr = RolesLookupErrorRepr('foo.py', 0, [], exc.message)
        elif call.excinfo and call.excinfo.errisinstance(EquipmentLookupError):
            rep.longrepr = RolesLookupErrorRepr('foo.py', 0, [], exc.message)
