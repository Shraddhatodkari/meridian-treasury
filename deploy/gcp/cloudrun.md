# Google Cloud Run (source deploy, no Dockerfile) + Claude on Vertex AI

Cloud Run builds from source with Google Cloud buildpacks, which detect `pyproject.toml` and use the
`Procfile` entrypoint. No Dockerfile is involved.

```bash
gcloud services enable run.googleapis.com aiplatform.googleapis.com secretmanager.googleapis.com
gcloud secrets create meridian-mongodb-uri --data-file=-   # paste the Atlas URI
gcloud secrets create meridian-jwt-secret --data-file=-    # 32+ random bytes
gcloud run deploy meridian-api --source . --region us-east5 \
  --set-env-vars MERIDIAN_MODE=production,MERIDIAN_MARKET_DATA=live,MERIDIAN_LLM_PROVIDER=vertex,MERIDIAN_GCP_PROJECT=$PROJECT,MERIDIAN_GCP_REGION=us-east5,MERIDIAN_EMBEDDER=transformer \
  --set-secrets MERIDIAN_MONGODB_URI=meridian-mongodb-uri:latest,MERIDIAN_JWT_SECRET=meridian-jwt-secret:latest \
  --service-account meridian-api@$PROJECT.iam.gserviceaccount.com --no-allow-unauthenticated
```

- Grant the service account `roles/aiplatform.user` so it can call Claude on Vertex AI.
  Claude must be enabled in the Vertex AI Model Garden for the project, and `MERIDIAN_LLM_MODEL` must use
  the Vertex model ID for the Claude version you enable.
- Install the extra: `pip install -e ".[vertex,nlp]"`. Buildpacks pick up the `[project]` dependencies.
  For the extras, add `GOOGLE_PYTHON_PACKAGE_MANAGER`/requirements as your org standard requires.
- Put Cloud Run behind IAP or an internal load balancer. Treasury tools should not be public.

- Before go-live run `meridian doctor` with the same environment; it must report READY.
