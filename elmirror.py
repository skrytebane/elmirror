#!/usr/bin/env python3

import logging, requests, os, sys, argparse, json
import re, subprocess, shutil
from urllib.parse import urlparse
from requests.packages.urllib3.util import Retry
from requests import exceptions
from requests.adapters import HTTPAdapter

PACKAGE_URL="http://package.elm-lang.org/all-packages"
PACKAGE_ROOT="/var/tmp/elmirror/"

REPO_EXPR=re.compile(r"""
^
  ([a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9])  # User name
/
  ([a-zA-Z0-9_-][a-zA-Z0-9_.-]*)         # Repo path
$
""", re.VERBOSE)

logger = logging.getLogger(__name__)
session = None

def setup_session():
     global session
     if session:
          return session

     logger.debug('Creating new requests session')
     session = requests.Session()
     retry_strategy = Retry(total = 20,
                            backoff_factor = 0.2,
                            status_forcelist=[408, 429, 500, 502, 503, 504])
     session.mount('http://', HTTPAdapter(max_retries = retry_strategy))
     session.mount('https://', HTTPAdapter(max_retries = retry_strategy))
     return session

def package_url(name):
     return "https://github.com/%s" % name

def package_user_path(name):
     (user, _) = name.split('/')
     return os.path.join(PACKAGE_ROOT, user)

def package_git_dir(package):
     (user, repo) = package['name'].split('/')
     return os.path.join(PACKAGE_ROOT, user, repo)

def is_valid_repo_name(name):
     return REPO_EXPR.match(name)

def ensure_path_exists(path):
     os.makedirs(path, mode=0o755, exist_ok=True)

def is_package_url_available(url, session=setup_session()):
     r = session.head(url)
     if r.status_code in (200, 301):
          return True
     else:
          logger.warn('%s returned %s', url, r.status_code)
          return False

def parse_semver(s):
     m = re.match(r'^(\d+)\.(\d+)\.(\d+)$', s)
     return tuple(map(int, m.groups())) if m else None

def max_version(versions):
     return max({ v for v
                  in map(parse_semver, versions)
                  if v })

def run_git(*arguments):
     proc = subprocess.run(['git'] + list(arguments),
                           check=True,
                           stderr=subprocess.PIPE,
                           stdout=subprocess.PIPE)
     return [ line
              for line
              in proc.stdout.decode('UTF-8').strip().split('\n')
              if line ]

def get_git_tags(git_dir):
     return run_git('--git-dir=' + git_dir, 'tag', '-l')

def git_update_server_info(git_dir):
     return run_git('--git-dir=' + git_dir, 'update-server-info')

def create_zipballs(package):
     git_dir = package_git_dir(package)
     tags = set(get_git_tags(git_dir))
     versions = set(package.get('versions', [])).intersection(tags)
     destination_dir = os.path.join(git_dir, "zipball")
     ensure_path_exists(destination_dir)
     for version in versions:
          destination = os.path.join(destination_dir, version)
          if os.path.exists(destination):
               continue
          describe_id = run_git('--git-dir=' + git_dir,
                                'describe', '--always', version)[0]
          prefix = package['name'].replace('/', '-') + '-' + describe_id + '/'
          run_git('--git-dir=' + git_dir, 'archive', '--prefix=' + prefix,
                  '--output=' + destination, '--format=zip', version)

def has_complete_mirror(package):
     """Return True if the highest version number from the Git repo
tags are higher than or equal to the highest version number from the
package index."""
     versions = set(package.get('versions', []))
     git_dir = package_git_dir(package)

     tags = get_git_tags(git_dir)

     if not tags:
          return False
     else:
          return max_version(tags) >= max_version(versions)

def valid_git_repo(git_dir):
     """Try to determine if what is at git_dir looks like a valid
Git repo."""
     try:
          run_git('--git-dir=' + git_dir, 'log', '-1')
          return True
     except:
          return False

def update_package(package):
     name = package['name']
     url = package_url(name)
     git_dir = package_git_dir(package)

     if valid_git_repo(git_dir):
          if has_complete_mirror(package):
               logger.debug('Package %s is not in need of an update', name)
               return
          logger.info('Updating package %s...', name)
          if is_package_url_available(url):
               logger.debug('Package %s exists, looking for new versions...', name)
               run_git('--git-dir=' + git_dir, 'fetch', '--quiet', '-p', 'origin')
     else:
          logger.warn('Invalid git repo in %s. Removing and trying again...', git_dir)
          shutil.rmtree(git_dir)
          run_git('clone', '--quiet', '--mirror', url, git_dir)

def clone_package(package):
     name = package['name']
     url = package_url(name)
     git_dir = package_git_dir(package)

     if is_package_url_available(url):
          logger.debug('Initial mirror of package %s...', name)
          ensure_path_exists(package_user_path(name))
          run_git('clone', '--quiet', '--mirror', url, git_dir)

def mirror_package(package, session=setup_session()):
     git_dir = package_git_dir(package)

     if os.path.exists(git_dir):
          update_package(package)
     else:
          clone_package(package)

     if valid_git_repo(git_dir):
          create_zipballs(package)
          git_update_server_info(git_dir)

def get_package_index(url, session = setup_session()):
     "Return the Elm package index and also store it in PACKAGE_ROOT."
     logger.info('Fetching package index...')
     data = session.get(url).text
     ensure_path_exists(PACKAGE_ROOT)
     with open(os.path.join(PACKAGE_ROOT, 'all-packages'), 'w') as out:
          out.write(data)
     return json.loads(data)

def setup():
     global PACKAGE_ROOT

     parser = argparse.ArgumentParser()
     parser.add_argument('-d', '--destination-directory',
                         help='Destination directory for downloaded files.',
                         default=PACKAGE_ROOT)
     parser.add_argument('-i', '--override-index',
                         help='Override index from specified file. (For debugging.)')
     parser.add_argument('-b', '--base-url',
                         help='Elm packages base URL.',
                         default=PACKAGE_URL)
     parser.add_argument('-v', '--verbose',
                         help='Enable verbose output.',
                         action='store_true')
     parser.add_argument('-q', '--quiet',
                         help='Quiet execution.',
                         action='store_true')

     args = parser.parse_args()

     PACKAGE_ROOT=args.destination_directory

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
          with open(args.override_index, 'r') as fp:
               packages = json.load(fp)
     else:
          packages = get_package_index(args.base_url)

     for package in packages:
          # Just making sure the package names don't contain anything funny,
          # so we don't end up doing shutil.rmtree("foo/..") or similar.
          if is_valid_repo_name(package['name']):
               mirror_package(package)
          else:
               logger.warn('"%s" is not a valid package name, ignoring!', package['name'])

if __name__ == "__main__":
    main()
