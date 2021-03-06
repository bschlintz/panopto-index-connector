# pylint: disable=invalid-name
"""Compile pip requirements"""

from __future__ import absolute_import
from __future__ import print_function

# Std lib
import argparse
import json
import os
import platform
import sys
from subprocess import check_output, CalledProcessError

DIR = os.path.normpath(os.path.dirname(__file__))


# Allows controlling compilation sets by python version
COMPILE_SETS = {
    'py37': {
        'python-versions': "3.7",
        'source': 'requirements.in',
        'target': 'requirements.txt',
    },
    'build37': {
        'python-versions': "3.7",
        'source': 'requirements-build.in',
        'target': 'requirements-build.txt',
    },
}


class Pushd(object):
    """
    Context to push a directory
    """
    # pylint: disable=too-few-public-methods

    def __init__(self, *args):
        self.original_path = os.getcwd()
        self._newdir = os.path.join(*args)

    def __enter__(self):
        os.chdir(self._newdir)

    def __exit__(self, typ, value, throwback):
        os.chdir(self.original_path)


def parse_args(argv):
    """
    Get args
    """

    maj_min_version = '.'.join(platform.python_version_tuple()[:2])

    print('Identifying targets for python version', maj_min_version)

    choices = [a for a, v in COMPILE_SETS.items() if maj_min_version in v['python-versions']]

    parser = argparse.ArgumentParser(
        description='Compiles pip requirement files',
        epilog='Can only compile a source from a target it is meant to support. Use the "--list-all" '
               'to see options not available in your current python environment')

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--list-all', action='store_true', help='List all compile options for all python platforms')
    group.add_argument('-t', '--targets', nargs='+', choices=choices,
                       help='Compile requirements for given targets')

    update_group = parser.add_mutually_exclusive_group(required=False)
    update_group.add_argument('--upgrade', action='store_true',
                              help='Update all dependencies to latest release version')
    update_group.add_argument('--upgrade-package', nargs='+',
                              help='Update a given package and its dependencies if necessary')
    return parser.parse_args(argv)


def main():
    """Compile pip"""

    with Pushd(DIR):

        args = parse_args(sys.argv[1:])

        # If list all, pretty print
        if args.list_all:

            print(json.dumps(COMPILE_SETS, indent=4))

        # If targets supplied, compile them
        elif args.targets:

            if args.upgrade:
                base_command = ('pip-compile', '--upgrade', '--output-file')
            elif args.upgrade_package:
                base_command = ('pip-compile', '--upgrade-package') + tuple(args.upgrade_package) + ('--output-file',)
            else:
                base_command = ('pip-compile', '--output-file')

            try:

                for dep_target in args.targets:
                    source = COMPILE_SETS[dep_target]['source']
                    target = COMPILE_SETS[dep_target]['target']

                    # First compile the docs
                    print('Generating', target, 'from', source)
                    check_output(base_command + (target, source))

                    # Next, fix the file line to be local instead of full path
                    newreqlines = []
                    with open(target, 'r') as reqfile:
                        for line in reqfile:
                            if line.startswith('-e'):
                                line = '-e .\n'
                            newreqlines.append(line)
                    with open(target, 'w') as reqfile:
                        reqfile.writelines(newreqlines)

            except CalledProcessError as cpe:
                print('Compile %s to %s failed: %s', source, target, cpe.returncode)

        else:
            raise NotImplementedError("Some new args must've been added which we didn't expect")


if __name__ == '__main__':
    main()
