import json
import os
import pathlib
import requests
import time

from lib.log import *

from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

RELEASE_CACHE_LOCATION = os.path.join(pathlib.Path.home(), '.{app}.release_cache')
MAX_CACHE_AGE = 86400 # 1 day
MAX_ASSETS_CACHE_TIME = 5


def get_unified_release(release_string):
    return '.'.join(release_string.split('-')[0].split('.')[:3])


class UnauthorizedException(Exception):
    pass


class MissingNonceException(Exception):
    pass


class RestGraphqlApi(object):
    """Representation of REST/Graphql connection."""

    token = None
    authorized = False
    headers = {
        'Content-Type': 'application/json',
    }
    assets = []
    assets_fetched_ts = 0

    def __init__(self, host='localhost', verify=False, user='admin', password=None, app=__file__):
        self.host = host
        self.verify = verify
        self.user = user
        self.password = password
        basename = os.path.basename(app).split('.')[0]
        self.user_agent = basename
        self.token_file = os.path.join(
             pathlib.Path.home(), '.{}.api.token'.format(basename))
        self.read_token()
        self.headers.update({
             'User-Agent': self.user_agent,
             'Authorization': f'Bearer {self.token}',
        })
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.session.hooks['response'].append(self.refresh_token)
        self.release_cache_location = RELEASE_CACHE_LOCATION.format(app=app)


    def read_token(self):
        try:
            debug('Reading API token from:', self.token_file)
            with open(self.token_file) as fd:
                self.token = fd.read()
        except FileNotFoundError:
            pass

    def write_token(self):
        try:
            with open(self.token_file, 'w') as fd:
                fd.write(self.token)
        except:
            raise

    def refresh_token(self, r, *args, **kwargs):
        if r.status_code == 401:
            token = self.login()
            self.session.headers.update({'Authorization': f'Bearer {token}'})
            r.request.headers['Authorization'] = self.session.headers['Authorization']
            return self.session.send(r.request, verify=self.verify)

    def get(self, location, **kwargs):
        """Get data per REST API."""
        url = 'https://{}/api/v1/{}'.format(self.host, location.strip('/'))
        request = self.session.get(url, verify=self.verify, **kwargs)
        return request

    def post(self, location, json, **kwargs):
        """Send data per REST API via post."""
        url = 'https://{}/api/v1/{}'.format(self.host, location.strip('/'))
        request = self.session.post(url, json=json, verify=self.verify, **kwargs)
        return request

    def patch(self, location, json, **kwargs):
        """Send data per REST API via post."""
        url = 'https://{}/api/v1/{}'.format(self.host, location.strip('/'))
        request = self.session.patch(url, json=json, verify=self.verify, **kwargs)
        return request

    def delete(self, location, **kwargs):
        """Delete data per REST API."""
        url = 'https://{}/api/v1/{}'.format(self.host, location.strip('/'))
        request = self.session.delete(url, verify=self.verify, **kwargs)
        return request

    def query(self, data):
        """Query data per GraphQL."""
        request = self.post('/graphql', json=data)
        return request

    def login(self):
        json = {
            'username': self.user,
        }
        if self.password:
            json['password'] = self.password
        else:
            key_file = 'pdc_ssh_key'
            if not os.path.isfile(key_file):
                key_file = '/home/admin/.ssh/pdc_ssh_key'

            key_content = ''
            with open(key_file) as fd:
                key_content = fd.read()
            json['local'] = key_content
        request = self.post('/login', json)
        if request.status_code == 200:
            self.token = request.json()['token']
            self.write_token()
            return self.token
        else:
            message = request.json()['message']
            raise UnauthorizedException(message)

    def get_conductor_name(self):
        system = self.get('/system').json()
        return system['router']

    def get_conductor_version(self):
        system = self.get('/system').json()
        return system['softwareVersion']

    def get_routers(self):
        return self.get('/router').json()

    def get_router_name(self):
        self.router_name = self.get_routers()[0]['name']
        return self.router_name

    def get_router_names(self):
        return [r['name'] for r in self.get_routers()]

    def get_nodes(self, router_name):
        return self.get('/config/running/authority/router/{}/node'.format(
            router_name)).json()

    def get_node_name(self):
        request = self.get('/router/{}/node'.format(self.router_name))
        self.node_name = request.json()[0]['name']
        return self.node_name

    def get_node_names(self, router_name):
        return [n['name'] for n in self.get_nodes(router_name)]

    def get_upgrade_versions(self, cached=True):
        releases = []
        cache_location = self.release_cache_location
        if cached and os.path.exists(cache_location):
            now = time.time()
            cache_expired = now - os.path.getmtime(cache_location) > MAX_CACHE_AGE
            if not cache_expired:
                try:
                    with open(cache_location) as fd:
                        debug('Reading repository releases from:', cache_location)
                        releases = json.load(fd)
                except json.decoder.JSONDecodeError:
                    # something went wrong reading the json file - try next option
                    pass
                return releases

        data = self.get('/upgrade/versions?onlyUpgrades=false').json()
        if data:
            releases = [d['version'].replace('.el7', '') for d in data]
            # write releases to cache
            try:
                with open(cache_location, 'w') as fd:
                    json.dump(releases, fd)
            except:
                pass
        return releases

    def get_assets(self):
        now = int(time.time())
        if now - self.assets_fetched_ts > MAX_ASSETS_CACHE_TIME:
            location = '/asset?verbose=true'
            r = self.get(location)
            if r.status_code == 200:
                self.assets = r.json()
                self.assets_fetched_ts = now
        return self.assets

    def write_assets_data(self):
        with open('/tmp/assets.json', 'w') as fd:
            json.dump(self.assets, fd)

    def get_running_release(self, router_name):
        releases = {}
        self.get_assets()
        for asset in self.assets:
            if asset['routerName'] == router_name:
                return get_unified_release(asset['t128Version'])

    def get_downloaded_releases(self, router_names):
        releases = {}
        self.get_assets()
        for asset in self.assets:
            router_name = asset['routerName']
            if router_name in router_names:
                releases[router_name] = asset['softwareVersions']['downloadedVersion']
        return releases

    def get_available_releases(self, router_name):
        releases = []
        for asset in self.assets:
            if router_name == asset['routerName']:
                releases = asset['softwareVersions']['availableVersion']
                break
        return releases

    def get_full_release(self, router_name, target):
        available_releases = self.get_available_releases(router_name)
        full_release = None
        for release in available_releases:
            if release.startswith(target):
                full_release = release
                break
        if not full_release:
            debug(f'Available_releases: {available_releases}')
        return full_release

    def get_router_status(self, router_name):
        self.get_assets()
        statuses = []
        for asset in self.assets:
            if router_name == asset['routerName']:
                status = asset['status'].upper()
                text = asset['text']
                if asset['softwareVersions']['refresh']['inProgress']:
                    status = 'DOWNLOADING'
                if asset['softwareVersions']['currentlyDownloadingVersion']:
                    status = 'DOWNLOADING'
                statuses.append((status, text))

        if not statuses:
            warning(f'No assets for router {router_name} found. This should not happen.')

        return statuses

    def send_command_yum_cache_refresh(self, router):
        return self.post('/provisioning/refresh', {'routerNames': [router]})

    def download_release(self, router, release):
        data = {
            'query': '''
                mutation AssetDownload($routerNames: [String]!, $version: String!) {
                    sendAssetDownloadSoftwareRequest(routerNames: $routerNames, version: $version) {
                        routerName
                        response
                    }
                }''',
            'variables': {
                'routerNames': [ router ],
                'version': release,
            }
        }
        request = self.query(data)

    def upgrade_router(self, router, release):
        data = {
            'query': '''
                mutation AssetUpgrade($routerNames: [String]!, $version: String!, $force: Boolean, $ignorePreCheck: Boolean) {
                    sendAssetUpgradeRequest(routerNames: $routerNames, version: $version, force: $force, ignorePreCheck: $ignorePreCheck) {
                        routerName
                        response
                    }
                }''',
            'variables': {
                'routerNames': [ router ],
                'version': release,
            }
        }
        request = self.query(data)
