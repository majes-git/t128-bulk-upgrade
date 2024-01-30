#!/usr/bin/env python3

import argparse
import time

from lib.log import *
from lib.rest import RestGraphqlApi, get_unified_release

APP = 't128-bulk-upgrade'
RUNNING_STATUSES = ('RUNNING', 'RESYNCHRONIZING')


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
    parser.add_argument('--filter', '-f', action='append',
                        help='Filter routers based on FILTER (name.list, name.startswith, name.contains, name.equals, version.startswith, version.equals)')
    parser.add_argument('--router-file',
                        help='Read selected routers from file')
    parser.add_argument('--blacklist',
                        help='Ignore routers in blacklist file')
    parser.add_argument('--status-file',
                        help='Write router status to file')
    parser.add_argument('--debug',action='store_true',
                        help='Show debug messages')
    parser.add_argument('--dry-run', action='store_true',
                        help='Do not modify config, just print actions')
    parser.add_argument('--wait-running',action='store_true',
                        help='Wait until an ugraded router is RUNNING before continue')
    parser.add_argument('--ignore-download-errors',action='store_true',
                        help='Ignore errors during download and continue with upgrades')
    parser.add_argument('--version', action='version', version=f'{APP} 0.2')
    return parser.parse_args()


def filter_releases(releases):
    filtered = []
    for release in releases:
        major, minor, patch = release.split('-')[0].split('.')[:3]
        version = 1000000 * int(major) + 1000 * int(minor) + int(patch)
        if version >= 5004000:
            # ignore all releases before 5.4.0
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
        # don't include the conductor itself
        routers.remove(api.get_conductor_name())

    if args.blacklist:
        with open(args.blacklist) as fd:
            for name in fd.read().splitlines():
                if name in routers.copy():
                    debug(f'Router {name} is blacklisted.')
                    routers.remove(name)

    if args.filter:
        for filter in args.filter:
            if '=' not in filter:
                error('Filter is incorrect. Exiting.')

            key, value = filter.split('=')
            if key == 'name.list':
                for name in routers.copy():
                    if name not in value.split(','):
                        routers.remove(name)
                continue
            if key == 'name.startswith':
                for name in routers.copy():
                    if not name.startswith(value):
                        routers.remove(name)
                continue
            if key == 'name.equals':
                if value in routers:
                    routers = [value]
                else:
                    routers = []
                continue
            if key == 'name.contains':
                for name in routers.copy():
                    if value not in name:
                        routers.remove(name)
                continue

            if key.startswith('version.'):
                assets = api.get_assets()
                candidates = []
                if key == 'version.equals':
                    for asset in assets:
                        if asset['t128Version'] == value:
                            candidates.append(asset['routerName'])
                if key == 'version.startswith':
                    for asset in assets:
                        if asset['t128Version'].startswith(value):
                            candidates.append(asset['routerName'])
                for name in routers.copy():
                    if name not in candidates:
                        routers.remove(name)

    return routers


def write_status(router_status):
    try:
        with open(status_file, 'w') as fd:
            for router, status in router_status.items():
                fd.write(f'{router:{max_len_router_name + 4}} {status}\n')
    except NameError:
        # status_file not definied -> skip
        pass


def download(api, routers, router_status, target, timeout, dry_run, ignore_errors=False):
    download_started = time.time()
    all_routers_ready_for_upgrade = False
    first_loop = True
    while not all_routers_ready_for_upgrade:
        all_routers_ready_for_upgrade = True
        for router, releases in api.get_downloaded_releases(routers).items():
            # check if an upgrade is already in progress
            status_data = api.get_router_status(router)
            if not status_data:
                # something went wrong - skip this router
                continue
            if len(status_data) <= 2:
                statuses = [element[0] for element in status_data]
                texts = '|'.join([element[1] for element in status_data])
            else:
                error('Status is undefined:', status_data)

            if any([status == 'UPGRADING' for status in statuses]):
                # ignore this router for download operation
                router_status[router] = 'UPGRADE_IN_PROGRESS'

            elif any([status == 'DOWNLOADING' for status in statuses]):
                debug(f'Router {router} is downloading. Details: {texts}')
                router_status[router] = 'DOWNLOAD_IN_PROGRESS'
                all_routers_ready_for_upgrade = False

            else:
                releases = [get_unified_release(release) for release in releases]
                if get_unified_release(target) not in releases:
                    # found a router has not downloaded the target release yet
                    all_routers_ready_for_upgrade = False
                    debug('Downloaded releases on {}: {}'.format(router, releases))
                    full_release = api.get_full_release(router, target)
                    if not full_release:
                        write_status(router_status)
                        api.write_assets_data()
                        message = f'Release {target} is not available on router {router}'
                        router_status[router] = 'DOWNLOAD_NOT_POSSIBLE'
                        if ignore_errors:
                            warning(message)
                            # ignore this router for further processing
                            routers.remove(router)
                        else:
                            error(message)
                    elif dry_run:
                        debug('Argument --dry-run provided. Skipping downloads.')
                        all_routers_ready_for_upgrade = True
                    else:
                        info('Downloading', full_release, 'to router', router, '...')
                        api.download_release(router, full_release)
                        router_status[router] = 'DOWNLOAD_IN_PROGRESS'

                elif first_loop:
                    info(f'Download skipped on {router}')
                    router_status[router] = 'DOWNLOAD_NOT_NEEDED'

                elif router_status.get(router) != 'DOWNLOAD_NOT_NEEDED':
                    info(f'Download of {target} on {router} has completed.')
                    router_status[router] = 'DOWNLOAD_COMPLETED'

        write_status(router_status)

        if not all_routers_ready_for_upgrade:
            debug('Waiting 30 seconds until the next check if routers have downloaded the software...')
            time.sleep(30)

        now = time.time()
        if now - download_started > timeout:
            message = f'Downloading current chunk took longer than {timeout} seconds.'

            # set all routers that are still in progress to timed out
            for router, status in router_status.items():
                if status == 'DOWNLOAD_IN_PROGRESS':
                    router_status[router] = 'DOWNLOAD_TIMED_OUT'
                    # remove router since it cannot be upgraded anyways
                    routers.remove(router)
            write_status(router_status)

            if ignore_errors:
                warning(message)
                return
            else:
                error(message)

        first_loop = False


def upgrade(api, routers, router_status, target, timeout, wait_running=False):
    upgrade_done = {}
    upgrade_started = time.time()
    all_routers_done = False
    while not all_routers_done:
        all_routers_done = True
        for router in routers:
            # get router status
            status_data = api.get_router_status(router)
            if not status_data:
                # something went wrong - skip this router
                continue
            if len(status_data) <= 2:
                statuses = [element[0] for element in status_data]
                texts = '|'.join([element[1] for element in status_data])
            else:
                error('Status is undefined:', status_data)

            if any([status == 'UPGRADING' for status in statuses]):
                debug(f'Router {router} is upgrading. Details: {texts}')
                router_status[router] = 'UPGRADE_IN_PROGRESS'
                all_routers_done = False

            elif api.get_running_release(router) != get_unified_release(target):
                # router not yet done
                all_routers_done = False

                if all([status == 'RUNNING' for status in statuses]):
                    debug(f'Router {router} is in state RUNNING. Upgrading it.')
                    full_release = api.get_full_release(router, target)
                    if not full_release:
                        write_status(router_status)
                        api.write_assets_data()
                        message = 'Release', target, 'is not available on router', router
                        error(message)
                    info('Upgrading router', router, 'to release', full_release, '...')
                    api.upgrade_router(router, full_release)

                if all([status == 'DISCONNECTED' for status in statuses]):
                    debug(f'Router {router} is DISCONNECTED. Waiting for it to come back online.')

            elif wait_running and any([status not in RUNNING_STATUSES for status in statuses]):
                # router is not in RUNNING state, but already upgraded -> wait
                debug(f'Router {router} was upgraded. Waiting for it to get into RUNNING state.')
                all_routers_done = False

            else:
                if router_status.get(router) != 'UPGRADE_COMPLETED':
                    router_status[router] = 'UPGRADE_COMPLETED'
                    info(f'Upgrade of router {router} has completed.')

        write_status(router_status)

        if not all_routers_done:
            debug('Waiting 30 seconds until the next check if routers are upgraded...')
            time.sleep(30)

        now = time.time()
        if now - upgrade_started > timeout:
            error(f'Upgrading current chunk took longer than {timeout} seconds.')


def main():
    global status_file
    global max_len_router_name
    args = parse_arguments()

    if args.debug:
        set_debug(APP)

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

    if is_older_release(api.get_conductor_version(), args.release):
        error('The specified release must not be newer than conductor running.')

    routers = select_routers(api, args)
    if not routers:
        error('Could not find matching routers to upgrade.')

    unified_releases = map(get_unified_release, releases)
    if args.release not in releases and args.release not in unified_releases:
        error('The specified release is not available.')

    if args.status_file:
        status_file = args.status_file
        max_len_router_name = max([len(router) for router in routers])
    router_status = {}
    debug('All matching routers:', ', '.join(routers[:args.max]))
    start = 0
    end = 0
    maximum = (args.max or len(routers))
    # iterate over routers up to maximum or all routers if no max given
    while end < maximum:
        if args.parallel:
            end = min(end + args.parallel, maximum)
        else:
            end = maximum
        chunk = list(routers[start:end])
        info('Processing routers in this chunk:', ', '.join(chunk))
        chunk_not_upgraded = []
        for router in chunk:
            running = api.get_running_release(router)
            if not running:
                warning('Could not retrieve running version for router:', router)
                router_status[router] = 'UNKNOWN'
                continue
            if is_older_release(running, args.release):
                info(f'Router {router} is running version {running} and will be upgraded.')
                chunk_not_upgraded.append(router)
            else:
                info(f'Router {router} is already running version {running}. Skipping it.')
                router_status[router] = 'NOOP'

        write_status(router_status)
        if chunk_not_upgraded:
            download_timeout = (args.download_timeout or args.timeout)
            download(api, chunk_not_upgraded, router_status, args.release,
                     download_timeout, args.dry_run, args.ignore_download_errors)
            write_status(router_status)
            debug(f'All {len(chunk_not_upgraded)} routers in the chunk are ready for the upgrade.')

            if args.download_only:
                debug('Argument --download-only provided. Skipping upgrades.')
            elif args.dry_run:
                debug('Argument --dry-run provided. Skipping upgrades.')
            else:
                upgrade(api, chunk_not_upgraded, router_status, args.release,
                        args.timeout, args.wait_running)
                write_status(router_status)
            info('Chunk has been completed.')
        else:
            # nothing to do in this chunk
            info('No routers to be upgraded in this chunk.')

        if args.parallel:
            start += args.parallel


if __name__ == '__main__':
    main()
