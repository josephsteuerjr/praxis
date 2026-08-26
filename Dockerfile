FROM rust:1.85-slim-bookworm AS hands-builder
WORKDIR /build
COPY hands/Cargo.toml hands/Cargo.lock ./
COPY hands/src ./src
RUN cargo test --release --locked && cargo build --release --locked

FROM python:3.12-slim
WORKDIR /app
# Системные пакеты отделены от Python-зависимостей: этот manifest можно проверить
# через apt отдельно. Office работает headless; шрифты Noto покрывают Unicode/CJK,
# а Liberation/Carlito/Caladea — свободные метрические замены MS-шрифтов без EULA.
COPY requirements-apt.txt .
RUN apt-get update \
    && sed -e 's/#.*//' -e '/^[[:space:]]*$/d' requirements-apt.txt \
       | xargs -r apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global --add safe.directory /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scripts/smoke_office_pdf.py /usr/local/bin/smoke-office-pdf
RUN chmod +x /usr/local/bin/smoke-office-pdf && smoke-office-pdf
COPY . .
COPY --from=hands-builder /build/target/release/praxis-hands /usr/local/bin/praxis-hands
ENV PRAXIS_HANDS=/usr/local/bin/praxis-hands
CMD ["python", "bootguard.py"]
