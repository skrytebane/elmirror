#!/usr/bin/env python3

import logging, requests, os, sys
from urllib.parse import urlparse
from requests.packages.urllib3.util import Retry
from requests import exceptions
from requests.adapters import HTTPAdapter

BASE_URL="http://package.elm-lang.org/"
PACKAGE_ROOT="/var/tmp/elmirror/"

logger = logging.getLogger(__name__)
session = None # Initialize per worker.

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

def package_url(name, version):
     return "https://github.com/{name}/zipball/{version}/" \
          .format(name=name, version=version)

def package_local_path(url):
     global PACKAGE_ROOT
     path_part = urlparse(url).path.strip('/')
     return os.path.join(PACKAGE_ROOT, path_part)

def save_package(local_path, data):
     logger.info('Storing package to %s...', local_path)
     base_path = os.path.dirname(local_path)
     os.makedirs(base_path, mode=0o755, exist_ok=True)
     with open(local_path, 'wb') as fp:
          fp.write(data)

def local_package_exists(local_path):
     return os.path.exists(local_path) and \
          os.path.isfile(local_path) and \
          os.path.getsize(local_path) > 0

def fetch_package(name, version):
     logger.info('Fetching package %s v%s...', name, version)
     url = package_url(name, version)
     local_path = package_local_path(url)
     if local_package_exists(local_path):
          logger.info('Package %s v%s already downloaded!', name, version)
     else:
          save_package(local_path, session.get(url).content)

def get_all_package_versions(session = setup_session()):
     """Return a list of all (name, version) tuples
from the Elm package index."""
     logger.info('Fetching package index...')
     data = session.get(BASE_URL + "all-packages").json()
     return [ (pkg.get('name'), version)
              for pkg in data
              for version in reversed(sorted(pkg.get('versions', []))) ]

def main():
     logging.basicConfig(
          stream=sys.stderr,
          level=logging.INFO,
          format="%(asctime)s:%(levelname)s:%(module)s:%(funcName)s: %(message)s")

     (n, v) = get_all_package_versions()[0]
     fetch_package(n, v)

if __name__ == "__main__":
    main()
