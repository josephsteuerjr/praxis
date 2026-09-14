"""Explicit local KEAT narrowing control; never issues or widens grants.

Use only against an approved local capture canon. This is not an authorization
API for remote/untrusted users; host filesystem ownership remains the boundary.
"""
import argparse
import json

from keat_capture import NarrowingReader


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--grant', required=True)
    parser.add_argument('--revoke', action='store_true', required=True)
    args = parser.parse_args(argv)
    try:
        NarrowingReader(args.directory, args.namespace).narrow(args.grant)
    except Exception:
        # Exceptions can include private paths or source snippets. Do not echo.
        print(json.dumps({'schema': 'keat.control-receipt.v1',
                          'operation': 'revoke', 'status': 'failed'}))
        return 1
    print(json.dumps({'schema': 'keat.control-receipt.v1',
                      'operation': 'revoke', 'status': 'committed'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
