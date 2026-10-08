# ADR-0005: Deploy without containers

**Status:** Accepted

**Context.** The target organisation standardises on managed runtimes and wants minimal platform overhead.

**Decision.** Package as a standard Python project (`pyproject.toml`, `Procfile`). Supported targets:
- Render native Python
- Google Cloud Run source deploy (buildpacks), with Claude on Vertex AI
- AWS App Runner source runtime, with Claude on Bedrock
- systemd on a VM behind nginx

**Consequences.** Simpler supply chain. Container hardening becomes the cloud provider's job. Containers
remain an easy later option because the app is 12-factor.
