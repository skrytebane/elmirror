#!/usr/bin/env python3

"""Script for mirroring the Elm package repository."""

import argparse
import glob
import html
import json
import logging
import operator
import os
import re
import shutil
import subprocess
import sys
from itertools import groupby

import requests
from requests.adapters import HTTPAdapter
# noinspection PyUnresolvedReferences
from requests.packages.urllib3.util import Retry

PACKAGE_INDEX_URL = "https://package.elm-lang.org/all-packages"
PACKAGE_ROOT = "/var/tmp/elmirror/"

REPO_EXPR = re.compile(r"""
^
  ([a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9])  # User name
/
  ([a-zA-Z0-9_-][a-zA-Z0-9_.-]*)         # Repo path
$
""", re.VERBOSE)

logger = logging.getLogger(__name__)


def setup_session():
    logger.debug('Creating new requests session')
    session = requests.Session()
    retry_strategy = Retry(total=20,
                           backoff_factor=0.2,
                           status_forcelist=[408, 429, 500, 502, 503, 504])
    session.mount('http://', HTTPAdapter(max_retries=retry_strategy))
    session.mount('https://', HTTPAdapter(max_retries=retry_strategy))
    return session


def package_url(name):
    return "https://github.com/%s" % name


def package_user_path(name):
    (user, _) = name.split('/')
    return os.path.join(PACKAGE_ROOT, user)


def package_git_dir(package_name):
    (user, repo) = package_name.split('/')
    return os.path.join(PACKAGE_ROOT, user, repo)


def is_valid_repo_name(name):
    return REPO_EXPR.match(name)


def ensure_path_exists(path):
    os.makedirs(path, mode=0o755, exist_ok=True)


def is_package_url_available(url, session=setup_session()):
    req = session.head(url)
    if req.status_code in (200, 301):
        return True

    logger.warning('%s returned %s', url, req.status_code)
    return False


def parse_semver(s):
    match = re.match(r'^(\d+)\.(\d+)\.(\d+)$', s)
    return tuple(map(int, match.groups())) if match else None


def max_version(versions):
    return max({v for v
                in map(parse_semver, versions)
                if v})


def raw_git(arguments):
    return subprocess.run(['git'] + arguments,
                          check=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)


def run_git_lines(*arguments):
    return [line for line
            in raw_git(list(arguments)).stdout.decode('UTF-8').strip().split('\n')
            if line]


def run_git_string(*arguments):
    return raw_git(list(arguments)).stdout.decode('UTF-8').strip()


def get_git_tags(git_dir):
    return run_git_lines('--git-dir=' + git_dir, 'tag', '-l')


def git_update_server_info(git_dir):
    return run_git_lines('--git-dir=' + git_dir, 'update-server-info')


def is_interesting_version(target_version, version_expr):
    return target_version in version_expr


def create_zipballs_and_descriptions(package_name, package_versions):
    git_dir = package_git_dir(package_name)
    tags = set(get_git_tags(git_dir))
    versions = set(package_versions).intersection(tags)

    zipball_destination_dir = os.path.join(git_dir, "zipball")
    description_destination_dir = os.path.join(git_dir, "descriptions")

    for version in versions:
        desc_destination = os.path.join(description_destination_dir, version)
        try:
            raw_description = run_git_string('--git-dir=' + git_dir, 'show', version + ':elm.json')

            description = json.loads(raw_description)
            if not is_interesting_version("0.19.", description.get('elm-version')):
                logger.warning("Don't care about version %s of %s",
                               description.get('elm-version'), package_name)
                continue

            ensure_path_exists(description_destination_dir)
            with open(desc_destination, 'w') as desc_file:
                desc_file.write(raw_description)
        except subprocess.CalledProcessError:
            # Probably an old (pre-0.19) package version with elm-package.json instead:
            logger.debug('Unable to get elm.json for %s v%s', package_name, version)
            continue

        ensure_path_exists(zipball_destination_dir)
        zip_destination = os.path.join(zipball_destination_dir, version)
        if not os.path.exists(zip_destination):
            abbrev_ref_id = run_git_string('--git-dir=' + git_dir,
                                           'show-ref', '--abbrev', '--hash', version)
            prefix = package_name.replace('/', '-') + '-' + abbrev_ref_id + '/'
            run_git_string('--git-dir=' + git_dir, 'archive', '--prefix=' + prefix,
                           '--output=' + zip_destination, '--format=zip', version)


def has_complete_mirror(package_name, package_versions):
    """Return True if the highest version number from the Git repo
tags are higher than or equal to the highest version number from the
package index."""
    versions = set(package_versions)
    git_dir = package_git_dir(package_name)

    tags = get_git_tags(git_dir)

    if not tags:
        return False

    return max_version(tags) >= max_version(versions)


def valid_git_repo(git_dir):
    """Try to determine if what is at git_dir looks like a valid
Git repo."""
    try:
        run_git_string('--git-dir=' + git_dir, 'log', '-1')
        return True
    except subprocess.CalledProcessError:
        return False


def careful_rmtree(path):
    path = path.rstrip('/')
    abspath = os.path.abspath(path)
    common_prefix = os.path.commonprefix([abspath, PACKAGE_ROOT])

    if os.path.isdir(path) and path == abspath and common_prefix == PACKAGE_ROOT:
        logger.warning("Deleting '%s'", path)
        shutil.rmtree(path)
    else:
        raise Exception("Something doesn't look right, not deleting '%s'" % path)


def update_package(package_name, package_versions):
    url = package_url(package_name)
    git_dir = package_git_dir(package_name)

    if valid_git_repo(git_dir):
        if has_complete_mirror(package_name, package_versions):
            logger.debug('Package %s is not in need of an update', package_name)
            return
        logger.info('Updating package %s...', package_name)
        if is_package_url_available(url):
            logger.debug('Package %s exists, looking for new versions...', package_name)
            run_git_string('--git-dir=' + git_dir, 'fetch', '--quiet', '-p', 'origin')
    else:
        logger.warning('Invalid git repo in %s. Removing and trying again...', git_dir)
        careful_rmtree(git_dir)
        run_git_string('clone', '--quiet', '--mirror', url, git_dir)


def clone_package(package_name):
    url = package_url(package_name)
    git_dir = package_git_dir(package_name)

    if is_package_url_available(url):
        logger.debug('Initial mirror of package %s...', package_name)
        ensure_path_exists(package_user_path(package_name))
        run_git_string('clone', '--quiet', '--mirror', url, git_dir)


def mirror_package(package_name, package_versions):
    git_dir = package_git_dir(package_name)

    # Just making sure the package names don't contain anything funny,
    # so we don't end up doing shutil.rmtree("foo/..") or similar.
    if not is_valid_repo_name(package_name):
        logger.warning('"%s" is not a valid package name, ignoring!', package_name)
    elif os.path.exists(git_dir):
        update_package(package_name, package_versions)
    else:
        clone_package(package_name)

    if valid_git_repo(git_dir):
        create_zipballs_and_descriptions(package_name, package_versions)
        git_update_server_info(git_dir)


def get_package_index(url, session=setup_session()):
    """Return the Elm package index and also store it in PACKAGE_ROOT."""
    logger.info('Fetching package index...')
    data = session.get(url).text
    with open(os.path.join(PACKAGE_ROOT, 'all-packages'), 'w') as out:
        out.write(data)
    return json.loads(data)


def gather_downloaded_package_metadata():
    metadata_filenames = glob.glob(os.path.join(PACKAGE_ROOT, "*", "*", "descriptions", "*"))

    def read_metadata(filename):
        with open(filename) as f:
            try:
                return json.load(f)
            except Exception as e:
                logger.warning('Unable to parse "%s": %s', filename, e)

    def is_valid(item):
        return item and \
               isinstance(item, dict) and \
               item.get('name', '').count('/') == 1

    valid_data = filter(is_valid, map(read_metadata, metadata_filenames))

    return {project_name: list(versions)
            for (project_name, versions)
            in groupby(valid_data, key=operator.itemgetter('name'))}


def make_zipball_urls(package_name, versions):
    return ", ".join([f'<a href="{package_name}/zipball/{version["version"]}" '
                      f'download="{package_name.split("/")[1]}-{version["version"]}.zip">'
                      f'{version["version"]}</a>'
                      for version
                      in versions])


def generate_index_html(package_metadatas):
    package_info = [f"<dl><dt><strong>{package_name}</strong> (<a href=\"{package_name}\">Git</a>)</dt>"
                    f"<dd>{html.escape(versions[0]['summary'])}<br>"
                    f"<strong>Releases:</strong> {make_zipball_urls(package_name, versions)}</dd></dl>"
                    for (package_name, versions)
                    in sorted(package_metadatas.items(), key=operator.itemgetter(0))]

    return '<!doctype html>\n' + \
           '<html><head><meta charset="UTF-8"><title>Elm packages</title></head><body>' + \
           '<h1>Elm package mirror</h1>\n' + \
           '\n'.join(package_info) + \
           "</body></html>"


def setup():
    global PACKAGE_ROOT

    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--destination-directory',
                        help="""Destination directory for downloaded files.
Defaults to "%s".""" % PACKAGE_ROOT,
                        default=PACKAGE_ROOT)
    parser.add_argument('-i', '--override-index',
                        help='Override index from specified local file. (For debugging.)')
    parser.add_argument('-p', '--package-index-url',
                        help="""Elm package index base URL.
Defaults to "%s".""" % PACKAGE_INDEX_URL,
                        default=PACKAGE_INDEX_URL)
    parser.add_argument('-v', '--verbose',
                        help='Enable verbose output.',
                        action='store_true')
    parser.add_argument('-q', '--quiet',
                        help='Quiet execution.',
                        action='store_true')

    args = parser.parse_args()

    PACKAGE_ROOT = args.destination_directory

    level = logging.ERROR if args.quiet else \
        (logging.DEBUG if args.verbose else logging.INFO)

    logging.basicConfig(
        stream=sys.stderr,
        level=level,
        format="%(asctime)s:%(levelname)s:%(module)s:%(funcName)s: %(message)s")

    return args


def main():
    args = setup()

    if args.override_index:
        with open(args.override_index, 'r') as package_file:
            packages = json.load(package_file)
    else:
        packages = get_package_index(args.package_index_url)

    for (package_name, package_versions) in packages.items():
        try:
            mirror_package(package_name, package_versions)
        except Exception as error:
            logger.error('Error mirroring %s: %s', package_name, error)

    ensure_path_exists(PACKAGE_ROOT)
    package_metadatas = gather_downloaded_package_metadata()
    with open(os.path.join(PACKAGE_ROOT, 'index.html'), 'w') as idx:
        idx.write(generate_index_html(package_metadatas))


if __name__ == "__main__":
    main()
