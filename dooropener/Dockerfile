ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Python and pip
RUN apk add --no-cache python3 py3-pip

WORKDIR /app

# Isolated venv instead of clobbering Alpine's system site-packages with
# --break-system-packages; keeps the base image's Python untouched.
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "${VIRTUAL_ENV}"
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# Install dependencies first (better layer caching). gunicorn is pinned in
# requirements.txt, so no ad-hoc unpinned install here.
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py config.py security.py ha_client.py users_store.py ./
COPY templates/ ./templates/
COPY static/ ./static/

# Bypass s6-overlay init — single-process add-on doesn't need it
COPY run.sh /
RUN chmod +x /run.sh

# Last, so bumping the version does not invalidate the dependency layers above.
ARG BUILD_VERSION=0.0.0
ENV APP_VERSION=${BUILD_VERSION}

ENTRYPOINT []
CMD ["/run.sh"]
