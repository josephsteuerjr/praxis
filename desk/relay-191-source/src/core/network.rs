use anyhow::{bail, Result};
use reqwest::Client;
use std::{sync::OnceLock, time::Duration};

pub fn build(mode: &str, address: &str) -> Result<Client> {
    let mut builder = Client::builder().tcp_keepalive(Duration::from_secs(30));
    match mode {
        "system" => {},
        "direct" => builder = builder.no_proxy(),
        "proxy" => {
            let url = reqwest::Url::parse(address).map_err(|_| anyhow::anyhow!("Invalid proxy address"))?;
            if !matches!(url.scheme(), "http" | "https" | "socks5" | "socks5h") || url.host_str().is_none()
                || url.query().is_some() || url.fragment().is_some() || !matches!(url.path(), "" | "/") {
                bail!("Proxy requires HTTP, HTTPS or SOCKS5 host and port without a path");
            }
            let proxy = reqwest::Proxy::all(address).map_err(|_| anyhow::anyhow!("Cannot initialize proxy"))?;
            builder = builder.no_proxy().proxy(proxy);
        },
        _ => bail!("Unknown relay network mode"),
    }
    builder.build().map_err(|_| anyhow::anyhow!("Cannot initialize outbound route"))
}

pub fn client() -> Result<Client> {
    static CLIENT: OnceLock<Result<Client, String>> = OnceLock::new();
    CLIENT.get_or_init(|| build(&std::env::var("RELAY_NETWORK_MODE").unwrap_or_else(|_| "system".into()),
        &std::env::var("RELAY_PROXY_URL").unwrap_or_default()).map_err(|error| error.to_string()))
        .as_ref().cloned().map_err(|error| anyhow::anyhow!(error.clone()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};

    #[tokio::test]
    async fn http_proxy_routes_without_resolving_target() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
            let mut request = Vec::new();
            let mut byte = [0];
            while !request.ends_with(b"\r\n\r\n") {
                stream.read_exact(&mut byte).unwrap();
                request.push(byte[0]);
            }
            let request = String::from_utf8(request).unwrap();
            assert!(request.starts_with("GET http://example.invalid/probe "));
            assert!(request.to_lowercase().contains("proxy-authorization: basic dxnlcjpzzwnyzxq="));
            stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok").unwrap();
        });
        let response = build("proxy", &format!("http://user:secret@{address}")).unwrap()
            .get("http://example.invalid/probe").timeout(Duration::from_secs(3)).send().await.unwrap();
        assert_eq!(response.status(), 200);
        server.join().unwrap();
    }

    #[test]
    fn supported_proxy_schemes_and_redacted_refusals() {
        for scheme in ["http", "https", "socks5", "socks5h"] {
            assert!(build("proxy", &format!("{scheme}://user:secret@127.0.0.1:9")).is_ok());
        }
        assert!(!build("proxy", "unknown://user:secret@127.0.0.1").unwrap_err().to_string().contains("secret"));
        assert!(build("proxy", "http://proxy.invalid/private").is_err());
    }
}
