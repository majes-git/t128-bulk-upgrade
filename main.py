#!/usr/bin/env python3

import argparse
import time

from lib.log import *
from lib.rest import RestGraphqlApi, get_unified_release

APP = 't128-bulk-upgrade'

def is_positive(value):
    number = int(value)
    if number < 0:
        msg = '{} is not a positive number'.format(number)
        raise argparse.ArgumentTypeError(msg)
    return number



def parse_arguments():
    """Get commandline arguments."""
    parser = argparse.ArgumentParser(
        description='Manage SSR router upgrades for large deployments')

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--release', '-r',
                       help='Target release for upgrades')
    group.add_argument('--list-releases', action='store_true',
                       help='Show available releases for a token and exit.')

    parser.add_argument('--host', help='Conductor/router hostname')
    parser.add_argument('--user',
                        help='Conductor/router username (if no key auth)')
    parser.add_argument('--password',
                        help='Conductor/router password (if no key auth)')
    parser.add_argument('--parallel', '-p', type=is_positive, default=1,
                        help='Upgrade PARALLEL routers at the same time')
    parser.add_argument('--max', '-m', type=int,
                        help='Upgrade only MAX routers and then exit')
    parser.add_argument('--download-only', '-d', action='store_true',
                        help='Download new release but do not upgrade')
    parser.add_argument('--timeout', '-t', type=int, default=3600,
                        help='Stop processing when one router is not finished within TIMEOUT seconds')
    parser.add_argument('--download-timeout', type=int,
                        help='Define a different --timeout for downloads (default: use the same timeout for download and upgrade)')
    parser.add_argument('--filter', '-f',
                        help='Filter routers based on FILTER (name.startswith, name.contains, name.equals, version.startswith, version.equals)')
    parser.add_argument('--router-file',
                        help='Read selected routers from file')
    parser.add_argument('--debug',action='store_true',
                        help='Show debug messages')
    parser.add_argument('--dry-run', action='store_true',
                        help='Do not modify config, just print actions')
    parser.add_argument('--version', action='version', version=f'{APP} 0.1')
    return parser.parse_args()


def filter_releases(releases):
    filtered = []
    for release in releases:
        major, minor, patch = release.split('-')[0].split('.')[:3]
        version = 1000000 * int(major) + 1000 * int(minor) + int(patch)
        if version >= 5004000:
            # inore all releases before 5.4.0
            filtered.append(release)
    return filtered

def is_older_release(first, second):
    def to_list(release_string):
        return release_string.split('-')[0].split('.')
    first = to_list(first)
    second = to_list(second)
    for i in range(3):
        if int(first[i]) < int(second[i]):
            return True
        if int(first[i]) > int(second[i]):
            return False
    return False


def select_routers(api, args):
    all_routers_names = api.get_router_names()
    if args.router_file:
        routers = []
        with open(args.router_file) as fd:
            for name in fd.read().splitlines():
                if name in all_routers_names:
                    routers.append(name)
    else:
        routers = all_routers_names

    if args.filter:
        if '=' not in args.filter:
            error('Filter is incorrect. Exiting.')

        key, value = args.filter.split('=')
        if key == 'name.startswith':
            for name in routers.copy():
                if not name.startswith(value):
                    routers.remove(name)
            return routers
        if key == 'name.equals':
            if value in routers:
                routers = [value]
            else:
                routers = []
            return routers
        if key == 'name.contains':
            for name in routers.copy():
                if value not in name:
                    routers.remove(name)
            return routers

        if key.startswith('version.'):
            api.get_assets()
            assets = api.assets
            routers = []
            if key == 'version.equals':
                for asset in assets:
                    if asset['t128Version'] == value:
                        routers.append(asset['routerName'])
                return list(set(routers))
            if key == 'version.startswith':
                for asset in assets:
                    if asset['t128Version'].startswith(value):
                        routers.append(asset['routerName'])
                return list(set(routers))
    return routers


def download(api, routers, target, timeout, dry_run):
    download_started = time.time()
    all_routers_ready_for_upgrade = False
    while not all_routers_ready_for_upgrade:
        all_routers_ready_for_upgrade = True
        for router, releases in api.get_downloaded_releases(routers).items():
            # check if an upgrade is already in progress
            is_upgrading, _ = api.router_is_upgrading(router)
            if is_upgrading:
                # ignore this router
                continue

            releases = [get_unified_release(release) for release in releases]
            if get_unified_release(target) not in releases:
                # found a router has not downloaded the target release yet
                all_routers_ready_for_upgrade = False

                debug('Downloaded releases on {}: {}'.format(router, releases))
                available_releases = api.get_available_releases(router)
                full_release = None
                for release in available_releases:
                    if release.startswith(target):
                        full_release = release
                        break
                if not full_release:
                    error('Release', target, 'is not available on router', router)

                status, text = api.router_is_downloading(router)
                if status:
                    debug('Status:', status)
                    debug(f'Router {router} is already downloading. Details: {text}')
                else:
                    if dry_run:
                        debug('Argument --dry-run provided. Skipping downloads.')
                        all_routers_ready_for_upgrade = True
                    else:
                        info('Downloading', full_release, 'to router', router, '...')
                        api.download_release(router, full_release)

        if not all_routers_ready_for_upgrade:
            debug('Waiting 30 seconds until the next check if routers have downloaded the software...')
            time.sleep(30)

        now = time.time()
        if now - download_started > timeout:
            error(f'Downloading current chunk took longer than {timeout} seconds.')


def upgrade(api, routers, target, timeout):
    upgrade_started = time.time()
    all_routers_done = False
    while not all_routers_done:
        all_routers_done = True
        for router in routers:
            if api.get_running_release(router) != get_unified_release(target):
                # router not yet done
                all_routers_done = False

                status, text = api.router_is_upgrading(router)
                if status:
                    #debug('Status:', status)
                    debug(f'Router {router} is already upgrading. Details: {text}')
                    # TODO: check if status == RUNNING????
                    # TODO: is HA handled properly? (asset state - node1 vs. node2)
                else:
                    available_releases = api.get_available_releases(router)
                    full_release = None
                    for release in available_releases:
                        if release.startswith(target):
                            full_release = release
                            break
                    if not full_release:
                        error('Release', target, 'is not available on router', router)
                    info('Upgrading router', router, 'to release', full_release, '...')
                    api.upgrade_router(router, full_release)

        if not all_routers_done:
            debug('Waiting 30 seconds until the next check if routers are upgraded...')
            time.sleep(30)

        now = time.time()
        if now - upgrade_started > timeout:
            error(f'Upgrading current chunk took longer than {timeout} seconds.')


def main():
    args = parse_arguments()

    if args.debug:
        set_debug()

    params = {}
    if args.host:
        params['host'] = args.host
        if args.user and args.password:
            params['user'] = args.user
            params['password'] = args.password

    api = RestGraphqlApi(**params, app=APP)

    releases = filter_releases(api.get_upgrade_versions())
    if args.list_releases:
        info('Available releases:')
        for release in releases:
            print(' *', release)
        return

    routers = select_routers(api, args)
    if not routers:
        error('Could not find matching routers to upgrade.')

    unified_releases = map(get_unified_release, releases)
    if args.release not in releases and args.release not in unified_releases:
        error('The specified release is not available.')

    #routers = range(10)
    debug('routers:', routers[:args.max])
    start = 0
    end = 0
    max = (args.max or len(routers))
    # iterate over routers up to maximum or all routers if no max given
    while end < max:
        if args.parallel:
            end = min(end + args.parallel, max)
        else:
            end = max
        chunk = list(routers[start:end])
        info('Processing routers in this chunk:', ', '.join(chunk))
        chunk_not_upgraded = []
        for router in chunk:
            running = api.get_running_release(router)
            if not running:
                warning('Could not retrieve running version for router:', router)
                continue
            if is_older_release(running, args.release):
                info('Router', router, 'is running version',
                     running, 'and will be upgraded.')
                chunk_not_upgraded.append(router)

        if chunk_not_upgraded:
            download_timeout = (args.download_timeout or args.timeout)
            download(api, chunk_not_upgraded, args.release, download_timeout, args.dry_run)
            debug('All routers in the chunk are ready for the upgrade.')

            if args.download_only:
                debug('Argument --download-only provided. Skipping upgrades.')
            elif args.dry_run:
                debug('Argument --dry-run provided. Skipping upgrades.')
            else:
                upgrade(api, chunk_not_upgraded, args.release, args.timeout)
            info('Chunk has been completed.')
        else:
            # nothing to do in this chunk
            info('No routers to be upgraded in this chunk.')

        if args.parallel:
            start += args.parallel


if __name__ == '__main__':
    main()
