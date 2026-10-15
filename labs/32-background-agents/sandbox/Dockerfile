# The sandbox image: Python, the repository's CI tools, the agent and the egress proxy. Nothing else.
FROM {BASE_IMAGE}
COPY requirements.txt /opt/fleet/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check --require-hashes -r /opt/fleet/requirements.txt
COPY agent.py egress.py /opt/fleet/
# A home on tmpfs, no bytecode files in the repository, and no pip self-check reaching for the network.
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 RUFF_NO_CACHE=true
USER 1000:1000
WORKDIR /workspace
