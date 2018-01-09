#!/usr/bin/env python3

import logging, requests, os, sys, argparse, json
import re, subprocess, shutil
from urllib.parse import urlparse
from requests.packages.urllib3.util import Retry
from requests import exceptions
from requests.adapters import HTTPAdapter

BASE_URL="http://package.elm-lang.org/"
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

def package_user_path(url):
     path_part = urlparse(url).path.strip('/')
     (user, _) = path_part.split('/')
     return os.path.join(PACKAGE_ROOT, user)

def package_git_dir(url):
     path_part = urlparse(url).path.strip('/')
     return os.path.join(PACKAGE_ROOT, path_part)

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
     m = re.match('^(\d+)\.(\d+)\.(\d+)$', s)
     return tuple(map(int, m.groups())) if m else None

def max_version(versions):
     return max({ v for v
                  in map(parse_semver, versions)
                  if v })

def run_git(*arguments):
     proc = subprocess.run(['/usr/bin/git'] + list(arguments),
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

def has_complete_mirror(package):
     """Return True if there are versions we don't have local tags for. I.e. if
we need to update this repository."""
     name = package['name']
     versions = set(package.get('versions', []))
     git_dir = package_git_dir(package_url(name))

     tags = get_git_tags(git_dir)

     if not tags:
          return False
     else:
          return max_version(tags) >= max_version(versions)

def valid_git_repo(git_dir):
     try:
          run_git('--git-dir=' + git_dir, 'log', '-1')
          return True
     except:
          return False

def mirror_package(package, no_update=False, session=setup_session()):
     name = package['name']

     logger.info('Mirroring package %s...', name)
     url = package_url(name)
     user_path = package_user_path(url)
     git_dir = package_git_dir(url)

     if os.path.exists(git_dir):
          if valid_git_repo(git_dir):
               if no_update or has_complete_mirror(package):
                    create_zipballs(package)
                    git_update_server_info(git_dir)
               elif is_package_url_available(url):
                    logger.debug('Package %s exists, looking for new versions...', name)
                    run_git('--git-dir=' + git_dir, 'fetch', '--quiet', '-p', 'origin')
                    create_zipballs(package)
                    git_update_server_info(git_dir)
          else:
               logger.warn('Invalid git repo in %s. Removing and trying again...', git_dir)
               shutil.rmtree(git_dir)
               run_git('clone', '--quiet', '--mirror', url, git_dir)
               create_zipballs(package)
               git_update_server_info(git_dir)
     elif is_package_url_available(url):
          logger.debug('Initial mirror of package %s...', name)
          ensure_path_exists(user_path)
          run_git('clone', '--quiet', '--mirror', url, git_dir)
          create_zipballs(package)
          git_update_server_info(git_dir)

def create_zipballs(package):
     full_name = package['name']
     git_dir = package_git_dir(package_url(full_name))
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
          prefix = full_name.replace('/', '-') + '-' + describe_id + '/'
          run_git('--git-dir=' + git_dir, 'archive', '--prefix=' + prefix,
                  '--output=' + destination, '--format=zip', version)

def get_package_index(session = setup_session()):
     "Return the Elm package index and also store it in PACKAGE_ROOT."
     logger.info('Fetching package index...')
     data = session.get(BASE_URL + "all-packages").text
     ensure_path_exists(PACKAGE_ROOT)
     with open(os.path.join(PACKAGE_ROOT, 'all-packages'), 'w') as out:
          out.write(data)
     return json.loads(data)

def setup():
     parser = argparse.ArgumentParser()
     parser.add_argument('-d', '--destination-directory',
                         help='Destination directory for downloaded files.',
                         default=PACKAGE_ROOT)
     parser.add_argument('-i', '--override-index',
                         help='Override index from specified file. (For debugging.)')
     parser.add_argument('-b', '--base-url',
                         help='Elm packages base URL.',
                         default=BASE_URL)
     parser.add_argument('-n', '--no-update',
                         help="Don't update packages we have. (For testing.)",
                         action='store_true')
     parser.add_argument('-v', '--verbose',
                         help='Enable verbose output.',
                         action='store_true')
     parser.add_argument('-q', '--quiet',
                         help='Quiet execution.',
                         action='store_true')

     args = parser.parse_args()

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
          packages = get_package_index()

     for package in packages:
          # Just making sure the package names don't contain anything funny,
          # so we don't end up doing shutil.rmtree("foo/..") or similar.
          if is_valid_repo_name(package['name']):
               mirror_package(package, args.no_update)
          else:
               logger.warn('"%s" is not a valid package name, ignoring!', package['name'])

if __name__ == "__main__":
    main()
