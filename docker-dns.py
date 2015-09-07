#!/usr/bin/env python2

'''Based on https://gist.github.com/KellyLSB/4315a0323ed0fe1d79b6#file-readme-md
'''

import re
import json
import subprocess
import cStringIO
import logging
logging.basicConfig(level=logging.INFO)

slog = logging.getLogger(__name__)

# defaults
NAME_SERVER='localhost'
ZONE='named.zone.'
TTL=60

# docker events example:
# '2015-07-15T10:28:39.000000000+02:00 08dafd55da6b8a91dea188ee64ba762d8e763196bf13e90997a361a6236afbaf: (from flinkwork/backend) die\n'
rx = re.compile(r'([^ ]+) ([0-9a-f]+): \(from (.+)\) (.+)')

def norm_hostname(hostname):
    return re.sub("[ _/]", "-", hostname)

class SubprocessError(Exception):
    pass

class EmptyHostnameError(Exception):
    pass

def iter_docker_events():
    p = subprocess.Popen(['docker', 'events'], stdout=subprocess.PIPE)
    while True:
        line = p.stdout.readline()
        m = rx.match(line)
        if m:
            # yields: timestamp string, container id, image, action
            yield m.groups()


def container_data(cid):
    p = subprocess.Popen(['docker', 'inspect', cid], stdout=subprocess.PIPE)
    sout, serr = p.communicate()
    exit_code = p.wait()
    if exit_code:
        raise SubprocessError(exit_code, sout, serr)
    data = json.loads(sout)
    return data[0]

def get_running_containers():
    p = subprocess.Popen(['docker', 'ps', '-q', '--no-trunc'], stdout=subprocess.PIPE)
    sout, serr = p.communicate()
    exit_code = p.wait()
    if exit_code:
        raise SubprocessError(exit_code, sout, serr)
    return sout.splitlines()

class DNSUpdater(object):
    def __init__(self, server, zone, key, ttl):
        self.server = server
        self.zone = zone
        self.key = key
        self.ttl = ttl

    @property
    def domain(self):
        return self.zone.strip('.')

    def update_host(self, hostname, ip):
        if not hostname:
            raise EmptyHostnameError()
        hostname = norm_hostname(hostname)
        fqdn = '{}.{}'.format(hostname, self.domain)

        buf = cStringIO.StringIO()
        buf.write('server {}\n'.format(self.server))
        buf.write('zone {}\n'.format(self.zone))
        buf.write('update delete {}\n'.format(fqdn))
        buf.write('update add {} {} A {}\n'.format(fqdn, self.ttl, ip))
        buf.write('send')
        slog.debug('nsupdate:\n{}'.format(buf.getvalue()))
        p = subprocess.Popen(['nsupdate', '-k', self.key],
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE)
        sout, serr = p.communicate(buf.getvalue())
        exit_code = p.wait()
        if exit_code:
            raise SubprocessError(exit_code, sout, serr)
        slog.info('Updated {} {}'.format(fqdn, ip))

    def update_container(self, cid):
        try:
            data = container_data(cid)
        except IndexError:
            slog.info('failed to fetch container data for event {!r}'.format((ts, cid, image, action)))
            return False

        hostname = data['Name'].strip('/')
        ip = data['NetworkSettings']['IPAddress']
        self.update_host(hostname, ip)
        return True

def main(server, zone, key, ttl, scan=False):
    updater = DNSUpdater(server, zone, key, ttl)

    if scan:
        slog.debug('scanning running containers')
        for cid in get_running_containers():
            slog.debug('adding container {}'.format(cid))
            updater.update_container(cid)

    slog.debug('starting event loop')
    for ts, cid, image, action in iter_docker_events():
        if action not in ('start',):
            slog.info('igoring event {!r}'.format((ts, cid, image, action)))
            continue

        try:
            updater.update_container(cid)
        except Exception:
            slog.info('while handling event {!r}'.format((ts, cid, image, action)))

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Update DNS entries from docker events.')
    parser.add_argument('--server', default=NAME_SERVER, help='Target name server')
    parser.add_argument('--zone', default=ZONE, help='DNS zone (do not forget the trailing dot!)')
    parser.add_argument('--ttl', type=int, default=TTL, help='DNS record TTL')
    parser.add_argument('--scan', action='store_true', help='Scan running containers')
    parser.add_argument('key', metavar='KEY', help='DNS update key')

    args = parser.parse_args()
    main(**vars(args))
