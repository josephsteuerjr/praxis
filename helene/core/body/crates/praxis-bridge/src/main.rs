mod artifacts;
mod spool;

use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};
use artifacts::ArtifactStore;
use axum::extract::ws::{Message, WebSocket};
use axum::extract::{DefaultBodyLimit, Path, State, WebSocketUpgrade};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, put};
use axum::{Json, Router};
use chrono::Utc;
use clap::Parser;
use futures_util::{SinkExt, StreamExt};
use praxis_body_protocol::{Envelope, ExecutionKind, Frame, PeerRole};
use serde::Deserialize;
use serde_json::{Value, json};
use spool::{Spool, TO_CONTROLLER, TO_DEVICE};
use tokio::sync::{Mutex, RwLock, mpsc};
use tower_http::trace::TraceLayer;
use tracing::{debug, info, warn};
use uuid::Uuid;

#[derive(Debug, Parser)]
#[command(
    name = "praxis-bridge",
    version,
    about = "Brainless relay for Praxis execution bodies"
)]
struct Args {
    #[arg(long, env = "PRAXIS_BRIDGE_LISTEN", default_value = "127.0.0.1:9473")]
    listen: SocketAddr,
    #[arg(long, env = "PRAXIS_BRIDGE_STATE", default_value = "state/bridge")]
    state_dir: PathBuf,
    #[arg(long, env = "PRAXIS_BRIDGE_DEVICE_TOKEN")]
    device_token: String,
    #[arg(long, env = "PRAXIS_BRIDGE_CONTROLLER_TOKEN")]
    controller_token: String,
    #[arg(long, env = "PRAXIS_BRIDGE_CHUNK_SIZE", default_value_t = 4 * 1024 * 1024)]
    chunk_size: u64,
}

#[derive(Default)]
struct Peers {
    devices: PeerMap,
    controllers: PeerMap,
}

type PeerMap = RwLock<HashMap<String, PeerConnection>>;

#[derive(Default)]
struct DispatchLocks {
    by_device: Mutex<HashMap<String, Arc<Mutex<()>>>>,
}

impl DispatchLocks {
    async fn for_device(&self, device_id: &str) -> Arc<Mutex<()>> {
        let mut locks = self.by_device.lock().await;
        locks
            .entry(device_id.to_owned())
            .or_insert_with(|| Arc::new(Mutex::new(())))
            .clone()
    }
}

#[derive(Clone)]
struct PeerConnection {
    generation: Uuid,
    sender: mpsc::Sender<Message>,
    writer_abort: tokio::task::AbortHandle,
}

const PEER_QUEUE: usize = 256;
const PEER_ENQUEUE_TIMEOUT: Duration = Duration::from_secs(10);
const REPLAY_PAGE: usize = 128;

#[derive(Clone)]
struct AppState {
    bridge_instance_id: Uuid,
    device_token: Arc<str>,
    controller_token: Arc<str>,
    peers: Arc<Peers>,
    dispatch: Arc<DispatchLocks>,
    spool: Arc<Spool>,
    artifacts: Arc<ArtifactStore>,
}

#[derive(Debug, Deserialize)]
struct ControllerInvoke {
    #[serde(default)]
    request_id: Option<String>,
    #[serde(default)]
    operation_id: Option<String>,
    #[serde(default = "interactive_execution")]
    execution: ExecutionKind,
    capability: String,
    #[serde(default)]
    args: Value,
    #[serde(default)]
    deadline: Option<chrono::DateTime<Utc>>,
}

fn interactive_execution() -> ExecutionKind {
    ExecutionKind::Interactive
}

impl AppState {
    fn bearer(headers: &HeaderMap) -> Option<&str> {
        headers
            .get(axum::http::header::AUTHORIZATION)?
            .to_str()
            .ok()?
            .strip_prefix("Bearer ")
    }

    fn authorized(&self, role: PeerRole, headers: &HeaderMap) -> bool {
        let expected: &str = match role {
            PeerRole::Device => &self.device_token,
            PeerRole::Controller => &self.controller_token,
        };
        Self::bearer(headers).is_some_and(|value| value == expected)
    }

    fn authorized_any(&self, headers: &HeaderMap) -> bool {
        Self::bearer(headers)
            .is_some_and(|value| value == &*self.device_token || value == &*self.controller_token)
    }
}

/// Умер родитель — уходим. На Windows мост живёт в job-объекте движка Hélène и умирает
/// вместе с ним; на macOS/Linux такого нет, и осиротевший мост продолжал бы слушать порт
/// после выхода программы. Осиротевший процесс переезжает под init/launchd (ppid
/// становится 1) — это и есть сигнал. Родителя нет уже сейчас (нас подняли напрямую) —
/// сторож не ставится. Копия `mac::watch_parent` из тела: в бридже нет `mac.rs`,
/// тащить его сюда ради одной функции незачем.
#[cfg(unix)]
fn watch_parent(name: &'static str) {
    let parent = unsafe { libc::getppid() };
    if parent <= 1 {
        return;
    }
    let spawned = std::thread::Builder::new()
        .name(format!("{name}-parent-watch"))
        .spawn(move || {
            loop {
                std::thread::sleep(Duration::from_secs(1));
                let now = unsafe { libc::getppid() };
                if now != parent {
                    warn!(
                        "{name}: родитель {parent} исчез (теперь ppid {now}) — завершаюсь вместе с ним"
                    );
                    std::process::exit(0);
                }
            }
        });
    if let Err(error) = spawned {
        warn!("{name}: сторож родителя не поднялся: {error}");
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "praxis_bridge=info,tower_http=info".into()),
        )
        .json()
        .init();
    // Сторож родителя — после init лога: иначе предупреждение «сторож не поднялся» терялось
    // (ревью 25.09, A8 F5).
    #[cfg(unix)]
    watch_parent("praxis-bridge");
    let args = Args::parse();
    validate_tokens(&args.device_token, &args.controller_token)?;
    let chunk_size = args.chunk_size.clamp(64 * 1024, 16 * 1024 * 1024);
    let upload_body_limit = usize::try_from(chunk_size)?;
    std::fs::create_dir_all(&args.state_dir)?;
    let state = AppState {
        bridge_instance_id: Uuid::new_v4(),
        device_token: Arc::from(args.device_token),
        controller_token: Arc::from(args.controller_token),
        peers: Arc::new(Peers::default()),
        dispatch: Arc::new(DispatchLocks::default()),
        spool: Arc::new(Spool::open(&args.state_dir.join("spool.db"))?),
        artifacts: Arc::new(ArtifactStore::open(
            args.state_dir.join("artifacts"),
            chunk_size,
        )?),
    };
    let app = Router::new()
        .route("/healthz", get(health))
        .route("/v1/ws/device/{device_id}", get(device_ws))
        .route("/v1/ws/controller/{device_id}", get(controller_ws))
        .route("/v1/controller/{device_id}/invoke", post(controller_invoke))
        .route(
            "/v1/controller/{device_id}/requests/{request_id}",
            get(controller_result),
        )
        .route("/v1/artifacts/{sha256}", get(artifacts::status))
        .route("/v1/artifacts/{sha256}/offer", post(artifacts::offer))
        .route(
            "/v1/artifacts/{sha256}/chunks/{offset}",
            put(artifacts::put_chunk).layer(DefaultBodyLimit::max(upload_body_limit)),
        )
        .route("/v1/artifacts/{sha256}/complete", post(artifacts::complete))
        .route("/v1/artifacts/{sha256}/content", get(artifacts::get_chunk))
        .layer(TraceLayer::new_for_http())
        .with_state(state);
    let listener = tokio::net::TcpListener::bind(args.listen)
        .await
        .with_context(|| format!("bind {}", args.listen))?;
    info!(listen = %args.listen, "praxis bridge listening");
    axum::serve(listener, app).await?;
    Ok(())
}

fn validate_tokens(device: &str, controller: &str) -> Result<()> {
    if device.is_empty() || controller.is_empty() {
        anyhow::bail!("both device and controller tokens are required");
    }
    if device == controller {
        anyhow::bail!("device and controller tokens must be distinct");
    }
    Ok(())
}

async fn health(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(json!({
        "ok": true,
        "service": "praxis-bridge",
        "protocol": praxis_body_protocol::PROTOCOL,
        "instance_id": state.bridge_instance_id,
    }))
}

async fn controller_invoke(
    State(state): State<AppState>,
    Path(device_id): Path<String>,
    headers: HeaderMap,
    Json(input): Json<ControllerInvoke>,
) -> Response {
    if !state.authorized(PeerRole::Controller, &headers) {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"ok": false, "error": "invalid controller token"})),
        )
            .into_response();
    }
    if device_id.trim().is_empty() || input.capability.trim().is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"ok": false, "error": "device and capability are required"})),
        )
            .into_response();
    }
    let request_id = input
        .request_id
        .unwrap_or_else(|| Uuid::new_v4().to_string());
    let operation_id = input
        .operation_id
        .unwrap_or_else(|| format!("op-{}", Uuid::new_v4()));
    let dispatch = state.dispatch.for_device(&device_id).await;
    let _dispatch_guard = dispatch.lock().await;
    let seq = match state.spool.next_seq(&device_id, TO_DEVICE) {
        Ok(value) => value,
        Err(error) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"ok": false, "error": error.to_string()})),
            )
                .into_response();
        }
    };
    let envelope = Envelope::new(
        device_id.clone(),
        seq,
        Frame::Invoke {
            request_id: request_id.clone(),
            operation_id: operation_id.clone(),
            execution: input.execution,
            capability: input.capability,
            args: input.args,
            deadline: input.deadline,
        },
    );
    let raw = match serde_json::to_string(&envelope) {
        Ok(value) => value,
        Err(error) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"ok": false, "error": error.to_string()})),
            )
                .into_response();
        }
    };
    if let Err(error) = state.spool.store(TO_DEVICE, &envelope, &raw) {
        let conflict = error.downcast_ref::<spool::IdConflict>().is_some();
        return (
            if conflict {
                StatusCode::CONFLICT
            } else {
                StatusCode::INTERNAL_SERVER_ERROR
            },
            Json(json!({"ok": false,
                "code": if conflict { "id_conflict" } else { "spool_error" },
                "error": error.to_string()})),
        )
            .into_response();
    }
    let online = matches!(
        enqueue_published(&state.peers.devices, &device_id, Message::Text(raw.into())).await,
        Delivery::Enqueued
    );
    (
        StatusCode::ACCEPTED,
        Json(json!({
            "ok": true,
            "queued": true,
            "online": online,
            "request_id": request_id,
            "operation_id": operation_id,
            "seq": seq,
        })),
    )
        .into_response()
}

async fn controller_result(
    State(state): State<AppState>,
    Path((device_id, request_id)): Path<(String, String)>,
    headers: HeaderMap,
) -> Response {
    if !state.authorized(PeerRole::Controller, &headers) {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"ok": false, "error": "invalid controller token"})),
        )
            .into_response();
    }
    match state.spool.response(&request_id, &device_id) {
        Ok(Some(raw)) => match serde_json::from_str::<Value>(&raw) {
            Ok(value) => Json(json!({"ok": true, "response": value})).into_response(),
            Err(error) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({"ok": false, "error": error.to_string()})),
            )
                .into_response(),
        },
        Ok(None) => (
            StatusCode::ACCEPTED,
            Json(json!({"ok": true, "pending": true, "request_id": request_id})),
        )
            .into_response(),
        Err(error) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"ok": false, "error": error.to_string()})),
        )
            .into_response(),
    }
}

async fn device_ws(
    ws: WebSocketUpgrade,
    State(state): State<AppState>,
    Path(device_id): Path<String>,
    headers: HeaderMap,
) -> Response {
    upgrade(ws, state, device_id, headers, PeerRole::Device)
}

async fn controller_ws(
    ws: WebSocketUpgrade,
    State(state): State<AppState>,
    Path(device_id): Path<String>,
    headers: HeaderMap,
) -> Response {
    upgrade(ws, state, device_id, headers, PeerRole::Controller)
}

fn upgrade(
    ws: WebSocketUpgrade,
    state: AppState,
    device_id: String,
    headers: HeaderMap,
    role: PeerRole,
) -> Response {
    if device_id.trim().is_empty() || device_id.len() > 128 {
        return (StatusCode::BAD_REQUEST, "invalid device id").into_response();
    }
    if !state.authorized(role, &headers) {
        return (StatusCode::UNAUTHORIZED, "invalid bridge token").into_response();
    }
    ws.on_upgrade(move |socket| handle_socket(socket, state, device_id, role))
        .into_response()
}

async fn handle_socket(socket: WebSocket, state: AppState, device_id: String, role: PeerRole) {
    let (mut sink, mut stream) = socket.split();
    let (tx, mut rx) = mpsc::channel::<Message>(PEER_QUEUE);
    let writer = tokio::spawn(async move {
        while let Some(message) = rx.recv().await {
            if sink.send(message).await.is_err() {
                break;
            }
        }
    });
    let writer_abort = writer.abort_handle();
    let generation = Uuid::new_v4();
    let map = match role {
        PeerRole::Device => &state.peers.devices,
        PeerRole::Controller => &state.peers.controllers,
    };
    let dispatch = state.dispatch.for_device(&device_id).await;
    let _dispatch_guard = dispatch.lock().await;
    // Keep the peer unpublished while its durable backlog is queued. The per-device dispatch
    // gate makes producers wait until replay is complete, so every later live frame is enqueued
    // after every older durable frame. The peer map itself is only locked for remove/insert;
    // replay backpressure for one device must not block unrelated peers from connecting.
    let previous = map.write().await.remove(&device_id);
    if let Some(previous) = previous {
        previous.writer_abort.abort();
        warn!(%device_id, ?role, old_generation = %previous.generation, %generation, "peer connection replaced");
    }
    let ack = Envelope::new(
        device_id.clone(),
        0,
        Frame::HelloAck {
            bridge_instance_id: state.bridge_instance_id,
            resume_from: 0,
            connected_at: Utc::now(),
        },
    );
    if let Ok(raw) = serde_json::to_string(&ack)
        && !enqueue_unpublished(&tx, Message::Text(raw.into())).await
    {
        writer.abort();
        return;
    }

    let replay_direction = match role {
        PeerRole::Device => TO_DEVICE,
        PeerRole::Controller => TO_CONTROLLER,
    };
    let mut after_seq = 0;
    loop {
        let rows =
            match state
                .spool
                .pending_page(&device_id, replay_direction, after_seq, REPLAY_PAGE)
            {
                Ok(rows) => rows,
                Err(error) => {
                    warn!(%device_id, %error, "spool replay failed; peer remains unpublished");
                    writer.abort();
                    return;
                }
            };
        if rows.is_empty() {
            break;
        }
        for (seq, row) in rows {
            if !enqueue_unpublished(&tx, Message::Text(row.into())).await {
                writer.abort();
                return;
            }
            after_seq = seq;
        }
    }

    map.write().await.insert(
        device_id.clone(),
        PeerConnection {
            generation,
            sender: tx.clone(),
            writer_abort,
        },
    );
    drop(_dispatch_guard);
    info!(%device_id, ?role, %generation, "peer replay queued and connection published");

    while let Some(message) = stream.next().await {
        let dispatch = state.dispatch.for_device(&device_id).await;
        let _dispatch_guard = dispatch.lock().await;
        if !peer_is_current(map, &device_id, generation).await {
            break;
        }
        let raw = match message {
            Ok(Message::Text(value)) => value.to_string(),
            Ok(Message::Ping(value)) => {
                if tx.try_send(Message::Pong(value)).is_err() {
                    break;
                }
                continue;
            }
            Ok(Message::Close(_)) | Err(_) => break,
            _ => continue,
        };
        let mut envelope: Envelope = match serde_json::from_str(&raw) {
            Ok(value) => value,
            Err(error) => {
                warn!(%device_id, %error, "invalid bridge frame");
                continue;
            }
        };
        if envelope.validate().is_err() || envelope.device_id != device_id {
            warn!(%device_id, "rejected protocol or device mismatch");
            continue;
        }
        if let Frame::Ack { through_seq } = &envelope.frame {
            let direction = match role {
                PeerRole::Device => TO_DEVICE,
                PeerRole::Controller => TO_CONTROLLER,
            };
            if let Err(error) = state.spool.acknowledge(&device_id, direction, *through_seq) {
                warn!(%device_id, %error, "spool acknowledgement failed");
            }
            continue;
        }
        if role == PeerRole::Device {
            let response = match &envelope.frame {
                Frame::Accepted {
                    request_id,
                    operation_id,
                    ..
                } => Some((
                    request_id.as_str(),
                    Some(operation_id.as_str()),
                    "accepted",
                    false,
                )),
                Frame::Progress {
                    request_id,
                    operation_id,
                    status,
                    ..
                } => Some((
                    request_id.as_str(),
                    Some(operation_id.as_str()),
                    "progress",
                    status.terminal(),
                )),
                Frame::Result {
                    request_id,
                    operation_id,
                    ..
                } => Some((
                    request_id.as_str(),
                    Some(operation_id.as_str()),
                    "result",
                    true,
                )),
                Frame::Error {
                    request_id: Some(request_id),
                    operation_id,
                    ..
                } => Some((request_id.as_str(), operation_id.as_deref(), "error", true)),
                Frame::Cancelled {
                    request_id,
                    operation_id,
                    ..
                } => Some((
                    request_id.as_str(),
                    Some(operation_id.as_str()),
                    "cancelled",
                    true,
                )),
                _ => None,
            };
            if let Some((request_id, operation_id, frame_type, terminal)) = response
                && let Err(error) = state.spool.record_response(
                    request_id,
                    &device_id,
                    operation_id,
                    frame_type,
                    terminal,
                    &raw,
                )
            {
                warn!(%device_id, %request_id, %error, "record controller response failed");
            }
        }
        if matches!(
            &envelope.frame,
            Frame::Hello {
                role: PeerRole::Controller,
                ..
            }
        ) {
            continue;
        }
        if matches!(&envelope.frame, Frame::Heartbeat { .. }) {
            continue;
        }
        let (direction, peer_map) = match role {
            PeerRole::Device => (TO_CONTROLLER, &state.peers.controllers),
            PeerRole::Controller => (TO_DEVICE, &state.peers.devices),
        };
        // The bridge is the sole sequence authority for forwarded frames. The per-device gate
        // covers allocation, durable store and peer enqueue for HTTP and websocket producers, so
        // cumulative acknowledgements can never observe N+1 before N.
        envelope.seq = match state.spool.next_seq(&device_id, direction) {
            Ok(value) => value,
            Err(error) => {
                warn!(%device_id, %error, "forward sequence allocation failed");
                continue;
            }
        };
        let forward_raw = match serde_json::to_string(&envelope) {
            Ok(value) => value,
            Err(error) => {
                warn!(%device_id, %error, "forward frame serialization failed");
                continue;
            }
        };
        if let Err(error) = state.spool.store(direction, &envelope, &forward_raw) {
            warn!(%device_id, %error, "spool store failed");
            continue;
        }
        match enqueue_published(peer_map, &device_id, Message::Text(forward_raw.into())).await {
            Delivery::Enqueued => {}
            // Not a fault and not a disconnection. Hélène's controller is an HTTP poller and
            // never attaches as a websocket peer, so EVERY frame the body sends lands here —
            // 276 times a day in one journal. Said as a warning it read as "the link broke half
            // a second after it opened", and cost a day of hunting on 14.09. The frame is in the
            // spool and the peer replays it on attach; that is the whole story.
            Delivery::NoPeer => {
                debug!(%device_id, ?role, "no peer of the other role is attached; frame waits in the spool")
            }
            Delivery::QueueClosed => {
                warn!(%device_id, ?role, "peer outbound queue is closed; frame remains spooled")
            }
        }
    }

    remove_peer_if_current(map, &device_id, generation).await;
    writer.abort();
    info!(%device_id, ?role, %generation, "peer disconnected");
}

async fn enqueue_unpublished(sender: &mpsc::Sender<Message>, message: Message) -> bool {
    matches!(
        tokio::time::timeout(PEER_ENQUEUE_TIMEOUT, sender.send(message)).await,
        Ok(Ok(()))
    )
}

/// Why a forwarded frame did not reach a live peer. "Nobody is listening" and "the one who
/// listened has gone" are different facts, and only the second is a fault worth a warning.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Delivery {
    Enqueued,
    NoPeer,
    QueueClosed,
}

async fn enqueue_published(map: &PeerMap, device_id: &str, message: Message) -> Delivery {
    let Some(peer) = map.read().await.get(device_id).cloned() else {
        return Delivery::NoPeer;
    };
    if enqueue_unpublished(&peer.sender, message).await {
        return Delivery::Enqueued;
    }
    remove_peer_if_current(map, device_id, peer.generation).await;
    Delivery::QueueClosed
}

async fn peer_is_current(map: &PeerMap, device_id: &str, generation: Uuid) -> bool {
    map.read()
        .await
        .get(device_id)
        .is_some_and(|peer| peer.generation == generation)
}

async fn remove_peer_if_current(map: &PeerMap, device_id: &str, generation: Uuid) -> bool {
    let mut peers = map.write().await;
    let current = peers
        .get(device_id)
        .is_some_and(|peer| peer.generation == generation);
    let removed = current.then(|| peers.remove(device_id)).flatten();
    drop(peers);
    if let Some(peer) = removed {
        peer.writer_abort.abort();
        true
    } else {
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;

    #[test]
    fn device_token_cannot_become_a_controller_token() {
        assert!(validate_tokens("device", "controller").is_ok());
        assert!(
            validate_tokens("same", "same")
                .unwrap_err()
                .to_string()
                .contains("distinct")
        );
    }

    #[tokio::test]
    async fn stale_disconnect_does_not_remove_replacement_peer() {
        let map = PeerMap::default();
        let old_generation = Uuid::new_v4();
        let new_generation = Uuid::new_v4();
        let (old_sender, _old_receiver) = mpsc::channel(1);
        let (new_sender, _new_receiver) = mpsc::channel(1);
        let old_writer = tokio::spawn(std::future::pending::<()>());
        let new_writer = tokio::spawn(std::future::pending::<()>());

        map.write().await.insert(
            "pc".into(),
            PeerConnection {
                generation: old_generation,
                sender: old_sender,
                writer_abort: old_writer.abort_handle(),
            },
        );
        map.write().await.insert(
            "pc".into(),
            PeerConnection {
                generation: new_generation,
                sender: new_sender,
                writer_abort: new_writer.abort_handle(),
            },
        );

        assert!(!remove_peer_if_current(&map, "pc", old_generation).await);
        assert_eq!(
            map.read().await.get("pc").map(|peer| peer.generation),
            Some(new_generation)
        );
        assert!(remove_peer_if_current(&map, "pc", new_generation).await);
        assert!(!map.read().await.contains_key("pc"));
    }

    #[tokio::test]
    async fn changed_intent_conflicts_before_cached_success_can_be_polled() {
        let root = std::env::temp_dir().join(format!("praxis-bridge-intent-{}", Uuid::new_v4()));
        let state = AppState {
            bridge_instance_id: Uuid::new_v4(),
            device_token: Arc::from("device-token"),
            controller_token: Arc::from("controller-token"),
            peers: Arc::new(Peers::default()),
            dispatch: Arc::new(DispatchLocks::default()),
            spool: Arc::new(Spool::open(&root.join("spool.db")).unwrap()),
            artifacts: Arc::new(ArtifactStore::open(root.join("artifacts"), 64 * 1024).unwrap()),
        };
        for (index, text, expected) in [
            (0, "A", StatusCode::ACCEPTED),
            (1, "A", StatusCode::ACCEPTED),
            (2, "B", StatusCode::CONFLICT),
        ] {
            let mut headers = HeaderMap::new();
            headers.insert(
                axum::http::header::AUTHORIZATION,
                HeaderValue::from_static("Bearer controller-token"),
            );
            let response = controller_invoke(State(state.clone()), Path("pc".into()), headers,
                Json(ControllerInvoke {
                    request_id: Some("stable-r".into()), operation_id: Some("stable-o".into()),
                    execution: ExecutionKind::Interactive, capability: "desktop.element.act".into(),
                    args: json!({"do": "set_value", "select": {"automation_id": "field"}, "text": text}),
                    deadline: None,
                })).await;
            assert_eq!(response.status(), expected);
            if index == 0 {
                state
                    .spool
                    .record_response(
                        "stable-r",
                        "pc",
                        Some("stable-o"),
                        "result",
                        true,
                        r#"{"type":"result","ok":true,"result":{"value":"A"}}"#,
                    )
                    .unwrap();
            }
            if index == 2 {
                let bytes = axum::body::to_bytes(response.into_body(), 4096)
                    .await
                    .unwrap();
                let body: Value = serde_json::from_slice(&bytes).unwrap();
                assert_eq!(body["code"], "id_conflict");
                assert_eq!(body["ok"], false);
            }
        }
        assert_eq!(state.spool.pending_count("pc", TO_DEVICE), 2);
        assert!(
            state
                .spool
                .response("stable-r", "pc")
                .unwrap()
                .unwrap()
                .contains("A")
        );
        // Durable binding survives frame acknowledgement and bridge restart.
        state
            .spool
            .acknowledge("pc", TO_DEVICE, u64::MAX / 2)
            .unwrap();
        drop(state);
        let reopened = Spool::open(&root.join("spool.db")).unwrap();
        let changed = Envelope::new(
            "pc",
            100,
            Frame::Invoke {
                request_id: "stable-r".into(),
                operation_id: "stable-o".into(),
                execution: ExecutionKind::Interactive,
                capability: "desktop.element.act".into(),
                args: json!({"text": "B"}),
                deadline: None,
            },
        );
        assert!(
            reopened
                .store(
                    TO_DEVICE,
                    &changed,
                    &serde_json::to_string(&changed).unwrap()
                )
                .unwrap_err()
                .downcast_ref::<spool::IdConflict>()
                .is_some()
        );
        drop(reopened);
        let _ = std::fs::remove_dir_all(root);
    }

    #[tokio::test]
    async fn concurrent_http_invokes_are_enqueued_in_bridge_sequence_order() {
        let root = std::env::temp_dir().join(format!("praxis-bridge-order-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let state = AppState {
            bridge_instance_id: Uuid::new_v4(),
            device_token: Arc::from("device-token"),
            controller_token: Arc::from("controller-token"),
            peers: Arc::new(Peers::default()),
            dispatch: Arc::new(DispatchLocks::default()),
            spool: Arc::new(Spool::open(&root.join("spool.db")).unwrap()),
            artifacts: Arc::new(ArtifactStore::open(root.join("artifacts"), 64 * 1024).unwrap()),
        };
        let (sender, mut receiver) = mpsc::channel(64);
        let writer = tokio::spawn(std::future::pending::<()>());
        state.peers.devices.write().await.insert(
            "pc".into(),
            PeerConnection {
                generation: Uuid::new_v4(),
                sender,
                writer_abort: writer.abort_handle(),
            },
        );
        let mut headers = HeaderMap::new();
        headers.insert(
            axum::http::header::AUTHORIZATION,
            HeaderValue::from_static("Bearer controller-token"),
        );

        let mut calls = Vec::new();
        for index in 0..32 {
            let state = state.clone();
            let headers = headers.clone();
            calls.push(tokio::spawn(async move {
                controller_invoke(
                    State(state),
                    Path("pc".into()),
                    headers,
                    Json(ControllerInvoke {
                        request_id: Some(format!("request-{index}")),
                        operation_id: Some(format!("operation-{index}")),
                        execution: ExecutionKind::Interactive,
                        capability: "fs.stat".into(),
                        args: json!({"path": index.to_string()}),
                        deadline: None,
                    }),
                )
                .await
            }));
        }
        for call in calls {
            call.await.unwrap();
        }

        let mut observed = Vec::new();
        for _ in 0..32 {
            let Message::Text(raw) = receiver.recv().await.unwrap() else {
                panic!("expected text frame");
            };
            let envelope: Envelope = serde_json::from_str(raw.as_str()).unwrap();
            observed.push(envelope.seq);
        }
        assert_eq!(observed, (1..=32).collect::<Vec<_>>());

        drop(receiver);
        drop(state);
        let _ = std::fs::remove_dir_all(root);
    }
}
