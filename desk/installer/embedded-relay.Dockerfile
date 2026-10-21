# Parent-authored build recipe; upstream embedded source is pinned separately.
FROM --platform=linux/amd64 rust:1-bookworm AS builder
ENV CARGO_BUILD_JOBS=1 CARGO_TERM_COLOR=always
RUN apt-get update && apt-get install -y --no-install-recommends pkg-config libssl-dev ca-certificates cmake perl build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /usr/src/codex
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked
