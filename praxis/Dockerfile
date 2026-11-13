FROM python:3.12-slim-trixie
WORKDIR /app
# Системные пакеты отделены от Python-зависимостей: этот manifest можно проверить
# через apt отдельно. Office работает headless; шрифты Noto покрывают Unicode/CJK,
# а Liberation/Carlito/Caladea — свободные метрические замены MS-шрифтов без EULA.
COPY requirements-apt.txt .
COPY scripts/check-system-security-versions.sh /usr/local/bin/check-system-security-versions
RUN apt-get update \
    && sed -e 's/#.*//' -e '/^[[:space:]]*$/d' requirements-apt.txt \
       | xargs -r apt-get install -y --no-install-recommends \
    && sh /usr/local/bin/check-system-security-versions \
    && rm -rf /var/lib/apt/lists/* \
    && git config --global --add safe.directory /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scripts/smoke_office_pdf.py /usr/local/bin/smoke-office-pdf
RUN chmod +x /usr/local/bin/smoke-office-pdf && smoke-office-pdf
COPY . .
CMD ["python", "bootguard.py"]
