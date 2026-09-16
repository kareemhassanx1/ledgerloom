FROM python:3.12-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements-dev.lock /opt/ledgerloom/requirements-dev.lock
RUN python -m pip install --no-cache-dir -r /opt/ledgerloom/requirements-dev.lock
WORKDIR /work
