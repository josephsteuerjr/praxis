#!/bin/sh
# Debian trixie security floors, not exact pins: allow later security updates.
set -eu

check_package() {
    package=$1
    minimum=$2
    status=$(dpkg-query -W -f='${db:Status-Status}' "$package")
    if [ "$status" != installed ]; then
        echo "$package is not installed" >&2
        exit 1
    fi
    installed=$(dpkg-query -W -f='${Version}' "$package")
    if ! dpkg --compare-versions "$installed" ge "$minimum"; then
        echo "$package $installed is below security minimum $minimum" >&2
        exit 1
    fi
}

check_package ca-certificates 20250419+deb13u1
check_package libbrotli1 1.1.0-2+deb13u1
