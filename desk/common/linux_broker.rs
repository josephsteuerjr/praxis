// Linux root broker client. SO_PEERCRED supplies the caller identity; no token or password.
#[cfg(target_os = "linux")]
fn linux_broker_call(request: &serde_json::Value, limit: std::time::Duration) -> Result<serde_json::Value, String> {
    use std::io::{BufRead, BufReader, Write};
    let mut socket = std::os::unix::net::UnixStream::connect("/run/helene/broker.sock")
        .map_err(|e| format!("брокер не отвечает: {e}. Включи нулевую сессию в настройках"))?;
    socket.set_read_timeout(Some(limit)).map_err(|e| e.to_string())?;
    socket.set_write_timeout(Some(limit)).map_err(|e| e.to_string())?;
    writeln!(socket, "{request}").map_err(|e| e.to_string())?;
    let mut line = String::new();
    BufReader::new(socket).read_line(&mut line).map_err(|e| e.to_string())?;
    serde_json::from_str(&line).map_err(|e| format!("ответ брокера не разобрался: {e}"))
}
