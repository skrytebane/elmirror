#!/usr/bin/env python3

"Simple server that serves the elm-package.json of the package queried."

import os
import json
import subprocess
from flask import Flask, request, make_response

app = Flask(__name__)

BASE_PATH = os.getenv("ELM_REPO_PATH", "/var/tmp/elmirror")

def run_git(*arguments):
    proc = subprocess.run(['git'] + list(arguments),
                          check=True,
                          stderr=subprocess.PIPE,
                          stdout=subprocess.PIPE)
    return proc.stdout.decode('UTF-8').strip()

def fetch_description(name, version):
    git_dir = os.path.join(BASE_PATH, name)
    # Just to make sure that it's actually valid JSON:
    return json.dumps(
        json.loads(
            run_git('--git-dir=' + git_dir, 'show', version + ':elm-package.json')))

@app.route('/description')
def get_description():
    elm_version = request.args.get('elm-package-version', '')
    if elm_version != '0.18':
        return "This silly server doesn't support version other than 0.18!", 400
    name = request.args.get('name')
    version = request.args.get('version')
    txt = fetch_description(name, version)
    resp = make_response(txt)
    resp.headers['Content-Type'] = 'application/json'
    return resp
