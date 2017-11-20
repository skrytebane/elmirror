#!/usr/bin/env python3

import logging
from requests.packages.urllib3.util import Retry
from requests import exceptions
from requests.adapters import HTTPAdapter

BASE_URL="http://package.elm-lang.org/"

logger = logging.getLogger(__name__)
session = None # Initialize per worker.

def create_package_url(name_pkg, version):
    return "https://github.com/%{name_pkg}s/zipball/%{version}s/" % {
        'name_pkg': name_pkg,
        'version': version
    }

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

# Returns this...
# [
#     {
#         "name": "1602/elm-feather",
#         "summary": "Feather icons for elm",
#         "versions": [
#             "1.0.2",
#             "1.0.1",
#             "1.0.0"
#         ]
#     },
#     {
#         "name": "1602/json-schema",
#         "summary": "JSON Schema for elm",
#         "versions": [
#             "3.0.0",
#             "2.0.0",
#             "1.1.0",
#             "1.0.0"
#         ]
#     },
# ...
def get_all_package_names(session = setup_session()):
    return session.get(BASE_URL + "all-packages")