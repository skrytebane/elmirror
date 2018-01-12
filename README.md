# elmirror

A simple, hacky Python script to mirror the Elm package index and the
packages themselves from Github.

It requires Python 3 and the Requests library and `git` installed in
your `PATH`. You might want to ask for permission before running it,
but the repo is not that large and after the initial download, it
tries to avoid making additional requests if it can determine that all
the tags are present in the Git repository by checking against the
package index.

After it's done downloading a package, it will create a zipballs
directory containing zipped packages in the path expected by
elm-package if you serve them straight up as a web service. Also,
`git update-server-info` is executed so you can serve the repositories with
a dumb web server that doesn't know about Git repositories.

I've tried to make sure it won't delete all your files, but there's a
`shutil.rmtree` call in there that removes Git repositories if they
appear to be broken before cloning them again. (Can happen if the
mirroring process is interrupted.)

```bash
$ ./elmirror.py -h
usage: elmirror.py [-h] [-d DESTINATION_DIRECTORY] [-i OVERRIDE_INDEX]
                   [-p PACKAGE_INDEX_URL] [-v] [-q]

optional arguments:
  -h, --help            show this help message and exit
  -d DESTINATION_DIRECTORY, --destination-directory DESTINATION_DIRECTORY
                        Destination directory for downloaded files. Defaults
                        to "/var/tmp/elmirror/".
  -i OVERRIDE_INDEX, --override-index OVERRIDE_INDEX
                        Override index from specified local file. (For
                        debugging.)
  -p PACKAGE_INDEX_URL, --package-index-url PACKAGE_INDEX_URL
                        Elm package index base URL. Defaults to
                        "http://package.elm-lang.org/all-packages".
  -v, --verbose         Enable verbose output.
  -q, --quiet           Quiet execution.

```
