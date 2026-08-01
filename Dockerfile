# The LLM simulator, as an image — FOR CONSUMERS OUTSIDE THIS REPO.
#
# ⚠️ THIS IS NOT HOW THE RIG RUNS THE SIMULATOR, and it must not become how.
# scripts/install.sh builds the llm-sim-script ConfigMap from scripts/llm-sim.py
# and the compose stack mounts the same file. Three reasons that path stays as
# it is, and the third is the one that bites:
#
#   * `task selftest` and `--print` run the file directly with no build step,
#     which is what makes the simulator editable in seconds;
#   * the compose path mounts the same file, so an image would fork the two;
#   * an image-based Deployment pins a TAG, so a local edit to llm-sim.py would
#     stop reaching the cluster — silently, since the pod would still be
#     Running. That is precisely the failure the checksum annotation in
#     install.sh was added to fix, reintroduced one layer up.
#
# So: this image exists so someone who has NOT cloned this repo can point their
# own vLLM dashboards, recording rules and alert expressions at a realistic
# metric surface. See docs/llm-simulation.md.
#
# BUILD CONTEXT IS THE REPO ROOT, and the COPY below reaches scripts/llm-sim.py
# where it lives. There is deliberately no second copy of that file under
# docker/ or images/ — the image is a derived artefact on the same terms as
# dist/ and the dashboard ConfigMaps: one source, several forms. A drifted copy
# of the simulator would be undetectable from the outside, which is exactly the
# property tests/contracts/ exists to guarantee for the DCGM surface.
#
#   docker build -t vllm-metrics-sim .
#
# The stdlib-only constraint is what makes this trivial — a FROM and a COPY,
# with no pip install, no requirements file and no wheel to keep patched. That
# constraint is unchanged and is NOT reversed by this image existing.
FROM python:3.12-slim

# Non-root, matching how the rig runs it (manifests/llm/20-simulators.yaml sets
# runAsNonRoot with a read-only root filesystem). Nothing here writes to disk:
# the simulator holds its state in memory and serves it on a read.
RUN useradd --uid 65532 --create-home --shell /usr/sbin/nologin nonroot

WORKDIR /app

# Named llm_sim.py rather than llm-sim.py for the same reason the ConfigMap key
# is: a hyphen is not importable, and someone will eventually want to import it.
COPY scripts/llm-sim.py /app/llm_sim.py

USER 65532

# Documentation only — publishing the port is the runtime's job. This is the
# default the script falls back to, not a second place the number is decided.
EXPOSE 9401

# ⚠️ The port override is LLM_SIM_LISTEN_PORT and NOT the more obvious
# LLM_SIM_PORT. That name is not ours to read inside Kubernetes: kubelet injects
# a Docker-link-compatible <SVCNAME>_PORT for every Service in the namespace, so
# a Service named llm-sim sets LLM_SIM_PORT=tcp://<ip>:9401 and int() gets a
# URL. Every pod died at startup, visible only as a blank dashboard. The full
# account is in default_port() in the script itself. Do not "simplify" it back
# to the obvious name — the obvious name is the bug.
#
# No --profile: the simulator falls back to DEFAULT_PROFILE, which is a
# self-consistent steady tenant. Mount one and pass --profile to change it.
ENTRYPOINT ["python3", "/app/llm_sim.py"]

# Sanity, not liveness — this only proves the HTTP server is answering. Written
# with urllib rather than curl because the slim base has no curl and adding one
# would put a package manager in the image to check a socket.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD ["python3", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('LLM_SIM_LISTEN_PORT','9401')+'/metrics',timeout=4).read(1)"]
