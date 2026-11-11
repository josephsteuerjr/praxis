FROM rust:1.94-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /source
COPY . .
RUN cargo test --locked --bin praxis-relay-headless && cargo build --release --locked --bin praxis-relay-headless
FROM scratch
COPY --from=build /source/target/release/praxis-relay-headless /praxis-relay-headless
COPY --from=build /source/LICENSE /LICENSE
COPY --from=build /source/LICENSE-APACHE /LICENSE-APACHE
COPY --from=build /source/NOTICE /NOTICE
