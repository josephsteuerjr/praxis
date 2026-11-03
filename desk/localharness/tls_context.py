"""Verified HTTPS on minimal hosts, preserving owner-selected trust stores."""
import os
import ssl


def default_context():
    context=ssl.create_default_context()
    # Explicit trust configuration is authoritative, even if it is empty or
    # invalid. A normal desktop uses its existing system roots unchanged.
    if not context.get_ca_certs() and not any(os.environ.get(n) for n in ('SSL_CERT_FILE','SSL_CERT_DIR')):
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    return context
