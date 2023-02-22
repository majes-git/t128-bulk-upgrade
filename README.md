# t128-bulk-upgrade
This tool helps to upgrade large SSR deployments.

In general every SSR upgrade consists of two phases:

1. Download the target release from an online software repository to the selected router
2. Upgrade the software of the selected router to the target release

In order to perform a software upgrade of an SSR deployment, the `t128-bulk-upgrade` tool connects to conductor server. The conductor has its own asset repository of all routers of a deployment and provides functionality to trigger software downloads and upgrades.

The `t128-bulk-upgrade` command line tool is designed to run in chunks of configurable size. All routers in a chunk will download the target software release first, and, if everything was successful, all routers will be upgraded.
When a chunk was successfully upgraded, the tool continues with the next chunk until all (selected) routers are done or the maximum amount of routers (see `--max` commandline parameter) has reached.

## Connect to the conductor

The connection between `t128-bulk-upgrade` and the conductor is always HTTPS.

However, there is two ways of authenticating an API user:

1. using username and password (in the same fassion like using a web browser)
2. using a locally stored authentication file on the conductor.

### Connect via an external computer

Running `t128-bulk-upgrade` from an external computer requires IP connectivity to the conductor (works if the conductor can be reached from a web browser) and a Python (version 3) installation.

The following command line parameters are relevant to connect to the conductor:

```
  --host HOST           Conductor/router hostname
  --user USER           Conductor/router username (if no key auth)
  --password PASSWORD   Conductor/router password (if no key auth)
```

To test connectivity the following command can be used to connect to a conductor running at IP address 10.0.0.128:

```
python3 t128-bulk-upgrade --host 10.0.0.128 --user admin --password 128tRoutes --list-releases
```

The tool should list all available releases depending on the installed license.

### Connect directly from the conductor

Similar to external connections, the script can be ran directly from the conductor's Linux shell (typically with `t128` user). In this case `sudo` privileges are required to access the authentication file:

```
sudo python3 t128-bulk-upgrade --list-releases
```

## Router selection

By default the tool upgrades all routers in `Running` state, unless a filter is provided using the `--filter` commandline parameter (or in short `-f`).

The filter expect the following format: `key=PATTERN`.

The the following keys are supported:

* name.startswith
* name.contains
* name.equals
* version.startswith
* version.equals

where `name` refers to the router name and `version` to currently running version of a router:

Examples are:

```
sudo python3 t128-bulk-upgrade --filter name.contains=headend --release 5.4.11 --max 1
sudo python3 t128-bulk-upgrade --filter version.startswith=5.4 --release 5.5.8
```

Another way to select routers is a text file with router names, one router per line. This file can be specified with the `--router-file` parameter. For example:

```
$ cat routers.txt
headend1
branch1
branch2
$ sudo python3 t128-bulk-upgrade --router-file routers.txt --release 5.5.8
```

## Chunk adjustment

As all routers in a chunk are triggered in parallel for a download and upgrade, the number of routers per chunk is configurable by the parameter `--parallel` (or in short `-p`).

The default chunk size is `1` (one router).

## Timeouts

During the download and upgrade process, the `t128-bulk-upgrade` tool declares a chunk not be successful when a timeout has exceeded and stops further processing.

The default timeout for downloads and upgrades is one hour (`3600` seconds). It can be overridden by the `--timeout` parameter (or in short `-t`) followed by an integer value. The timeout value is in seconds.

The parameter `--download-timeout` allows to adjust the timeout for downloads only.

For example to allow more time for an upgrade (e.g. 2h = 7200s):

```
sudo python3 t128-bulk-upgrade --release 5.4.11 --timeout 7200
```

## Monitoring upgrade progress

The tool prints its actions into the terminal to standard output. To get a better view over the upgrade progress, a status file can be written with the `--status-file` parameter. The file is and so the router states are updated on a regular basis when the tool is running and can help to identify a failed router in case of errors.

These are the possible states of a router:

* `NOOP` (no operation) = No action was needed for the router (typically when the router was already upgraded before)
* `UNKNOWN` = The running version of a router could not determined (typically when a router is offline or has connection issues with the conductor)
* `DOWNLOAD_IN_PROGRESS` = download has not yet finished
* `DOWNLOAD_COMPLETED` = download has finished, but upgrade has not yet started
* `UPGRADE_IN_PROGRESS` = upgrade has not yet finished
* `UPGRADE_IN_PROGRESS` = upgrade has finished

## Other useful parameters

Especially during tests, some parameters are useful to adjust the upgrade process:

* `--dry-run` does not perform any download or upgrade action, but shows what would be performed.
* `--download-only` (or in short `-d`) does perform only download actions, but no upgrades. This allows to pre-download the software on routers to have it available at a later point (e.g. maintenance) in order to reduce the time for an actual upgrade window.
* `--wait-running` - wait until all routers in a chunk come back into `Running` state after upgrade  before continue with the next chunk. This should avoid upgrading all routers having salt issues. In such a severe situation the tool would stop after the first chunk.
* `--ignore-download-errors` - in some cases it may be desired to allow upgrades of all routers, even if some of them cannot download the target release. This parameter skips the failed routers and continues with the uprade for all other routers in the chunk.