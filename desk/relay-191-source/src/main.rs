#[tokio::main]
async fn main() {
    if let Err(error) = praxis_relay::cli_main().await {
        eprintln!("Praxis Relay: {error:#}");
        std::process::exit(1);
    }
}
