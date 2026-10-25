//! One process-owned outbound client for models, inference, limits and OAuth.
//! Hélène owns persistence in helene.json; a changed relay spec restarts only
//! this child. Explicit direct mode never falls back to ambient system proxies.
use anyhow::{bail, Result};
use reqwest::Client;
use std::{sync::OnceLock, time::Duration};

pub fn build(mode: &str, address: &str) -> Result<Client> {
    let mut builder=Client::builder().tcp_keepalive(Duration::from_secs(30))
        .pool_idle_timeout(Duration::from_secs(90));
    match mode {
        "system" => {},
        "direct" => { builder=builder.no_proxy(); },
        "proxy" => {
            let url=reqwest::Url::parse(address).map_err(|_|anyhow::anyhow!("Invalid proxy address"))?;
            if !matches!(url.scheme(),"http"|"https"|"socks5"|"socks5h") || url.host_str().is_none()
                || url.query().is_some() || url.fragment().is_some() || !matches!(url.path(),""|"/") {
                bail!("Proxy requires HTTP, HTTPS or SOCKS5 host and port without a path");
            }
            let proxy=reqwest::Proxy::all(address).map_err(|_|anyhow::anyhow!("Cannot initialize the selected proxy"))?;
            builder=builder.no_proxy().proxy(proxy);
        },
        _=>bail!("Unknown relay network mode"),
    }
    builder.build().map_err(|_|anyhow::anyhow!("Cannot initialize the selected outbound route"))
}

pub fn client() -> Result<Client> {
    static CLIENT:OnceLock<Result<Client,String>>=OnceLock::new();
    CLIENT.get_or_init(||build(&std::env::var("RELAY_NETWORK_MODE").unwrap_or_else(|_|"system".into()),
        &std::env::var("RELAY_PROXY_URL").unwrap_or_default()).map_err(|e|e.to_string()))
        .as_ref().cloned().map_err(|error|anyhow::anyhow!(error.clone()))
}

pub fn status() -> serde_json::Value {
    serde_json::json!({"mode":std::env::var("RELAY_NETWORK_MODE").unwrap_or_else(|_|"system".into()),
        "proxy_configured":std::env::var("RELAY_PROXY_URL").is_ok_and(|s|!s.is_empty()),"generation_probe":false})
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read,Write};
    fn headers(stream:&mut std::net::TcpStream)->String {
        stream.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
        let mut data=Vec::new();let mut byte=[0];
        while !data.ends_with(b"\r\n\r\n") && data.len()<8192 {stream.read_exact(&mut byte).unwrap();data.push(byte[0]);}
        String::from_utf8(data).unwrap()
    }
    fn respond(stream:&mut std::net::TcpStream) {stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok").unwrap();}
    #[tokio::test]
    async fn http_proxy_receives_target_and_basic_auth_without_direct_dns() {
        let listener=std::net::TcpListener::bind("127.0.0.1:0").unwrap();let address=listener.local_addr().unwrap();
        let server=std::thread::spawn(move||{
            let (mut stream,_)=listener.accept().unwrap();let request=headers(&mut stream);
            assert!(request.starts_with("GET http://example.invalid/probe "));
            use base64::Engine;
            let authorization=request.lines().find(|line|line.to_ascii_lowercase().starts_with("proxy-authorization:")).unwrap().split_once(':').unwrap().1.trim();
            assert_eq!(authorization,format!("Basic {}",base64::engine::general_purpose::STANDARD.encode(b"user:secret")));
            respond(&mut stream);
        });
        let client=build("proxy",&format!("http://user:secret@{address}")).unwrap();
        assert!(client.get("http://example.invalid/probe").timeout(Duration::from_secs(3)).send().await.unwrap().status().is_success());
        server.join().unwrap();
    }
    #[tokio::test]
    async fn socks5h_uses_remote_dns_and_the_selected_username_password() {
        let listener=std::net::TcpListener::bind("127.0.0.1:0").unwrap();let address=listener.local_addr().unwrap();
        let server=std::thread::spawn(move||{
            let (mut stream,_)=listener.accept().unwrap();stream.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
            let mut greeting=[0;2];stream.read_exact(&mut greeting).unwrap();assert_eq!(greeting[0],5);
            let mut methods=vec![0;greeting[1] as usize];stream.read_exact(&mut methods).unwrap();assert!(methods.contains(&2));stream.write_all(&[5,2]).unwrap();
            let mut login=[0;2];stream.read_exact(&mut login).unwrap();assert_eq!(login[0],1);
            let mut username=vec![0;login[1] as usize];stream.read_exact(&mut username).unwrap();assert_eq!(username,b"user");
            let mut size=[0];stream.read_exact(&mut size).unwrap();let mut password=vec![0;size[0] as usize];stream.read_exact(&mut password).unwrap();assert_eq!(password,b"secret");stream.write_all(&[1,0]).unwrap();
            let mut connect=[0;4];stream.read_exact(&mut connect).unwrap();assert_eq!(connect,[5,1,0,3]);
            stream.read_exact(&mut size).unwrap();let mut hostname=vec![0;size[0] as usize];stream.read_exact(&mut hostname).unwrap();assert_eq!(hostname,b"example.invalid");
            let mut port=[0;2];stream.read_exact(&mut port).unwrap();assert_eq!(u16::from_be_bytes(port),80);
            stream.write_all(&[5,0,0,1,127,0,0,1,0,80]).unwrap();assert!(headers(&mut stream).starts_with("GET /probe "));respond(&mut stream);
        });
        let client=build("proxy",&format!("socks5h://user:secret@{address}")).unwrap();
        assert!(client.get("http://example.invalid/probe").timeout(Duration::from_secs(3)).send().await.unwrap().status().is_success());
        server.join().unwrap();
    }
    #[test]
    fn supported_routes_build_without_a_network_request() {
        assert!(build("system","").is_ok()); assert!(build("direct","").is_ok());
        for address in ["http://user:secret@127.0.0.1:9","https://127.0.0.1:9","socks5://user:secret@127.0.0.1:9","socks5h://127.0.0.1:9"] {
            assert!(build("proxy",address).is_ok(),"supported proxy route");
        }
    }
    #[test]
    fn invalid_route_refuses_without_exposing_credentials() {
        let error=build("proxy","unknown://user:secret@127.0.0.1").unwrap_err().to_string();
        assert!(!error.contains("secret")); assert!(build("proxy","https://proxy.invalid/private-path").is_err());
    }
}
