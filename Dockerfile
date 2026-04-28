ARG BUILD_FROM
FROM $BUILD_FROM

# Install Python and dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-flask \
    py3-requests \
    sqlite \
    tzdata

# Set timezone (HA passes TZ environment variable)
ENV PYTHONUNBUFFERED=1 \
    FLASK_APP=app.main \
    FINANCE_DATA_DIR=/data \
    FINANCE_PORT=8765

# Copy app code
WORKDIR /finance
COPY app/ /finance/app/
COPY rootfs /

# Install Python deps not in Alpine packages
RUN pip3 install --break-system-packages --no-cache-dir \
    pdfplumber==0.11.4 \
    pypdf==5.1.0

# Make run script executable
RUN chmod a+x /etc/services.d/finance/run

EXPOSE 8765

# s6-overlay handles startup via /etc/services.d/finance/run
